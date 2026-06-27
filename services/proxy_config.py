"""统一的本地代理配置 —— 海外接口 / 外发请求共用一个本地代理。

代理软件重启后端口会漂移(7890→7897…), 这是海外接口同步反复失效退回过时
缓存值的元凶。故这里支持:
  - 设置面板统一管理(DB 持久化), 不再靠 env 手改
  - 自动探测(扫常见端口 + 本机在听的代理进程端口, 挑通海外的)
  - 变更时通知各 session 即时更新 proxies

注意: 境内行情源走代理反而失败, 它们故意直连, 不受这里影响。
"""
from __future__ import annotations
import os
import re
import subprocess
import requests as _requests

_proxy: str = ""                     # 当前生效代理 URL, 空 = 直连
_listeners: list = []                # 代理变化回调: 各 session 据此更新自己的 proxies

# 常见本地代理端口
_COMMON_PORTS = [7890, 7897, 7891, 7892, 7898, 7899, 1080, 1087, 8889, 10809, 8080]
_PROBE_URL = "https://www.gstatic.com/generate_204"   # 通用海外连通性探测, 204, 快


def get_proxy() -> str:
    return _proxy


def on_change(cb) -> None:
    """注册代理变化回调(立即用当前值调一次)。各 session 用它同步 proxies。"""
    _listeners.append(cb)
    try:
        cb(_proxy)
    except Exception:
        pass


def configure(url: str) -> None:
    global _proxy
    _proxy = (url or "").strip()
    for cb in _listeners:
        try:
            cb(_proxy)
        except Exception:
            pass


def _probe(url: str, timeout: float = 4.0) -> bool:
    """该代理能否够到海外(空 url 视为直连, 也探一次)。"""
    try:
        proxies = {"http": url, "https": url} if url else {"http": None, "https": None}
        r = _requests.get(_PROBE_URL, proxies=proxies, timeout=timeout)
        return r.status_code in (200, 204)
    except Exception:
        return False


def _listening_ports() -> list[int]:
    """本机在听的代理进程端口(按常见进程名匹配)。"""
    ports: list[int] = []
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=4,
        ).stdout
        for line in out.splitlines():
            if re.search(r"clash|mihomo|verge|v2ray|sing-box|proxy", line, re.I):
                m = re.search(r"127\.0\.0\.1:(\d+)", line)
                if m:
                    ports.append(int(m.group(1)))
    except Exception:
        pass
    return ports


def auto_detect() -> str | None:
    """扫(在听的代理端口 + 常见端口), 返回第一个能通海外的代理 URL; 都不行返回 None。"""
    seen: set[int] = set()
    for p in _listening_ports() + _COMMON_PORTS:
        if p in seen:
            continue
        seen.add(p)
        url = f"http://127.0.0.1:{p}"
        if _probe(url):
            return url
    return None


def resolve_and_apply(stored: str = "") -> dict:
    """解析并应用代理。优先级: env CRYPTO_PROXY > stored(DB) > 自动探测。
    返回 {proxy, source, ok}。source ∈ configured/auto/none。"""
    env = os.environ.get("CRYPTO_PROXY", "").strip()
    for cand, src in ((env, "configured"), (stored, "configured")):
        if cand and _probe(cand):
            configure(cand)
            return {"proxy": cand, "source": src, "ok": True}
    found = auto_detect()
    if found:
        configure(found)
        return {"proxy": found, "source": "auto", "ok": True}
    # 都不通: 保留 stored/env 原值(至少不丢配置), 标记不可用
    fallback = stored or env or ""
    configure(fallback)
    return {"proxy": fallback, "source": "none", "ok": False}
