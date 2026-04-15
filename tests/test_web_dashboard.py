import sqlite3
from unittest import mock

from tests.support import ConsultantDashboardTestCase
from consultant_dashboard.core.auth import _load_admin_users
from consultant_dashboard.core.db import get_consultant_by_email, get_db


class ConsultantDashboardWebTest(ConsultantDashboardTestCase):
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
        self.assertIn(b"Alex Demo", response.data)
        self.assertIn(b"Linked Clients", response.data)

    def test_consultant_can_create_client_and_view_detail(self):
        self.consultant_login()
        response = self.client.post(
            "/consultant/clients",
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
            "/consultant/clients",
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

    def test_consultant_client_create_rejects_invalid_phone(self):
        self.consultant_login()
        response = self.client.post(
            "/consultant/clients",
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
        self.assertIn(b"Ready", response.data)

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
