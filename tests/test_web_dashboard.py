import json
import sqlite3
import time
import urllib.parse
from urllib.parse import parse_qs, urlparse
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
from consultant_dashboard.core.meetings import get_pair_channel, make_signed_meeting_access_token, verify_signed_join_bootstrap
from consultant_dashboard.core.messaging import hash_access_token


class ConsultantDashboardWebTest(ConsultantDashboardTestCase):
    def _future_local_time(self, *, days: int = 1, minutes: int = 0, timezone_name: str = "Europe/London") -> str:
        return (
            datetime.now(timezone.utc) + timedelta(days=days, minutes=minutes)
        ).astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%dT%H:%M")

    def _extract_meeting_response_token(self, reply_link: str) -> str:
        path = urlparse(reply_link).path.rstrip("/")
        if path.endswith("/join"):
            path = path[: -len("/join")]
        return path.rsplit("/", 1)[-1]

    def test_consultant_routes_require_login(self):
        response = self.client.get("/consultant/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consultant/login", response.location)

    def test_tenant_prefixed_consultant_routes_require_tenant_login(self):
        response = self.client.get("/v/mindfix/consultant/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/v/mindfix/consultant/login", response.location)

    def test_consultant_session_cannot_cross_vendor_prefix(self):
        self.admin_login()
        response = self.client.post(
            "/admin/vendors/new",
            data={
                "domain": "acmehealth.com",
                "name": "",
                "storage_root": "",
                "www_root": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        self.consultant_login()
        response = self.client.get("/v/acmehealth/consultant/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/v/acmehealth/consultant/login", response.location)

    def test_admin_routes_require_login(self):
        response = self.client.get("/admin/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.location)

    def test_admin_consultants_page_uses_name_link_and_add_consultant_link(self):
        self.admin_login()
        response = self.client.get("/admin/consultants")
        self.assertEqual(response.status_code, 200)
        db = get_db(self.app.config)
        consultant = get_consultant_by_email(db, "consultant@example.com", vendor_id=self.vendor_id)
        db.close()
        self.assertIn(
            f'href="/v/mindfix/admin/consultants/{consultant["id"]}">Test Consultant</a>'.encode(),
            response.data,
        )
        self.assertIn(b'href="/v/mindfix/admin/consultants/new"', response.data)
        self.assertNotIn(b">Edit</a>", response.data)

    def test_admin_vendors_page_uses_vendors_label_and_add_vendor_link(self):
        self.admin_login()
        response = self.client.get("/admin/vendors")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<h1>Vendors</h1>", response.data)
        self.assertIn(b'href="/v/mindfix/admin/vendors/new"', response.data)
        self.assertNotIn(b"Tenants", response.data)

    def test_admin_can_create_vendor_from_domain_defaults(self):
        self.admin_login()
        response = self.client.post(
            "/admin/vendors/new",
            data={
                "domain": "acmehealth.com",
                "name": "",
                "storage_root": "",
                "www_root": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Vendor created", response.data)

        db = get_db(self.app.config)
        vendor = db.execute("SELECT slug, name, primary_host, storage_root, www_root FROM vendors WHERE slug = ?", ("acmehealth",)).fetchone()
        db.close()
        self.assertIsNotNone(vendor)
        self.assertEqual(vendor["name"], "Acmehealth")
        self.assertEqual(vendor["primary_host"], "https://acmehealth.com")
        self.assertEqual(vendor["storage_root"], "/home/ubuntu/mindfix-runtime/vendors/acmehealth")
        self.assertEqual(vendor["www_root"], "/home/ubuntu/mindfix/consultant_dashboard/www/acmehealth")

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

    def test_consultant_sessions_table_hides_summary_text(self):
        self.ingest_session(session_id="sess_compact_001", urgent_escalation=False)
        self.consultant_login()
        response = self.client.get("/consultant/sessions")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<th>Duration</th>", response.data)
        self.assertNotIn(b"Generalized summary.", response.data)

    def test_tenant_prefixed_consultant_login_flow_preserves_prefix(self):
        response = self.client.post(
            "/v/mindfix/consultant/login",
            data={"email": "consultant@example.com", "password": "consultpass123"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/v/mindfix/consultant/verify", response.location)

        response = self.client.post(
            "/v/mindfix/consultant/verify",
            data={"code": "000000"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/v/mindfix/consultant/dashboard", response.location)

        dashboard = self.client.get("/v/mindfix/consultant/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"/v/mindfix/consultant/clients", dashboard.data)

    def test_tenant_prefixed_public_index_serves_vendor_site(self):
        response = self.client.get("/v/mindfix/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/v/mindfix/index.html", response.location)

        response = self.client.get("/v/mindfix/index.html")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MindFix \xe2\x80\x94 AI Mental Wellness Guided by Therapists", response.data)
        self.assertIn(b'/v/mindfix/consultant/login', response.data)
        self.assertIn(b'/v/mindfix/admin/login', response.data)
        self.assertIn(b'/v/mindfix/app', response.data)

    def test_consultant_login_uses_vendor_topbar_wordmark(self):
        response = self.client.get("/v/mindfix/consultant/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MindFix", response.data)
        self.assertIn(b'href="https://mindfix.me"', response.data)
        self.assertIn(b"/v/mindfix/consultant/google", response.data)
        self.assertIn(b">Continue<", response.data)

    def test_consultant_google_dev_flow_uses_same_account_and_vendor_prefix(self):
        response = self.client.get("/v/mindfix/consultant/google", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/v/mindfix/consultant/google/callback?code=dev-mode", response.location)

        response = self.client.get(response.location, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/v/mindfix/consultant/verify", response.location)

    def test_local_support_login_is_hidden_when_disabled(self):
        response = self.client.get(
            "/v/mindfix/consultant/local-support-login",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 404)

    def test_local_support_login_requires_localhost(self):
        self.app.config["LOCAL_SUPPORT_LOGIN_ENABLED"] = True
        self.app.config["LOCAL_SUPPORT_LOGIN_SECRET"] = "support-secret"
        response = self.client.get(
            "/v/mindfix/consultant/local-support-login",
            environ_overrides={"REMOTE_ADDR": "203.0.113.5"},
        )
        self.assertEqual(response.status_code, 404)

    def test_local_support_login_can_create_consultant_session_for_localhost(self):
        self.app.config["LOCAL_SUPPORT_LOGIN_ENABLED"] = True
        self.app.config["LOCAL_SUPPORT_LOGIN_SECRET"] = "support-secret"
        response = self.client.post(
            "/v/mindfix/consultant/local-support-login",
            data={"email": "consultant@example.com", "secret": "support-secret"},
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/v/mindfix/consultant/dashboard", response.location)

        dashboard = self.client.get(
            "/v/mindfix/consultant/dashboard",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Consultant Dashboard", dashboard.data)

    def test_consultant_google_uses_shared_google_callback_uri(self):
        self.app.config["AUTH_DEV_MODE"] = False
        self.app.config["GOOGLE_CLIENT_ID"] = "google-client-id"
        response = self.client.get("/v/mindfix/consultant/google", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response.location)
        params = parse_qs(parsed.query)
        self.assertEqual(
            params["redirect_uri"],
            [f"{self.app.config['PUBLIC_BASE_URL']}/auth/google/callback"],
        )
        self.assertIn("state", params)
        from consultant_dashboard.core.auth import _peek_dashboard_handoff
        state_payload = _peek_dashboard_handoff(params["state"][0])
        self.assertEqual(
            state_payload["complete_url"],
            "http://localhost/v/mindfix/consultant/google/callback",
        )

    def test_consultant_google_callback_accepts_signed_handoff_token(self):
        from consultant_dashboard.core.auth import _sign_dashboard_handoff
        with self.app.app_context():
            consultant_token = _sign_dashboard_handoff(
                {
                    "purpose": "consultant_google_complete",
                    "email": "consultant@example.com",
                    "vendor_slug": "mindfix",
                    "exp": int(time.time()) + 300,
                }
            )
        with mock.patch("consultant_dashboard.core.auth._send_or_store_code", return_value=None):
            response = self.client.get(
                f"/v/mindfix/consultant/google/callback?consultant_token={urllib.parse.quote(consultant_token)}",
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/v/mindfix/consultant/verify", response.location)

    def test_consultant_google_callback_redirects_to_verify_for_matching_vendor_email(self):
        self.app.config["AUTH_DEV_MODE"] = False
        self.app.config["GOOGLE_CLIENT_ID"] = "google-client-id"
        self.app.config["GOOGLE_CLIENT_SECRET"] = "google-client-secret"

        token_payload = {
            "sub": "google-sub-123",
            "email": "consultant@example.com",
            "name": "Test Consultant",
        }
        token_segment = b'eyJhbGciOiJub25lIn0'
        import base64, json as _json
        payload_segment = base64.urlsafe_b64encode(_json.dumps(token_payload).encode()).rstrip(b"=")
        fake_id_token = token_segment.decode() + "." + payload_segment.decode() + ".sig"

        class FakeGoogleTokenResponse:
            def read(self):
                import json as _json
                return _json.dumps({"id_token": fake_id_token}).encode()
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False

        self.client.get("/v/mindfix/consultant/login")
        with mock.patch("urllib.request.urlopen", return_value=FakeGoogleTokenResponse()), \
             mock.patch("consultant_dashboard.core.auth._send_or_store_code", return_value=None):
            response = self.client.get("/consultant/google/callback?code=test-code", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/v/mindfix/consultant/verify", response.location)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_tenant_prefixed_meeting_invite_links_stay_in_vendor(self, mocked_deliver_email):
        response = self.client.post(
            "/v/mindfix/consultant/login",
            data={"email": "consultant@example.com", "password": "consultpass123"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.client.post("/v/mindfix/consultant/verify", data={"code": "000000"}, follow_redirects=False)

        response = self.client.post(
            "/v/mindfix/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Tenant Invite",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        reply_link = mocked_deliver_email.call_args.kwargs["reply_link"]
        self.assertIn("/v/mindfix/meetings/respond/", reply_link)


    def test_consultant_can_create_client_and_view_detail(self):
        self.consultant_login()
        response = self.client.post(
            "/consultant/clients/new",
            data={
                "first_name": "Jamie",
                "last_name": "Demo",
                "year_of_birth": "1985",
                "sex": "female",
                "email": "jamie@example.com",
                "password": "jamiepass123",
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
        row = db.execute("SELECT password_hash, year_of_birth, sex FROM clients WHERE email = ?", ("jamie@example.com",)).fetchone()
        db.close()
        self.assertTrue(row["password_hash"])
        self.assertEqual(row["year_of_birth"], 1985)
        self.assertEqual(row["sex"], "female")

    def test_clients_table_uses_text_link_for_new_meeting_action(self):
        self.consultant_login()
        response = self.client.get("/consultant/clients")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'href="/v/mindfix/consultant/clients/{self.client_id}/meetings/new">New Meeting</a>'.encode(),
            response.data,
        )
        self.assertNotIn(
            f'href="/v/mindfix/consultant/clients/{self.client_id}/meetings/new" class="button-secondary button-link">'.encode(),
            response.data,
        )

    def test_consultant_client_creation_rejects_short_initial_password(self):
        self.consultant_login()
        response = self.client.post(
            "/consultant/clients/new",
            data={
                "first_name": "Jamie",
                "last_name": "Demo",
                "email": "jamie@example.com",
                "password": "short",
                "phone_country_code": "UK",
                "phone_number": "07700900333",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Password must be at least 8 characters.", response.data)

        db = get_db(self.app.config)
        row = db.execute("SELECT id FROM clients WHERE email = ?", ("jamie@example.com",)).fetchone()
        db.close()
        self.assertIsNone(row)

    def test_consultant_can_create_gmail_client_without_password(self):
        self.consultant_login()
        response = self.client.post(
            "/consultant/clients/new",
            data={
                "first_name": "Jamie",
                "last_name": "Gmail",
                "email": "jamie@gmail.com",
                "password": "ignoredpass123",
                "phone_country_code": "UK",
                "phone_number": "07700900333",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Jamie Gmail", response.data)

        db = get_db(self.app.config)
        row = db.execute(
            "SELECT first_name, last_name, display_name, password_hash FROM clients WHERE email = ?",
            ("jamie@gmail.com",),
        ).fetchone()
        db.close()
        self.assertEqual(row["first_name"], "Jamie")
        self.assertEqual(row["last_name"], "Gmail")
        self.assertEqual(row["display_name"], "Jamie Gmail")
        self.assertEqual(row["password_hash"], "")

    def test_consultant_can_update_client_access_fields(self):
        self.consultant_login()
        response = self.client.post(
            f"/consultant/clients/{self.client_id}",
            data={
                "first_name": "Alex",
                "last_name": "Demo Updated",
                "year_of_birth": "1980",
                "sex": "female",
                "email": "alex.updated@example.com",
                "phone_country_code": "US",
                "phone_number": "4155551212",
                "notification_email": "notify@example.com",
                "escalation_phone_country_code": "UK",
                "escalation_phone_number": "07700900444",
                "password": "clientreset123",
                "notes": "Updated generalized context.",
                "direction": "Start with a check-in on sleep.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client updated", response.data)
        self.assertIn(b"Client password updated", response.data)
        self.assertIn(b"Alex Demo Updated", response.data)
        self.assertIn(b"Start with a check-in on sleep.", response.data)

        db = get_db(self.app.config)
        row = db.execute(
            "SELECT first_name, last_name, display_name, email, phone_number, escalation_phone_number, year_of_birth, sex, password_hash FROM clients WHERE id = ?",
            (self.client_id,),
        ).fetchone()
        identity = db.execute(
            "SELECT email_hash, phone_hash FROM client_auth_identities WHERE client_id = ?",
            (self.client_id,),
        ).fetchone()
        db.close()
        self.assertEqual(row["first_name"], "Alex")
        self.assertEqual(row["last_name"], "Demo Updated")
        self.assertEqual(row["display_name"], "Alex Demo Updated")
        self.assertEqual(row["email"], "alex.updated@example.com")
        self.assertEqual(row["phone_number"], "+14155551212")
        self.assertEqual(row["escalation_phone_number"], "+447700900444")
        self.assertEqual(row["year_of_birth"], 1980)
        self.assertEqual(row["sex"], "female")
        self.assertTrue(row["password_hash"])
        self.assertIsNotNone(identity["email_hash"])
        self.assertIsNotNone(identity["phone_hash"])

    def test_consultant_update_to_gmail_client_clears_password(self):
        self.consultant_login()
        response = self.client.post(
            f"/consultant/clients/{self.client_id}",
            data={
                "first_name": "Alex",
                "last_name": "Demo",
                "email": "alex@gmail.com",
                "phone_country_code": "UK",
                "phone_number": "07700900111",
                "notification_email": "alex@gmail.com",
                "escalation_phone_country_code": "UK",
                "escalation_phone_number": "07700900111",
                "password": "ignoredpass123",
                "notes": "",
                "direction": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        db = get_db(self.app.config)
        row = db.execute("SELECT email, password_hash FROM clients WHERE id = ?", (self.client_id,)).fetchone()
        db.close()
        self.assertEqual(row["email"], "alex@gmail.com")
        self.assertEqual(row["password_hash"], "")

    def test_consultant_can_update_notes_and_direction_from_client_page(self):
        self.ingest_session(session_id="sess_notes_001", urgent_escalation=False)
        self.consultant_login()
        response = self.client.post(
            f"/consultant/clients/{self.client_id}",
            data={
                "form_name": "save_context",
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

    def test_session_detail_shows_audio_disabled_empty_state(self):
        self.ingest_session(session_id="sess_audio_disabled_001", urgent_escalation=False)
        db = get_db(self.app.config)
        db.execute(
            """
            UPDATE sessions
            SET audio_biomarkers_enabled = 0,
                video_biomarkers_enabled = 1,
                biomarker_storage_key = NULL
            WHERE id = ?
            """,
            ("sess_audio_disabled_001",),
        )
        db.commit()
        db.close()

        self.consultant_login()
        response = self.client.get("/consultant/sessions/sess_audio_disabled_001")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Audio biomarkers were not enabled for this session.", response.data)

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
                    vendor_id, client_id, consultant_id, status, title, timezone_name,
                    scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
                    channel_name, response_access_link_id, cancelled_at
                ) VALUES (?, ?, ?, 'cancelled', 'Cancelled Meeting', 'Europe/London',
                          '2026-04-20T10:00:00Z', '2026-04-20T10:30:00Z', '2026-04-20T09:45:00Z', '2026-04-20T11:00:00Z',
                          'CANCEL1234', ?, CURRENT_TIMESTAMP)
                """,
                (self.vendor_id, self.client_id, self.consultant_id, access_link_id),
            )
            db.commit()
            db.close()

        self.authenticate_client_session()
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
        response = self.client.get(f"/meetings/respond/{signed_token}", base_url="https://mindfix.me")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.location)
        self.assertIn("return=https%3A%2F%2Fmindfix.me%2Fmeetings%2Frespond%2F", response.location)

        self.client.set_cookie(key="mindfix_client_auth", value=self.client_auth_cookie())
        authed_response = self.client.get(f"/meetings/respond/{signed_token}")
        self.assertEqual(authed_response.status_code, 200)
        self.assertIn(b"Signed Access Meeting", authed_response.data)

    def test_meeting_response_page_reauths_when_wrong_client_is_signed_in(self):
        with self.client.application.app_context():
            db = get_db(self.app.config)
            access_link_id = create_client_access_link(
                db,
                client_id=self.client_id,
                created_by=self.consultant_id,
                token_hash=hash_access_token("wrong-client-token"),
                expires_at="2099-01-01T00:00:00Z",
            )
            db.execute(
                """
                INSERT INTO scheduled_meetings (
                    vendor_id, client_id, consultant_id, meeting_type, status, title, timezone_name,
                    scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
                    channel_name, response_access_link_id
                ) VALUES (?, ?, ?, 'human', 'scheduled', 'Wrong Client Meeting', 'Europe/London',
                          '2026-04-20T10:00:00Z', '2026-04-20T10:30:00Z', '2026-04-20T09:45:00Z', '2026-04-20T11:00:00Z',
                          'WRONGCLIENT1', ?)
                """,
                (self.vendor_id, self.client_id, self.consultant_id, access_link_id),
            )
            db.commit()
            db.close()

        self.client.set_cookie(key="mindfix_client_auth", value=self.client_auth_cookie(client_id="someone-else"))
        response = self.client.get("/meetings/respond/wrong-client-token")
        self.assertEqual(response.status_code, 302)
        self.assertIn("reauth=1", response.location)

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
                    vendor_id, client_id, consultant_id, status, title, timezone_name,
                    scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
                    channel_name, response_access_link_id, completed_at
                ) VALUES (?, ?, ?, 'completed', 'Completed Meeting', 'Europe/London',
                          '2026-04-20T10:00:00Z', '2026-04-20T10:30:00Z', '2026-04-20T09:45:00Z', '2026-04-20T11:00:00Z',
                          'DONE123456', ?, CURRENT_TIMESTAMP)
                """,
                (self.vendor_id, self.client_id, self.consultant_id, access_link_id),
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
                    vendor_id, client_id, consultant_id, status, title, timezone_name,
                    scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
                    channel_name, response_access_link_id, completed_at
                ) VALUES (?, ?, ?, 'completed', 'Completed No Show', 'Europe/London',
                          '2026-04-20T10:00:00Z', '2026-04-20T10:30:00Z', '2026-04-20T09:45:00Z', '2026-04-20T11:00:00Z',
                          'NOSHOWDONE1', ?, CURRENT_TIMESTAMP)
                """,
                (self.vendor_id, self.client_id, self.consultant_id, access_link_id),
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
                    vendor_id, client_id, consultant_id, status, title, timezone_name,
                    scheduled_start_at, scheduled_end_at, join_window_start_at, join_window_end_at,
                    channel_name, response_access_link_id
                ) VALUES (?, ?, ?, 'accepted', 'Open No Show', 'Europe/London',
                          '2026-04-20T10:00:00Z', '2026-04-20T10:30:00Z', '2026-04-20T09:45:00Z', '2026-04-20T11:00:00Z',
                          'NOSHOWOPEN1', ?)
                """,
                (self.vendor_id, self.client_id, self.consultant_id, access_link_id),
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
        token = self._extract_meeting_response_token(mocked_link)
        self.authenticate_client_session()

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
    def test_immediate_meeting_email_links_directly_to_join(self, mocked_deliver_email):
        self.consultant_login()
        response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Meet Now Direct Join",
                "scheduled_start_at": self._future_local_time(days=0, minutes=0),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        reply_link = mocked_deliver_email.call_args.kwargs["reply_link"]
        html_body = mocked_deliver_email.call_args.kwargs["html_override"]
        self.assertIn("/meetings/respond/", reply_link)
        self.assertIn("/join", reply_link)
        self.assertIn(reply_link, html_body)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_client_can_toggle_between_accept_and_decline(self, mocked_deliver_email):
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
        token = self._extract_meeting_response_token(mocked_deliver_email.call_args.kwargs["reply_link"])
        self.authenticate_client_session()

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
        self.assertIn(b"Meeting declined", decline_response.data)

        db = get_db(self.app.config)
        meeting = db.execute(
            "SELECT status, accepted_at, declined_at FROM scheduled_meetings WHERE id = ?",
            (meeting_id,),
        ).fetchone()
        db.close()
        self.assertEqual(meeting["status"], "declined")
        self.assertIsNone(meeting["accepted_at"])
        self.assertTrue(meeting["declined_at"])

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_declining_weekly_meeting_creates_next_occurrence_and_invites_it(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "meeting_type": "human",
                "repeat_weekly": "1",
                "title": "Weekly Decline Test",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "Weekly invite.",
            },
            follow_redirects=True,
        )
        first_token = self._extract_meeting_response_token(mocked_deliver_email.call_args.kwargs["reply_link"])
        self.authenticate_client_session()
        response = self.client.post(
            f"/meetings/respond/{first_token}",
            data={"action": "decline"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Meeting declined", response.data)

        db = get_db(self.app.config)
        rows = db.execute(
            """
            SELECT id, status, repeat_weekly, scheduled_start_at, invite_delivery_status,
                   reminder_24h_sent_at, reminder_1m_sent_at
            FROM scheduled_meetings
            WHERE client_id = ? AND consultant_id = ? AND title = ?
            ORDER BY scheduled_start_at ASC
            """,
            (self.client_id, self.consultant_id, "Weekly Decline Test"),
        ).fetchall()
        db.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "declined")
        self.assertEqual(rows[1]["status"], "scheduled")
        self.assertEqual(rows[1]["repeat_weekly"], 1)
        first_start = datetime.fromisoformat(rows[0]["scheduled_start_at"].replace("Z", "+00:00"))
        next_start = datetime.fromisoformat(rows[1]["scheduled_start_at"].replace("Z", "+00:00"))
        self.assertEqual(next_start - first_start, timedelta(days=7))
        self.assertEqual(rows[1]["invite_delivery_status"], "sent")
        self.assertIsNone(rows[1]["reminder_24h_sent_at"])
        self.assertIsNone(rows[1]["reminder_1m_sent_at"])
        self.assertEqual(mocked_deliver_email.call_count, 2)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_cancelling_weekly_meeting_creates_next_occurrence(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "meeting_type": "human",
                "repeat_weekly": "1",
                "title": "Weekly Cancel Test",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        db = get_db(self.app.config)
        meeting_id = db.execute(
            "SELECT id FROM scheduled_meetings WHERE title = ? ORDER BY created_at DESC LIMIT 1",
            ("Weekly Cancel Test",),
        ).fetchone()["id"]
        db.close()
        response = self.client.post(
            f"/consultant/meetings/{meeting_id}",
            data={"action": "cancel"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Meeting cancelled", response.data)

        db = get_db(self.app.config)
        rows = db.execute(
            """
            SELECT status, repeat_weekly, invite_delivery_status
            FROM scheduled_meetings
            WHERE client_id = ? AND consultant_id = ? AND title = ?
            ORDER BY scheduled_start_at ASC
            """,
            (self.client_id, self.consultant_id, "Weekly Cancel Test"),
        ).fetchall()
        db.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "cancelled")
        self.assertEqual(rows[1]["status"], "scheduled")
        self.assertEqual(rows[1]["repeat_weekly"], 1)
        self.assertEqual(rows[1]["invite_delivery_status"], "sent")
        self.assertEqual(mocked_deliver_email.call_count, 2)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_marking_weekly_meeting_no_show_creates_next_occurrence(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "meeting_type": "human",
                "repeat_weekly": "1",
                "title": "Weekly No Show Test",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        db = get_db(self.app.config)
        meeting_id = db.execute(
            "SELECT id FROM scheduled_meetings WHERE title = ? ORDER BY created_at DESC LIMIT 1",
            ("Weekly No Show Test",),
        ).fetchone()["id"]
        db.close()
        response = self.client.post(
            f"/consultant/meetings/{meeting_id}",
            data={"action": "mark_no_show", "attendance_outcome": "client_no_show"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No-show recorded", response.data)

        db = get_db(self.app.config)
        rows = db.execute(
            """
            SELECT status, attendance_outcome, repeat_weekly
            FROM scheduled_meetings
            WHERE client_id = ? AND consultant_id = ? AND title = ?
            ORDER BY scheduled_start_at ASC
            """,
            (self.client_id, self.consultant_id, "Weekly No Show Test"),
        ).fetchall()
        db.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[0]["attendance_outcome"], "client_no_show")
        self.assertEqual(rows[1]["status"], "scheduled")
        self.assertEqual(rows[1]["repeat_weekly"], 1)

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
        first_token = self._extract_meeting_response_token(mocked_deliver_email.call_args.kwargs["reply_link"])
        self.authenticate_client_session()
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
        self.assertIn(b"Enter Meeting Room", response.data)
        self.assertIn(b"Join Flow Test", response.data)

        token = self._extract_meeting_response_token(mocked_deliver_email.call_args.kwargs["reply_link"])
        self.authenticate_client_session()
        response = self.client.post(
            f"/meetings/respond/{token}",
            data={"action": "accept"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Meeting accepted", response.data)
        self.assertIn(b"Status: Accepted", response.data)
        self.assertIn(b"Enter Meeting Room", response.data)
        self.assertIn(b"Add to calendar", response.data)
        self.assertIn(f"/meetings/respond/{token}/join".encode("utf-8"), response.data)
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
        token = self._extract_meeting_response_token(mocked_deliver_email.call_args.kwargs["reply_link"])
        self.authenticate_client_session()
        response = self.client.get(f"/meetings/respond/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b">Accept<", response.data)
        self.assertIn(b">Decline<", response.data)
        self.assertIn(b"Status: Invited", response.data)
        self.assertIn(b"Enter Meeting Room", response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_meeting_response_shows_toggle_actions_after_accept(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Toggle Response",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        token = self._extract_meeting_response_token(mocked_deliver_email.call_args.kwargs["reply_link"])
        self.authenticate_client_session()
        self.client.post(
            f"/meetings/respond/{token}",
            data={"action": "accept"},
            follow_redirects=True,
        )
        response = self.client.get(f"/meetings/respond/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'value="accept"', response.data)
        self.assertIn(b'value="decline"', response.data)
        self.assertIn(b"Add to calendar", response.data)
        self.assertIn(b"Status: Accepted", response.data)
        self.assertIn(b"reminder email with your meeting link", response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_early_host_join_does_not_show_in_progress_to_client_before_start(self, mocked_deliver_email):
        self.consultant_login()
        start_at = (datetime.now(timezone.utc) + timedelta(minutes=12)).astimezone(ZoneInfo("Europe/London"))
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Early Host Join",
                "scheduled_start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        token = self._extract_meeting_response_token(mocked_deliver_email.call_args.kwargs["reply_link"])
        self.authenticate_client_session()
        self.client.post(
            f"/meetings/respond/{token}",
            data={"action": "accept"},
            follow_redirects=True,
        )
        db = get_db(self.app.config)
        meeting = db.execute(
            "SELECT id, consultant_id FROM scheduled_meetings ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        db.close()
        body = json.dumps(
            {
                "meeting_id": meeting["id"],
                "participant_role": "host",
                "consultant_id": meeting["consultant_id"],
            },
            separators=(",", ":"),
        )
        auth_response = self.client.post(
            "/internal/authorize-meeting-join",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/authorize-meeting-join", body),
        )
        self.assertEqual(auth_response.status_code, 200)
        joined_body = json.dumps(
            {
                "meeting_id": meeting["id"],
                "participant_role": "host",
                "participant_id": meeting["consultant_id"],
            },
            separators=(",", ":"),
        )
        joined_response = self.client.post(
            "/internal/meeting-joined",
            data=joined_body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/meeting-joined", joined_body),
        )
        self.assertEqual(joined_response.status_code, 200)
        response = self.client.get(f"/meetings/respond/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Status: Accepted", response.data)
        self.assertNotIn(b"Status: in progress", response.data)
        self.assertNotIn(b"Meeting now", response.data)
        self.assertIn(b"Enter Meeting Room", response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_cancelled_invite_page_can_still_enter_current_pair_meeting(self, mocked_deliver_email):
        self.consultant_login()
        start_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).astimezone(ZoneInfo("Europe/London"))
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Old Invite",
                "scheduled_start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        old_token = self._extract_meeting_response_token(mocked_deliver_email.call_args.kwargs["reply_link"])
        db = get_db(self.app.config)
        old_meeting_id = db.execute(
            "SELECT id FROM scheduled_meetings ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["id"]
        db.close()
        self.client.post(
            f"/consultant/meetings/{old_meeting_id}",
            data={"action": "cancel"},
            follow_redirects=True,
        )
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Current Meeting",
                "scheduled_start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.authenticate_client_session()
        response = self.client.get(f"/meetings/respond/{old_token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Status: Cancelled", response.data)
        self.assertIn(b"Enter Meeting Room", response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_old_consultant_meeting_join_uses_current_pair_context(self, mocked_deliver_email):
        self.consultant_login()
        start_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).astimezone(ZoneInfo("Europe/London"))
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Old Consultant Meeting",
                "scheduled_start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        db = get_db(self.app.config)
        old_meeting_id = db.execute(
            "SELECT id FROM scheduled_meetings ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["id"]
        db.close()
        self.client.post(
            f"/consultant/meetings/{old_meeting_id}",
            data={"action": "cancel"},
            follow_redirects=True,
        )
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Current Consultant Meeting",
                "scheduled_start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        db = get_db(self.app.config)
        current_meeting = db.execute(
            "SELECT id, channel_name FROM scheduled_meetings WHERE title = ? ORDER BY created_at DESC LIMIT 1",
            ("Current Consultant Meeting",),
        ).fetchone()
        db.close()

        response = self.client.get(f"/consultant/meetings/{old_meeting_id}/join", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        params = parse_qs(urlparse(response.location).query)
        self.assertNotIn("autoconnect", params)
        token = params["join_bootstrap"][0]
        payload = verify_signed_join_bootstrap(self.app.config["INTERNAL_SHARED_SECRET"], token)
        self.assertEqual(payload["meeting_id"], current_meeting["id"])
        self.assertEqual(payload["channel_name"], current_meeting["channel_name"])

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_meeting_response_join_redirects_to_tech_check_first(self, mocked_deliver_email):
        self.consultant_login()
        start_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).astimezone(ZoneInfo("Europe/London"))
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Tech Check Redirect",
                "scheduled_start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        token = self._extract_meeting_response_token(mocked_deliver_email.call_args.kwargs["reply_link"])
        self.authenticate_client_session()
        response = self.client.get(f"/meetings/respond/{token}/join", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        params = parse_qs(urlparse(response.location).query)
        self.assertEqual(params.get("meeting_mode"), ["true"])
        self.assertNotIn("autoconnect", params)
        self.assertIn("join_bootstrap", params)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_consultant_meeting_detail_shows_client_response_status(self, mocked_deliver_email):
        self.consultant_login()
        self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Response Status Detail",
                "scheduled_start_at": self._future_local_time(days=1),
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        token = self._extract_meeting_response_token(mocked_deliver_email.call_args.kwargs["reply_link"])
        self.authenticate_client_session()
        self.client.post(
            f"/meetings/respond/{token}",
            data={"action": "accept"},
            follow_redirects=True,
        )
        db = get_db(self.app.config)
        meeting_id = db.execute(
            "SELECT id FROM scheduled_meetings ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["id"]
        db.close()
        response = self.client.get(f"/consultant/meetings/{meeting_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client response", response.data)
        self.assertIn(b"Accepted", response.data)

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
        token = self._extract_meeting_response_token(mocked_deliver_email.call_args.kwargs["reply_link"])
        self.authenticate_client_session()
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
        self.assertIn(b"Resend Invite", response.data)
        self.assertIn(b"Enter Meeting Room", response.data)
        db = get_db(self.app.config)
        meeting_id = db.execute(
            "SELECT id FROM scheduled_meetings ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["id"]
        db.close()
        join_response = self.client.get(
            f"/consultant/meetings/{meeting_id}/join",
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)
        self.assertIn("returnurl=%2Fv%2Fmindfix%2Fconsultant%2Fdashboard", join_response.location)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_past_meeting_detail_hides_resend_invite_but_keeps_room_entry(self, mocked_deliver_email):
        self.consultant_login()
        response = self.client.post(
            "/consultant/meetings/new",
            data={
                "client_id": self.client_id,
                "title": "Past Meeting",
                "scheduled_start_at": "2026-04-20T10:00",
                "duration_minutes": "30",
                "timezone_name": "Europe/London",
                "invite_message": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        db = get_db(self.app.config)
        meeting_id = db.execute(
            "SELECT id FROM scheduled_meetings ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["id"]
        db.close()
        response = self.client.get(f"/consultant/meetings/{meeting_id}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Resend Invite", response.data)
        self.assertIn(b"Enter Meeting Room", response.data)

    def test_consultant_sessions_support_client_filter_and_summary_search(self):
        payload = {
            "client_id": self.client_id,
            "consultant_id": self.consultant_id,
            "session_id": "sess_a",
            "profile": "therapy",
            "channel": "channel-a",
            "started_at": "2026-04-13T18:00:00Z",
            "ended_at": "2026-04-13T18:05:00Z",
            "duration_seconds": 300,
            "status": "completed",
            "summary": {"brief_overview": "Sleep is improving"},
            "biomarkers": {"averages": {"stress_index": 40.0}},
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
        db = get_db(self.app.config)
        from consultant_dashboard.core.db import create_client
        other_client_id = create_client(
            db,
            consultant_id=self.consultant_id,
            first_name="Other",
            last_name="Client",
            email="other@example.com",
            password_hash="",
            phone_number="+447700900222",
            notification_email="other@example.com",
            escalation_phone_number="+447700900222",
            year_of_birth=None,
            sex="",
            notes="",
            direction="",
        )
        db.commit()
        db.close()
        payload = {
            "client_id": other_client_id,
            "consultant_id": self.consultant_id,
            "session_id": "sess_b",
            "profile": "therapy",
            "channel": "channel-b",
            "started_at": "2026-04-14T18:00:00Z",
            "ended_at": "2026-04-14T18:05:00Z",
            "duration_seconds": 300,
            "status": "completed",
            "summary": {"brief_overview": "Work stress remains high"},
            "biomarkers": {"averages": {"stress_index": 70.0}},
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
        self.consultant_login()
        response = self.client.get(f"/consultant/sessions?client_id={self.client_id}&q=sleep")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/consultant/sessions/sess_a", response.data)
        self.assertNotIn(b"/consultant/sessions/sess_b", response.data)
        self.assertNotIn(b"Work stress remains high", response.data)
        self.assertIn(b"All clients", response.data)

    @mock.patch("consultant_dashboard.core.web.deliver_email", return_value=("sent", ""))
    def test_consultant_cannot_delete_meeting_from_detail_page(self, mocked_deliver_email):
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
        self.assertIn(b"Meeting deletion is disabled", response.data)

        db = get_db(self.app.config)
        deleted = db.execute(
            "SELECT id FROM scheduled_meetings WHERE id = ?",
            (row["id"],),
        ).fetchone()
        db.close()
        self.assertIsNotNone(deleted)

    def test_consultant_client_create_rejects_invalid_phone(self):
        self.consultant_login()
        response = self.client.post(
            "/consultant/clients/new",
            data={
                "first_name": "Jamie",
                "last_name": "Demo",
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
        self.assertNotIn(b"Last Session", response.data)
        self.assertIn(b"Messages", response.data)
        self.assertIn(b"Edit Contact Details", response.data)
        self.assertIn(b"Notes And Direction", response.data)
        self.assertIn(b"Client Key Point Summary - AI Sessions", response.data)
        self.assertIn(b"Client Key Point Summary - Human Sessions", response.data)
        self.assertIn(b"Average", response.data)
        self.assertIn(b"Max", response.data)
        self.assertRegex(response.data, rb"\d+%")
        self.assertEqual(response.data.count(b'data-biomarker-section-root="1"'), 1)
        self.assertNotIn(b"Client Sign-In Match", response.data)

        response = self.client.get("/consultant/sessions")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"sess_web_001", response.data)

        response = self.client.get("/consultant/sessions/sess_web_001")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Elevated stress", response.data)
        self.assertIn(f'href="/v/mindfix/consultant/clients/{self.client_id}#notes"'.encode(), response.data)
        self.assertIn(b"Average", response.data)
        self.assertIn(b"Max", response.data)
        self.assertIn(b"Session Key Point Summary", response.data)
        self.assertRegex(response.data, rb"\d+%")
        self.assertEqual(response.data.count(b'data-biomarker-section-root="1"'), 1)
        self.assertNotIn(b"Notes And Direction", response.data)
        self.assertNotIn(b"Biomarker summary", response.data)
        self.assertNotIn(b"Brief summary", response.data)
        self.assertNotIn(b"Full summary", response.data)
        self.assertNotIn(f'action="/v/mindfix/consultant/clients/{self.client_id}/messages/send"'.encode(), response.data)
        self.assertNotIn(f'data-thread-endpoint="/v/mindfix/consultant/clients/{self.client_id}/messages/thread"'.encode(), response.data)

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
        self.authenticate_client_session()

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
        self.authenticate_client_session()
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
                vendor_id, email, password_hash, name, phone_number, notification_email, escalation_phone_number, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (self.vendor_id, "other@example.com", "hash", "Other", "+447700900222", "other@example.com", "+447700900222"),
        )
        other_id = db.execute("SELECT id FROM consultants WHERE email = ?", ("other@example.com",)).fetchone()["id"]
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO consultant_clients (vendor_id, consultant_id, client_id, role) VALUES (?, ?, ?, 'primary')",
                (self.vendor_id, other_id, self.client_id),
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
        self.assertIn(b"Add Consultant", response.data)
        self.assertIn(b"Search name, vendor, email or phone", response.data)

    def test_admin_can_create_consultant_and_duplicate_is_handled(self):
        self.admin_login()
        response = self.client.post(
            "/admin/consultants/new",
            data={
                "vendor_id": self.vendor_id,
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
            "/admin/consultants/new",
            data={
                "vendor_id": self.vendor_id,
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
        self.assertIn("/consultant/login", response.location)

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
        self.assertIn("/admin/login", response.location)

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
