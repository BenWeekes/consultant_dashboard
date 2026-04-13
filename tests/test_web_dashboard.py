from tests.support import ConsultantDashboardTestCase


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
                "phone_number": "+447700900333",
                "notes": "General check-in.",
                "direction": "Review coping strategies.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Jamie Demo", response.data)
        self.assertIn(b"Review coping strategies.", response.data)
        self.assertIn(b"Identity linked", response.data)

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

        response = self.client.get("/admin/consultants")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"consultant@example.com", response.data)

    def test_admin_can_create_consultant_and_duplicate_is_handled(self):
        self.admin_login()
        response = self.client.post(
            "/admin/consultants",
            data={
                "name": "Second Consultant",
                "email": "second@example.com",
                "phone_number": "+447700900222",
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
                "phone_number": "+447700900222",
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
