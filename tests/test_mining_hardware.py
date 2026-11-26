import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.gettempdir(), "omnia-mining-gateway-tests.sqlite3"))
from app import app


class MiningHardwareStatusTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["superadmin"] = True

    def test_unconfigured_gateway_is_reported_honestly(self):
        with patch.dict(os.environ, {"MINING_GATEWAY_URL": "", "MINING_GATEWAY_TOKEN": ""}):
            response = self.client.get("/api/mining/hardware/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "not_configured")
        self.assertFalse(response.get_json()["connected"])

    def test_plain_http_gateway_is_rejected(self):
        with patch.dict(os.environ, {"MINING_GATEWAY_URL": "http://localhost:8080", "MINING_GATEWAY_TOKEN": "test"}):
            response = self.client.get("/api/mining/hardware/status")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "invalid_configuration")

    def test_gateway_requires_a_token(self):
        with patch.dict(os.environ, {"MINING_GATEWAY_URL": "https://miner.example", "MINING_GATEWAY_TOKEN": ""}):
            response = self.client.get("/api/mining/hardware/status")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "missing_gateway_token")


if __name__ == "__main__":
    unittest.main()
