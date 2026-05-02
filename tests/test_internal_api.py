import json
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

from consultant_dashboard.core.db import get_db
from consultant_dashboard.core.meetings import build_join_window, generate_meeting_channel, get_pair_channel, iso_utc

from tests.support import ConsultantDashboardTestCase


class ConsultantDashboardInternalApiTest(ConsultantDashboardTestCase):
    def test_internal_health_is_public(self):
        response = self.client.get("/internal/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")

    def test_internal_endpoints_reject_missing_signature(self):
        response = self.client.get("/internal/resolve-client")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"], "Missing internal auth headers")

    def test_internal_endpoints_reject_expired_timestamp(self):
        timestamp = int(time.time()) - 1000
        query_string = "email_hash=test"
        response = self.client.get(
            f"/internal/resolve-client?{query_string}",
            headers=self.internal_headers(
                "GET",
                "/internal/resolve-client",
                query_string,
                timestamp=timestamp,
            ),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"], "Expired timestamp")

    def test_internal_endpoints_reject_invalid_signature(self):
        response = self.client.get(
            "/internal/resolve-client?email_hash=test",
            headers={
                "X-Consultant-Timestamp": str(int(time.time())),
                "X-Consultant-Signature": "bad-signature",
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"], "Invalid signature")

    def test_resolve_client_returns_404_when_not_found(self):
        query_string = f"email_hash={self.sha256('missing@example.com')}"
        response = self.client.get(
            f"/internal/resolve-client?{query_string}",
            headers=self.internal_headers("GET", "/internal/resolve-client", query_string),
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json["found"])

    def test_resolve_client_returns_consultant_mapping(self):
        query_string = f"email_hash={self.sha256('alex@example.com')}"
        response = self.client.get(
            f"/internal/resolve-client?{query_string}",
            headers=self.internal_headers("GET", "/internal/resolve-client", query_string),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["client_id"], self.client_id)
        self.assertEqual(response.json["consultant_id"], self.consultant_id)
        self.assertTrue(response.json["is_active"])
        self.assertEqual(response.json["first_name"], "Alex")
        self.assertEqual(response.json["last_name"], "Demo")

    def test_resolve_client_by_phone_hash_returns_mapping(self):
        query_string = f"phone_hash={self.sha256('+447700900111')}"
        response = self.client.get(
            f"/internal/resolve-client?{query_string}",
            headers=self.internal_headers("GET", "/internal/resolve-client", query_string),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["client_id"], self.client_id)

    def test_client_context_requires_client_id(self):
        response = self.client.get(
            "/internal/client-context",
            headers=self.internal_headers("GET", "/internal/client-context", ""),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "client_id required")

    def test_client_context_returns_404_for_unknown_client(self):
        query_string = "client_id=missing-client"
        response = self.client.get(
            f"/internal/client-context?{query_string}",
            headers=self.internal_headers("GET", "/internal/client-context", query_string),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["error"], "client not found")

    def test_client_context_returns_summary_baseline_and_alerts(self):
        self.ingest_session(session_id="sess_internal_001", urgent_escalation=True)
        human_payload = {
            "client_id": self.client_id,
            "consultant_id": self.consultant_id,
            "session_id": "sess_internal_human_001",
            "session_kind": "consultant_live_session",
            "profile": "therapy",
            "channel": "human-channel",
            "started_at": "2026-04-13T19:00:00Z",
            "ended_at": "2026-04-13T19:30:00Z",
            "duration_seconds": 1800,
            "status": "completed",
            "summary": {
                "key_point_summary": {
                    "headline": "Human session follow-up",
                    "body": "Human therapist follow-up focused on routines and coping.",
                },
                "brief_overview": "Human session follow-up.",
                "full_summary": "Human therapist follow-up focused on routines and coping.",
                "biomarker_summary": "",
                "risk_overview": "",
                "follow_up": "Continue reviewing routines with the therapist.",
                "source": "custom-llm",
            },
            "human_personal_summary": {
                "key_point_summary": {
                    "headline": "Client Key Point Summary - Human Sessions",
                    "body": "Human therapist sessions continue to focus on routines, coping, and stress management.",
                },
                "brief_overview": "Client Key Point Summary - Human Sessions",
                "full_summary": "Human therapist sessions continue to focus on routines, coping, and stress management.",
            },
            "biomarkers": {"averages": {}},
            "alerts": [],
        }
        human_body = json.dumps(human_payload, separators=(",", ":"))
        human_response = self.client.post(
            "/internal/session-complete",
            data=human_body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/session-complete", human_body),
        )
        self.assertEqual(human_response.status_code, 200)
        query_string = f"client_id={self.client_id}"
        response = self.client.get(
            f"/internal/client-context?{query_string}",
            headers=self.internal_headers("GET", "/internal/client-context", query_string),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["display_name"], "Alex Demo")
        self.assertEqual(response.json["year_of_birth"], 1974)
        self.assertEqual(response.json["sex"], "male")
        self.assertEqual(response.json["consultant_id"], self.consultant_id)
        self.assertEqual(response.json["consultant_name"], "Test Consultant")
        self.assertEqual(response.json["notes"], "Generalized notes only.")
        self.assertEqual(response.json["direction"], "Check stress and routines.")
        self.assertIsNotNone(response.json["latest_summary"])
        self.assertEqual(
            response.json["latest_summary"]["full_summary"],
            "Human therapist follow-up focused on routines and coping.",
        )
        self.assertEqual(response.json["ai_session_count"], 1)
        self.assertEqual(response.json["human_session_count"], 1)
        self.assertIsNotNone(response.json["ai_personal_summary"])
        self.assertIsNotNone(response.json["human_personal_summary"])
        self.assertEqual(
            response.json["ai_personal_summary"]["key_point_summary"]["headline"],
            "Client Key Point Summary - AI Sessions",
        )
        self.assertEqual(
            response.json["human_personal_summary"]["key_point_summary"]["headline"],
            "Client Key Point Summary - Human Sessions",
        )
        self.assertEqual(len(response.json["recent_summaries"]), 2)
        self.assertIsNotNone(response.json["baseline"])
        self.assertEqual(response.json["baseline"]["window_sessions"], 2)
        self.assertEqual(response.json["baseline"]["maxes"]["stress_index"], 64.2)
        self.assertEqual(response.json["baseline"]["maxes"]["safety_level"], 1.0)
        self.assertEqual(len(response.json["alerts"]), 2)

    def test_client_context_preserves_prior_ai_kps_when_second_session_omits_it(self):
        self.ingest_session(session_id="sess_internal_keep_kps_001", urgent_escalation=False)
        db = get_db(self.app.config)
        row = db.execute(
            "SELECT ai_summary_storage_key FROM clients WHERE id = ?",
            (self.client_id,),
        ).fetchone()
        initial_key = row["ai_summary_storage_key"]
        db.close()

        payload = {
            "client_id": self.client_id,
            "consultant_id": self.consultant_id,
            "session_id": "sess_internal_keep_kps_002",
            "profile": "therapy",
            "channel": "keep-kps-channel",
            "started_at": "2026-04-14T18:00:00Z",
            "ended_at": "2026-04-14T18:05:00Z",
            "duration_seconds": 300,
            "status": "completed",
            "summary": {"overview": "Second session without KPS update."},
            "ai_personal_summary": None,
            "biomarkers": {"averages": {"stress_index": 41.0}},
            "alerts": [],
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/session-complete",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/session-complete", body),
        )
        self.assertEqual(response.status_code, 200)

        query_string = f"client_id={self.client_id}"
        response = self.client.get(
            f"/internal/client-context?{query_string}",
            headers=self.internal_headers("GET", "/internal/client-context", query_string),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["ai_personal_summary"]["key_point_summary"]["body"],
            "Recurring AI-session themes include stress, burnout, and the need for steady routines.",
        )

        db = get_db(self.app.config)
        row = db.execute(
            "SELECT ai_summary_storage_key FROM clients WHERE id = ?",
            (self.client_id,),
        ).fetchone()
        db.close()
        self.assertEqual(row["ai_summary_storage_key"], initial_key)

    def test_crisis_escalate_init_returns_bundle_for_ai_session_without_scheduled_meeting(self):
        payload = {
            "client_id": self.client_id,
            "session_id": "sess_crisis_001",
            "channel_name": "ai-room-001",
            "level": 3,
            "alert": "urgent",
            "source": "thymia",
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/crisis-escalate-init",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/crisis-escalate-init", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])
        self.assertTrue(response.json["escalate"])
        self.assertEqual(response.json["channel_name"], "ai-room-001")
        self.assertEqual(response.json["escalation_phone_number"], "+447700900000")
        self.assertEqual(response.json["pstn_uid"], "43455")
        self.assertTrue(response.json["rtc_token"])

    def test_crisis_escalate_init_returns_skipped_when_phone_missing(self):
        db = get_db(self.app.config)
        db.execute("UPDATE clients SET escalation_phone_number = '' WHERE id = ?", (self.client_id,))
        db.commit()
        db.close()
        payload = {
            "client_id": self.client_id,
            "session_id": "sess_crisis_002",
            "channel_name": "ai-room-002",
            "level": 3,
            "alert": "urgent",
            "source": "thymia",
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/crisis-escalate-init",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/crisis-escalate-init", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])
        self.assertFalse(response.json["escalate"])
        self.assertEqual(response.json["reason"], "missing_phone")

    def test_crisis_escalate_init_requires_numeric_level(self):
        payload = {
            "client_id": self.client_id,
            "session_id": "sess_crisis_bad_level",
            "channel_name": "ai-room-bad-level",
            "alert": "urgent",
            "source": "thymia",
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/crisis-escalate-init",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/crisis-escalate-init", body),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("numeric level required", response.json["error"])

    def test_crisis_escalate_status_persists_generated_texts(self):
        meeting_id = self.create_meeting(meeting_type="ai")
        init_payload = {
            "meeting_id": meeting_id,
            "client_id": self.client_id,
            "session_id": "sess_crisis_003",
            "level": 3,
            "alert": "urgent",
            "source": "thymia",
        }
        init_body = json.dumps(init_payload, separators=(",", ":"))
        init_response = self.client.post(
            "/internal/crisis-escalate-init",
            data=init_body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/crisis-escalate-init", init_body),
        )
        event_id = init_response.json["escalation_event_id"]
        payload = {
            "escalation_event_id": event_id,
            "phase": "answered",
            "client_announcement_text": "I am calling your trusted contact now.",
            "recipient_summary_text": "This is the MindFix escalation assistant.",
            "provider_result": "OK",
            "session_id": "sess_crisis_003",
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/crisis-escalate-status",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/crisis-escalate-status", body),
        )
        self.assertEqual(response.status_code, 200)
        db = get_db(self.app.config)
        row = db.execute(
            "SELECT status, provider_result, client_announcement_text, recipient_summary_text FROM escalation_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        db.close()
        self.assertEqual(row["status"], "answered")
        self.assertEqual(row["provider_result"], "OK")
        self.assertEqual(row["client_announcement_text"], "I am calling your trusted contact now.")
        self.assertEqual(row["recipient_summary_text"], "This is the MindFix escalation assistant.")

    def test_crisis_escalate_status_rejects_invalid_phase(self):
        payload = {
            "escalation_event_id": "evt-doesnt-matter",
            "phase": "bogus_phase",
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/crisis-escalate-status",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/crisis-escalate-status", body),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid phase", response.json["error"])

    def test_crisis_escalate_status_returns_404_when_event_missing(self):
        payload = {
            "escalation_event_id": "evt-missing",
            "phase": "answered",
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/crisis-escalate-status",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/crisis-escalate-status", body),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["error"], "escalation event not found")

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_session_complete_for_weekly_meeting_creates_one_next_occurrence(self, mocked_deliver_email):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting
            from consultant_dashboard.core.messaging import hash_access_token

            start_at = datetime.now(timezone.utc) + timedelta(days=1)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("weekly-session-complete-token"),
                expires_at=iso_utc(end_at + timedelta(days=30)),
            )
            meeting_id = create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                meeting_type="human",
                repeat_weekly=True,
                title="Weekly Session Complete Test",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=generate_meeting_channel(),
                response_access_link_id=access_link_id,
            )
            db.commit()
            db.close()

        payload = {
            "client_id": self.client_id,
            "consultant_id": self.consultant_id,
            "meeting_id": meeting_id,
            "session_id": "sess_weekly_complete_001",
            "profile": "therapy",
            "channel": "weekly-recurrence-channel",
            "started_at": "2026-04-13T18:00:00Z",
            "ended_at": "2026-04-13T18:05:00Z",
            "duration_seconds": 300,
            "status": "completed",
            "summary": {"overview": "Generalized summary."},
            "biomarkers": {"averages": {"stress_index": 52.5}},
            "alerts": [],
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/session-complete",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/session-complete", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])

        response_repeat = self.client.post(
            "/internal/session-complete",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/session-complete", body),
        )
        self.assertEqual(response_repeat.status_code, 200)
        self.assertTrue(response_repeat.json["ok"])

        db = get_db(self.app.config)
        rows = db.execute(
            """
            SELECT status, repeat_weekly, scheduled_start_at, invite_delivery_status
            FROM scheduled_meetings
            WHERE client_id = ? AND consultant_id = ? AND title = ?
            ORDER BY scheduled_start_at ASC
            """,
            (self.client_id, self.consultant_id, "Weekly Session Complete Test"),
        ).fetchall()
        db.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[1]["status"], "scheduled")
        self.assertEqual(rows[1]["repeat_weekly"], 1)
        self.assertEqual(rows[1]["invite_delivery_status"], "sent")
        first_start = datetime.fromisoformat(rows[0]["scheduled_start_at"].replace("Z", "+00:00"))
        next_start = datetime.fromisoformat(rows[1]["scheduled_start_at"].replace("Z", "+00:00"))
        self.assertEqual(next_start - first_start, timedelta(days=7))
        self.assertEqual(mocked_deliver_email.call_count, 1)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_run_reminders_skips_24h_for_meeting_created_inside_24h_window(self, mocked_deliver_email):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting
            from consultant_dashboard.core.messaging import hash_access_token

            start_at = datetime.now(timezone.utc) + timedelta(minutes=13)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("meeting-created-inside-24h-token"),
                expires_at=iso_utc(end_at + timedelta(days=7)),
            )
            create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                meeting_type="human",
                repeat_weekly=False,
                title="Inside 24h Reminder Test",
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

        body = ""
        response = self.client.post(
            "/internal/run-reminders",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/run-reminders", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["sent_24h"], 0)
        self.assertEqual(response.json["sent_1m"], 0)
        mocked_deliver_email.assert_not_called()

        conn = get_db(self.app.config)
        row = conn.execute(
            "SELECT reminder_24h_sent_at, reminder_1m_sent_at FROM scheduled_meetings WHERE title = ?",
            ("Inside 24h Reminder Test",),
        ).fetchone()
        conn.close()
        self.assertFalse(row["reminder_24h_sent_at"])
        self.assertFalse(row["reminder_1m_sent_at"])

    def test_meeting_signals_returns_configured_flags(self):
        from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting
        from consultant_dashboard.core.meetings import build_join_window

        db = get_db(self.app.config)
        start_at = datetime(2026, 4, 24, 11, 0, tzinfo=timezone.utc)
        end_at = start_at + timedelta(minutes=30)
        join_start, join_end = build_join_window(start_at, end_at)
        access_link_id = create_client_access_link(
            db,
            client_id=self.client_id,
            created_by=self.consultant_id,
            token_hash=self.sha256("meeting-signal-token"),
            expires_at=iso_utc(end_at + timedelta(days=1)),
        )
        meeting_id = create_scheduled_meeting(
            db,
            client_id=self.client_id,
            consultant_id=self.consultant_id,
            title="AI Meeting Flags",
            invite_message="",
            timezone_name="Europe/London",
            scheduled_start_at=iso_utc(start_at),
            scheduled_end_at=iso_utc(end_at),
            join_window_start_at=iso_utc(join_start),
            join_window_end_at=iso_utc(join_end),
            channel_name=get_pair_channel(self.consultant_id, self.client_id),
            response_access_link_id=access_link_id,
            meeting_type="ai",
            transcription_enabled=True,
            audio_biomarkers_enabled=True,
            video_biomarkers_enabled=False,
        )
        db.commit()
        db.close()

        query_string = f"meeting_id={meeting_id}"
        response = self.client.get(
            f"/internal/meeting-signals?{query_string}",
            headers=self.internal_headers("GET", "/internal/meeting-signals", query_string),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])
        self.assertEqual(response.json["meeting_type"], "ai")
        self.assertTrue(response.json["transcription_enabled"])
        self.assertTrue(response.json["audio_biomarkers_enabled"])
        self.assertFalse(response.json["video_biomarkers_enabled"])

    def test_verify_client_password_returns_client_mapping(self):
        body = json.dumps({"email": "alex@example.com", "password": "clientpass123"}, separators=(",", ":"))
        response = self.client.post(
            "/internal/verify-client-password",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/verify-client-password", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])
        self.assertEqual(response.json["client_id"], self.client_id)
        self.assertEqual(response.json["first_name"], "Alex")
        self.assertEqual(response.json["last_name"], "Demo")
        self.assertEqual(response.json["phone_number"], "+447700900111")

    def test_verify_client_password_rejects_invalid_credentials(self):
        body = json.dumps({"email": "alex@example.com", "password": "wrongpass"}, separators=(",", ":"))
        response = self.client.post(
            "/internal/verify-client-password",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/verify-client-password", body),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"], "invalid_credentials")

    def test_session_complete_creates_session_baseline_and_alert_rows(self):
        response = self.ingest_session(
            session_id="sess_internal_002",
            urgent_escalation=True,
            transcript={"provider": "agora_stt", "text": "Client discussed stress at work."},
        )
        self.assertTrue(response.json["ok"])
        self.assertIn("baseline_storage_key", response.json)

        conn = get_db(self.app.config)
        session_row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?",
            ("sess_internal_002",),
        ).fetchone()
        self.assertIsNotNone(session_row)
        self.assertEqual(session_row["client_id"], self.client_id)
        self.assertEqual(session_row["consultant_id"], self.consultant_id)
        self.assertEqual(session_row["urgent_escalation"], 1)
        self.assertTrue(session_row["transcript_storage_key"])
        self.assertEqual(session_row["transcription_enabled"], 0)
        self.assertEqual(session_row["audio_biomarkers_enabled"], 1)
        self.assertEqual(session_row["video_biomarkers_enabled"], 1)
        alert_count = conn.execute(
            "SELECT COUNT(*) AS c FROM session_alerts WHERE session_id = ?",
            ("sess_internal_002",),
        ).fetchone()["c"]
        self.assertEqual(alert_count, 2)
        audit_count = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'session_complete_ingested' AND session_id = ?",
            ("sess_internal_002",),
        ).fetchone()["c"]
        self.assertEqual(audit_count, 1)
        conn.close()

    def test_session_complete_is_idempotent_for_session_row(self):
        response_one = self.ingest_session(session_id="sess_internal_003", urgent_escalation=False)
        self.assertTrue(response_one.json["ok"])
        response_two = self.ingest_session(session_id="sess_internal_003", urgent_escalation=False)
        self.assertTrue(response_two.json["ok"])

        conn = get_db(self.app.config)
        session_count = conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE id = ?",
            ("sess_internal_003",),
        ).fetchone()["c"]
        self.assertEqual(session_count, 1)
        conn.close()

    def test_session_complete_marks_linked_meeting_completed(self):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting
            from consultant_dashboard.core.meetings import build_join_window, generate_meeting_channel, iso_utc
            from consultant_dashboard.core.messaging import hash_access_token
            from datetime import datetime, timedelta, timezone

            start_at = datetime.now(timezone.utc)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("meeting-session-token"),
                expires_at=iso_utc(end_at + timedelta(days=1)),
            )
            meeting_id = create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                transcription_enabled=True,
                audio_biomarkers_enabled=False,
                video_biomarkers_enabled=True,
                title="Meeting Link Test",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=generate_meeting_channel(),
                response_access_link_id=access_link_id,
            )
            db.commit()
            db.close()

        payload = {
            "client_id": self.client_id,
            "consultant_id": self.consultant_id,
            "session_id": "sess_meeting_link_001",
            "session_kind": "consultant_live_session",
            "meeting_id": meeting_id,
            "profile": "therapy",
            "channel": "meeting-link-channel",
            "started_at": "2026-04-16T09:00:00Z",
            "ended_at": "2026-04-16T09:30:00Z",
            "duration_seconds": 1800,
            "status": "completed",
            "summary": {
                "brief_overview": "Meeting completed.",
                "full_summary": "Meeting completed with deterministic biomarker artifact.",
            },
            "biomarkers": {"averages": {"stress": 0.44}},
            "alerts": [],
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/session-complete",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/session-complete", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])

        conn = get_db(self.app.config)
        meeting_row = conn.execute(
            """
            SELECT status, linked_session_id, summary_storage_key, biomarker_storage_key, completed_at
            FROM scheduled_meetings
            WHERE id = ?
            """,
            (meeting_id,),
        ).fetchone()
        session_row = conn.execute(
            """
            SELECT transcription_enabled, audio_biomarkers_enabled, video_biomarkers_enabled
            FROM sessions
            WHERE id = ?
            """,
            ("sess_meeting_link_001",),
        ).fetchone()
        conn.close()
        self.assertEqual(meeting_row["status"], "completed")
        self.assertEqual(meeting_row["linked_session_id"], "sess_meeting_link_001")
        self.assertTrue(meeting_row["summary_storage_key"])
        self.assertEqual(session_row["transcription_enabled"], 1)
        self.assertEqual(session_row["audio_biomarkers_enabled"], 0)
        self.assertEqual(session_row["video_biomarkers_enabled"], 1)
        self.assertTrue(meeting_row["biomarker_storage_key"])
        self.assertTrue(meeting_row["completed_at"])

    @mock.patch("consultant_dashboard.core.internal_api.utc_now")
    def test_meeting_ended_does_not_complete_future_meeting_when_host_leaves_early(self, mocked_utc_now):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting
            from consultant_dashboard.core.meetings import build_join_window, generate_meeting_channel
            from consultant_dashboard.core.messaging import hash_access_token

            start_at = datetime.now(timezone.utc) + timedelta(minutes=45)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("meeting-ended-future-token"),
                expires_at=iso_utc(end_at + timedelta(days=1)),
            )
            meeting_id = create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                title="Future host leave test",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=generate_meeting_channel(),
                response_access_link_id=access_link_id,
            )
            db.commit()
            db.close()

        mocked_utc_now.return_value = start_at - timedelta(minutes=10)
        body = json.dumps(
            {
                "meeting_id": meeting_id,
                "participant_role": "host",
                "ended_by_role": "host",
                "ended_by_id": self.consultant_id,
            },
            separators=(",", ":"),
        )
        response = self.client.post(
            "/internal/meeting-ended",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/meeting-ended", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])

        conn = get_db(self.app.config)
        row = conn.execute(
            """
            SELECT status, attendance_outcome, completed_at, consultant_left_at
            FROM scheduled_meetings
            WHERE id = ?
            """,
            (meeting_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row["status"], "scheduled")
        self.assertFalse(row["attendance_outcome"])
        self.assertFalse(row["completed_at"])
        self.assertTrue(row["consultant_left_at"])

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_run_reminders_sends_due_24h_reminder(self, mocked_deliver_email):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting
            from consultant_dashboard.core.meetings import build_join_window
            from consultant_dashboard.core.messaging import hash_access_token

            start_at = datetime.now(timezone.utc) + timedelta(hours=23, minutes=30)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("meeting-reminder-token"),
                expires_at=iso_utc(end_at + timedelta(days=7)),
            )
            create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                meeting_type="human",
                repeat_weekly=False,
                title="Reminder Meeting",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=get_pair_channel(self.consultant_id, self.client_id, "human"),
                response_access_link_id=access_link_id,
            )
            db.execute(
                """
                UPDATE scheduled_meetings
                SET created_at = datetime('now', '-1 day'),
                    updated_at = datetime('now', '-1 day')
                WHERE title = 'Reminder Meeting'
                """
            )
            db.commit()
            db.close()

        body = ""
        response = self.client.post(
            "/internal/run-reminders",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/run-reminders", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["sent_24h"], 1)
        mocked_deliver_email.assert_called_once()

        conn = get_db(self.app.config)
        row = conn.execute(
            "SELECT reminder_24h_sent_at, reminder_1m_sent_at FROM scheduled_meetings WHERE title = ?",
            ("Reminder Meeting",),
        ).fetchone()
        conn.close()
        self.assertTrue(row["reminder_24h_sent_at"])
        self.assertFalse(row["reminder_1m_sent_at"])

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_run_reminders_sends_due_1m_reminder_with_enter_meeting_room_cta(self, mocked_deliver_email):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting
            from consultant_dashboard.core.messaging import hash_access_token
            from consultant_dashboard.core.meetings import build_join_window

            start_at = datetime.now(timezone.utc) + timedelta(seconds=30)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("meeting-1m-reminder-token"),
                expires_at=iso_utc(end_at + timedelta(days=7)),
            )
            create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                meeting_type="human",
                repeat_weekly=False,
                title="1m Reminder Meeting",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=get_pair_channel(self.consultant_id, self.client_id, "human"),
                response_access_link_id=access_link_id,
            )
            db.execute(
                """
                UPDATE scheduled_meetings
                SET created_at = datetime('now', '-2 days'),
                    updated_at = datetime('now', '-2 days'),
                    reminder_24h_sent_at = CURRENT_TIMESTAMP
                WHERE title = '1m Reminder Meeting'
                """
            )
            db.commit()
            db.close()

        body = ""
        response = self.client.post(
            "/internal/run-reminders",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/run-reminders", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["sent_1m"], 1)
        mocked_deliver_email.assert_called_once()

        _, kwargs = mocked_deliver_email.call_args
        self.assertTrue(kwargs["reply_link"].endswith("/join"))
        self.assertIn("Enter Meeting Room", kwargs["html_override"])
        self.assertNotIn("Review and respond", kwargs["html_override"])

        conn = get_db(self.app.config)
        row = conn.execute(
            "SELECT reminder_24h_sent_at, reminder_1m_sent_at FROM scheduled_meetings WHERE title = ?",
            ("1m Reminder Meeting",),
        ).fetchone()
        conn.close()
        self.assertTrue(row["reminder_24h_sent_at"])
        self.assertTrue(row["reminder_1m_sent_at"])

    def test_authorize_meeting_join_returns_runtime_key_and_stable_channel(self):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting
            from consultant_dashboard.core.meetings import build_join_window, iso_utc
            from consultant_dashboard.core.messaging import hash_access_token
            from datetime import datetime, timedelta, timezone

            start_at = datetime.now(timezone.utc)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("meeting-auth-token"),
                expires_at=iso_utc(end_at + timedelta(days=1)),
            )
            meeting_id = create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                title="Meeting Auth Test",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=get_pair_channel(self.consultant_id, self.client_id),
                response_access_link_id=access_link_id,
            )
            db.execute(
                "UPDATE scheduled_meetings SET status = 'accepted', accepted_at = CURRENT_TIMESTAMP WHERE id = ?",
                (meeting_id,),
            )
            db.commit()
            db.close()

        body = json.dumps(
            {
                "participant_role": "guest",
                "meeting_id": meeting_id,
                "response_access_link_id": access_link_id,
            },
            separators=(",", ":"),
        )
        response = self.client.post(
            "/internal/authorize-meeting-join",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/authorize-meeting-join", body),
        )
        self.assertEqual(response.status_code, 200)
        expected_channel = get_pair_channel(self.consultant_id, self.client_id)
        expected_app_id = self.app.config.get("APP_ID") or "app"
        self.assertEqual(response.json["channel_name"], expected_channel)
        self.assertFalse(response.json["transcription_enabled"])
        self.assertTrue(response.json["audio_biomarkers_enabled"])
        self.assertTrue(response.json["video_biomarkers_enabled"])
        self.assertEqual(
            response.json["meeting_runtime_key"],
            f"{expected_app_id}:{expected_channel}:{meeting_id}",
        )

    def test_session_complete_baseline_uses_avg_from_structured_biomarkers(self):
        payload = {
            "client_id": self.client_id,
            "consultant_id": self.consultant_id,
            "session_id": "sess_internal_structured_001",
            "profile": "therapy",
            "channel": "structured-channel",
            "started_at": "2026-04-13T18:00:00Z",
            "ended_at": "2026-04-13T18:05:00Z",
            "duration_seconds": 300,
            "status": "completed",
            "summary": {"brief_overview": "Structured", "full_summary": "Structured summary."},
            "biomarkers": {
                "averages": {
                    "stress": {"avg": 0.62, "min": 0.21, "max": 0.88, "count": 12},
                    "fatigue": {"avg": 0.31, "min": 0.12, "max": 0.54, "count": 12},
                }
            },
            "alerts": [],
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/session-complete",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/session-complete", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])

        query_string = f"client_id={self.client_id}"
        response = self.client.get(
            f"/internal/client-context?{query_string}",
            headers=self.internal_headers("GET", "/internal/client-context", query_string),
        )
        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(response.json["baseline"]["averages"]["stress"], 0.62)
        self.assertAlmostEqual(response.json["baseline"]["averages"]["fatigue"], 0.31)

    def test_session_complete_baseline_ignores_non_numeric_structured_biomarkers(self):
        payload = {
            "client_id": self.client_id,
            "consultant_id": self.consultant_id,
            "session_id": "sess_internal_structured_002",
            "profile": "therapy",
            "channel": "structured-channel-2",
            "started_at": "2026-04-13T18:10:00Z",
            "ended_at": "2026-04-13T18:15:00Z",
            "duration_seconds": 300,
            "status": "completed",
            "summary": {"brief_overview": "Structured", "full_summary": "Structured summary."},
            "biomarkers": {
                "averages": {
                    "stress": {"avg": 0.75, "min": 0.21, "max": 0.88, "count": 12},
                    "fatigue": {"avg": "bad"},
                    "burnout": None,
                }
            },
            "alerts": [],
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/session-complete",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/session-complete", body),
        )
        self.assertEqual(response.status_code, 200)

        query_string = f"client_id={self.client_id}"
        response = self.client.get(
            f"/internal/client-context?{query_string}",
            headers=self.internal_headers("GET", "/internal/client-context", query_string),
        )
        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(response.json["baseline"]["averages"]["stress"], 0.75)
        self.assertNotIn("fatigue", response.json["baseline"]["averages"])
        self.assertNotIn("burnout", response.json["baseline"]["averages"])

    def test_authorize_meeting_join_allows_host_before_client_accepts(self):
        self.consultant_login()
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting
            from consultant_dashboard.core.meetings import build_join_window, generate_meeting_channel, iso_utc
            from consultant_dashboard.core.messaging import hash_access_token
            from datetime import datetime, timedelta, timezone

            start_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("host-token"),
                expires_at=iso_utc(end_at + timedelta(days=1)),
            )
            meeting_id = create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                title="Host Join Test",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=generate_meeting_channel(),
                response_access_link_id=access_link_id,
            )
            db.commit()
            db.close()

        body = json.dumps(
            {
                "participant_role": "host",
                "meeting_id": meeting_id,
                "consultant_id": self.consultant_id,
            },
            separators=(",", ":"),
        )
        response = self.client.post(
            "/internal/authorize-meeting-join",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/authorize-meeting-join", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["participant_role"], "host")
        self.assertEqual(response.json["participant_uid"], "103")

    def test_authorize_meeting_join_allows_guest_without_acceptance_inside_join_window(self):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting
            from consultant_dashboard.core.meetings import build_join_window, generate_meeting_channel, iso_utc
            from consultant_dashboard.core.messaging import hash_access_token
            from datetime import datetime, timedelta, timezone

            start_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("guest-token"),
                expires_at=iso_utc(end_at + timedelta(days=1)),
            )
            create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                title="Guest Join Test",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=generate_meeting_channel(),
                response_access_link_id=access_link_id,
            )
            db.commit()
            db.close()

        body = json.dumps(
            {
                "participant_role": "guest",
                "access_token": "guest-token",
            },
            separators=(",", ":"),
        )
        response = self.client.post(
            "/internal/authorize-meeting-join",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/authorize-meeting-join", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["participant_role"], "guest")

    def test_authorize_meeting_join_accepts_guest_bootstrap_lookup(self):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting, update_meeting_response_status
            from consultant_dashboard.core.meetings import build_join_window, generate_meeting_channel, iso_utc
            from consultant_dashboard.core.messaging import hash_access_token
            from datetime import datetime, timedelta, timezone

            start_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("guest-bootstrap-token"),
                expires_at=iso_utc(end_at + timedelta(days=1)),
            )
            meeting_id = create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                title="Guest Bootstrap Test",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=generate_meeting_channel(),
                response_access_link_id=access_link_id,
            )
            self.assertTrue(update_meeting_response_status(db, meeting_id=meeting_id, status="accepted"))
            db.commit()
            db.close()

        body = json.dumps(
            {
                "participant_role": "guest",
                "meeting_id": meeting_id,
                "response_access_link_id": access_link_id,
            },
            separators=(",", ":"),
        )
        response = self.client.post(
            "/internal/authorize-meeting-join",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/authorize-meeting-join", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["participant_role"], "guest")
        self.assertEqual(response.json["participant_uid"], "101")

    def test_authorize_meeting_join_allows_guest_outside_original_window_for_human_room(self):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting, update_meeting_response_status
            from consultant_dashboard.core.meetings import build_join_window, generate_meeting_channel, iso_utc
            from consultant_dashboard.core.messaging import hash_access_token
            from datetime import datetime, timedelta, timezone

            start_at = datetime.now(timezone.utc) + timedelta(minutes=12)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("early-guest-token"),
                expires_at=iso_utc(end_at + timedelta(days=1)),
            )
            meeting_id = create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                title="Early Guest Join Test",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=generate_meeting_channel(),
                response_access_link_id=access_link_id,
            )
            self.assertTrue(update_meeting_response_status(db, meeting_id=meeting_id, status="accepted"))
            db.commit()
            db.close()

        body = json.dumps(
            {
                "participant_role": "guest",
                "access_token": "early-guest-token",
            },
            separators=(",", ":"),
        )
        response = self.client.post(
            "/internal/authorize-meeting-join",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/authorize-meeting-join", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["participant_role"], "guest")

    def test_authorize_meeting_join_allows_guest_for_declined_human_meeting_room(self):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            from consultant_dashboard.core.db import create_client_access_link, create_scheduled_meeting, update_meeting_response_status
            from consultant_dashboard.core.meetings import build_join_window, generate_meeting_channel, iso_utc
            from consultant_dashboard.core.messaging import hash_access_token
            from datetime import datetime, timedelta, timezone

            start_at = datetime.now(timezone.utc) - timedelta(days=2)
            end_at = start_at + timedelta(minutes=30)
            join_start, join_end = build_join_window(start_at, end_at)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("declined-room-token"),
                expires_at=iso_utc(datetime.now(timezone.utc) + timedelta(days=30)),
            )
            meeting_id = create_scheduled_meeting(
                db,
                client_id=self.client_id,
                consultant_id=self.consultant_id,
                title="Declined Room Test",
                invite_message="",
                timezone_name="Europe/London",
                scheduled_start_at=iso_utc(start_at),
                scheduled_end_at=iso_utc(end_at),
                join_window_start_at=iso_utc(join_start),
                join_window_end_at=iso_utc(join_end),
                channel_name=generate_meeting_channel(),
                response_access_link_id=access_link_id,
            )
            self.assertTrue(update_meeting_response_status(db, meeting_id=meeting_id, status="declined"))
            db.commit()
            db.close()

        body = json.dumps(
            {
                "participant_role": "guest",
                "access_token": "declined-room-token",
            },
            separators=(",", ":"),
        )
        response = self.client.post(
            "/internal/authorize-meeting-join",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/authorize-meeting-join", body),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["participant_role"], "guest")
