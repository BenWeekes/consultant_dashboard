"""Optional live stack smoke tests.

These checks hit the locally running services used by the therapy demo stack.
They are opt-in so the normal unit suite stays hermetic.
"""

import json
import os
import unittest
import urllib.error
import urllib.parse
import urllib.request


def _get(url: str, timeout: int = 5):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return resp.status, body


@unittest.skipUnless(
    os.environ.get("RUN_LIVE_STACK_TESTS", "").lower() in {"1", "true", "yes"},
    "set RUN_LIVE_STACK_TESTS=1 to run live stack smoke tests",
)
class LiveStackSmokeTest(unittest.TestCase):
    backend_base = os.environ.get("LIVE_BACKEND_URL", "http://127.0.0.1:8082")
    client_url = os.environ.get(
        "LIVE_CLIENT_URL",
        "http://localhost:8084?profile=therapy&autoconnect=true",
    )
    dashboard_base = os.environ.get("LIVE_DASHBOARD_URL", "http://127.0.0.1:8090")
    custom_llm_base = os.environ.get("LIVE_CUSTOM_LLM_URL", "http://127.0.0.1:8101")
    tunnel_ping_url = os.environ.get(
        "LIVE_TUNNEL_PING_URL",
        "https://artwork-davidson-unable-informative.trycloudflare.com/ping",
    )

    def test_backend_health(self):
        status, body = _get(f"{self.backend_base}/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data.get("status"), "ok")

    def test_auth_check_redirect_contract(self):
        auth_check_url = (
            f"{self.backend_base}/auth-check?profile=therapy&return_url="
            + urllib.parse.quote(self.client_url, safe="")
        )
        status, body = _get(auth_check_url)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data.get("auth_required"))
        self.assertIn("auth_url", data)

    def test_dashboard_health(self):
        status, body = _get(f"{self.dashboard_base}/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data.get("status"), "ok")

    def test_custom_llm_ping(self):
        status, body = _get(f"{self.custom_llm_base}/ping")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data.get("message"), "pong")

    def test_tunnel_ping(self):
        try:
            status, body = _get(self.tunnel_ping_url)
        except urllib.error.URLError as exc:
            self.fail(f"Tunnel ping failed: {exc}")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data.get("message"), "pong")


if __name__ == "__main__":
    unittest.main()
