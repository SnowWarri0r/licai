"""OKX API v5 client (read-only for trading bot + account).

Credentials stored in macOS Keychain via `security` CLI. Service name: `okx-trading-api`.
Format: JSON {api_key, secret_key, passphrase}.
"""
from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

import requests as _requests


OKX_BASE = "https://www.okx.com"
KEYCHAIN_SERVICE = "okx-trading-api"
KEYCHAIN_ACCOUNT = "default"

# Session with CN-compatible proxy (same config as crypto quote fetcher)
_CRYPTO_PROXY = os.environ.get("CRYPTO_PROXY", "http://127.0.0.1:7897")
_okx_session = _requests.Session()
_okx_session.trust_env = False
_okx_session.proxies = {"http": _CRYPTO_PROXY, "https": _CRYPTO_PROXY}


# --- Credential storage (macOS Keychain, explicit login.keychain-db path) ---

def _login_keychain() -> str | None:
    """Absolute path to the user's login.keychain-db."""
    home = os.path.expanduser("~")
    p = os.path.join(home, "Library/Keychains/login.keychain-db")
    return p if os.path.exists(p) else None


def save_credentials(api_key: str, secret_key: str, passphrase: str) -> tuple[bool, str]:
    """Returns (ok, error_detail)."""
    blob = json.dumps({"api_key": api_key, "secret_key": secret_key, "passphrase": passphrase})
    kc = _login_keychain()
    args = ["security", "add-generic-password",
            "-s", KEYCHAIN_SERVICE,
            "-a", KEYCHAIN_ACCOUNT,
            "-w", blob,
            "-U"]
    if kc:
        args.append(kc)
    try:
        # Delete any existing entry first
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT]
            + ([kc] if kc else []),
            capture_output=True, timeout=5,
        )
        r = subprocess.run(args, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or f"rc={r.returncode}").strip()
        # Immediately verify the item is readable
        verify = load_credentials()
        if not verify:
            return False, "add 成功但立即读取不到（Keychain 被锁？）"
        return True, ""
    except Exception as e:
        return False, str(e)


def load_credentials() -> Optional[dict]:
    """Return {api_key, secret_key, passphrase} or None."""
    kc = _login_keychain()
    args = ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"]
    if kc:
        args.append(kc)
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout.strip())
    except Exception:
        return None


def clear_credentials() -> bool:
    kc = _login_keychain()
    args = ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT]
    if kc:
        args.append(kc)
    try:
        r = subprocess.run(args, capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def has_credentials() -> bool:
    return load_credentials() is not None


# --- Request signing ---

def _sign(secret: str, timestamp: str, method: str, request_path: str, body: str = "") -> str:
    message = f"{timestamp}{method}{request_path}{body}"
    mac = hmac.new(secret.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _timestamp() -> str:
    # OKX wants ISO with ms precision in UTC
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _authed_get(path: str, params: dict | None = None) -> dict | None:
    """Execute a signed GET request. `path` should start with /api/v5/..."""
    creds = load_credentials()
    if not creds:
        return {"error": "credentials not configured"}

    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        request_path = f"{path}?{qs}"
    else:
        request_path = path

    ts = _timestamp()
    sig = _sign(creds["secret_key"], ts, "GET", request_path, "")
    headers = {
        "OK-ACCESS-KEY": creds["api_key"],
        "OK-ACCESS-SIGN": sig,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": creds["passphrase"],
        "Content-Type": "application/json",
    }

    try:
        r = _okx_session.get(OKX_BASE + request_path, headers=headers, timeout=15)
        data = r.json()
        if data.get("code") != "0":
            return {"error": f"OKX {data.get('code')}: {data.get('msg', 'unknown')}"}
        return data
    except Exception as e:
        return {"error": f"request failed: {e}"}


# --- High-level APIs ---

GRID_TYPES = ["grid", "contract_grid"]  # moon_grid 不是合法 algoOrdType


async def list_bots() -> dict:
    """Fetch all grid-family trading bots (pending + history)."""
    results = []
    for algo_ord_type in GRID_TYPES:
        r = await asyncio.to_thread(
            _authed_get, "/api/v5/tradingBot/grid/orders-algo-pending",
            {"algoOrdType": algo_ord_type},
        )
        if r and not r.get("error"):
            for b in r.get("data", []):
                results.append(_normalize_bot(b, active=True))

    for algo_ord_type in GRID_TYPES:
        r = await asyncio.to_thread(
            _authed_get, "/api/v5/tradingBot/grid/orders-algo-history",
            {"algoOrdType": algo_ord_type, "limit": "50"},
        )
        if r and not r.get("error"):
            for b in r.get("data", []):
                results.append(_normalize_bot(b, active=False))

    return {"bots": results, "count": len(results)}


async def list_signal_bots() -> dict:
    """Signal bot listing — OKX's /signal/orders-algo-details requires a specific
    algoId, there's no bulk-list endpoint in the public v5 API. Skipped."""
    return {"bots": [], "count": 0}


def _normalize_signal(raw: dict, active: bool) -> dict:
    def f(key, default=0.0):
        try: return float(raw.get(key) or 0)
        except: return default
    investment = f("investAmt") or f("totalInvestAmt") or f("investment")
    pnl = f("totalPnl") or f("pnl")
    return {
        "algo_id": raw.get("algoId", ""),
        "inst_id": raw.get("instIds", "") or raw.get("instId", ""),
        "bot_type": "signal",
        "kind_label": "信号机器人",
        "state": raw.get("state", ""),
        "active": active,
        "investment_usdt": round(investment, 2),
        "total_pnl_usdt": round(pnl, 2),
        "current_value_usdt": round(investment + pnl, 2),
        "pnl_pct": round(pnl / investment * 100, 2) if investment > 0 else 0,
        "created_at_ms": int(raw.get("cTime", 0) or 0),
        "created_at": "",
    }


def _normalize_bot(raw: dict, active: bool) -> dict:
    """Map OKX raw bot fields to our simplified shape. All money in USDT."""
    def f(key, default=0.0):
        try: return float(raw.get(key) or 0)
        except: return default

    investment = f("investment")
    total_pnl = f("totalPnl")
    float_profit = f("floatProfit")  # 当前未平仓的浮动盈亏 — OKX UI 里的"浮动"
    current_val = investment + total_pnl

    bot_type = raw.get("algoOrdType", "")  # grid / contract_grid / moon_grid
    state = raw.get("state", "")           # running / stopped / ...
    c_time = raw.get("cTime", "")

    # Extended bot kind inference — OKX doesn't have a direct "martingale" flag in grid.
    # Spot grid = 网格, contract_grid = 合约网格, moon_grid = 趋势网格.
    # "马丁格尔" 实际是 DCA / recurring buy. OKX has `/api/v5/tradingBot/recurring/orders-algo-pending` for that.
    kind_label = {
        "grid": "现货网格",
        "contract_grid": "合约网格",
        "moon_grid": "趋势网格",
    }.get(bot_type, bot_type)

    return {
        "algo_id": raw.get("algoId", ""),
        "inst_id": raw.get("instId", ""),
        "bot_type": bot_type,
        "kind_label": kind_label,
        "state": state,
        "active": active,
        "investment_usdt": round(investment, 2),
        "total_pnl_usdt": round(total_pnl, 2),
        "float_profit_usdt": round(float_profit, 4),
        "current_value_usdt": round(current_val, 2),
        "pnl_pct": round(total_pnl / investment * 100, 2) if investment > 0 else 0,
        "created_at_ms": int(c_time) if c_time else 0,
        "created_at": datetime.fromtimestamp(int(c_time) / 1000).strftime("%Y-%m-%d %H:%M") if c_time else "",
    }


DCA_ORD_TYPES = ["spot_dca", "contract_dca"]


async def list_dca_bots() -> dict:
    """Fetch DCA / 马丁格尔 bots.

    Correct OKX paths (per docs 2026-04):
      GET /api/v5/tradingBot/dca/ongoing-list  (active, requires algoOrdType)
      GET /api/v5/tradingBot/dca/history-list  (finished)
    algoOrdType must be one of: spot_dca (现货马丁), contract_dca (合约马丁).
    Docs say 权限:读取, but OKX returns 50120 for some legacy keys — requires
    recreating the API key with current scopes.
    """
    results = []
    for ord_type in DCA_ORD_TYPES:
        r = await asyncio.to_thread(
            _authed_get, "/api/v5/tradingBot/dca/ongoing-list",
            {"algoOrdType": ord_type},
        )
        if r and not r.get("error"):
            for b in r.get("data", []):
                results.append(_normalize_dca(b, active=True))
    for ord_type in DCA_ORD_TYPES:
        r = await asyncio.to_thread(
            _authed_get, "/api/v5/tradingBot/dca/history-list",
            {"algoOrdType": ord_type, "limit": "50"},
        )
        if r and not r.get("error"):
            for b in r.get("data", []):
                results.append(_normalize_dca(b, active=False))
    return {"bots": results, "count": len(results)}


def _normalize_dca(raw: dict, active: bool) -> dict:
    def f(key, default=0.0):
        try: return float(raw.get(key) or 0)
        except: return default
    # Per OKX /dca/ongoing-list schema
    investment = f("investmentAmt") or f("totalInvestment") or f("investment")
    pnl = f("totalPnl")
    ord_type = raw.get("algoOrdType", "")
    label_map = {"spot_dca": "现货马丁", "contract_dca": "合约马丁"}
    kind = label_map.get(ord_type, "马丁格尔")
    return {
        "algo_id": raw.get("algoId", ""),
        "inst_id": raw.get("instId", ""),
        "bot_type": ord_type or "dca",
        "kind_label": kind,
        "state": raw.get("state", ""),
        "active": active,
        "investment_usdt": round(investment, 2),
        "total_pnl_usdt": round(pnl, 2),
        "current_value_usdt": round(investment + pnl, 2),
        "pnl_pct": round(f("pnlRatio") * 100, 2) if raw.get("pnlRatio") else (
            round(pnl / investment * 100, 2) if investment > 0 else 0
        ),
        "created_at_ms": int(raw.get("cTime", 0) or 0),
        "created_at": "",
    }


_bot_details_cache: dict[str, tuple[dict | None, float]] = {}
_BOT_DETAILS_TTL = 15  # seconds — enough freshness for crypto, avoids OKX rate limit


async def get_bot_details(algo_id: str, algo_ord_type: str = "grid") -> dict | None:
    """Fetch one specific bot's live status. Routes to grid or DCA endpoint. Cached 15s."""
    import time as _time
    key = f"{algo_ord_type}:{algo_id}"
    cached = _bot_details_cache.get(key)
    if cached and _time.time() - cached[1] < _BOT_DETAILS_TTL:
        return cached[0]
    result = await _fetch_bot_details_raw(algo_id, algo_ord_type)
    _bot_details_cache[key] = (result, _time.time())
    return result


async def _fetch_bot_details_raw(algo_id: str, algo_ord_type: str) -> dict | None:
    """Actual fetch without caching."""
    if algo_ord_type in ("spot_dca", "contract_dca"):
        # DCA / 马丁: refetch via ongoing-list filtered by algoId
        r = await asyncio.to_thread(
            _authed_get, "/api/v5/tradingBot/dca/ongoing-list",
            {"algoOrdType": algo_ord_type, "algoId": algo_id},
        )
        if not r or r.get("error") or not r.get("data"):
            return None
        raw = r["data"][0]
        return _normalize_dca(raw, active=raw.get("state") == "running")
    # Grid family
    r = await asyncio.to_thread(
        _authed_get, "/api/v5/tradingBot/grid/orders-algo-details",
        {"algoOrdType": algo_ord_type, "algoId": algo_id},
    )
    if not r or r.get("error") or not r.get("data"):
        return None
    raw = r["data"][0]
    return _normalize_bot(raw, active=raw.get("state") == "running")


async def test_credentials() -> dict:
    """Verify credentials by probing both /account/config and tradingBot list.

    The trading bot endpoint is what we actually use, so if only that works the
    creds are still useful. UID is nice-to-have.
    """
    result = {"ok": False, "uid": "", "account_level": "", "bot_count": 0, "errors": []}

    # 1) Try /account/config (may be permission-denied on bot-only keys)
    r = await asyncio.to_thread(_authed_get, "/api/v5/account/config")
    if r and not r.get("error"):
        data = (r.get("data") or [{}])[0]
        result["uid"] = data.get("uid", "") or data.get("mainUid", "")
        result["account_level"] = data.get("acctLv", "")
    elif r and r.get("error"):
        result["errors"].append(f"account/config: {r['error']}")

    # 2) Try the tradingBot list — this is what actually matters
    r2 = await asyncio.to_thread(
        _authed_get, "/api/v5/tradingBot/grid/orders-algo-pending", {"algoOrdType": "grid"}
    )
    if r2 and not r2.get("error"):
        result["ok"] = True
        result["bot_count"] = len(r2.get("data") or [])
    elif r2 and r2.get("error"):
        result["errors"].append(f"tradingBot: {r2['error']}")

    if not result["ok"] and not result["errors"]:
        result["errors"].append("OKX 未返回数据（可能网络问题）")
    return result
