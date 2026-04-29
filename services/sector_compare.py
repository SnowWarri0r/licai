"""板块雷达 — 用同花顺 (THS) 行业板块自动对标，避免硬编码维护.

数据源 cascade:
  1. THS 行业板块 (90 个细粒度: 能源金属/电池/光伏设备/铜/钴/...)
     - akshare stock_board_industry_summary_ths() 拉名单 + 实时涨跌
     - akshare stock_board_industry_index_ths() 拉历史 K 线
     - 比 EM 一级行业 (32 个) 细，比 EM 二级板块 (496 个) 稳定
  2. 硬编码 ETF (~35 个常见行业) - THS 失败时 fallback
  3. 沪深 300 - 都没匹配时 vs 大盘对标

新加股票 / 新行业不需要改代码 (THS 板块自动覆盖)。
"""
from __future__ import annotations
import asyncio
import os
import time
from datetime import datetime, timedelta

# 在 import akshare 前清掉 proxy env, 避免它的 session trust_env 走代理
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)


# --- Caches ---
_BOARDS_TTL = 24 * 3600
_ths_boards: dict | None = None
_ths_boards_ts: float = 0
_ths_boards_lock = asyncio.Lock()

_KLINE_TTL = 600  # 10 min, 同板块多只持仓共享
_kline_cache: dict[str, tuple[list, float]] = {}

_COMPARE_TTL = 1800
_compare_cache: dict[str, tuple[dict, float]] = {}


# --- 硬编码 ETF (THS 不可用时 fallback) ---
INDUSTRY_TO_ETF: dict[str, tuple[str, str]] = {
    "有色金属":   ("512400", "有色金属ETF"),
    "贵金属":     ("518880", "黄金ETF"),
    "钢铁":       ("515210", "钢铁ETF"),
    "煤炭":       ("515220", "煤炭ETF"),
    "石油石化":   ("159930", "能源ETF"),
    "化工":       ("159870", "化工ETF"),
    "建筑装饰":   ("516970", "基建ETF"),
    "建筑材料":   ("159745", "建材ETF"),
    "房地产":     ("512200", "地产ETF"),
    "银行":       ("512800", "银行ETF"),
    "非银金融":   ("512000", "证券ETF"),
    "证券":       ("512000", "证券ETF"),
    "保险":       ("512070", "保险ETF"),
    "医药生物":   ("512010", "医药ETF"),
    "医药":       ("512010", "医药ETF"),
    "电子":       ("512480", "半导体ETF"),
    "半导体":     ("512480", "半导体ETF"),
    "计算机":     ("512720", "计算机ETF"),
    "通信":       ("515880", "通信ETF"),
    "传媒":       ("512980", "传媒ETF"),
    "汽车":       ("516110", "汽车ETF"),
    "新能源车":   ("515030", "新能源车ETF"),
    "电力设备":   ("515790", "新能源ETF"),
    "电气设备":   ("515790", "新能源ETF"),
    "电源设备":   ("159755", "电池ETF"),
    "机械设备":   ("562500", "机械ETF"),
    "国防军工":   ("512660", "军工ETF"),
    "食品饮料":   ("515170", "食品饮料ETF"),
    "白酒":       ("512690", "酒ETF"),
    "家用电器":   ("159996", "家电ETF"),
    "纺织服饰":   ("159771", "纺织服装ETF"),
    "农林牧渔":   ("159825", "农业ETF"),
    "环保":       ("512580", "环保ETF"),
    "交通运输":   ("159666", "运输ETF"),
    "公用事业":   ("159611", "电力ETF"),
    "商贸零售":   ("159928", "消费ETF"),
}
DEFAULT_FALLBACK_ETF = ("510300", "沪深300")


# --- THS 板块加载 ---

def _fetch_ths_boards_sync() -> list[str]:
    """拉 THS 所有行业板块名。返回 list[str]."""
    try:
        import akshare as ak
        df = ak.stock_board_industry_summary_ths()
        if df is None or df.empty:
            return []
        return df["板块"].astype(str).tolist()
    except Exception as e:
        print(f"[ths-boards] failed: {e}")
        return []


async def _load_ths_boards(force: bool = False) -> list[str]:
    """串行化板块表加载。"""
    global _ths_boards, _ths_boards_ts
    if not force and _ths_boards and time.time() - _ths_boards_ts < _BOARDS_TTL:
        return _ths_boards
    async with _ths_boards_lock:
        if not force and _ths_boards and time.time() - _ths_boards_ts < _BOARDS_TTL:
            return _ths_boards
        boards = await asyncio.to_thread(_fetch_ths_boards_sync)
        if boards:
            _ths_boards = boards
            _ths_boards_ts = time.time()
    return _ths_boards or []


# EM 行业名 → THS 板块名的常见 keyword 映射 (THS 不用一级"有色金属"分类，
# 而用更细的"工业金属/贵金属/小金属/能源金属"). 这层是给 THS 匹配补的桥.
KEYWORD_HINTS: list[tuple[list[str], str]] = [
    # 金属类
    (["铜", "铝", "锌", "铅", "镍", "铁矿"], "工业金属"),
    (["金", "黄金", "银", "白银", "铂", "钯"], "贵金属"),
    (["锂", "钴", "稀土", "钨", "钼"], "能源金属"),  # 锂钴优先归能源金属
    (["小金属"], "小金属"),
    # 电池/新能源
    (["锂电", "动力电池", "电池"], "电池"),
    (["光伏", "硅料", "硅片"], "光伏设备"),
    (["风电"], "风电设备"),
    (["储能"], "电网设备"),
    (["逆变器"], "逆变器"),
    # 半导体/科技
    (["芯片", "半导体"], "半导体"),
    (["软件"], "软件开发"),
    (["云计算"], "通信设备"),
    # 医药
    (["创新药", "中药", "医药"], "化学制药"),
    (["医疗器械"], "医疗器械"),
    # 消费
    (["白酒"], "白酒"),
    (["啤酒"], "饮料制造"),
    (["乳业", "乳制品"], "乳业"),
    # 金融
    (["银行"], "银行"),
    (["券商", "证券"], "证券"),
    (["保险"], "保险"),
    # 材料
    (["钢"], "钢铁"),
    (["水泥"], "水泥"),
    (["玻璃"], "玻璃玻纤"),
    (["化工", "化纤"], "化学制品"),
    # 能源
    (["煤"], "煤炭"),
    (["石油", "原油"], "油气开采"),
    (["燃气"], "燃气"),
    # 电力 / 公用事业
    (["电力", "核电"], "电力"),
]


def _resolve_ths_board(industry: str, boards: list[str]) -> str | None:
    """行业字符串 → THS 板块名. 多层匹配:
       1. 精确等于板块名
       2. segment ∈ 板块名 (如 "电源" → "电源设备")
       3. KEYWORD_HINTS 关键词桥 (EM 行业名 → THS 板块名)
       4. 板块名 ∈ segment (反向兜底)
    """
    if not industry or not boards:
        return None
    parts = [p.strip() for p in industry.split("-") if p.strip()]
    # 1) Exact
    for p in parts:
        if p in boards:
            return p
    # 2) segment ∈ board (subseq)
    for p in parts:
        for name in boards:
            if p and p in name:
                return name
    # 3) Keyword hints
    industry_join = "/".join(parts)
    for keywords, board in KEYWORD_HINTS:
        if any(k in industry_join for k in keywords) and board in boards:
            return board
    # 4) Reverse: board ∈ segment
    for p in parts:
        for name in boards:
            if name and len(name) >= 2 and name in p:
                return name
    return None


def _resolve_etf_hardcoded(industry: str) -> tuple[str, str] | None:
    if not industry:
        return None
    parts = [p.strip() for p in industry.split("-") if p.strip()]
    for p in parts:
        if p in INDUSTRY_TO_ETF:
            return INDUSTRY_TO_ETF[p]
    return None


# --- THS 板块历史 K 线 ---

def _fetch_ths_kline_sync(board_name: str, days: int = 80) -> list[dict]:
    """拉 THS 板块历史日 K (带 cache)."""
    cached = _kline_cache.get(board_name)
    if cached and time.time() - cached[1] < _KLINE_TTL:
        return cached[0]
    try:
        import akshare as ak
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
        df = ak.stock_board_industry_index_ths(symbol=board_name, start_date=start, end_date=end)
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.tail(days).iterrows():
            try:
                rows.append({
                    "date": str(r["日期"])[:10],
                    "open": float(r["开盘价"]),
                    "close": float(r["收盘价"]),
                    "high": float(r["最高价"]),
                    "low": float(r["最低价"]),
                })
            except (ValueError, TypeError, KeyError):
                continue
        if rows:
            _kline_cache[board_name] = (rows, time.time())
        return rows
    except Exception as e:
        print(f"[ths-kline] {board_name} failed: {e}")
        return []


# --- helpers ---

def _industry_first_segment(industry: str) -> str:
    if not industry:
        return ""
    return industry.split("-")[0].strip()


def _close_series_pct(closes: list[float], n: int) -> float | None:
    if len(closes) < n + 1:
        return None
    last = closes[-1]
    prior = closes[-n - 1]
    if prior <= 0:
        return None
    return round((last / prior - 1) * 100, 2)


def _stock_close_series(df) -> list[float] | None:
    if df is None or df.empty:
        return None
    if "收盘" in df.columns:
        return df["收盘"].astype(float).tolist()
    if "close" in df.columns:
        return df["close"].astype(float).tolist()
    return None


# --- 主入口 ---

async def get_sector_compare(stock_code: str, force: bool = False) -> dict:
    now = time.time()
    if not force:
        cached = _compare_cache.get(stock_code)
        if cached and now - cached[1] < _COMPARE_TTL:
            return cached[0]

    from services.market_data import _lookup_industry, get_historical_data

    industry = await asyncio.to_thread(_lookup_industry, stock_code)
    sector_label = _industry_first_segment(industry) or "未知"

    boards = await _load_ths_boards()
    matched_ths = _resolve_ths_board(industry, boards) if boards else None

    result: dict = {
        "stock_code": stock_code,
        "industry": industry or "",
        "sector_label": sector_label,
        # 字段名沿用 etf_* 前端兼容; 内容是 THS 板块 / 硬编码 ETF / 沪深300
        "etf_code": None, "etf_name": None,
        "stock_30d": None, "stock_60d": None,
        "etf_30d": None, "etf_60d": None,
        "alpha_30d": None, "alpha_60d": None,
        "etf_kline": [],
        "source": None,  # ths / etf / fallback
    }

    # Resolve cascade
    use_ths = False
    if matched_ths:
        board_id = matched_ths      # THS 用名字当 id
        board_name = matched_ths
        result["source"] = "ths"
        use_ths = True
    else:
        etf_match = _resolve_etf_hardcoded(industry)
        if etf_match:
            board_id, board_name = etf_match
            result["source"] = "etf"
        else:
            board_id, board_name = DEFAULT_FALLBACK_ETF
            result["source"] = "fallback"
            result["sector_label"] = f"{sector_label} (无对标，用沪深300)"

    result["etf_code"] = board_id
    result["etf_name"] = board_name

    # 拉股票 K 线 + 板块 K 线
    try:
        if use_ths:
            stock_df, board_kline = await asyncio.gather(
                get_historical_data(stock_code, days=80),
                asyncio.to_thread(_fetch_ths_kline_sync, board_name, 80),
            )
            b_closes_list = [k["close"] for k in board_kline if k.get("close")]
        else:
            stock_df, etf_df = await asyncio.gather(
                get_historical_data(stock_code, days=80),
                get_historical_data(board_id, days=80),
            )
            board_kline = []
            b_closes_list = []
            if etf_df is not None and not etf_df.empty:
                col = etf_df["收盘"] if "收盘" in etf_df.columns else (etf_df["close"] if "close" in etf_df.columns else None)
                if col is not None:
                    b_closes_list = col.astype(float).tolist()
                date_col = "日期" if "日期" in etf_df.columns else "date"
                close_col = "收盘" if "收盘" in etf_df.columns else "close"
                for _, row in etf_df.tail(60).iterrows():
                    try:
                        board_kline.append({
                            "date": str(row.get(date_col, ""))[:10],
                            "close": float(row.get(close_col) or 0),
                        })
                    except (ValueError, TypeError):
                        continue
    except Exception as e:
        print(f"[sector] kline fetch failed for {stock_code}/{board_id}: {e}")
        _compare_cache[stock_code] = (result, now)
        return result

    s_closes = _stock_close_series(stock_df)
    b_closes = b_closes_list

    s30 = _close_series_pct(s_closes, 30) if s_closes else None
    s60 = _close_series_pct(s_closes, 60) if s_closes else None
    e30 = _close_series_pct(b_closes, 30) if b_closes else None
    e60 = _close_series_pct(b_closes, 60) if b_closes else None

    result["stock_30d"] = s30
    result["stock_60d"] = s60
    result["etf_30d"] = e30
    result["etf_60d"] = e60
    if s30 is not None and e30 is not None:
        result["alpha_30d"] = round(s30 - e30, 2)
    if s60 is not None and e60 is not None:
        result["alpha_60d"] = round(s60 - e60, 2)

    if board_kline:
        tail = board_kline[-min(60, len(board_kline)):]
        result["etf_kline"] = [{"date": k["date"], "close": k["close"]} for k in tail]

    _compare_cache[stock_code] = (result, now)
    return result
