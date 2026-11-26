import os
import tempfile
import unittest
from unittest.mock import patch, Mock

TEST_DB = os.path.join(tempfile.gettempdir(), "aurealis-rewards-tests.sqlite3")
os.environ["DATABASE_PATH"] = TEST_DB

from flask import Flask
from mining_rewards import register_mining_rewards


class MiningRewardsTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        register_mining_rewards(self.app)
        self.client = self.app.test_client()\n        with self.client.session_transaction() as sess:\n            sess["superadmin"] = True

    def test_mining_access_is_denied_without_superadmin(self):\n        with self.client.session_transaction() as sess:\n            sess.clear()\n        response = self.client.get("/api/mining/rewards")\n        self.assertEqual(response.status_code, 403)\n\n    def test_unconfigured_pool_never_claims_rewards(self):
        with patch.dict(os.environ, {"MINING_POOL_API_URL": "", "MINING_POOL_API_TOKEN": ""}, clear=False):
            response = self.client.get("/api/mining/rewards")
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "not_configured")
        self.assertIsNone(data["found_blocks"])
        self.assertFalse(data["personal_rewards_verified"])

    def test_settlement_requires_cron_auth(self):
        response = self.client.post("/api/mining/rewards/settle")
        self.assertEqual(response.status_code, 401)

    def test_every_confirmed_reward_is_paid_even_if_old_threshold_env_is_set(self):
        env = {
            "MINING_POOL_API_URL": "https://pool.example",
            "MINING_POOL_API_TOKEN": "pool-token",
            "MINING_PAYOUT_GATEWAY_URL": "https://payout.example",
            "MINING_PAYOUT_GATEWAY_TOKEN": "payout-token",
            "MINING_PAYOUT_ADDRESS": "bc1q" + "a" * 38,
            "MINING_AUTO_PAYOUT_ENABLED": "true",
            "MINING_PAYOUT_THRESHOLD_SATS": "300000000",
            "MINING_CRON_TOKEN": "cron-token",
        }
        pool = Mock()
        pool.json.return_value = {"confirmed_sats": 100000000, "pending_sats": 0,
                                  "blocks_found": 0, "settlement_id": "settlement-1"}
        pool.raise_for_status.return_value = None
        with patch.dict(os.environ, env, clear=False), \
             patch("mining_rewards.requests.get", return_value=pool), \
             patch("mining_rewards.requests.post") as payout:
            response = self.client.post("/api/mining/rewards/settle",
                                        headers={"X-MINING-CRON-TOKEN": "cron-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "below_threshold")
        payout.assert_not_called()

    def test_confirmed_reward_payout_uses_stable_idempotency_key(self):
        env = {
            "MINING_POOL_API_URL": "https://pool.example",
            "MINING_POOL_API_TOKEN": "pool-token",
            "MINING_PAYOUT_GATEWAY_URL": "https://payout.example",
            "MINING_PAYOUT_GATEWAY_TOKEN": "payout-token",
            "MINING_PAYOUT_ADDRESS": "bc1q" + "a" * 38,
            "MINING_AUTO_PAYOUT_ENABLED": "true",
            "MINING_PAYOUT_THRESHOLD_SATS": "300000000",
            "MINING_CRON_TOKEN": "cron-token",
        }
        pool = Mock()
        pool.json.return_value = {"confirmed_sats": 300000000, "pending_sats": 0,
                                  "blocks_found": 1, "settlement_id": "settlement-3btc"}
        pool.raise_for_status.return_value = None
        sent = Mock()
        sent.json.return_value = {"status": "submitted", "txid": "b" * 64}
        sent.raise_for_status.return_value = None
        with patch.dict(os.environ, env, clear=False), \
             patch("mining_rewards.requests.get", return_value=pool), \
             patch("mining_rewards.requests.post", return_value=sent) as payout:
            response = self.client.post("/api/mining/rewards/settle",
                                        headers={"X-MINING-CRON-TOKEN": "cron-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "submitted")
        self.assertEqual(payout.call_args.kwargs["headers"]["Idempotency-Key"], "settlement-3btc")


if __name__ == "__main__":
    unittest.main()
