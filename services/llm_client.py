"""Claude API client.

Auth resolution order:
1. ANTHROPIC_API_KEY env (if set)
2. macOS Keychain entry "Claude Code-credentials" (写入by Claude Code CLI 登录后)
"""
from __future__ import annotations
import os
import json
import subprocess
import uuid

import requests

API_URL = "https://api.anthropic.com/v1/messages?beta=true"

# Claude API is overseas — needs proxy. Create a dedicated session that uses proxy
# even though the main app clears proxy env vars for domestic APIs.
_PROXY_URL = "http://127.0.0.1:7897"
_llm_session = requests.Session()
_llm_session.proxies = {"http": _PROXY_URL, "https": _PROXY_URL}
_llm_session.trust_env = False  # use our explicit proxy, not env

# Required: when using OAuth token, system prompt must begin with this identity
CLAUDE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

CLAUDE_CODE_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
    "anthropic-dangerous-direct-browser-access": "true",
    "User-Agent": "claude-cli/2.1.97 (external, cli)",
    "x-app": "cli",
    "X-Stainless-Lang": "js",
    "X-Stainless-Package-Version": "0.81.0",
    "X-Stainless-Runtime": "node",
}

_cached_token: str | None = None


def _resolve_token() -> tuple[str, bool]:
    """Returns (token, is_oauth)."""
    global _cached_token

    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key and "your-key" not in env_key:
        return env_key, "sk-ant-oat" in env_key

    if _cached_token:
        return _cached_token, True

    # macOS Keychain (Claude Code CLI 登录后自动写入)
    if os.uname().sysname == "Darwin":
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout.strip())
                token = data.get("claudeAiOauth", {}).get("accessToken", "")
                if token:
                    _cached_token = token
                    return token, True
        except Exception:
            pass

    raise RuntimeError("无法获取 Claude API 凭证。请运行 `claude setup-token` 或设置 ANTHROPIC_API_KEY。")


def _build_request(token: str, is_oauth: bool, user_prompt: str,
                   system: str | None, model: str, max_tokens: int):
    if is_oauth:
        headers = {**CLAUDE_CODE_HEADERS, "Authorization": f"Bearer {token}"}
        headers["X-Claude-Code-Session-Id"] = str(uuid.uuid4())
        system_blocks = [{"type": "text", "text": CLAUDE_IDENTITY}]
        if system:
            system_blocks.append({"type": "text", "text": system})
    else:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": token,
        }
        system_blocks = system
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_prompt}],
        "system": system_blocks,
    }
    return headers, payload


def call_claude(
    user_prompt: str,
    system: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 2048,
) -> str:
    """Call Claude API. Returns the text response.

    On 401 (OAuth token expired), invalidate the in-process cache and re-resolve
    once — the macOS Keychain may already hold a refreshed token from Claude Code.
    """
    global _cached_token

    token, is_oauth = _resolve_token()
    headers, payload = _build_request(token, is_oauth, user_prompt, system, model, max_tokens)
    resp = _llm_session.post(API_URL, headers=headers, json=payload, timeout=60)

    if resp.status_code == 401 and is_oauth:
        # Cached token may be stale; flush + re-resolve from keychain/profile
        _cached_token = None
        try:
            new_token, new_is_oauth = _resolve_token()
        except Exception:
            raise RuntimeError(
                "Claude API 401: OAuth token expired and re-resolution failed. "
                "Run `claude setup-token` 或重启 Claude Code 让 Keychain 刷新。"
            )
        if new_token != token:
            headers, payload = _build_request(new_token, new_is_oauth, user_prompt, system, model, max_tokens)
            resp = _llm_session.post(API_URL, headers=headers, json=payload, timeout=60)

    if not resp.ok:
        if resp.status_code == 401:
            raise RuntimeError(
                "Claude API 401 鉴权失败。Keychain 里的 OAuth token 已过期且没自动刷新出来。"
                " 解决：(1) 跑 `claude setup-token` 重新登录；(2) 或者设置 ANTHROPIC_API_KEY 走 API key 模式。"
            )
        raise RuntimeError(f"Claude API error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    parts = data.get("content", [])
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
