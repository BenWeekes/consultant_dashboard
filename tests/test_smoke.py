import json
from tests.support import ConsultantDashboardTestCase


class ConsultantDashboardSmokeTest(ConsultantDashboardTestCase):

    def test_health_login_and_ingestion(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")

        self.consultant_login()

        query_string = f"email_hash={self.sha256('alex@example.com')}"
        response = self.client.get(
            f"/internal/resolve-client?{query_string}",
            headers=self.internal_headers("GET", "/internal/resolve-client", query_string),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["client_id"], self.client_id)

        body = json.dumps(
            {
                "client_id": self.client_id,
                "session_id": "sess_smoke_001",
                "profile": "therapy",
                "channel": "smoke-channel",
                "started_at": "2026-04-13T18:00:00Z",
                "ended_at": "2026-04-13T18:05:00Z",
                "duration_seconds": 300,
                "status": "completed",
                "summary": {"overview": "Generalized summary."},
                "biomarkers": {"averages": {"stress_index": 52.5, "hrv": 31.0}},
                "alerts": [{"severity": "warning", "source": "thymia", "title": "Elevated stress"}],
            },
            separators=(",", ":"),
        )
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
        self.assertEqual(response.json["display_name"], "Alex Demo")
        self.assertIn("latest_summary", response.json)
        self.assertIn("baseline", response.json)
        self.assertEqual(len(response.json["alerts"]), 1)


if __name__ == "__main__":
    unittest.main()
