"""Verified mining-pool rewards and threshold payout orchestration.

The app never signs Bitcoin transactions or stores wallet private keys. Pool and payout
adapters are separately operated HTTPS services configured through Render environment.
"""
import hmac
import os
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from flask import jsonify, request, session

BTC_ADDRESS_RE = re.compile(r"^(bc1[ac-hj-np-z02-9]{39,59}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$")


def _https_base(value):
    parsed = urlparse((value or "").strip())
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def _db_path():
    return os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "omnia.sqlite3"))


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS mining_payouts (
        settlement_id TEXT PRIMARY KEY,
        amount_sats INTEGER NOT NULL,
        address TEXT NOT NULL,
        status TEXT NOT NULL,
        txid TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.commit()
    return conn


def _config():
    pool_url = os.getenv("MINING_POOL_API_URL", "").strip().rstrip("/")
    pool_token = os.getenv("MINING_POOL_API_TOKEN", "").strip()
    payout_url = os.getenv("MINING_PAYOUT_GATEWAY_URL", "").strip().rstrip("/")
    payout_token = os.getenv("MINING_PAYOUT_GATEWAY_TOKEN", "").strip()
    address = os.getenv("MINING_PAYOUT_ADDRESS", "").strip()
    # User policy: pay every newly confirmed reward batch; no 3 BTC threshold.
    threshold = 1
    return {
        "pool_configured": _https_base(pool_url) and bool(pool_token),
        "payout_gateway_configured": _https_base(payout_url) and bool(payout_token),
        "payout_address_configured": bool(BTC_ADDRESS_RE.fullmatch(address)),
        "auto_payout_enabled": os.getenv("MINING_AUTO_PAYOUT_ENABLED", "false").lower() == "true",
        "threshold_sats": max(1, threshold),
        "pool_url": pool_url,
        "pool_token": pool_token,
        "payout_url": payout_url,
        "payout_token": payout_token,
        "address": address,
    }


def _pool_rewards(cfg):
    response = requests.get(
        cfg["pool_url"] + "/api/rewards",
        headers={"Authorization": "Bearer " + cfg["pool_token"], "Accept": "application/json"},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("invalid_pool_response")
    confirmed = data.get("confirmed_sats")
    pending = data.get("pending_sats", 0)
    blocks = data.get("blocks_found", 0)
    settlement_id = data.get("settlement_id")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (confirmed, pending, blocks)):
        raise ValueError("invalid_pool_reward_fields")
    return {
        "confirmed_sats": confirmed,
        "pending_sats": pending,
        "blocks_found": blocks,
        "settlement_id": str(settlement_id)[:160] if settlement_id else None,
        "blocks": data.get("blocks", []) if isinstance(data.get("blocks", []), list) else [],
        "source": "configured_mining_pool",
    }


def register_mining_rewards(app):
    @app.get("/api/mining/rewards")
    def mining_rewards_status():
        cfg = _config()
        public_config = {k: cfg[k] for k in (
            "pool_configured", "payout_gateway_configured", "payout_address_configured",
            "auto_payout_enabled", "threshold_sats"
        )}
        if not cfg["pool_configured"]:
            return jsonify(status="not_configured", personal_rewards_verified=False,
                           found_blocks=None, confirmed_sats=None, pending_sats=None,
                           configuration=public_config,
                           required_environment=["MINING_POOL_API_URL", "MINING_POOL_API_TOKEN"]), 200
        try:
            rewards = _pool_rewards(cfg)
            ready = (cfg["payout_gateway_configured"] and cfg["payout_address_configured"]
                     and cfg["auto_payout_enabled"])
            rewards.update(status="connected", personal_rewards_verified=True,
                           payout_threshold_reached=rewards["confirmed_sats"] >= cfg["threshold_sats"],
                           payout_policy="every_confirmed_reward_batch",
                           payout_ready=ready, configuration=public_config)
            return jsonify(rewards), 200
        except requests.RequestException:
            return jsonify(status="pool_unreachable", personal_rewards_verified=False,
                           error="Mining-Pool-API derzeit nicht erreichbar.", configuration=public_config), 502
        except (ValueError, TypeError):
            return jsonify(status="invalid_pool_response", personal_rewards_verified=False,
                           error="Mining-Pool lieferte kein gültiges Reward-Format.", configuration=public_config), 502

    @app.post("/api/mining/rewards/settle")
    def mining_rewards_settle():
        cfg = _config()
        expected = os.getenv("MINING_CRON_TOKEN", "").strip()
        supplied = request.headers.get("X-MINING-CRON-TOKEN", "").strip()
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return jsonify(error="cron_authentication_required"), 401
        if not cfg["auto_payout_enabled"]:
            return jsonify(status="disabled", error="Automatische Auszahlung ist nicht freigegeben."), 409
        if not (cfg["pool_configured"] and cfg["payout_gateway_configured"] and cfg["payout_address_configured"]):
            return jsonify(status="not_configured", error="Pool, Auszahlungsgateway und Ziel-Wallet müssen konfiguriert sein."), 409
        try:
            rewards = _pool_rewards(cfg)
        except requests.RequestException:
            return jsonify(status="pool_unreachable"), 502
        except (ValueError, TypeError):
            return jsonify(status="invalid_pool_response"), 502
        amount = rewards["confirmed_sats"]
        if amount < 1:
            return jsonify(status="below_threshold", confirmed_sats=amount,
                           threshold_sats=cfg["threshold_sats"]), 200
        settlement_id = rewards.get("settlement_id")
        if not settlement_id:
            return jsonify(status="missing_settlement_id",
                           error="Pool muss eine stabile settlement_id für idempotente Auszahlungen liefern."), 502
        now = datetime.now(timezone.utc).isoformat()
        conn = _connect()
        try:
            existing = conn.execute("SELECT * FROM mining_payouts WHERE settlement_id=?", (settlement_id,)).fetchone()
            if existing and existing["status"] == "submitted":
                return jsonify(status="already_submitted", settlement_id=settlement_id,
                               txid=existing["txid"], amount_sats=existing["amount_sats"]), 200
            conn.execute("""INSERT INTO mining_payouts(settlement_id,amount_sats,address,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(settlement_id) DO UPDATE SET
                amount_sats=excluded.amount_sats,address=excluded.address,status='processing',updated_at=excluded.updated_at""",
                (settlement_id, amount, cfg["address"], "processing", now, now))
            conn.commit()
        finally:
            conn.close()
        try:
            response = requests.post(
                cfg["payout_url"] + "/api/payouts",
                json={"settlement_id": settlement_id, "amount_sats": amount, "address": cfg["address"]},
                headers={"Authorization": "Bearer " + cfg["payout_token"],
                         "Idempotency-Key": settlement_id, "Content-Type": "application/json"},
                timeout=20,
            )
            response.raise_for_status()
            result = response.json()
            txid = result.get("txid") if isinstance(result, dict) else None
            status = result.get("status") if isinstance(result, dict) else None
            if status not in ("submitted", "broadcast", "confirmed") or not isinstance(txid, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", txid):
                raise ValueError("payout_gateway_did_not_return_verified_txid")
            conn = _connect()
            try:
                conn.execute("UPDATE mining_payouts SET status='submitted',txid=?,updated_at=? WHERE settlement_id=?",
                             (txid, datetime.now(timezone.utc).isoformat(), settlement_id))
                conn.commit()
            finally:
                conn.close()
            return jsonify(status="submitted", settlement_id=settlement_id, amount_sats=amount, txid=txid), 200
        except (requests.RequestException, ValueError):
            conn = _connect()
            try:
                conn.execute("UPDATE mining_payouts SET status='retryable_error',updated_at=? WHERE settlement_id=?",
                             (datetime.now(timezone.utc).isoformat(), settlement_id))
                conn.commit()
            finally:
                conn.close()
            return jsonify(status="payout_failed_or_unverified",
                           error="Auszahlung nicht als erfolgreich bestätigt. Derselbe Idempotency-Key wird bei Wiederholung verwendet.",
                           settlement_id=settlement_id), 502
