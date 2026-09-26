"""场外基金「待公布净值」的回归估算: 每只基金用自己最近 120 个净值日, 对一组指数/ETF 日涨跌 + top10 持仓加权做岭回归。

为什么: 现行 top10 加权(services/fund_proxy)有两处系统误差 ——
  ① 归一化: top10 只覆盖 20%~60% 净值, 却按 100% 外推(部分全球科技 QDII 的 top10 合计仅约 25%);
  ② 指数型 QDII 的 top10 只是前十大成分, 代表不了整个指数。
离线评估(近 180 个净值日, 平均绝对误差, 百分点): 标普500 QDII 0.61→0.08, 纳指100 QDII 0.69→0.12,
  信息科技主动基金 ~0.98→~0.62, 全球科技 QDII 0.8~1.2→0.45~0.85; 黄金联接持平(0.25)。
流程:
  fit(code): 每天一次(后台, 结果缓存到 data/fund_nav_model.json): 净值历史 + 因子日涨跌 + top10 历史加权 → 标准化岭回归系数
  estimate(code, nav_date, top10_pct): 待公布净值日 = 最新已公布日之后的 A 股交易日; 每个净值日的因子值 = 上一个净值日之后
      到它的累计涨跌(A 股假期里港股/美股的涨跌并进下一个净值日, 训练同口径); 最新已公布日之后没有新 K 线 → 0
      (开盘前实时行情仍是昨天的涨跌, 现行 top10 会把已进净值的涨跌再算一遍); top10 实时加权只放进最后一个待公布日。
⚠️ 跨市场 QDII: 美股因子取最近一个已收盘的美股交易日, A股/港股取今天 —— 与训练时「同一日期对齐」略有出入。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from pathlib import Path

import numpy as np

WIN = 120
_PATH = Path(__file__).resolve().parent.parent / "data" / "fund_nav_model.json"
FACTORS = {"沪深300": ("cn", "sh000300"), "中证1000": ("cn", "sh000852"), "创业板": ("cn", "sz399006"),
           "科创50": ("cn", "sh000688"), "恒生": ("cn", "hkHSI"), "恒生科技": ("cn", "hkHSTECH"),
           "纳指100": ("us", ".NDX"), "标普500": ("us", ".INX"), "费城半导体": ("us", "SOXX"), "沪金": ("cn", "nf_AU0")}
_models: dict | None = None
_fitting: set = set()
_fit_lock: asyncio.Lock | None = None   # 首次用时再建(py3.9 在导入时建会绑到别的事件循环)
_fcache: dict = {}             # 因子日线 {sym: (bars, ts)}
_FTTL = 600


def _load() -> dict:
    global _models
    if _models is None:
        try:
            _models = json.loads(_PATH.read_text())
        except Exception:  # noqa: BLE001
            _models = {}
    return _models


def _save():
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(_models, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(f"[fund-nav-model] save failed: {e}")


def _us_bars(sym: str, n: int = 400) -> list[dict]:
    import requests
    url = (f"http://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20_{sym.strip('.')}=/"
           f"US_MinKService.getDailyK?symbol={sym}&num={n}")
    s = requests.Session()
    s.trust_env = False
    t = s.get(url, headers={"Referer": "https://finance.sina.com.cn/stock/usstock/"}, timeout=10).text
    eq = t.find("=(")
    if eq < 0:
        return []
    arr = json.loads(t[eq + 2:t.rfind(");")])
    return [{"date": x["d"], "close": float(x["c"])} for x in arr]


def _bars_sync(kind: str, sym: str, n: int = 400) -> list[dict]:
    hit = _fcache.get((sym, n))
    if hit and time.time() - hit[1] < _FTTL:
        return hit[0]
    if kind == "us":
        b = _us_bars(sym, n)
    else:
        from services.market_data import _kline_for_symbol
        b = [{"date": str(x["date"])[:10], "close": float(x["close"])} for x in _kline_for_symbol(sym, n)]
    _fcache[(sym, n)] = (b, time.time())
    return b


def _returns(bars: list[dict]) -> dict:
    out = {}
    for a, b in zip(bars[:-1], bars[1:]):
        if a["close"] > 0:
            out[b["date"][:10]] = (b["close"] / a["close"] - 1) * 100
    return out


def _fold(r: dict, lo: str, hi: str) -> float | None:
    """因子在 (lo, hi] 区间的累计涨跌 %(净值日之间隔着 A 股假期时, 港股/美股那几天的涨跌并进下一个净值日)。
    区间内一根 K 线都没有 → None。"""
    xs = [v for d, v in r.items() if lo < d <= hi]
    if not xs:
        return None
    acc = 1.0
    for v in xs:
        acc *= 1 + v / 100
    return (acc - 1) * 100


def _nav_history_sync(code: str, n: int = 160) -> dict:
    import requests
    s = requests.Session()
    s.trust_env = False
    rows, page = [], 1
    while len(rows) < n and page <= 12:
        time.sleep(0.4)
        r = s.get("https://api.fund.eastmoney.com/f10/lsjz", timeout=10,
                  params={"fundCode": code, "pageIndex": page, "pageSize": 20},
                  headers={"Referer": "http://fundf10.eastmoney.com/", "User-Agent": "Mozilla/5.0"})
        items = (r.json().get("Data") or {}).get("LSJZList") or []
        if not items:
            break
        rows += items
        page += 1
    return {x["FSRQ"]: float(x["JZZZL"]) for x in rows if x.get("JZZZL") not in (None, "")}


def _holding_sym(h: dict) -> tuple[str, str] | None:
    m, c = h.get("market"), h.get("code")
    if m == "US":
        return ("us", c)
    if m == "HK":
        return ("cn", f"hk{c}")
    if m in ("CN_SH", "CN_SZ"):
        return ("cn", ("sh" if m == "CN_SH" else "sz") + c)
    return None


def _fit_sync(code: str, holdings: list[dict], bond: bool) -> dict | None:
    y = _nav_history_sync(code)
    if len(y) < WIN + 10:
        return None
    F = {k: _returns(_bars_sync(t, s)) for k, (t, s) in FACTORS.items()}
    names = list(FACTORS)
    top = None
    if holdings:
        parts = []
        for h in holdings:
            hs = _holding_sym(h)
            if hs:
                try:
                    parts.append((float(h.get("weight") or 0), _returns(_bars_sync(hs[0], hs[1], 200))))
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.15)
        if parts:
            top = {}
            for d in y:
                num = sum(w * r[d] for w, r in parts if d in r)
                cov = sum(w for w, r in parts if d in r)
                if cov > 0:
                    top[d] = num if bond else num / cov
            names.append("top10")
            F["top10"] = top
    # 按净值日对齐: 每个净值日取各因子「上一个净值日之后到这一天」的累计涨跌(与 estimate 同口径)
    nd = sorted(y)
    G = {k: {} for k in names}
    for a, b2 in zip(nd[:-1], nd[1:]):
        for k in names:
            v = _fold(F[k], a, b2)
            if v is not None:
                G[k][b2] = v
    days = [d for d in nd[1:] if all(d in G[k] for k in names)][-WIN:]
    if len(days) < 60:
        # 某个因子缺得多: 只留覆盖 ≥90% 的因子
        names = [k for k in names if len(G[k]) >= 0.9 * (len(nd) - 1)]
        days = [d for d in nd[1:] if all(d in G[k] for k in names)][-WIN:]
        if len(days) < 60:
            return None
    A = np.array([[G[k][d] for k in names] for d in days])
    Y = np.array([y[d] for d in days])
    mu, sd = A.mean(0), A.std(0) + 1e-9
    Z = (A - mu) / sd
    lam = len(Z) * 0.02
    b = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (Y - Y.mean()))
    fit = Y.mean() + Z @ b
    return {"fitted": date.today().isoformat(), "names": names, "mu": mu.tolist(), "sd": sd.tolist(), "b": b.tolist(),
            "mean": float(Y.mean()), "mae": round(float(np.abs(fit - Y).mean()), 3), "n": len(days),
            "last_day": days[-1]}


async def ensure_fit(code: str, name: str = "") -> None:
    """后台拟合(每天一次)。不阻塞调用方。"""
    models = _load()
    m = models.get(code)
    if (m and m.get("fitted") == date.today().isoformat()) or code in _fitting:
        return
    _fitting.add(code)

    global _fit_lock
    if _fit_lock is None:
        _fit_lock = asyncio.Lock()

    async def run():
        try:
            async with _fit_lock:                      # 一次只拟合一只, 别并发打行情源
                from services.fund_holdings import get_fund_top10
                hs = await get_fund_top10(code) or []
                bond = ("债" in name or "固收" in name)
                r = await asyncio.to_thread(_fit_sync, code, hs, bond)
                if r:
                    _load()[code] = r
                    _save()
        except Exception as e:  # noqa: BLE001
            print(f"[fund-nav-model] fit {code} failed: {e}")
        finally:
            _fitting.discard(code)
    asyncio.create_task(run())


def estimate(code: str, nav_date: str, top10_pct: float | None) -> dict | None:
    """待公布净值(最新已公布日之后)的累计涨跌估算 %。没有模型 → None。
    待公布的净值日 = 最新已公布日之后、到今天为止的 A 股交易日; 每个净值日的因子值 = 上一个净值日之后到它的累计涨跌,
    还没有 K 线的因子(美股当天没收盘等)按 0。"""
    from datetime import timedelta
    from services.market_data import _is_a_share_trading_day
    m = _load().get(code)
    if not m or not nav_date:
        return None
    nav_date = str(nav_date)[:10]
    names, mu, sd, b = m["names"], np.array(m["mu"]), np.array(m["sd"]), np.array(m["b"])
    series = {}
    for k in names:
        if k == "top10":
            continue
        t, sym = FACTORS[k]
        try:
            series[k] = _returns(_bars_sync(t, sym, 12))
        except Exception:  # noqa: BLE001
            series[k] = {}
    latest_bar = max((d for r in series.values() for d in r), default="")
    d, pend = date.fromisoformat(nav_date) + timedelta(days=1), []
    while d.isoformat() <= min(date.today().isoformat(), latest_bar or nav_date):
        if _is_a_share_trading_day(d):
            pend.append(d.isoformat())
        d += timedelta(days=1)
    if not pend:
        return {"pct": 0.0, "through": nav_date, "days": 0, "mae": m["mae"]}
    acc, prev = 1.0, nav_date
    for i, dd in enumerate(pend):
        hi = dd if i < len(pend) - 1 else latest_bar          # 最后一个待公布日: 把之后已有的 K 线(如美股)也算进来
        x = []
        for k in names:
            if k == "top10":
                x.append(top10_pct if (i == len(pend) - 1 and top10_pct is not None) else m["mu"][names.index(k)])
            else:
                v = _fold(series[k], prev, hi)
                x.append(v if v is not None else 0.0)
        z = (np.array(x) - mu) / sd
        acc *= 1 + float(m["mean"] + z @ b) / 100
        prev = dd
    return {"pct": round((acc - 1) * 100, 2), "through": pend[-1], "days": len(pend), "mae": m["mae"]}
