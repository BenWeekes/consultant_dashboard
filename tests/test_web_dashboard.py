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
        self.assertIn(b"Identity linked", response.data)

        db = get_db(self.app.config)
        row = db.execute("SELECT password_hash FROM clients WHERE email = ?", ("jamie@example.com",)).fetchone()
        db.close()
        self.assertTrue(row["password_hash"])

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
        self.assertIn(b"Potential self-harm concern detected", response.data)
        self.assertIn(b"Identity linked", response.data)

        response = self.client.get("/consultant/sessions")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"sess_web_001", response.data)

        response = self.client.get("/consultant/sessions/sess_web_001")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Elevated stress", response.data)

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
        response = self.client.post("/logout", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/home", response.location)

        response = self.client.get("/consultant/dashboard", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consultant/login", response.location)

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
