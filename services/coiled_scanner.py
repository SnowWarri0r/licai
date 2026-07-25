"""横盘蓄势扫描: 在"龙头池"里找"横盘很久 + 放量启动(突破前后)"的结构。

设计(v3, 挑好的而非排差的):
  1) 龙头池: 全市场(含北交所)按 市值≥100亿 + ≥30家基金持有 + 盈利 圈定 —— 机构认可的
     百亿公司, 天然排掉垃圾/庄股/微盘, 不再需要"今天必须放量"这种入口闸。
  2) 整池日K体检(结构阈值放低): 箱体≤30%、横盘≥15日、近5日内有放量攻上沿、未跌回箱体。
     回踩日/安静日也能看到, 不只突破当天。
  3) 综合评分排序: 横盘时长 + 箱体越窄越好 + 缩量蓄势 + 放量甜区 + 收盘实体 + 突破位置。

产出纯客观结构描述(标签: 突破后回踩/临界/刚突破/突破延伸), 不构成任何买卖建议。
整池首扫 ~1 分钟(日K有 SQLite 缓存, 之后快), 由后台预热兜底 + 10 分钟缓存。
"""
from __future__ import annotations
import asyncio
import time
from datetime import date

_cache: dict = {}
_TTL = 600   # 10 分钟

_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81"   # 沪深A + 北交所
_FIELDS = "f3,f6,f10,f12,f14,f20,f23,f24,f26,f37,f41,f45,f46,f100,f115,f160"
_HOSTS = ["push2delay.eastmoney.com", "push2.eastmoney.com", "1.push2.eastmoney.com"]


def _clist_pool(pages: int = 20) -> list[dict]:
    """按市值降序拉前 N 页(百亿以上公司约 1100 家, 20 页够覆盖)。"""
    import requests
    s = requests.Session(); s.trust_env = False
    out: list[dict] = []
    for pn in range(1, pages + 1):
        p = {"pn": str(pn), "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2",
             "fid": "f20", "fs": _FS, "fields": _FIELDS}
        got = None
        for h in _HOSTS:
            try:
                d = s.get(f"https://{h}/api/qt/clist/get", params=p, timeout=7).json().get("data")
                if d and d.get("diff"):
                    got = d["diff"]; break
            except Exception:
                continue
        if not got:
            break
        out.extend(got)
        if len(got) < 100:
            break
        # 已翻到百亿以下就停
        try:
            if float(got[-1].get("f20") or 0) / 1e8 < 100:
                break
        except (TypeError, ValueError):
            pass
    return out


_fund_hold_cache: dict = {}


def _fund_hold_map() -> dict:
    """全市场基金持仓表(季报) → {code: 持有基金家数}。缓存 1 天; 拉不到返回空(上层 fail-open)。"""
    c = _fund_hold_cache.get("m")
    if c and time.time() - c[1] < 86400:
        return c[0]
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    m: dict = {}
    try:
        import akshare as ak
        d = date.today()
        quarters = [f"{y}{md}" for y in (d.year, d.year - 1) for md in ("1231", "0930", "0630", "0331")
                    if f"{y}{md}" <= d.strftime("%Y%m%d")]
        for q in quarters[:4]:      # 最新报告期可能未披露(表里只有零星早鸟), 要足够完整才采纳
            try:
                df = ak.stock_report_fund_hold(symbol="基金持仓", date=q)
            except Exception:
                continue
            if df is not None and len(df) >= 500:
                for _, r in df.iterrows():
                    try:
                        m[str(r["股票代码"]).zfill(6)] = int(r["持有基金家数"])
                    except (TypeError, ValueError):
                        continue
                break
    except Exception:
        pass
    _fund_hold_cache["m"] = (m, time.time())
    return m


_yjyg_cache: dict = {}
_FC_POS = {"预增", "略增", "扭亏", "续盈", "减亏"}
_FC_NEG = {"预减", "略减", "首亏", "续亏", "增亏"}


def _forecast_map() -> dict:
    """最新报告期业绩预告(当前=中报) → {code: {类型/幅度%/期/日期}}。
    每股取一条, 指标优先级: 归母净利 > 净利润 > 扣非。缓存6h; 拉不到返回空(fail-open)。
    没进表 = 未披露(预告只对大幅变动强制), 与业绩差是两回事。"""
    c = _yjyg_cache.get("m")
    if c and time.time() - c[1] < 6 * 3600:
        return c[0]
    m: dict = {}
    try:
        import os
        for k in list(os.environ):
            if "proxy" in k.lower():
                os.environ.pop(k, None)
        import akshare as ak
        d = date.today()
        ends = sorted(f"{y}{md}" for y in (d.year, d.year - 1)
                      for md in ("0331", "0630", "0930", "1231"))
        period = max(e for e in ends if e <= d.strftime("%Y%m%d"))
        label = {"0331": "一季报", "0630": "中报", "0930": "三季报", "1231": "年报"}[period[4:]]
        df = ak.stock_yjyg_em(date=period)
        m["_期"] = label                        # 当期标签, _attach_forecast 兜底文案用
        prio = {"归属于上市公司股东的净利润": 0, "净利润": 1, "扣除非经常性损益后的净利润": 2}
        for _, r in (df.iterrows() if df is not None else []):
            code = str(r["股票代码"]).zfill(6)
            p = prio.get(str(r["预测指标"]), 9)
            if code in m and m[code]["_p"] <= p:
                continue
            try:
                chg = float(r["业绩变动幅度"])
            except (TypeError, ValueError):
                chg = None
            m[code] = {"_p": p, "类型": str(r["预告类型"]), "幅度%": chg,
                       "期": label, "日期": str(r["公告日期"])[:10]}
    except Exception:
        pass
    if m:
        _yjyg_cache["m"] = (m, time.time())
    return m


def _attach_forecast(rows: list[dict], fc_map: dict) -> None:
    """给候选行挂业绩凭据: 当期预告(预喜/预警进标签, ±6温和计分, 蓄势分主体仍是结构);
    未披露预告的退一级展示最近一期已披露实绩同比(旧信息, 只展示不计分)。"""
    plabel = fc_map.get("_期") or "当期"
    for r in rows:
        fc = fc_map.get(r["code"])
        if fc:
            amp = f" {fc['幅度%']:+.0f}%" if fc.get("幅度%") is not None else ""
            r["业绩预告"] = f"{fc['期']}{fc['类型']}{amp} ({fc['日期']}披露)"
            if fc["类型"] in _FC_POS:
                r["评分"] += 6
                r["标签"] += f"·{fc['期']}预喜"
            elif fc["类型"] in _FC_NEG:
                r["评分"] -= 6
                r["标签"] += f"·{fc['期']}预警"
            continue
        parts = [f"净利同比{r['净利同比%']:+.1f}%" if r.get("净利同比%") is not None else "",
                 f"营收同比{r['营收同比%']:+.1f}%" if r.get("营收同比%") is not None else ""]
        parts = [p for p in parts if p]
        if parts:
            r["业绩预告"] = f"{plabel}预告未披露 · 最近实绩 " + " ".join(parts)


def _build_universe(rows: list[dict], fund_map: dict | None = None) -> list[dict]:
    """龙头池: 百亿市值 + 机构重仓 + 盈利的正常经营公司。"""
    fund_map = fund_map or {}
    today = int(date.today().strftime("%Y%m%d"))
    out = []
    for x in rows:
        def _f(key):
            v = x.get(key)
            try:
                return float(v)
            except (TypeError, ValueError):
                return None      # 停牌/缺数字段是 "-"
        code = str(x.get("f12") or ""); name = str(x.get("f14") or "")
        if not code or "ST" in name.upper() or "退" in name:
            continue
        pct, cap, amount = _f("f3"), _f("f20"), _f("f6")
        if pct is None or cap is None:          # 停牌/无行情
            continue
        cap /= 1e8
        try:
            ipo = int(x.get("f26") or 0)
        except (TypeError, ValueError):
            ipo = 0
        if ipo and today - ipo < 10000:       # 上市满一年(新股无横盘史)
            continue
        if cap < 100:                          # 龙头门槛: 百亿市值
            continue
        if (amount or 0) / 1e8 < 1:            # 今日成交额≥1亿(流动性)
            continue
        fund_cnt = fund_map.get(code, 0)
        if fund_map and fund_cnt < 30:         # 机构重仓: ≥30家基金持有
            continue
        if pct < -5:                           # 今天正在崩的不列(结构再好也先让它跌完)
            continue
        profit, roe, rev_yoy, prof_yoy, pb, pe = _f("f45"), _f("f37"), _f("f41"), _f("f46"), _f("f23"), _f("f115")
        # 基本面闸(定位=排暴雷): 机构重仓龙头(≥60家基金)免检——创新药/卫星/量子/重资产半导体
        # 处在研发或扩产投入期, 亏损/低ROE属正常商业阶段, 机构已用真金白银投票
        if fund_cnt < 60:
            if profit is None or profit <= 0:      # 盈利中
                continue
            if roe is None or roe < 1.0:           # ROE(报告期累计口径): 排接近零利润的
                continue
            if prof_yoy is not None and prof_yoy < -60:   # 暴雷级下滑
                continue
            if pb is not None and pb <= 0:
                continue
        out.append({"code": code, "name": name, "pct": round(pct, 2),
                    "成交额亿": round((amount or 0) / 1e8, 2),
                    "量比": _f("f10"), "市值亿": round(cap, 0),
                    "行业": x.get("f100") or "",
                    "ROE%": roe, "净利同比%": round(prof_yoy, 1) if prof_yoy is not None else None,
                    "营收同比%": round(rev_yoy, 1) if rev_yoy is not None else None,
                    "PE_TTM": pe, "基金家数": fund_cnt})
    # 整池体检, 按基金家数排(机构最认可的优先算)
    out.sort(key=lambda c: -(c.get("基金家数") or 0))
    return out[:900]


async def _stage2(c: dict) -> dict | str:
    """日K结构体检: 箱体 + 横盘时长 + 缩量蓄势 + 近5日突破窗口。返回 dict(通过) 或 拒绝原因。"""
    from services.market_data import get_historical_data
    try:
        df = await get_historical_data(c["code"], days=150)   # 多拉一段作"自身波动基准"
    except Exception:
        return "K线不可达"
    if df is None or len(df) < 50:
        return "K线不足"
    closes = [float(v) for v in df["收盘"] if v]
    vols = [float(v) for v in df["成交量"]]
    if len(closes) < 50 or len(vols) != len(closes):
        return "K线不足"
    try:
        highs = [float(v) for v in df["最高"]]
        lows = [float(v) for v in df["最低"]]
    except Exception:
        highs = lows = []
    # 观察池定位: 找"仍在横盘蓄势中"的(突破前), 已突破/已启动的属于"偏晚"不进正选。
    # 箱体窗口 = 近40日(含最新, 因为目标状态就是"现在还横着")
    prev_c, prev_v = closes[-40:], vols[-40:]
    if len(prev_c) < 35:
        return "K线不足"
    bh, bl = max(prev_c), min(prev_c)
    if bl <= 0:
        return "K线不足"
    width = (bh / bl - 1) * 100
    # 静的两把尺子: 绝对σ(低波动蓝筹适用) + 相对自身收敛(高波动成长股适用)。
    # 半导体/创新药/机器人这类高beta票, 横盘时绝对振幅天然30%+、σ天然2.5+,
    # 蓄势与否要看"最近两个月是否明显比之前安静"(波动收敛, VCP逻辑)。
    rets = [prev_c[i] / prev_c[i - 1] - 1 for i in range(1, len(prev_c))]
    mean_r = sum(rets) / len(rets)
    sd = (sum((r - mean_r) ** 2 for r in rets) / len(rets)) ** 0.5 * 100
    sd_prior = None                                    # 基座前一段(~2个月)的自身波动基准
    base_c = closes[-80:-40]
    if len(base_c) >= 30:
        brets = [base_c[i] / base_c[i - 1] - 1 for i in range(1, len(base_c))]
        bm = sum(brets) / len(brets)
        sd_prior = (sum((r - bm) ** 2 for r in brets) / len(brets)) ** 0.5 * 100
    converging = bool(sd_prior) and sd <= sd_prior * 0.8     # 比自己此前安静≥20%
    if width > 28 and not (converging and width <= 42):      # 蓄势基座要窄(高波动票按收敛度放宽)
        return "箱体过宽"
    if sd > 2.4 and not (converging and sd <= 3.6):
        return "宽幅震荡(非安静横盘)"
    # 平(不对称): 重心上移=已在启动, 卡严(+5%); 重心缓慢下移只在"波动同时收敛"时算
    # 洗盘蓄势变体(下降楔形, 容忍到-12%)——不收敛的缓跌就是阴跌趋势, 不是盘整
    half_c = len(prev_c) // 2
    drift = ((sum(prev_c[half_c:]) / (len(prev_c) - half_c)) / (sum(prev_c[:half_c]) / half_c) - 1) * 100
    if drift > 5:
        return "重心上移(已非蓄势)"
    if drift < -12:
        return "阴跌下行(非横盘)"
    if drift < -4 and not converging:
        return "缓跌未收敛(阴跌非蓄势)"
    # 近期击穿: 近10日曾明显跌穿前段箱体下沿(哪怕弹回来了)——基座已破, 弹回属修复不属蓄势
    pre_low = min(closes[-40:-10]) if len(closes) >= 40 else bl
    if min(closes[-10:]) < pre_low * 0.965:
        return "近期击穿箱体后反弹(基座已破)"
    last_close = closes[-1]
    base_vol = sum(prev_v[:-3]) / max(len(prev_v) - 3, 1)
    if base_vol <= 0:
        return "K线不足"
    # 位置: 还在箱体内(观察目标); 已放量越过上沿的属于"突破(偏晚)", 跌到下沿边缘的属于破位风险
    if last_close > bh * 0.995 and (last_close / min(closes[-10:]) - 1) * 100 > 6:
        return "已突破(偏晚)"
    if len(closes) > 16 and last_close <= min(closes[-15:-1]) * 0.995:
        return "创近期新低(仍在下行)"
    # 近5日已明显启动的也偏晚
    if (last_close / closes[-6] - 1) * 100 > 8:
        return "已启动(偏晚)"
    # 横盘时长: 从最新往回数, 收盘都落在箱体(±2%容差)内的连续天数
    lo, hi = bl * 0.98, bh * 1.02
    days_flat = 0
    for cl in reversed(closes):
        if lo <= cl <= hi:
            days_flat += 1
        else:
            break
    # 近40日收盘天然全在箱体内(上下沿即取自这40日), days_flat 恒≥40;
    # 真闸在"箱体外还要再往前横": ≥45 = 箱体窗口外至少再横5日
    if days_flat < 45:
        return "横盘太短"
    # 缩量蓄势: 箱体后半均量 / 前半均量 (<1 = 越盘越缩)
    half = len(prev_v) // 2
    contraction = round((sum(prev_v[half:]) / (len(prev_v) - half)) / (sum(prev_v[:half]) / half), 2)
    dist = round((last_close / bh - 1) * 100, 1)
    # 初动迹象: 近3日均量相对基座均量温和放大(还没突破, 但量先热了)
    warm = round((sum(vols[-3:]) / 3) / base_vol, 2)
    pos_tag = "贴上沿" if dist >= -3 else ("箱体中部" if dist >= -12 else "箱体下部")
    if drift <= -4:
        pos_tag = "缓跌收敛·" + pos_tag       # 下降楔形/洗盘型基座
    elif width > 25 or sd > 2.4:
        pos_tag = "波动收敛·" + pos_tag       # 高波动品种, 靠相对自身收敛过闸
    tag = pos_tag + ("·量在暖" if warm >= 1.3 else "")
    # 蓄势质量分(0-115), 权重对齐 AI 审图口径(AI 杀单前两大理由=重心下移/未收敛):
    # 平度+收敛深度是主轴, 窄度/时长降权; 时长只数箱体窗口外的有效段(窗口内恒满不给分)
    narrow_score = ((25 - width) / 25 * 15 if width <= 25
                    else (9 if sd_prior and sd <= sd_prior * 0.65 else 5))
    flat_score = max(0.0, 1 - abs(drift) / 8) * 15                  # 重心越平越高, |8%|归零
    conv_score = (max(0.0, min(1.0, (1 - sd / sd_prior) / 0.5)) * 15
                  if sd_prior else 7)                                # 比前段安静50%=满分
    score = (min(max(days_flat - 40, 0), 40) / 40 * 20
             + narrow_score + flat_score + conv_score
             + (18 if contraction <= 0.8 else 12 if contraction <= 0.95 else 6 if contraction <= 1.1 else 0)
             + (15 if dist >= -3 else 10 if dist >= -8 else 5)
             + (12 if 1.3 <= warm <= 3 else 6 if warm >= 1.1 else 0)
             + (5 if (c.get("基金家数") or 0) >= 100 else 0))
    return {**c,
            "横盘日": days_flat, "箱体振幅%": round(width, 1),
            "缩量比": contraction, "近3日量比基座": warm,
            "距上沿%": dist, "重心漂移%": round(drift, 1),
            "波动收敛比": round(sd / sd_prior, 2) if sd_prior else None, "标签": tag,
            "评分": round(score),
            "箱体上沿": round(bh, 2), "箱体下沿": round(bl, 2), "现价": round(last_close, 2)}


_ai_cache: dict = {}   # (code, last_date, 标签) → 审核结果; 同日同形态复用, 不重复花钱

_AI_SYS = (
    "你是K线形态审核员, 任务是给K线图的『安静横盘蓄势基座(仍在箱体内, 突破尚未发生)』形态打贴合度分。\n"
    "审核范围(图上金色虚线=箱体下沿, 蓝色虚线=箱体上沿):\n"
    "· 基座 = 最近约两个月: 要求价格重心走平、波动收敛、大体在箱体内运行、成交量平稳或渐缩。"
    "更早的历史走势(基座之前的下跌或上涨)属于背景, 独立于基座质量之外——先跌一波再筑底属标准形态之一。"
    "基座形态除水平箱体外, 缓慢下倾的收敛通道(下降楔形/缓跌洗盘, 卖压逐步衰竭)同样属于合格的蓄势基座变体; 重心持续上移的爬坡通道则属已启动。"
    "『安静』以该股自身波动为基准: 高波动成长股(半导体/创新药/军工等)的基座振幅天然宽于蓝筹, "
    "只要近两个月波动明显小于更早时段且重心走平, 同样算合格的收敛基座;\n"
    "· 当前位置 = 最新几根K线仍在箱体内(贴近上沿或箱体中部都可), 已放量越过上沿并连续拉升的属于突破已发生, 大幅扣分;"
    "近期跌破下沿走弱的同样大幅扣分。\n"
    "加分项: 横盘时间长、箱体窄、越盘量越缩、近几日量能温和转暖但价格未动(蓄势末端特征)。\n"
    "以图形整体观感为准, 数字指标仅作参考。输出严格 JSON(只输出 JSON):\n"
    '{"贴合度": 0到100整数, "理由": "一句话, 指出图上的关键依据"}\n'
    "贴合度标定: ≥80=教科书级安静蓄势基座; 60-79=基座成立但有瑕疵; 40-59=形态勉强; <40=不是该形态。"
)


async def _ai_judge(row: dict) -> dict | None:
    """AI 看图精判一只候选。返回 {符合, 置信, 理由} 或 None(渲染/LLM不可用, 上层 fail-open)。"""
    from services.market_data import get_historical_data
    try:
        df = await get_historical_data(row["code"], days=90)
        if df is None or len(df) < 50:
            return None
        last_date = str(df["日期"].iloc[-1])[:10]
        key = (row["code"], last_date, row.get("标签"))
        if key in _ai_cache:
            return _ai_cache[key]
        bars = [(str(d)[:10], c, h, l, v, o) for d, c, h, l, v, o in zip(
            df["日期"], df["收盘"], df["最高"], df["最低"], df["成交量"], df["开盘"])]
        from services.chart_render import render_trend_chart
        # 把箱体上下沿画进图里(复用结构线通道: 台阶支撑=金色虚线→下沿, 颈线=蓝色虚线→上沿), AI 按线审基座
        box = {"台阶支撑": row.get("箱体下沿"), "颈线": row.get("箱体上沿")}
        png = await asyncio.to_thread(render_trend_chart, bars, code=row["code"],
                                      name=row["name"], structure={k: v for k, v in box.items() if v},
                                      display=70)
        if not png:
            return None
        import base64 as _b64
        import json as _json
        import re as _re
        stats = (f"候选: {row['name']}({row['code']}) {row.get('行业','')}\n"
                 f"规则侧指标: 横盘{row.get('横盘日')}日, 箱体振幅{row.get('箱体振幅%')}%, "
                 f"缩量比{row.get('缩量比')}, 距箱体上沿{row.get('距上沿%')}%, "
                 f"近3日量/基座量{row.get('近3日量比基座', '?')}。\n请按标准审核这张K线图。")
        messages = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": _b64.b64encode(png).decode()}},
            {"type": "text", "text": stats},
        ]}]
        from services.llm_client import call_claude_messages
        # sonnet-5 先输出 thinking 块再给正文, thinking 长度不定; 预算被吃光(text空)时加倍重试一次
        text = ""
        for budget in (2500, 6000):
            resp = await asyncio.to_thread(call_claude_messages, messages, _AI_SYS, "claude-sonnet-5", budget)
            text = "".join(p.get("text", "") for p in resp.get("content", []) if p.get("type") == "text")
            if text.strip():
                break
        m = _re.search(r"\{.*\}", text, _re.S)
        if not m:
            return None
        j = _json.loads(m.group(0))
        out = {"贴合度": int(j.get("贴合度") or 0),
               "理由": str(j.get("理由") or "").strip()[:120]}
        _ai_cache[key] = out
        return out
    except Exception:
        return None


_scan_lock: asyncio.Lock | None = None


async def scan_coiled(force: bool = False) -> dict:
    """横盘蓄势扫描主入口。10 分钟缓存。
    全程持锁单飞: 并发触发(预热循环+手动force)会各扫一遍且 AI 边缘判定有随机性,
    谁后写完谁覆盖缓存, 结果来回跳还烧双份钱——后到者等锁, 醒来发现缓存已被
    等待期间完成的扫描刷新过就直接用它。"""
    global _scan_lock
    c = _cache.get("coiled")
    if not force and c and time.time() - c[1] < _TTL:
        return c[0]
    if _scan_lock is None:
        _scan_lock = asyncio.Lock()
    entered = time.time()
    async with _scan_lock:
        c = _cache.get("coiled")
        if c and c[1] >= entered:
            return c[0]
        if not force and c and time.time() - c[1] < _TTL:
            return c[0]
        return await _scan_coiled_inner()


async def _scan_coiled_inner() -> dict:
    c = _cache.get("coiled")
    pool, fmap, fc_map = await asyncio.gather(asyncio.to_thread(_clist_pool),
                                              asyncio.to_thread(_fund_hold_map),
                                              asyncio.to_thread(_forecast_map))
    if not pool:
        return c[0] if c else {"error": "行情源暂不可达(东财抖动)"}
    universe = _build_universe(pool, fmap)
    sem = asyncio.Semaphore(8)

    async def _one(x):
        async with sem:
            return await _stage2(x)

    results = await asyncio.gather(*[_one(x) for x in universe], return_exceptions=True)
    rows = [r for r in results if isinstance(r, dict)]
    _attach_forecast(rows, fc_map)
    rows.sort(key=lambda r: -r["评分"])
    from collections import Counter
    rejected = Counter(r for r in results if isinstance(r, str))

    # AI 看图精判(规则只做宽召回, 形态是格式塔, 死阈值精度不够): 渲染K线图交模型审核,
    # 判不符合的剔除并计数; LLM 不可用时 fail-open 保留规则结果并标注未复核。
    # 送审位按行业限流(≤3只/行业): 评分尺子天然偏爱低波动行业(银行/基建), 限流保证
    # 半导体/医药/军工这类高波动行业的候选同样拿到 AI 复核位
    # 行业限流只在送审位拥挤时启用(它的作用是防评分尺子偏爱的行业占满坑位;
    # 候选本来就装得下时全员送审, 白杀没有意义)
    if len(rows) <= 50:
        picked = rows
    else:
        picked, per_ind = [], {}
        for r in rows:
            k = r.get("行业") or "?"
            if per_ind.get(k, 0) >= 3:
                continue
            per_ind[k] = per_ind.get(k, 0) + 1
            picked.append(r)
            if len(picked) >= 50:
                break
        rejected["规则通过但未获送审位(评分排序+行业限流)"] = len(rows) - len(picked)
    rows = picked
    ai_sem = asyncio.Semaphore(4)

    async def _judge_one(r):
        async with ai_sem:
            return await _ai_judge(r)

    verdicts = await asyncio.gather(*[_judge_one(r) for r in rows], return_exceptions=True)
    kept, dropped = [], []
    for r, v in zip(rows, verdicts):
        if isinstance(v, dict):
            r["AI置信"] = v["贴合度"]; r["AI理由"] = v["理由"]
            (kept if v["贴合度"] >= 45 else dropped).append(r)
        else:
            r["AI置信"] = None; r["AI理由"] = "AI未复核(渲染/LLM不可用), 仅规则筛选"
            kept.append(r)
    kept.sort(key=lambda r: (-(r["AI置信"] if r["AI置信"] is not None else -1), -r["评分"]))
    dropped.sort(key=lambda r: -(r["AI置信"] or 0))
    rows = kept
    if dropped:
        rejected["AI看图判不符合"] = len(dropped)

    out = {"as_of": time.strftime("%Y-%m-%d %H:%M"), "rows": rows[:40],
           "ai_dropped": dropped[:12],   # AI 判不符合的边缘候选(带分数判词), 折叠展示供人工过目
           "scanned": len(pool), "universe": len(universe), "rejected": dict(rejected),
           "note": "横盘蓄势观察池: 龙头池(百亿+≥30家基金+盈利, 机构重仓≥60家免盈利审查, 全市场含北交所)"
                   " → 规则召回仍在箱体内的安静横盘(窄/平/静/横盘≥45日, 缓跌须收敛、近期击穿箱体的剔除, 未突破——已突破/已启动的判偏晚剔除;"
                   " 高波动成长股按'比自己此前安静'的收敛度判, 送审位按行业限流) → AI看图按'安静蓄势基座'"
                   "贴合度精判。标签: 贴上沿/箱体中部/箱体下部, 波动收敛=高波动品种靠自身收敛过闸, "
                   "·量在暖=近3日量能温和转暖(蓄势末端特征), ·中报预喜/预警=最新报告期业绩预告凭据"
                   "(预喜+6分/预警-6分, 蓄势分主体仍是结构; 无标注=未披露, 预告只对大幅变动强制, 不代表业绩差,"
                   " 此时业绩预告字段退一级展示最近一期已披露实绩同比, 旧信息只展示不计分)。"
                   "纯客观结构描述, 横盘可能向下解决而非向上, 不构成任何买卖建议。"}
    _cache["coiled"] = (out, time.time())
    return out


async def coiled_prewarm_loop():
    """后台预热(整池日K首扫慢), 盘中每 15min 刷新, 非盘中每小时。"""
    await asyncio.sleep(40)
    while True:
        try:
            await scan_coiled(force=True)
        except Exception:
            pass
        try:
            from services.market_data import is_trading_day_active
            interval = 900 if is_trading_day_active() else 3600
        except Exception:
            interval = 3600
        await asyncio.sleep(interval)


# ---------- 结构完好扫描("没砸下去"): 大盘调整中K线结构未破坏的强势票 ----------

async def _stage2_unbroken(c: dict, bench10: float) -> dict | str:
    """K线结构体检: 距高点回撤浅 + 近10日无放量大阴 + 上行结构未破位 + 跑赢大盘。
    '没砸下去'=调整市里主力没撤的客观痕迹; 纯结构描述, 不构成买卖建议。"""
    from services.market_data import get_historical_data
    from services.stock_agent import _structure_scan
    try:
        df = await get_historical_data(c["code"], days=120)
    except Exception:
        return "K线不可达"
    if df is None or len(df) < 45:
        return "K线不足"
    closes = [float(v) for v in df["收盘"] if v]
    if len(closes) < 45:
        return "K线不足"
    try:
        highs = [float(v) for v in df["最高"]]
        lows = [float(v) for v in df["最低"]]
        vols = [float(v) for v in df["成交量"]]
    except Exception:
        highs = lows = [None] * len(closes)
        vols = [0] * len(closes)
    last = closes[-1]
    hi60 = max(closes[-60:])
    dist_high = round((last / hi60 - 1) * 100, 1)
    if dist_high < -12:
        return "距60日高回撤>12%(已被砸)"
    # 近10日单日大阴(收盘跌≥6%)= 砸过
    for i in range(max(1, len(closes) - 10), len(closes)):
        if closes[i] / closes[i - 1] - 1 <= -0.06:
            return "近10日有大阴(已被砸)"
    st = _structure_scan(closes, highs, lows, vols)
    if st.get("结构破位") or st.get("跌破颈线") or st.get("2B假突破"):
        return "结构破位/假突破"
    ma20 = sum(closes[-20:]) / 20
    uptrend = st.get("阶梯式上行") or st.get("抬高低点") or (last > ma20 and dist_high >= -8)
    if not uptrend:
        return "非上行结构"
    pct10 = (last / closes[-11] - 1) * 100 if len(closes) >= 11 else 0
    rs10 = round(pct10 - bench10, 1)
    if rs10 < -1:
        return "近10日明显跑输大盘"
    tags = [k for k in ("阶梯式上行", "抬高低点", "2B假破位", "突破底颈线") if st.get(k)]
    if dist_high >= -3:
        tags.append("贴近60日高")
    score = (rs10 * 2 + (12 + dist_high)
             + (8 if st.get("阶梯式上行") else 4 if st.get("抬高低点") else 0)
             + (4 if last > ma20 else 0))
    return {**c, "距60日高%": dist_high, "近10日超额%": rs10,
            "标签": "·".join(tags) or "站上MA20", "评分": round(score),
            "台阶支撑": st.get("台阶支撑")}


async def scan_unbroken(force: bool = False) -> dict:
    """结构完好扫描主入口: 龙头池里K线没被砸的。10分钟缓存, 与蓄势共享池与单飞锁。"""
    c = _cache.get("unbroken")
    if not force and c and time.time() - c[1] < _TTL:
        return c[0]
    pool, fmap = await asyncio.gather(asyncio.to_thread(_clist_pool),
                                      asyncio.to_thread(_fund_hold_map))
    if not pool:
        return c[0] if c else {"error": "行情源暂不可达(东财抖动)"}
    universe = _build_universe(pool, fmap)
    try:
        from services.market_data import _fetch_benchmark_history
        bdf = await asyncio.to_thread(_fetch_benchmark_history, "sh000300", 15)
        bcl = [float(r["close"]) for _, r in bdf.iterrows()]
        bench10 = (bcl[-1] / bcl[-11] - 1) * 100 if len(bcl) >= 11 else 0.0
    except Exception:
        bench10 = 0.0
    sem = asyncio.Semaphore(8)

    async def _one(x):
        async with sem:
            return await _stage2_unbroken(x, bench10)

    results = await asyncio.gather(*[_one(x) for x in universe], return_exceptions=True)
    rows = [r for r in results if isinstance(r, dict)]
    from collections import Counter
    rejected = Counter(r for r in results if isinstance(r, str))
    fc_map = await asyncio.to_thread(_forecast_map)
    _attach_forecast(rows, fc_map)
    rows.sort(key=lambda r: -r["评分"])
    out = {"as_of": time.strftime("%Y-%m-%d %H:%M"), "rows": rows[:60],
           "universe": len(universe), "rejected": dict(rejected),
           "bench10": round(bench10, 1),
           "note": "结构完好观察池('K线没砸下去'): 龙头池(百亿+≥30家基金+盈利) → 距60日高回撤≤12%、"
                   "近10日无单日大阴(≥6%)、上行结构未破位(阶梯/抬高低点/站上MA20)、近10日不明显跑输沪深300。"
                   "标签带业绩预告凭据(预喜+6/预警-6)。结构完好只是当下事实, 随时可能被砸, "
                   "纯客观结构描述, 不构成任何买卖建议。"}
    _cache["unbroken"] = (out, time.time())
    return out
