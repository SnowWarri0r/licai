"""经 ChatGPT 订阅(本机 Codex CLI 的登录)调用模型。

请求按 Codex CLI 自己发的样子构造(参照开源项目 magpie 的做法): 发到 ChatGPT 后端的
/codex/responses, 只流式、不留存; 顶层 instructions 用 Codex 自己的系统提示(取自本机
Codex 的模型缓存), 调用方的系统提示作为第一条 developer 消息; 请求头带 Codex CLI 的
originator 与 User-Agent, 会话缓存按 session-id 头归并。

登录取自 ~/.codex/auth.json(或 CODEX_HOME)。访问令牌快过期时用刷新令牌换新, 换到的新令牌写回
同一个文件供 Codex 继续用: 刷新令牌是一次性的, 不写回 Codex 下次刷新会失败。写回前确认文件里
还是刷新前那份登录(期间 Codex 自己刷新过或换了账号就不覆盖), 用文件锁避免两边同时刷新。
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from services import llm_formats as _fmt

logger = logging.getLogger(__name__)

BASE = "https://chatgpt.com/backend-api/codex"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"   # Codex CLI 自己的 OAuth client
FALLBACK_VERSION = "0.169.0"
# 不在 ChatGPT 后端接受范围内的字段
_DROP = ("max_output_tokens", "max_completion_tokens", "temperature", "top_p", "previous_response_id",
         "user", "safety_identifier", "prompt_cache_options", "service_tier")

_lock = threading.Lock()


class CodexAuthError(RuntimeError):
    pass


def home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def available() -> bool:
    try:
        a = _read_auth()
    except Exception:
        return False
    return bool((a.get("tokens") or {}).get("access_token"))


# ── 登录与令牌 ───────────────────────────────────────────

def _read_auth() -> dict:
    with open(home() / "auth.json", encoding="utf-8") as f:
        return json.load(f)


def _claims(jwt: str) -> dict:
    try:
        part = jwt.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def _account_id(a: dict) -> str:
    t = a.get("tokens") or {}
    if t.get("account_id"):
        return t["account_id"]
    auth = _claims(t.get("id_token", "")).get("https://api.openai.com/auth") or {}
    return auth.get("chatgpt_account_id", "")


def _fresh(tok: str, margin: float = 300) -> bool:
    exp = _claims(tok).get("exp")
    return not exp or exp - time.time() > margin


def token(force: str = "") -> tuple[str, str]:
    """(访问令牌, 账号 id)。force 是刚被后端判过期的令牌: 文件里还是它就强制刷新。"""
    with _lock:
        a = _read_auth()
        if a.get("auth_mode") == "apikey" or not (a.get("tokens") or {}).get("access_token"):
            raise CodexAuthError("本机 Codex 没有用 ChatGPT 账号登录; 在终端里运行 codex login")
        tok = a["tokens"]["access_token"]
        if (not force or tok != force) and _fresh(tok):
            return tok, _account_id(a)
        lock_path = home() / "auth.json.licai.lock"
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            a = _read_auth()                     # 拿锁后重读: 可能刚被别人刷新过
            tok = a["tokens"]["access_token"]
            if (not force or tok != force) and _fresh(tok):
                return tok, _account_id(a)
            return _refresh(a), _account_id(a)


def _refresh(a: dict) -> str:
    import requests
    rt = (a.get("tokens") or {}).get("refresh_token")
    if not rt:
        raise CodexAuthError("Codex 登录里没有刷新令牌; 在终端里运行 codex login")
    r = requests.post(TOKEN_URL, json={"client_id": CLIENT_ID, "grant_type": "refresh_token",
                                       "refresh_token": rt, "scope": "openid profile email"},
                      headers=_client_headers(), timeout=30)
    if r.status_code in (400, 401):
        raise CodexAuthError(f"Codex 登录已失效({r.status_code}); 在终端里运行 codex login")
    r.raise_for_status()
    fresh = r.json()
    path = home() / "auth.json"
    now = _read_auth()
    if (now.get("tokens") or {}).get("refresh_token") != rt:
        logger.warning("Codex 登录在刷新期间被改动, 新令牌只用于本次请求, 不写回")
        return fresh["access_token"]
    for k in ("access_token", "refresh_token", "id_token"):
        if fresh.get(k):
            now["tokens"][k] = fresh[k]
    now["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    tmp = path.with_suffix(".json.licai-tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(now, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return fresh["access_token"]


# ── 请求构造 ─────────────────────────────────────────────

_version_cache: dict = {}


def version() -> str:
    if _version_cache.get("at", 0) > time.time() - 600:
        return _version_cache["v"]
    best = FALLBACK_VERSION

    def newer(v: str):
        nonlocal best
        m = re.search(r"\d+\.\d+\.\d+", v or "")
        if m and tuple(map(int, m.group(0).split("."))) > tuple(map(int, best.split("."))):
            best = m.group(0)
    try:
        newer(json.loads((home() / "models_cache.json").read_text()).get("client_version", ""))
    except Exception:
        pass
    exe = shutil.which("codex")
    if exe:
        try:
            newer(subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5).stdout)
        except Exception:
            pass
    _version_cache.update(v=best, at=time.time())
    return best


def _os_name() -> str:
    if platform.system() == "Darwin":
        v = platform.mac_ver()[0] or ""
        while v and v.count(".") < 2:
            v += ".0"
        return f"Mac OS {v}".strip()
    if platform.system() == "Linux":
        return "Linux " + platform.release().split("-")[0]
    return platform.system()


def _client_headers(ver: str | None = None) -> dict:
    ver = ver or version()
    arch = {"arm64": "arm64", "x86_64": "x86_64", "AMD64": "x86_64"}.get(platform.machine(), platform.machine())
    term = "Apple_Terminal/455" if platform.system() == "Darwin" else "xterm-256color"
    return {"originator": "codex_cli_rs", "User-Agent": f"codex_cli_rs/{ver} ({_os_name()}; {arch}) {term}"}


def instructions(model: str) -> str:
    """Codex 给这个模型用的系统提示(本机 Codex 的模型缓存里有)。"""
    try:
        data = json.loads((home() / "models_cache.json").read_text())
    except Exception:
        return ""
    for m in data.get("models") or []:
        if m.get("slug") == model:
            return ((m.get("model_messages") or {}).get("instructions_template")
                    or m.get("base_instructions") or "")
    return ""


def models() -> list[str]:
    try:
        data = json.loads((home() / "models_cache.json").read_text())
        return [m["slug"] for m in data.get("models") or [] if m.get("visibility") == "list"]
    except Exception:
        return []


def default_model() -> str:
    """Codex 配置里的默认模型; 没配就取模型列表第一个。"""
    try:
        m = re.search(r'^model\s*=\s*"([^"]+)"', (home() / "config.toml").read_text(), re.M)
        if m:
            return m.group(1)
    except Exception:
        pass
    ms = models()
    return ms[0] if ms else "gpt-6-astra"


def pick_model(model: str) -> str:
    """licai 里写死的 Claude 模型名在这里换成 Codex 能用的模型。"""
    known = models()
    if model in known or (model.startswith("gpt-") and not known):
        return model
    return default_model()


def _instructions_after_input(model: str) -> bool:
    m = re.match(r"^gpt-(\d+)(?:\.(\d+))?(?:$|-)", model.lower())
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2) or 0)
    return major > 6 or (major == 6 and minor >= 2)


def _developer(text: str) -> dict:
    return {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": text}]}


def build_body(messages: list, system, model: str, tools: list | None, effort: str | None) -> dict:
    body = _fmt.to_responses_request(messages, system, model, 0, tools, stream=True)
    for k in _DROP:
        body.pop(k, None)
    own = body.pop("instructions", "") or ""
    items = list(body.get("input") or [])
    # 会话标识: 由开头(系统提示 + 第一条用户消息)决定, 同一会话的每一轮都相同, 后端按它命中缓存
    first_user = next((json.dumps(i, ensure_ascii=False, sort_keys=True) for i in items
                       if i.get("role") == "user"), "")
    body["prompt_cache_key"] = hashlib.sha256((own + "\n" + first_user).encode()).hexdigest()[:32]
    codex = instructions(model)
    if own.strip():
        items.insert(0, _developer(own))
    if codex and _instructions_after_input(model):
        items.insert(0, _developer(codex))
    elif codex:
        body["instructions"] = codex
    body["input"] = items
    body["store"] = False
    body["stream"] = True
    body["tool_choice"] = "auto"
    body.setdefault("parallel_tool_calls", True)
    body.setdefault("text", {"verbosity": "medium"})
    body["reasoning"] = {"effort": effort or "medium", "summary": "auto"}
    body["include"] = ["reasoning.encrypted_content"]
    return body


class _Stream(_fmt.ResponsesStream):
    """completed 事件里 output 为空时(不留存的请求), 用逐条 output_item.done 拼回。"""

    def __init__(self, on_text):
        super().__init__(on_text or (lambda _t: None))
        self.items: list = []

    def feed(self, data: dict) -> None:
        if data.get("type") == "response.output_item.done" and data.get("item"):
            self.items.append(data["item"])
        super().feed(data)

    def result(self) -> dict:
        if self.final is not None and not self.final.get("output") and self.items:
            self.final = dict(self.final, output=self.items)
        return super().result()


def call(messages: list, system, model: str, tools: list | None = None, effort: str | None = None,
         on_text=None, session=None) -> dict:
    """返回与 Anthropic Messages 同形状的响应。抛 CodexAuthError / StreamError / requests 异常。"""
    import requests
    http = session or requests
    body = build_body(messages, system, model, [t for t in tools or [] if "input_schema" in t], effort)
    tok, acct = token()
    for attempt in range(2):
        ver = version()
        headers = {"Authorization": f"Bearer {tok}", "OpenAI-Beta": "responses=experimental",
                   "version": ver, "Accept": "text/event-stream", "Content-Type": "application/json",
                   "session-id": body["prompt_cache_key"], "thread-id": body["prompt_cache_key"],
                   **_client_headers(ver)}
        if acct:
            headers["chatgpt-account-id"] = acct
        resp = http.post(BASE + "/responses", json=body, headers=headers, stream=True, timeout=(15, 600))
        if resp.status_code == 401 and attempt == 0:
            resp.close()
            tok, acct = token(force=tok)
            continue
        break
    if not resp.ok:
        text = resp.text[:400]
        resp.close()
        raise _fmt.StreamError(f"ChatGPT 后端返回 {resp.status_code}: {text}",
                               overloaded=resp.status_code in (429, 500, 502, 503, 529))
    acc = _Stream(on_text)
    try:
        for raw in resp.iter_lines(decode_unicode=False):
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                acc.feed(json.loads(payload))
        return acc.result()
    finally:
        resp.close()
