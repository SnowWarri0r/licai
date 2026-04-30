"""Claude API client.

Auth resolution order:
1. ANTHROPIC_API_KEY env (if set)
2. macOS Keychain entry "Claude Code-credentials" (写入by Claude Code CLI 登录后)
3. Claude CLI binary fallback (当 Keychain token 过期时，委托 CLI 调用)
"""
from __future__ import annotations
import os
import json
import subprocess
import uuid
import shutil

import requests

API_URL = "https://api.anthropic.com/v1/messages?beta=true"

# Claude API is overseas — optionally use a local proxy.
# Priority: env var LLM_PROXY > DB config (set via Settings UI) > direct connection.
_PROXY_URL: str = os.environ.get("LLM_PROXY", "")
_llm_session = requests.Session()
_llm_session.trust_env = False

# Direct-connection session used as fallback when proxy is unreachable
_direct_session = requests.Session()
_direct_session.trust_env = False


def configure_proxy(url: str):
    """Set (or clear) the proxy for LLM API calls at runtime.
    Called at startup from DB config and when user saves in Settings UI.
    env var LLM_PROXY always takes precedence.
    """
    global _PROXY_URL
    if os.environ.get("LLM_PROXY"):
        return  # env var wins
    _PROXY_URL = url.strip() if url else ""
    if _PROXY_URL:
        _llm_session.proxies = {"http": _PROXY_URL, "https": _PROXY_URL}
    else:
        _llm_session.proxies = {}


def get_proxy() -> str:
    """Return the currently active proxy URL (empty = direct)."""
    return os.environ.get("LLM_PROXY") or _PROXY_URL


# Apply env var proxy immediately if set
if _PROXY_URL:
    _llm_session.proxies = {"http": _PROXY_URL, "https": _PROXY_URL}

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


def _find_claude_cli() -> tuple[str | None, str | None]:
    """Find claude CLI binary and associated node binary.

    Returns (node_path, claude_path) or (None, None) if not found.
    """
    # Common nvm/brew/system locations
    candidates = []
    home = os.path.expanduser("~")
    nvm_dir = os.path.join(home, ".nvm", "versions", "node")
    if os.path.isdir(nvm_dir):
        for ver in sorted(os.listdir(nvm_dir), reverse=True):
            bin_dir = os.path.join(nvm_dir, ver, "bin")
            node = os.path.join(bin_dir, "node")
            claude = os.path.join(bin_dir, "claude")
            if os.path.isfile(node) and os.path.isfile(claude):
                candidates.append((node, claude))
    # System PATH
    system_claude = shutil.which("claude")
    system_node = shutil.which("node")
    if system_claude and system_node:
        candidates.append((system_node, system_claude))
    return candidates[0] if candidates else (None, None)


def _call_via_cli(user_prompt: str, system: str | None, model: str, max_tokens: int) -> str:
    """Fallback: call Claude via the local CLI binary, which refreshes OAuth internally."""
    node_path, claude_path = _find_claude_cli()
    if not node_path or not claude_path:
        raise RuntimeError("Claude CLI binary not found; cannot use CLI fallback.")

    cmd = [node_path, claude_path, "--print", "--model", model]
    if system:
        cmd += ["--append-system-prompt", system]
    cmd.append(user_prompt)

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
        env={**os.environ, "HOME": os.path.expanduser("~")},
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error (rc={result.returncode}): {result.stderr[:300]}")
    return result.stdout.strip()


def _post_with_fallback(headers, payload) -> requests.Response:
    """POST to API, falling back to direct if proxy errors."""
    try:
        return _llm_session.post(API_URL, headers=headers, json=payload, timeout=60)
    except requests.exceptions.ProxyError:
        return _direct_session.post(API_URL, headers=headers, json=payload, timeout=60)


def call_claude(
    user_prompt: str,
    system: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 2048,
) -> str:
    """Call Claude API. Returns the text response.

    Auth fallback chain:
    1. ANTHROPIC_API_KEY / Keychain OAuth token → direct API call
    2. On 401 (token expired): flush cache, retry Keychain once
    3. On persistent 401: fall back to claude CLI binary (handles own token refresh)
    """
    global _cached_token

    # --- Try direct API path ---
    try:
        token, is_oauth = _resolve_token()
        headers, payload = _build_request(token, is_oauth, user_prompt, system, model, max_tokens)
        resp = _post_with_fallback(headers, payload)

        if resp.status_code == 401 and is_oauth:
            # Flush cache and try once more from Keychain
            _cached_token = None
            try:
                new_token, new_is_oauth = _resolve_token()
                if new_token != token:
                    headers, payload = _build_request(new_token, new_is_oauth, user_prompt, system, model, max_tokens)
                    resp = _post_with_fallback(headers, payload)
            except Exception:
                pass

        if resp.status_code == 401:
            raise _AuthExpiredError("401")
        if not resp.ok:
            raise RuntimeError(f"Claude API error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        parts = data.get("content", [])
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text")

    except _AuthExpiredError:
        pass  # fall through to CLI fallback

    # --- CLI fallback: let the claude binary handle auth refresh ---
    try:
        print("[llm_client] Keychain token expired, falling back to claude CLI...")
        return _call_via_cli(user_prompt, system, model, max_tokens)
    except Exception as cli_err:
        raise RuntimeError(
            f"Claude API 401 且 CLI fallback 也失败: {cli_err}\n"
            "解决：(1) 跑 `claude setup-token` 重新登录；(2) 或者设置 ANTHROPIC_API_KEY。"
        )


class _AuthExpiredError(Exception):
    pass
