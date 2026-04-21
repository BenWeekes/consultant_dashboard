import sqlite3
import time
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from consultant_dashboard.core import realtime
from tests.support import ConsultantDashboardTestCase
from consultant_dashboard.core.auth import _load_admin_users
from consultant_dashboard.core.db import (
    create_client_access_link,
    get_consultant_by_email,
    get_db,
)
from consultant_dashboard.core.meetings import get_pair_channel, make_signed_meeting_access_token
from consultant_dashboard.core.messaging import hash_access_token


class ConsultantDashboardWebTest(ConsultantDashboardTestCase):
    def _future_local_time(self, *, days: int = 1, minutes: int = 0, timezone_name: str = "Europe/London") -> str:
        return (
            datetime.now(timezone.utc) + timedelta(days=days, minutes=minutes)
        ).astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%dT%H:%M")

    def test_consultant_routes_require_login(self):
        response = self.client.get("/consultant/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consultant/login", response.location)

    def test_admin_routes_require_login(self):
        response = self.client.get("/admin/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.location)

    def test_consultant_login_rejects_bad_password(self):
        response = self.client.post(
            "/consultant/login",
            data={"email": "consultant@example.com", "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Invalid email or password", response.data)

    def test_admin_login_rejects_bad_password(self):
        response = self.client.post(
            "/admin/login",
            data={"email": "admin@example.com", "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Invalid email or password", response.data)

    def test_consultant_dashboard_renders_after_login(self):
        self.consultant_login()
        response = self.client.get("/consultant/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Consultant Dashboard", response.data)
        self.assertIn(b"Signed in as Test Consultant", response.data)
        self.assertIn(b"Linked Clients", response.data)

    def test_consultant_can_create_client_and_view_detail(self):
        self.consultant_login()
        response = self.client.post(
            "/consultant/clients/new",
            data={
                "display_name": "Jamie Demo",
                "email": "jamie@example.com",
                "initial_password": "jamiepass123",
                "phone_country_code": "UK",
                "phone_number": "07700900333",
                "notes": "General check-in.",
                "direction": "Review coping strategies.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Jamie Demo", response.data)
        self.assertIn(b"Review coping strategies.", response.data)
        self.assertIn(b"Edit Contact Details", response.data)
        self.assertIn(b"Messages", response.data)

        db = get_db(self.app.config)
        row = db.execute("SELECT password_hash FROM clients WHERE email = ?", ("jamie@example.com",)).fetchone()
        db.close()
        self.assertTrue(row["password_hash"])

    def test_consultant_client_creation_rejects_short_initial_password(self):
        self.consultant_login()
        response = self.client.post(
            "/consultant/clients/new",
            data={
                "display_name": "Jamie Demo",
                "email": "jamie@example.com",
                "initial_password": "short",
                "phone_country_code": "UK",
                "phone_number": "07700900333",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Initial password must be at least 8 characters.", response.data)

        db = get_db(self.app.config)
        row = db.execute("SELECT id FROM clients WHERE email = ?", ("jamie@example.com",)).fetchone()
        db.close()
        self.assertIsNone(row)

    def test_consultant_can_update_client_access_fields(self):
        self.consultant_login()
        response = self.client.post(
            f"/consultant/clients/{self.client_id}",
            data={
                "display_name": "Alex Demo Updated",
                "email": "alex.updated@example.com",
                "phone_country_code": "US",
                "phone_number": "4155551212",
                "notification_email": "notify@example.com",
                "escalation_phone_country_code": "UK",
                "escalation_phone_number": "07700900444",
                "reset_password": "clientreset123",
                "notes": "Updated generalized context.",
                "direction": "Start with a check-in on sleep.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client updated", response.data)
        self.assertIn(b"Temporary client password updated", response.data)
        self.assertIn(b"Alex Demo Updated", response.data)
        self.assertIn(b"Start with a check-in on sleep.", response.data)

        db = get_db(self.app.config)
        row = db.execute(
            "SELECT email, phone_number, escalation_phone_number, password_hash FROM clients WHERE id = ?",
            (self.client_id,),
        ).fetchone()
        identity = db.execute(
            "SELECT email_hash, phone_hash FROM client_auth_identities WHERE client_id = ?",
            (self.client_id,),
        ).fetchone()
        db.close()
        self.assertEqual(row["email"], "alex.updated@example.com")
        self.assertEqual(row["phone_number"], "+14155551212")
        self.assertEqual(row["escalation_phone_number"], "+447700900444")
        self.assertTrue(row["password_hash"])
        self.assertIsNotNone(identity["email_hash"])
        self.assertIsNotNone(identity["phone_hash"])

    def test_consultant_can_update_notes_and_direction_from_session_page(self):
        self.ingest_session(session_id="sess_notes_001", urgent_escalation=False)
        self.consultant_login()
        response = self.client.post(
            "/consultant/sessions/sess_notes_001",
            data={
                "notes": "Updated notes from session page.",
                "direction": "Focus next session on sleep and stress.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Notes and direction updated", response.data)
        self.assertIn(b"Updated notes from session page.", response.data)
        self.assertIn(b"Focus next session on sleep and stress.", response.data)

        db = get_db(self.app.config)
        row = db.execute(
            "SELECT notes_current, direction_current FROM clients WHERE id = ?",
            (self.client_id,),
        ).fetchone()
        db.close()
        self.assertEqual(row["notes_current"], "Updated notes from session page.")
        self.assertEqual(row["direction_current"], "Focus next session on sleep and stress.")

    def test_consultant_can_delete_client_from_detail_page(self):
        self.consultant_login()
        response = self.client.post(
            f"/consultant/clients/{self.client_id}/delete",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client deleted", response.data)

        db = get_db(self.app.config)
        row = db.execute("SELECT is_active FROM clients WHERE id = ?", (self.client_id,)).fetchone()
        db.close()
        self.assertEqual(row["is_active"], 0)

    def test_consultant_can_delete_session_from_detail_page(self):
        self.ingest_session(session_id="sess_delete_001", urgent_escalation=False)
        self.consultant_login()
        response = self.client.post(
            "/consultant/sessions/sess_delete_001/delete",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Session deleted", response.data)

        db = get_db(self.app.config)
        row = db.execute("SELECT id FROM sessions WHERE id = ?", ("sess_delete_001",)).fetchone()
        db.close()
        self.assertIsNone(row)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_consultant_can_schedule_meeting(self, mocked_deliver_email):
        self.consultant_login()
        response = self.client.get("/consultant/meetings/new")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"New Meeting", response.data)
        self.assertIn(b"Select a client", response.data)
        self.assertIn(b"Alex Demo", response.data)

        response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Meet Now",
                "transcription_enabled": "1",
                "audio_biomarkers_enabled": "1",
                "video_biomarkers_enabled": "0",
                "transcription_provider": "agora_stt",
                "transcription_language": "en-US",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "Let's talk live.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Meeting scheduled", response.data)
        self.assertIn(b"Meet Now", response.data)
        mocked_deliver_email.assert_called_once()

        db = get_db(self.app.config)
        row = db.execute(
            """
            SELECT title, status, invite_delivery_status, response_access_link_id,
                   transcription_enabled, audio_biomarkers_enabled, video_biomarkers_enabled,
                   transcription_provider, transcription_language
            FROM scheduled_meetings ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
        db.close()
        self.assertEqual(row["title"], "Meet Now")
        self.assertEqual(row["status"], "scheduled")
        self.assertEqual(row["invite_delivery_status"], "sent")
        self.assertTrue(row["response_access_link_id"])
        self.assertEqual(row["transcription_enabled"], 1)
        self.assertEqual(row["audio_biomarkers_enabled"], 1)
        self.assertEqual(row["video_biomarkers_enabled"], 0)
        self.assertEqual(row["transcription_provider"], "agora_stt")
        self.assertEqual(row["transcription_language"], "en-US")

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_consultant_can_schedule_ai_meeting_with_weekly_repeat(self, mocked_deliver_email):
        self.consultant_login()
        response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "meeting_type": "ai",
                "repeat_weekly": "1",
                "title": "MindFix AI check-in",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "AI reminder.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MindFix AI check-in", response.data)

        db = get_db(self.app.config)
        row = db.execute(
            """
            SELECT meeting_type, repeat_weekly, channel_name,
                   transcription_enabled, audio_biomarkers_enabled, video_biomarkers_enabled,
                   transcription_provider, transcription_language
            FROM scheduled_meetings ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
        db.close()
        self.assertEqual(row["meeting_type"], "ai")
        self.assertEqual(row["repeat_weekly"], 1)
        self.assertEqual(row["channel_name"], get_pair_channel(self.consultant_id, self.client_id, "ai"))
        self.assertEqual(row["transcription_enabled"], 1)
        self.assertEqual(row["audio_biomarkers_enabled"], 1)
        self.assertEqual(row["video_biomarkers_enabled"], 1)
        self.assertEqual(row["transcription_provider"], "agora_stt")
        self.assertEqual(row["transcription_language"], "en-US")
        invite_plain_text = mocked_deliver_email.call_args.kwargs.get("plain_text_override", "")
        self.assertIn("Europe/London", invite_plain_text)
        self.assertIn("UTC+01:00", invite_plain_text)
        self.assertIn("Repeats: Weekly", invite_plain_text)
        self.assertNotIn("Open meeting details:", invite_plain_text)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_consultant_can_disable_biomarkers_on_meeting_form(self, mocked_deliver_email):
        self.consultant_login()
        response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Signals Off",
                "meeting_type": "human",
                "audio_biomarkers_enabled": "0",
                "video_biomarkers_enabled": "0",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        db = get_db(self.app.config)
        row = db.execute(
            """
            SELECT audio_biomarkers_enabled, video_biomarkers_enabled
            FROM scheduled_meetings ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
        db.close()
        self.assertEqual(row["audio_biomarkers_enabled"], 0)
        self.assertEqual(row["video_biomarkers_enabled"], 0)

    def test_session_detail_shows_signal_disabled_states(self):
        self.ingest_session(session_id="sess_disabled_signals_001", urgent_escalation=False)
        db = get_db(self.app.config)
        db.execute(
            """
            UPDATE sessions
            SET transcription_enabled = 0,
                audio_biomarkers_enabled = 0,
                video_biomarkers_enabled = 0,
                transcript_storage_key = NULL,
                biomarker_storage_key = NULL
            WHERE id = ?
            """,
            ("sess_disabled_signals_001",),
        )
        db.commit()
        db.close()

        self.consultant_login()
        response = self.client.get("/consultant/sessions/sess_disabled_signals_001")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Speech-to-text was not enabled for this session.", response.data)
        self.assertIn(b"Audio and video biomarkers were not enabled for this session.", response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_meeting_detail_shows_signal_settings(self, mocked_deliver_email):
        self.consultant_login()
        response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Signal Detail",
                "meeting_type": "human",
                "transcription_enabled": "1",
                "audio_biomarkers_enabled": "1",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Signals", response.data)
        self.assertIn(b"Speech-to-text", response.data)
        self.assertIn(b"Audio biomarkers", response.data)
        self.assertIn(b"Video biomarkers", response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_immediate_meetings_skip_overlap_validation(self, mocked_deliver_email):
        self.consultant_login()
        now_value = self._future_local_time(days=0, minutes=0)

        first = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Immediate One",
                "scheduled_start_at": now_value,
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(first.status_code, 200)
        self.assertIn(b"Opening meeting", first.data)
        self.assertIn(b"Open Meeting", first.data)

        second = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Immediate Two",
                "scheduled_start_at": now_value,
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(second.status_code, 200)
        self.assertIn(b"active meeting", second.data)
        self.assertIn(b"Open Meeting", second.data)

        db = get_db(self.app.config)
        count = db.execute(
            "SELECT COUNT(*) AS c FROM scheduled_meetings WHERE title IN ('Immediate One', 'Immediate Two')"
        ).fetchone()["c"]
        db.close()
        self.assertEqual(count, 1)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_human_and_ai_open_meetings_do_not_block_each_other(self, mocked_deliver_email):
        self.consultant_login()
        future_value = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        human_response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "meeting_type": "human",
                "title": "Human Check-In",
                "scheduled_start_at": future_value,
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(human_response.status_code, 200)

        ai_response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "meeting_type": "ai",
                "title": "AI Check-In",
                "scheduled_start_at": future_value,
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(ai_response.status_code, 200)

        db = get_db(self.app.config)
        rows = db.execute(
            "SELECT title, meeting_type, channel_name FROM scheduled_meetings WHERE title IN ('Human Check-In', 'AI Check-In') ORDER BY title"
        ).fetchall()
        db.close()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["channel_name"], rows[1]["channel_name"])

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_future_meeting_blocked_when_open_meeting_exists_for_pair(self, mocked_deliver_email):
        self.consultant_login()
        now_value = self._future_local_time(days=0, minutes=0)
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Immediate One",
                "scheduled_start_at": now_value,
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )

        future_value = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Future Meeting",
                "scheduled_start_at": future_value,
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"already has an active meeting", response.data)

        db = get_db(self.app.config)
        count = db.execute("SELECT COUNT(*) AS c FROM scheduled_meetings").fetchone()["c"]
        db.close()
        self.assertEqual(count, 1)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_same_pair_meetings_use_stable_pair_channel(self, mocked_deliver_email):
        self.consultant_login()
        first_time = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "First Stable Room Meeting",
                "scheduled_start_at": first_time,
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        db = get_db(self.app.config)
        first = db.execute(
            "SELECT id, channel_name FROM scheduled_meetings WHERE title = ?",
            ("First Stable Room Meeting",),
        ).fetchone()
        db.execute(
            "UPDATE scheduled_meetings SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (first["id"],),
        )
        db.commit()
        db.close()

        second_time = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Second Stable Room Meeting",
                "scheduled_start_at": second_time,
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        db = get_db(self.app.config)
        second = db.execute(
            "SELECT channel_name FROM scheduled_meetings WHERE title = ?",
            ("Second Stable Room Meeting",),
        ).fetchone()
        db.close()
        expected = get_pair_channel(self.consultant_id, self.client_id)
        self.assertEqual(first["channel_name"], expected)
        self.assertEqual(second["channel_name"], expected)

    def test_meetings_index_shows_new_meeting_action(self):
        self.consultant_login()
        response = self.client.get("/consultant/meetings")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"New Meeting", response.data)
        self.assertIn(b"Create one for now or schedule one for later", response.data)

    def test_cancelled_meeting_cannot_be_reaccepted(self):
        self.consultant_login()
        with self.client.application.app_context():
            db = get_db(self.app.config)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("cancelled-meeting-token"),
                expires_at="2099-01-01T00:00:00Z",
            )
            db.execute(
                """
                INSERT INTO scheduled_meetings (
                    client_id, consultant_id, status, title, timezone_name,
                    scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
                    channel_name, response_access_link_id, cancelled_at
                ) VALUES (?, ?, 'cancelled', 'Cancelled Meeting', 'Europe/London',
                          '2026-04-20T10:00:00Z', '2026-04-20T10:30:00Z', '2026-04-20T09:45:00Z', '2026-04-20T11:00:00Z',
                          'CANCEL1234', ?, CURRENT_TIMESTAMP)
                """,
                (self.client_id, self.consultant_id, access_link_id),
            )
            db.commit()
            db.close()

        response = self.client.post(
            "/meetings/respond/cancelled-meeting-token",
            data={"action": "accept"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"can no longer be accepted", response.data)

        db = get_db(self.app.config)
        row = db.execute("SELECT status FROM scheduled_meetings WHERE channel_name = 'CANCEL1234'").fetchone()
        db.close()
        self.assertEqual(row["status"], "cancelled")

    def test_meeting_response_page_accepts_signed_access_token(self):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting
            from consultant_dashboard.core.meetings import build_join_window, iso_utc
            from consultant_dashboard.core.messaging import hash_access_token

            start_at = datetime.now(timezone.utc) + timedelta(days=1)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("signed-access-seed"),
                expires_at=iso_utc(end_at + timedelta(days=7)),
            )
            create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                meeting_type="human",
                repeat_weekly=False,
                title="Signed Access Meeting",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=get_pair_channel(self.consultant_id, self.client_id, "human"),
                response_access_link_id=access_link_id,
            )
            db.commit()
            db.close()

        signed_token = make_signed_meeting_access_token(
            self.app.config["INTERNAL_SHARED_SECRET"],
            access_link_id,
            int(time.time()) + 3600,
        )
        response = self.client.get(f"/meetings/respond/{signed_token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Signed Access Meeting", response.data)

    def test_completed_meeting_cannot_be_cancelled(self):
        self.consultant_login()
        with self.client.application.app_context():
            db = get_db(self.app.config)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("completed-meeting-token"),
                expires_at="2099-01-01T00:00:00Z",
            )
            db.execute(
                """
                INSERT INTO scheduled_meetings (
                    client_id, consultant_id, status, title, timezone_name,
                    scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
                    channel_name, response_access_link_id, completed_at
                ) VALUES (?, ?, 'completed', 'Completed Meeting', 'Europe/London',
                          '2026-04-20T10:00:00Z', '2026-04-20T10:30:00Z', '2026-04-20T09:45:00Z', '2026-04-20T11:00:00Z',
                          'DONE123456', ?, CURRENT_TIMESTAMP)
                """,
                (self.client_id, self.consultant_id, access_link_id),
            )
            meeting_id = db.execute(
                "SELECT id FROM scheduled_meetings WHERE channel_name = 'DONE123456'"
            ).fetchone()["id"]
            db.commit()
            db.close()

        response = self.client.post(
            f"/consultant/meetings/{meeting_id}",
            data={"action": "cancel"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"can no longer be cancelled", response.data)

        db = get_db(self.app.config)
        row = db.execute("SELECT status FROM scheduled_meetings WHERE channel_name = 'DONE123456'").fetchone()
        db.close()
        self.assertEqual(row["status"], "completed")

    def test_completed_meeting_cannot_be_marked_no_show(self):
        self.consultant_login()
        with self.client.application.app_context():
            db = get_db(self.app.config)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("completed-no-show-token"),
                expires_at="2099-01-01T00:00:00Z",
            )
            db.execute(
                """
                INSERT INTO scheduled_meetings (
                    client_id, consultant_id, status, title, timezone_name,
                    scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
                    channel_name, response_access_link_id, completed_at
                ) VALUES (?, ?, 'completed', 'Completed No Show', 'Europe/London',
                          '2026-04-20T10:00:00Z', '2026-04-20T10:30:00Z', '2026-04-20T09:45:00Z', '2026-04-20T11:00:00Z',
                          'NOSHOWDONE1', ?, CURRENT_TIMESTAMP)
                """,
                (self.client_id, self.consultant_id, access_link_id),
            )
            meeting_id = db.execute(
                "SELECT id FROM scheduled_meetings WHERE channel_name = 'NOSHOWDONE1'"
            ).fetchone()["id"]
            db.commit()
            db.close()

        response = self.client.post(
            f"/consultant/meetings/{meeting_id}",
            data={"action": "mark_no_show", "attendance_outcome": "client_no_show"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"can no longer be marked as a no-show", response.data)

    def test_mark_no_show_completes_open_meeting(self):
        self.consultant_login()
        with self.client.application.app_context():
            db = get_db(self.app.config)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("open-no-show-token"),
                expires_at="2099-01-01T00:00:00Z",
            )
            db.execute(
                """
                INSERT INTO scheduled_meetings (
                    client_id, consultant_id, status, title, timezone_name,
                    scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
                    channel_name, response_access_link_id
                ) VALUES (?, ?, 'accepted', 'Open No Show', 'Europe/London',
                          '2026-04-20T10:00:00Z', '2026-04-20T10:30:00Z', '2026-04-20T09:45:00Z', '2026-04-20T11:00:00Z',
                          'NOSHOWOPEN1', ?)
                """,
                (self.client_id, self.consultant_id, access_link_id),
            )
            meeting_id = db.execute(
                "SELECT id FROM scheduled_meetings WHERE channel_name = 'NOSHOWOPEN1'"
            ).fetchone()["id"]
            db.commit()
            db.close()

        response = self.client.post(
            f"/consultant/meetings/{meeting_id}",
            data={"action": "mark_no_show", "attendance_outcome": "client_no_show"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No-show recorded", response.data)

        db = get_db(self.app.config)
        row = db.execute(
            "SELECT status, attendance_outcome, completed_at FROM scheduled_meetings WHERE id = ?",
            (meeting_id,),
        ).fetchone()
        db.close()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["attendance_outcome"], "client_no_show")
        self.assertTrue(row["completed_at"])

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_client_can_accept_scheduled_meeting(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            f"/consultant/clients/{self.client_id}/meetings/new",
            data={
                "title": "Accept Test",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        db = get_db(self.app.config)
        link = db.execute(
            """
            SELECT cal.token_hash, sm.id
            FROM scheduled_meetings sm
            JOIN client_access_links cal ON cal.id = sm.response_access_link_id
            ORDER BY sm.created_at DESC
            LIMIT 1
            """
        ).fetchone()
        db.close()
        self.assertIsNotNone(link)
        mocked_link = mocked_deliver_email.call_args.kwargs["reply_link"]
        token = mocked_link.rsplit("/", 1)[-1]

        response = self.client.post(
            f"/meetings/respond/{token}",
            data={"action": "accept"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Meeting accepted", response.data)

        db = get_db(self.app.config)
        meeting = db.execute(
            "SELECT status, accepted_at FROM scheduled_meetings WHERE id = ?",
            (link["id"],),
        ).fetchone()
        db.close()
        self.assertEqual(meeting["status"], "accepted")
        self.assertTrue(meeting["accepted_at"])

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_client_cannot_decline_after_accepting_meeting(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            f"/consultant/clients/{self.client_id}/meetings/new",
            data={
                "title": "Accept Then Decline Test",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        db = get_db(self.app.config)
        meeting_id = db.execute(
            "SELECT id FROM scheduled_meetings ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["id"]
        db.close()
        token = mocked_deliver_email.call_args.kwargs["reply_link"].rsplit("/", 1)[-1]

        accept_response = self.client.post(
            f"/meetings/respond/{token}",
            data={"action": "accept"},
            follow_redirects=True,
        )
        self.assertEqual(accept_response.status_code, 200)
        self.assertIn(b"Meeting accepted", accept_response.data)

        decline_response = self.client.post(
            f"/meetings/respond/{token}",
            data={"action": "decline"},
            follow_redirects=True,
        )
        self.assertEqual(decline_response.status_code, 200)
        self.assertIn(b"can no longer be declined", decline_response.data)

        db = get_db(self.app.config)
        meeting = db.execute(
            "SELECT status FROM scheduled_meetings WHERE id = ?",
            (meeting_id,),
        ).fetchone()
        db.close()
        self.assertEqual(meeting["status"], "accepted")

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_resend_invite_reopens_declined_meeting(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            f"/consultant/clients/{self.client_id}/meetings/new",
            data={
                "title": "Decline Then Reopen Test",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        first_token = mocked_deliver_email.call_args.kwargs["reply_link"].rsplit("/", 1)[-1]
        self.client.post(
            f"/meetings/respond/{first_token}",
            data={"action": "decline"},
            follow_redirects=True,
        )

        db = get_db(self.app.config)
        meeting_id = db.execute(
            "SELECT id FROM scheduled_meetings ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["id"]
        db.close()

        response = self.client.post(
            f"/consultant/meetings/{meeting_id}",
            data={"action": "resend_invite"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Meeting reopened for response", response.data)

        db = get_db(self.app.config)
        meeting = db.execute(
            "SELECT status, declined_at FROM scheduled_meetings WHERE id = ?",
            (meeting_id,),
        ).fetchone()
        db.close()
        self.assertEqual(meeting["status"], "scheduled")
        self.assertIsNone(meeting["declined_at"])

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_meeting_detail_and_response_pages_show_join_actions(self, mocked_deliver_email):
        self.consultant_login()
        start_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).astimezone(ZoneInfo("Europe/London"))
        response = self.client.post(
            f"/consultant/clients/{self.client_id}/meetings/new",
            data={
                "title": "Join Flow Test",
                "scheduled_start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "Join action verification.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Join Meeting", response.data)
        self.assertIn(b"Join Flow Test", response.data)

        token = mocked_deliver_email.call_args.kwargs["reply_link"].rsplit("/", 1)[-1]
        response = self.client.post(
            f"/meetings/respond/{token}",
            data={"action": "accept"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Meeting accepted", response.data)
        self.assertNotIn(b"Join Meeting", response.data)
        self.assertIn(b"Add to calendar", response.data)
        self.assertNotIn(f"/meetings/respond/{token}/join".encode("utf-8"), response.data)
        self.assertNotIn(b"join_bootstrap=", response.data)
        self.assertNotIn(b"access_token=", response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_meeting_response_can_offer_accept_and_join_now(self, mocked_deliver_email):
        self.consultant_login()
        start_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).astimezone(ZoneInfo("Europe/London"))
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Join Right Away",
                "scheduled_start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        token = mocked_deliver_email.call_args.kwargs["reply_link"].rsplit("/", 1)[-1]
        response = self.client.get(f"/meetings/respond/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Accept and join now", response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_meeting_response_ics_downloads(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Calendar Test",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "Bring questions.",
            },
            follow_redirects=True,
        )
        token = mocked_deliver_email.call_args.kwargs["reply_link"].rsplit("/", 1)[-1]
        self.client.post(
            f"/meetings/respond/{token}",
            data={"action": "accept"},
            follow_redirects=True,
        )
        response = self.client.get(f"/meetings/respond/{token}/invite.ics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/calendar")
        self.assertIn(b"BEGIN:VCALENDAR", response.data)
        self.assertIn(b"URL:", response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_future_meeting_detail_shows_cancel_action(self, mocked_deliver_email):
        self.consultant_login()
        response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Future Cancel Test",
                "scheduled_start_at": "2099-04-20T10:00",
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Cancel Meeting", response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_consultant_can_delete_meeting_from_detail_page(self, mocked_deliver_email):
        self.consultant_login()
        response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Delete Meeting",
                "scheduled_start_at": "2099-04-20T10:00",
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        db = get_db(self.app.config)
        row = db.execute(
            "SELECT id FROM scheduled_meetings WHERE title = 'Delete Meeting' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        db.close()
        self.assertIsNotNone(row)

        response = self.client.post(
            f"/consultant/meetings/{row['id']}",
            data={"action": "delete"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Meeting deleted", response.data)

        db = get_db(self.app.config)
        deleted = db.execute(
            "SELECT id FROM scheduled_meetings WHERE id = ?",
            (row["id"],),
        ).fetchone()
        db.close()
        self.assertIsNone(deleted)

    def test_consultant_client_create_rejects_invalid_phone(self):
        self.consultant_login()
        response = self.client.post(
            "/consultant/clients/new",
            data={
                "display_name": "Jamie Demo",
                "email": "jamie@example.com",
                "phone_country_code": "UK",
                "phone_number": "12345",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Enter a valid UK phone number.", response.data)

    def test_consultant_client_list_and_session_detail_render(self):
        self.ingest_session(session_id="sess_web_001", urgent_escalation=True)
        self.consultant_login()

        response = self.client.get("/consultant/clients")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Alex Demo", response.data)
        self.assertIn(b"New Meeting", response.data)
        self.assertIn(b"Add Client", response.data)

        response = self.client.get(f"/consultant/clients/{self.client_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"urgent escalation", response.data)
        self.assertIn(b"Last Session", response.data)
        self.assertIn(b"Messages", response.data)
        self.assertIn(b"Edit Contact Details", response.data)
        self.assertNotIn(b"Client Sign-In Match", response.data)

        response = self.client.get("/consultant/sessions")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"sess_web_001", response.data)

        response = self.client.get("/consultant/sessions/sess_web_001")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Elevated stress", response.data)
        self.assertIn(f'action="/consultant/clients/{self.client_id}/messages/send"'.encode(), response.data)
        self.assertIn(f'data-thread-endpoint="/consultant/clients/{self.client_id}/messages/thread"'.encode(), response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_consultant_can_send_email_message(self, mocked_deliver_email):
        self.consultant_login()
        response = self.client.post(
            f"/consultant/clients/{self.client_id}",
            data={
                "form_name": "send_message",
                "message_body": "How are you feeling today?",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Message sent", response.data)
        self.assertIn(b"How are you feeling today?", response.data)
        mocked_deliver_email.assert_called_once()

        db = get_db(self.app.config)
        row = db.execute(
            "SELECT direction, channel, subject, delivery_status FROM client_messages WHERE client_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.client_id,),
        ).fetchone()
        link = db.execute(
            "SELECT id FROM client_access_links WHERE client_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.client_id,),
        ).fetchone()
        db.close()
        self.assertEqual(row["direction"], "outbound")
        self.assertEqual(row["channel"], "email")
        self.assertEqual(row["subject"], "")
        self.assertEqual(row["delivery_status"], "sent")
        self.assertIsNotNone(link)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_message_metadata_does_not_store_plaintext_reply_link(self, mocked_deliver_email):
        self.consultant_login()
        response = self.client.post(
            f"/consultant/clients/{self.client_id}",
            data={
                "form_name": "send_message",
                "message_body": "Security test message.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        reply_link = mocked_deliver_email.call_args.kwargs["reply_link"]
        token = reply_link.rsplit("/", 1)[-1]

        db = get_db(self.app.config)
        row = db.execute(
            "SELECT metadata_json, access_link_id FROM client_messages WHERE client_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.client_id,),
        ).fetchone()
        db.close()
        self.assertNotIn(token, row["metadata_json"] or "")
        self.assertNotIn("reply_link", row["metadata_json"] or "")
        self.assertTrue(row["access_link_id"])

    @mock.patch("consultant_dashboard.core.web.deliver_sms", return_value=("not_sent", "twilio_messaging_not_configured"))
    def test_consultant_can_send_sms_message(self, mocked_deliver_sms):
        db = get_db(self.app.config)
        db.execute("UPDATE clients SET email = '' WHERE id = ?", (self.client_id,))
        db.commit()
        db.close()
        self.consultant_login()
        response = self.client.post(
            f"/consultant/clients/{self.client_id}",
            data={
                "form_name": "send_message",
                "message_body": "Please use your secure reply link.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Message saved, but client notifications are not configured yet", response.data)
        mocked_deliver_sms.assert_called_once()

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_client_can_reply_through_secure_message_link(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            f"/consultant/clients/{self.client_id}",
            data={
                "form_name": "send_message",
                "message_body": "How are you feeling today?",
            },
            follow_redirects=True,
        )
        db = get_db(self.app.config)
        link = db.execute(
            "SELECT token_hash FROM client_access_links WHERE client_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.client_id,),
        ).fetchone()
        db.close()
        self.assertIsNotNone(link)

        token = None
        # Recover the token from the mocked call rather than trying to reverse the hash.
        call_kwargs = mocked_deliver_email.call_args.kwargs
        reply_link = call_kwargs["reply_link"]
        token = reply_link.rsplit("/", 1)[-1]

        response = self.client.get(f"/client/messages/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"How are you feeling today?", response.data)

        response = self.client.post(
            f"/client/messages/{token}",
            data={"body": "I am doing better today."},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Reply sent", response.data)
        self.assertIn(b"I am doing better today.", response.data)

        db = get_db(self.app.config)
        inbound = db.execute(
            "SELECT direction, channel, body FROM client_messages WHERE client_id = ? AND direction = 'inbound' ORDER BY created_at DESC LIMIT 1",
            (self.client_id,),
        ).fetchone()
        db.close()
        self.assertEqual(inbound["direction"], "inbound")
        self.assertEqual(inbound["channel"], "portal")
        self.assertEqual(inbound["body"], "I am doing better today.")

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_consultant_thread_endpoint_returns_messages(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            f"/consultant/clients/{self.client_id}",
            data={"form_name": "send_message", "message_body": "Live refresh test."},
            follow_redirects=True,
        )
        response = self.client.get(f"/consultant/clients/{self.client_id}/messages/thread")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["messages"])
        self.assertEqual(payload["messages"][-1]["body"], "Live refresh test.")

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_consultant_send_endpoint_returns_json(self, mocked_deliver_email):
        self.consultant_login()
        response = self.client.post(
            f"/consultant/clients/{self.client_id}/messages/send",
            json={"body": "Endpoint send test."},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["delivery_status"], "sent")
        self.assertEqual(payload["messages"][-1]["body"], "Endpoint send test.")

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_client_thread_endpoint_returns_messages(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            f"/consultant/clients/{self.client_id}",
            data={"form_name": "send_message", "message_body": "Client thread refresh test."},
            follow_redirects=True,
        )
        reply_link = mocked_deliver_email.call_args.kwargs["reply_link"]
        token = reply_link.rsplit("/", 1)[-1]
        response = self.client.get(f"/client/messages/{token}/thread")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["messages"])
        self.assertEqual(payload["messages"][-1]["body"], "Client thread refresh test.")

    def test_expired_client_message_link_is_rejected_for_realtime(self):
        db = get_db(self.app.config)
        token = "expired-access-token"
        create_client_access_link(
            db,
            client_id=self.client_id,
            created_by=self.consultant_id,
            token_hash=hash_access_token(token),
            expires_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        )
        db.commit()
        db.close()

        with self.app.app_context():
            link = realtime.get_active_client_link(self.app.config, token)
        self.assertIsNone(link)

    def test_client_can_only_have_one_consultant_assignment(self):
        db = get_db(self.app.config)
        db.execute(
            """
            INSERT INTO consultants (
                email, password_hash, name, phone_number, notification_email, escalation_phone_number, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            ("other@example.com", "hash", "Other", "+447700900222", "other@example.com", "+447700900222"),
        )
        other_id = db.execute("SELECT id FROM consultants WHERE email = ?", ("other@example.com",)).fetchone()["id"]
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO consultant_clients (consultant_id, client_id, role) VALUES (?, ?, 'primary')",
                (other_id, self.client_id),
            )
        db.close()

    def test_admin_dashboard_and_consultants_page_render(self):
        self.admin_login()
        response = self.client.get("/admin/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin Dashboard", response.data)
        self.assertIn(b"Test Consultant", response.data)
        self.assertIn(b"/admin/account", response.data)

        response = self.client.get("/admin/consultants")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"consultant@example.com", response.data)
        self.assertIn(b"Notification Email", response.data)
        self.assertIn(b"Escalation Phone Number", response.data)

    def test_admin_can_create_consultant_and_duplicate_is_handled(self):
        self.admin_login()
        response = self.client.post(
            "/admin/consultants",
            data={
                "name": "Second Consultant",
                "email": "second@example.com",
                "phone_country_code": "UK",
                "phone_number": "07700900222",
                "password": "changeme123",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Second Consultant", response.data)

        response = self.client.post(
            "/admin/consultants",
            data={
                "name": "Second Consultant",
                "email": "second@example.com",
                "phone_country_code": "UK",
                "phone_number": "07700900222",
                "password": "changeme123",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"already exists", response.data)

    def test_logout_clears_dashboard_access(self):
        self.consultant_login()
        response = self.client.post("/consultant/logout", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/home", response.location)

        response = self.client.get("/consultant/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consultant/login", response.location)

    def test_admin_and_consultant_sessions_can_coexist(self):
        self.admin_login()
        self.consultant_login()

        response = self.client.get("/admin/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/consultant/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 200)

    def test_admin_logout_does_not_clear_consultant_session(self):
        self.admin_login()
        self.consultant_login()

        response = self.client.post("/admin/logout", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/home", response.location)

        response = self.client.get("/consultant/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/admin/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.location)

    def test_consultant_can_change_password(self):
        self.consultant_login()
        response = self.client.post(
            "/consultant/account",
            data={
                "current_password": "consultpass123",
                "new_password": "newconsultpass123",
                "confirm_password": "newconsultpass123",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Password updated", response.data)

        fresh = self.app.test_client()
        response = fresh.post(
            "/consultant/login",
            data={"email": "consultant@example.com", "password": "newconsultpass123"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consultant/verify", response.location)

    def test_admin_can_change_password(self):
        self.admin_login()
        response = self.client.post(
            "/admin/account",
            data={
                "current_password": "adminpass123",
                "new_password": "newadminpass123",
                "confirm_password": "newadminpass123",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Password updated", response.data)

        users, _secret, _ttl = _load_admin_users(self.app.config["ADMIN_AUTH_FILE"])
        self.assertIn("admin@example.com", users)

        fresh = self.app.test_client()
        response = fresh.post(
            "/admin/login",
            data={"email": "admin@example.com", "password": "newadminpass123"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/dashboard", response.location)

    def test_admin_can_edit_consultant(self):
        self.admin_login()
        response = self.client.post(
            f"/admin/consultants/{self.consultant_id}",
            data={
                "name": "Updated Consultant",
                "email": "consultant@example.com",
                "phone_country_code": "UK",
                "phone_number": "07700900999",
                "notification_email": "notify@example.com",
                "escalation_phone_country_code": "UK",
                "escalation_phone_number": "07700900998",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Consultant updated", response.data)
        self.assertIn(b"Updated Consultant", response.data)

        db = get_db(self.app.config)
        consultant = get_consultant_by_email(db, "consultant@example.com")
        db.close()
        self.assertEqual(consultant["name"], "Updated Consultant")
        self.assertEqual(consultant["notification_email"], "notify@example.com")
        self.assertEqual(consultant["escalation_phone_number"], "+447700900998")

    def test_admin_can_reset_consultant_password(self):
        self.admin_login()
        response = self.client.post(
            f"/admin/consultants/{self.consultant_id}",
            data={
                "name": "Test Consultant",
                "email": "consultant@example.com",
                "phone_country_code": "UK",
                "phone_number": "07700900000",
                "notification_email": "consultant@example.com",
                "escalation_phone_country_code": "UK",
                "escalation_phone_number": "07700900000",
                "reset_password": "resetpass123",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Temporary password updated", response.data)

        fresh = self.app.test_client()
        response = fresh.post(
            "/consultant/login",
            data={"email": "consultant@example.com", "password": "resetpass123"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consultant/verify", response.location)

    def test_admin_can_delete_consultant(self):
        self.admin_login()
        response = self.client.post(
            f"/admin/consultants/{self.consultant_id}/delete",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Consultant deleted", response.data)

        db = get_db(self.app.config)
        consultant = db.execute(
            "SELECT is_active FROM consultants WHERE id = ?",
            (self.consultant_id,),
        ).fetchone()
        db.close()
        self.assertEqual(consultant["is_active"], 0)

        fresh = self.app.test_client()
        response = fresh.post(
            "/consultant/login",
            data={"email": "consultant@example.com", "password": "consultpass123"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Invalid email or password", response.data)
