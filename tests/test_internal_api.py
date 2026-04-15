import json
import time

from consultant_dashboard.core.db import get_db

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
        query_string = f"client_id={self.client_id}"
        response = self.client.get(
            f"/internal/client-context?{query_string}",
            headers=self.internal_headers("GET", "/internal/client-context", query_string),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["display_name"], "Alex Demo")
        self.assertEqual(response.json["consultant_id"], self.consultant_id)
        self.assertEqual(response.json["consultant_name"], "Test Consultant")
        self.assertEqual(response.json["notes"], "Generalized notes only.")
        self.assertEqual(response.json["direction"], "Check stress and routines.")
        self.assertIsNotNone(response.json["latest_summary"])
        self.assertEqual(response.json["latest_summary"]["full_summary"], "Generalized summary.")
        self.assertEqual(len(response.json["recent_summaries"]), 1)
        self.assertIsNotNone(response.json["baseline"])
        self.assertEqual(response.json["baseline"]["window_sessions"], 1)
        self.assertEqual(len(response.json["alerts"]), 2)

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
        response = self.ingest_session(session_id="sess_internal_002", urgent_escalation=True)
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
