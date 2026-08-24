import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
import base64
from typing import Optional

from werkzeug.security import generate_password_hash

from consultant_dashboard.app import PASSWORD_HASH_METHOD, create_app
from consultant_dashboard.core import auth as dashboard_auth
from consultant_dashboard.core.db import (
    create_client,
    create_client_access_link,
    create_consultant,
    create_scheduled_meeting,
    get_db,
    init_db,
    upsert_client_auth_identity,
)
from consultant_dashboard.core.meetings import build_join_window, generate_meeting_channel, iso_utc
from consultant_dashboard.core.messaging import hash_access_token
from datetime import datetime, timedelta, timezone


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
        os.environ["THERAPY_AUTH_JWT_SECRET"] = "test-jwt-secret"
        os.environ["CONSULTANT_CLIENT_AUTH_JWT_SECRET"] = "test-jwt-secret"
        os.environ["AGORA_APP_ID"] = "20b7c51ff4c644ab80cf5a4e646b0537"
        os.environ["AGORA_APP_CERTIFICATE"] = "11111111111111111111111111111111"
        os.environ["CRISIS_CALL_FROM_NUMBER"] = "441473943851"
        os.environ["CRISIS_CALL_SIP_GATEWAY"] = "agora-us-east.pstn.ashburn.twilio.com"
        os.environ["CRISIS_CALL_REGION"] = "AREA_CODE_NA"
        os.environ["CRISIS_CALL_PSTN_UID"] = "43455"
        os.environ["CONSULTANT_LOCAL_SUPPORT_LOGIN_ENABLED"] = "false"
        os.environ["CONSULTANT_LOCAL_SUPPORT_LOGIN_SECRET"] = ""
        os.environ["SHEN_AVAILABLE"] = "true"

        with dashboard_auth._VERIFY_RATE_LOCK:
            dashboard_auth._SEND_RATE_LIMITS.clear()
            dashboard_auth._CHECK_RATE_LIMITS.clear()
            dashboard_auth._ADMIN_LOGIN_RATE_LIMITS.clear()

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
            first_name="Alex",
            last_name="Demo",
            email="alex@example.com",
            password_hash=generate_password_hash("clientpass123", method=PASSWORD_HASH_METHOD),
            phone_number="+447700900111",
            notification_email="consultant@example.com",
            escalation_phone_number="+447700900000",
            year_of_birth=1974,
            sex="male",
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

    def client_auth_cookie(self, client_id: Optional[str] = None, vendor_slug: str = "mindfix") -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "user_id": self.sha256(f"client|{client_id or self.client_id}"),
            "client_id": client_id or self.client_id,
            "vendor_slug": vendor_slug,
            "email": "alex@example.com",
            "name": "Alex Demo",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        header_b64 = base64.urlsafe_b64encode(
            json.dumps(header, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        payload_b64 = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        signature = hmac.new(
            self.app.config["CLIENT_AUTH_JWT_SECRET"].encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        sig_b64 = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        return f"{header_b64}.{payload_b64}.{sig_b64}"

    def authenticate_client_session(
        self, client_id: Optional[str] = None, vendor_slug: str = "mindfix"
    ) -> None:
        self.client.set_cookie(
            key="mindfix_client_auth",
            value=self.client_auth_cookie(client_id=client_id, vendor_slug=vendor_slug),
        )

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
            "ai_personal_summary": {
                "key_point_summary": {
                    "headline": "Client Key Point Summary - AI Sessions",
                    "body": "Recurring AI-session themes include stress, burnout, and the need for steady routines.",
                },
                "brief_overview": "Client Key Point Summary - AI Sessions",
                "full_summary": "Recurring AI-session themes include stress, burnout, and the need for steady routines.",
            },
            "biomarkers": {
                "averages": {"stress_index": 52.5, "hrv": 31.0, "stress": 0.42, "burnout": 0.31},
                "voice": {
                    "stress": {"avg": 0.42, "max": 0.67, "count": 4, "min": 0.21},
                    "burnout": {"avg": 0.31, "max": 0.49, "count": 4, "min": 0.14},
                    "happy": {"avg": 0.11, "max": 0.18, "count": 4, "min": 0.03},
                    "sad": {"avg": 0.27, "max": 0.43, "count": 4, "min": 0.09},
                },
                "vitals": {
                    "stress_index": {"avg": 52.5, "max": 64.2, "count": 4, "min": 44.8},
                    "hrv": {"avg": 31.0, "max": 38.0, "count": 4, "min": 24.0},
                    "heart_rate_bpm": {"avg": 72.0, "max": 81.0, "count": 4, "min": 66.0},
                },
                "safety": {
                    "level_stats": {"avg": 0.75, "max": 1.0, "count": 4, "min": 0.0},
                    "highest_level": 1,
                    "highest_alert": "monitor",
                    "highest_concerns": ["stress"],
                },
            },
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

    def create_meeting(self, meeting_type: str = "human", title: str = "Crisis Test Meeting") -> str:
        db = get_db(self.app.config)
        start_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        end_at = start_at + timedelta(minutes=30)
        join_start, join_end = build_join_window(start_at, end_at)
        access_link_id = create_client_access_link(
            db,
            client_id=self.client_id,
            created_by=self.consultant_id,
            token_hash=hash_access_token(f"meeting-token-{meeting_type}-{int(time.time())}"),
            expires_at=iso_utc(end_at + timedelta(days=7)),
        )
        meeting_id = create_scheduled_meeting(
            db,
            client_id=self.client_id,
            consultant_id=self.consultant_id,
            meeting_type=meeting_type,
            repeat_weekly=False,
            title=title,
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
        return meeting_id
