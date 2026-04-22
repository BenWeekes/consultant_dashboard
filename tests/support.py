import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from typing import Optional

from werkzeug.security import generate_password_hash

from consultant_dashboard.app import PASSWORD_HASH_METHOD, create_app
from consultant_dashboard.core.db import (
    create_client,
    create_consultant,
    get_db,
    init_db,
    upsert_client_auth_identity,
)


class ConsultantDashboardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = self.tmpdir.name
        self.db_path = os.path.join(root, "dashboard.sqlite3")
        self.storage_root = os.path.join(root, "storage")
        self.admin_auth_file = os.path.join(root, "admin_auth.conf")
        self.internal_secret = "smoke-secret"

        with open(self.admin_auth_file, "w", encoding="utf-8") as f:
            f.write("session_secret=test-session-secret\n")
            f.write("session_ttl=28800\n")
            f.write(
                "admin@example.com="
                + generate_password_hash("adminpass123", method=PASSWORD_HASH_METHOD)
                + "\n"
            )
        os.chmod(self.admin_auth_file, 0o600)

        os.environ["CONSULTANT_DB_PATH"] = self.db_path
        os.environ["THERAPY_STORAGE_ROOT"] = self.storage_root
        os.environ["THERAPY_STORAGE_BACKEND"] = "filesystem"
        os.environ["THERAPY_MASTER_KEY"] = "b" * 64
        os.environ["CONSULTANT_INTERNAL_SHARED_SECRET"] = self.internal_secret
        os.environ["CONSULTANT_ADMIN_AUTH_FILE"] = self.admin_auth_file
        os.environ["CONSULTANT_SESSION_TTL"] = "28800"
        os.environ["CONSULTANT_AUTH_DEV_MODE"] = "true"
        os.environ["THERAPY_DASHBOARD_BRAND_NAME"] = "mindfix.me"

        self.app = create_app()
        self.app.testing = True
        init_db(self.app.config)

        db = get_db(self.app.config)
        create_consultant(
            db,
            email="consultant@example.com",
            name="Test Consultant",
            phone_number="+447700900000",
            password_hash=generate_password_hash("consultpass123", method=PASSWORD_HASH_METHOD),
            notification_email="consultant@example.com",
            escalation_phone_number="+447700900000",
        )
        consultant = db.execute(
            "SELECT id, email, vendor_id FROM consultants WHERE email = ?",
            ("consultant@example.com",),
        ).fetchone()
        self.consultant_id = consultant["id"]
        self.vendor_id = consultant["vendor_id"]
        self.client_id = create_client(
            db,
            consultant_id=self.consultant_id,
            display_name="Alex Demo",
            email="alex@example.com",
            password_hash=generate_password_hash("clientpass123", method=PASSWORD_HASH_METHOD),
            phone_number="+447700900111",
            notification_email="consultant@example.com",
            escalation_phone_number="+447700900000",
            notes="Generalized notes only.",
            direction="Check stress and routines.",
        )
        upsert_client_auth_identity(
            db,
            client_id=self.client_id,
            email_hash=self.sha256("alex@example.com"),
            normalized_name_hash=self.sha256("alex demo"),
            phone_hash=self.sha256("+447700900111"),
        )
        db.commit()
        db.close()

        self.client = self.app.test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def sha256(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def internal_headers(self, method: str, path: str, payload: str, timestamp: int = None):
        ts = str(timestamp or int(time.time()))
        canonical = f"{ts}.{method}.{path}.{payload}".encode("utf-8")
        signature = hmac.new(
            self.internal_secret.encode("utf-8"),
            canonical,
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-Consultant-Timestamp": ts,
            "X-Consultant-Signature": signature,
        }

    def consultant_login(self):
        response = self.client.post(
            "/consultant/login",
            data={"email": "consultant@example.com", "password": "consultpass123"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consultant/verify", response.location)
        response = self.client.post(
            "/consultant/verify",
            data={"code": "000000"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consultant/dashboard", response.location)

    def admin_login(self):
        response = self.client.post(
            "/admin/login",
            data={"email": "admin@example.com", "password": "adminpass123"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/dashboard", response.location)

    def ingest_session(
        self,
        session_id: str = "sess_test_001",
        urgent_escalation: bool = False,
        include_alert: bool = True,
        transcript: Optional[dict] = None,
    ):
        payload = {
            "client_id": self.client_id,
            "consultant_id": self.consultant_id,
            "session_id": session_id,
            "profile": "therapy",
            "channel": "smoke-channel",
            "started_at": "2026-04-13T18:00:00Z",
            "ended_at": "2026-04-13T18:05:00Z",
            "duration_seconds": 300,
            "status": "completed",
            "summary": {"overview": "Generalized summary."},
            "biomarkers": {"averages": {"stress_index": 52.5, "hrv": 31.0}},
            "alerts": [],
        }
        if transcript is not None:
            payload["transcript"] = transcript
        if include_alert:
            payload["alerts"].append(
                {
                    "severity": "warning",
                    "source": "thymia",
                    "title": "Elevated stress",
                    "details": {"metric": "stress_index", "delta": 4.2},
                }
            )
        if urgent_escalation:
            payload["urgent_escalation"] = True
            payload["escalation_reason"] = "Potential self-harm concern detected"
            payload["escalation_source"] = "llm"
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(
            "/internal/session-complete",
            data=body,
            content_type="application/json",
            headers=self.internal_headers("POST", "/internal/session-complete", body),
        )
        self.assertEqual(response.status_code, 200)
        return response
