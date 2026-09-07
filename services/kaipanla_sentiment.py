"""开盘啦情绪面数据: 跳水榜 / 多空风向标 / 实际涨停跌停 / 连板梯队+市场评价。

为什么单独一个模块, 不塞进 limit_up_pool: 那个模块管的是**逐只涨停档案**(封单额/首封时刻,
按 snap_date 落库回测); 这里取的是**全市场当日情绪聚合**(不落库, 实时快照), 两者数据形态和
生命周期都不同。放一起会让"某个数到底是档案还是快照"说不清。

数据源是开盘啦(龙虎VIP)的免登录接口 —— 东财那套(_fetch_sentiment_sync)给的是涨停家数/炸板率,
开盘啦补的是东财没有的三样: 跳水榜(资金出逃的另一面)、多空风向标(全市场量能 vs 昨日同期)、
以及一句官方"市场评价"。多一个源交叉着看, 情绪判断更稳。

口径见 [[reference_kaipanla_unpack_and_apis]]: host 按功能分家, 非交易日返回 errcode 1020,
服务端不校验 VerSion。全部免登录, 无签名。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone, timedelta

_CST = timezone(timedelta(hours=8))
_VER = "5.21.0.2"
_UA = "Dalvik/2.1.0 (Linux; U; Android 9; Build/PQ3A.190605.01141736)"
_TTL = 300                    # 情绪是快照, 5 分钟缓存够了(和东财那套一致)
_cache: dict = {}


def _post(host: str, extra: dict, day: str) -> dict | list | None:
    """打一个开盘啦接口。非交易日/无数据服务端返 {errcode:1020}, 原样返回让调用方判。"""
    import requests
    s = requests.Session()
    s.trust_env = False
    data = {"PhoneOSNew": "1", "DeviceID": str(uuid.uuid4()), "VerSion": _VER,
            "apiv": "w42", "Day": day, "Date": day}
    data.update(extra)
    try:
        r = s.post(f"https://{host}.longhuvip.com/w1/api/index.php", data=data,
                   headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "User-Agent": _UA, "Host": f"{host}.longhuvip.com"},
                   timeout=12)
        return r.json()
    except Exception:
        return None


def _ok(j) -> bool:
    """1020=非交易日/无数据, 不是错但也没内容。"""
    return isinstance(j, dict) and str(j.get("errcode", "0")) != "1020"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_sync(day: str) -> dict:
    out: dict = {}

    # 跳水榜: 当日大幅回撤(涨幅冲高又跳水), 是"封单额看不见的另一面" —— 资金出逃。
    sw = _post("apphis", {"a": "SharpWithdrawal", "c": "HisHomeDingPan"}, day)
    if _ok(sw):
        rows = sw.get("info") or []
        out["跳水榜"] = {
            "只数": sw.get("num") or len(rows),
            "前几只": [{"代码": r[0], "名称": r[1], "当日涨跌%": round(_num(r[2]) or 0, 2),
                        "回撤%": round(_num(r[3]) or 0, 2)}
                       for r in rows[:8] if isinstance(r, list) and len(r) >= 4],
            "口径": "回撤=当日最高点到收盘的跌幅; 冲高跳水多=情绪退潮/资金出逃。",
        }

    # 多空风向标: 全市场量能与昨日同期比。bl<0=缩量(资金退), color 是档位。
    mood = _post("apphwhq", {"a": "MoodNumCount", "c": "MarketMood"}, day)
    if _ok(mood):
        d = (mood.get("list") or {}) if isinstance(mood, dict) else {}
        if d:
            out["多空风向标"] = {
                "上涨家数": d.get("SZJS"), "下跌家数": d.get("XDJS"),
                "涨停": d.get("ZTJS"), "跌停": d.get("DTJS"),
                "量能较昨同期%": _num(d.get("bl")),
                "口径": "量能较昨同期=此刻累计成交 vs 昨日同一时点; 负=缩量(增量资金退场)。"
                        "上涨/下跌家数是调用时点快照(盘中=实况, 收盘后=定格)。",
            }

    # 实际涨停/跌停: 东财池给的是"封住到收盘"的, 这个 SJZT 是真实涨停家数, 两者可差不少。
    zf = _post("apphis", {"a": "HisZhangFuDetail", "c": "HisHomeDingPan"}, day)
    if _ok(zf):
        info = zf.get("info") or {}
        if isinstance(info, dict) and info:
            out["实际涨跌停"] = {"实际涨停": _int(info.get("SJZT")), "实际跌停": _int(info.get("SJDT")),
                              "涨停": _int(info.get("ZT")), "跌停": _int(info.get("DT"))}

    # 连板梯队 + 一句官方市场评价。info 是定长数组, 见下标注释。
    zt = _post("apphis", {"a": "ZhangTingExpression", "c": "HisHomeDingPan"}, day)
    if _ok(zt):
        a = zt.get("info") or []
        if isinstance(a, list) and len(a) >= 12:
            out["连板梯队"] = {
                "首板": a[0], "二板": a[1], "三板": a[2], "高度板": a[3],
                "连板率%": round(_num(a[4]) or 0, 2),
                "昨首板今涨停破板率%": round(_num(a[7]) or 0, 2),
                "昨涨停今表现%": round(_num(a[8]) or 0, 2),
                "昨连板今表现%": round(_num(a[9]) or 0, 2),
                "市场评价": a[11],
                "口径": "昨连板今表现=昨日连板股今日平均涨幅(高位资金接力赚钱效应); "
                        "破板率高+昨连板今表现为负=分歧/退潮。市场评价是开盘啦官方定性, 转述即可。",
            }
    return out


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def sentiment(day: str | None = None) -> dict:
    """开盘啦情绪面聚合。默认今天。整块取不到就返回空 dict(调用方按"这块没有"处理)。"""
    if not day:
        day = datetime.now(tz=_CST).strftime("%Y-%m-%d")
    c = _cache.get(day)
    if c and (datetime.now(tz=_CST).timestamp() - c[1]) < _TTL:
        return c[0]
    out = await asyncio.to_thread(_fetch_sync, day)
    if out:
        out["数据源"] = "开盘啦(免登录)"
        _cache[day] = (out, datetime.now(tz=_CST).timestamp())
    return out
