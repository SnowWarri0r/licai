"""东财 push2 系行情主机(push2 / push2delay / 1.push2 / push2his)的统一取数 + 熔断。

这几台主机限流脚本客户端时是整组一起的: 连上即断(RemoteDisconnected, 0.1 秒级), 浏览器照常,
冷却 30-60 分钟才恢复, 而且反复重试会加重限流。原来每个取数函数各自轮换主机重试 6-12 次、
每次失败再睡 0.3 秒, 一次 get_fund_flow 就耗 8-9 秒、get_hot_concepts 6 秒(2026-10-27 实测),
一道题里几个工具叠起来全压在问答的关键路径上。

现在: 一次调用里每次尝试都是"连上即断", 就判定整组在限流, 之后 COOL_S 秒内所有调用直接返回
None, 各调用方照旧走自己的兜底源(新浪 / 东财数据中心 / 腾讯)。超时不算限流(可能只是网慢),
不触发熔断; 偶发掐首连只要后面有一次连上就不算。
"""
from __future__ import annotations

import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

HOSTS = ["push2delay.eastmoney.com", "push2.eastmoney.com", "1.push2.eastmoney.com"]
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
COOL_S = 600

_lock = threading.Lock()
_down_until = 0.0


def cooling() -> bool:
    """是否处在限流冷却期(期间不再去碰 push2 主机)。"""
    return time.time() < _down_until


def get_json(path: str, params: dict, hosts: list | None = None, tries: int | None = None,
             timeout: float = 7, headers: dict | None = None, ok=None):
    """轮换 hosts 取 https://<host><path>, 返回第一个让 ok(json) 为真的 JSON(不给 ok 则第一个能解析的)。

    都没拿到返回 None。tries 默认每台主机两次(吸收偶发掐首连); 全部"连上即断"则进入冷却。
    """
    global _down_until
    if cooling():
        return None
    hosts = hosts or HOSTS
    tries = tries or 2 * len(hosts)
    refused = 0
    for i in range(tries):
        try:
            j = requests.get(f"https://{hosts[i % len(hosts)]}{path}", params=params,
                             timeout=timeout, headers=headers or HEADERS).json()
        except requests.exceptions.Timeout:        # ConnectTimeout 也是 ConnectionError 的子类, 先接住
            continue
        except requests.exceptions.ConnectionError:
            refused += 1
            continue
        except ValueError:                          # 返回了非 JSON(维护页之类)
            continue
        if ok is None or ok(j):
            return j
    if refused == tries:
        with _lock:
            _down_until = time.time() + COOL_S
        logger.warning("东财 push2 主机 %d 次尝试全部连上即断, 判定限流, %d 秒内改走备用源", tries, COOL_S)
    return None
