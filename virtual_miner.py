"""Virtual ASIC demonstration for OMNIA. This is a simulation, never real Bitcoin mining."""
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from flask import jsonify

_lock = Lock()
_state = {"running": False, "started_mono": None, "cycles": 0}
VIRTUAL_HASHRATE_TH = 100.0  # illustrative display value; no SHA-256 work is performed


def _snapshot():
    running = _state["running"]
    elapsed = max(0, int(monotonic() - _state["started_mono"])) if running and _state["started_mono"] else 0
    return {
        "mode": "virtual_simulation",
        "hardware_type": "OMNIA Virtual ASIC (Demo)",
        "running": running,
        "status": "simulating" if running else "stopped",
        "simulated_hashrate_th_s": VIRTUAL_HASHRATE_TH if running else 0,
        "uptime_seconds": elapsed,
        "simulated_cycles": _state["cycles"] + (elapsed * 1000 if running else 0),
        "real_hardware_connected": False,
        "real_hashes_computed": False,
        "real_pool_shares": 0,
        "real_mining_rewards_sats": 0,
        "bitcoin_payout_eligible": False,
        "electricity_consumption_w": 0,
        "disclaimer": "Reine Visual-/Lernsimulation. Keine SHA-256-Mining-Arbeit, keine Pool-Shares, keine echten Bitcoin-Rewards oder Auszahlungen."
    }


def register_virtual_miner(app):
    @app.get("/api/mining/virtual/status")
    def virtual_miner_status():
        with _lock:
            return jsonify(_snapshot()), 200

    @app.post("/api/mining/virtual/start")
    def virtual_miner_start():
        with _lock:
            if not _state["running"]:
                _state["running"] = True
                _state["started_mono"] = monotonic()
            return jsonify(_snapshot()), 200

    @app.post("/api/mining/virtual/stop")
    def virtual_miner_stop():
        with _lock:
            if _state["running"] and _state["started_mono"]:
                _state["cycles"] += max(0, int(monotonic() - _state["started_mono"])) * 1000
            _state["running"] = False
            _state["started_mono"] = None
            return jsonify(_snapshot()), 200
