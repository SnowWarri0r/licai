"""板块扫描器 — 列出全部 THS 板块的 1d/5d/30d 涨幅, 标记是否已持仓.

作用: 发现"未持仓但有动量"的板块. 不算推荐, 只给数据.

数据流:
  1. THS 板块汇总 (90 个) 给 1d 涨幅 + 净流入 + 上涨家数 + 领涨股
  2. 按 1d 涨幅 top N 拉 K 线 (复用 sector_compare 的 cache) → 5d / 30d
  3. 用持仓 stock_code 反查行业, 映射到 THS 板块, 标记 held
  4. 兜底 ETF 从 INDUSTRY_TO_ETF 找
"""
from __future__ import annotations
import asyncio
import time

from services.sector_compare import (
    _load_ths_boards,
    _fetch_ths_kline_sync,
    _resolve_ths_board,
    INDUSTRY_TO_ETF,
)


_SUMMARY_TTL = 300
_summary_cache: tuple[list[dict], float] | None = None
_summary_lock = asyncio.Lock()

_KLINE_TOP_N = 999  # 全部 90 个板块都拉 K 线 (复用 sector_compare 的 _kline_cache, 命中率高)
_KLINE_CONCURRENCY = 12


def _fetch_summary_sync() -> list[dict]:
    try:
        import akshare as ak
        df = ak.stock_board_industry_summary_ths()
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            try:
                rows.append({
                    "name": str(r["板块"]).strip(),
                    "change_1d": float(r["涨跌幅"]),
                    "net_flow": float(r.get("净流入", 0) or 0),  # 单位: 亿
                    "up_count": int(r.get("上涨家数", 0) or 0),
                    "down_count": int(r.get("下跌家数", 0) or 0),
                    "leader": str(r.get("领涨股", "")).strip(),
                    "leader_change": float(r.get("领涨股-涨跌幅", 0) or 0),
                })
            except (ValueError, TypeError, KeyError):
                continue
        return rows
    except Exception as e:
        print(f"[scanner] summary failed: {e}")
        return []


async def _load_summary(force: bool = False) -> list[dict]:
    global _summary_cache
    now = time.time()
    if not force and _summary_cache and now - _summary_cache[1] < _SUMMARY_TTL:
        return _summary_cache[0]
    async with _summary_lock:
        if not force and _summary_cache and now - _summary_cache[1] < _SUMMARY_TTL:
            return _summary_cache[0]
        rows = await asyncio.to_thread(_fetch_summary_sync)
        if rows:
            _summary_cache = (rows, time.time())
    return _summary_cache[0] if _summary_cache else []


def _close_pct(closes: list[float], n: int) -> float | None:
    if len(closes) < n + 1:
        return None
    last = closes[-1]
    prior = closes[-n - 1]
    if prior <= 0:
        return None
    return round((last / prior - 1) * 100, 2)


async def _enrich_kline(rows: list[dict], top_n: int, must_include: set[str]) -> None:
    """对 1d 涨幅 top_n 的板块拉 K 线算 5d/30d. must_include 里的板块无条件包含 (用户持仓).
    原地修改 rows."""
    top_names = {r["name"] for r in sorted(rows, key=lambda r: -r["change_1d"])[:top_n]}
    target_names = top_names | must_include
    targets = [r for r in rows if r["name"] in target_names]
    sem = asyncio.Semaphore(_KLINE_CONCURRENCY)

    # days=80 与 sector_compare 对齐, 避免 _kline_cache (key=board_name) 串话导致 60d 数据不足
    async def fetch_one(row: dict):
        async with sem:
            kline = await asyncio.to_thread(_fetch_ths_kline_sync, row["name"], 80)
            closes = [k["close"] for k in kline if k.get("close")]
            row["change_5d"] = _close_pct(closes, 5)
            row["change_30d"] = _close_pct(closes, 30)
            # 最近 60 个 close 给前端画 sparkline
            tail = kline[-min(60, len(kline)):]
            row["kline_tail"] = [{"date": k["date"], "close": k["close"]} for k in tail]

    await asyncio.gather(*(fetch_one(r) for r in targets), return_exceptions=True)


# THS 细粒度板块 → 通用 ETF 兜底关键词表 (THS 板块名子串包含任一 keyword 就匹配).
# 顺序很重要: 越细的关键词放前面 (比如 "黄金" 在 "金" 前).
_BOARD_KEYWORD_ETF: list[tuple[list[str], tuple[str, str]]] = [
    # 金属 (THS 用细粒度: 能源金属/工业金属/贵金属/小金属)
    (["黄金", "贵金属", "白银"], ("518880", "黄金ETF")),
    (["能源金属", "工业金属", "小金属", "金属"], ("512400", "有色金属ETF")),
    # 化工 (THS: 化学制品/化学原料/化学纤维/塑料/橡胶/包装印刷)
    (["化学", "化纤", "塑料", "橡胶", "包装印刷"], ("159870", "化工ETF")),
    # 建材 (THS: 水泥/玻璃玻纤/装修建材)
    (["水泥", "玻璃", "建材", "装修"], ("159745", "建材ETF")),
    # 钢铁
    (["钢铁"], ("515210", "钢铁ETF")),
    # 煤炭
    (["煤炭", "焦炭"], ("515220", "煤炭ETF")),
    # 能源 (THS: 油气开采/油服工程)
    (["油气", "石油", "原油"], ("159930", "能源ETF")),
    # 公用事业 (THS: 电力/燃气/水务)
    (["电力", "燃气", "水务", "环境治理"], ("159611", "电力ETF")),
    # 环保
    (["环保"], ("512580", "环保ETF")),
    # 房地产
    (["房地产", "地产", "物业"], ("512200", "地产ETF")),
    # 建筑装饰 (THS: 房屋建设/基础建设/工程咨询)
    (["建筑", "工程咨询", "基础建设"], ("516970", "基建ETF")),
    # 银行 / 非银
    (["银行"], ("512800", "银行ETF")),
    (["证券", "券商", "多元金融"], ("512000", "证券ETF")),
    (["保险"], ("512070", "保险ETF")),
    # 医药 (THS: 化学制药/中药/生物制品/医疗器械/医疗服务/医药商业)
    (["医药", "中药", "化学制药", "生物制品", "医疗器械", "医疗服务"], ("512010", "医药ETF")),
    # 半导体 / 电子 (THS: 半导体/消费电子/光学光电子/其他电子/电子化学品/元件)
    (["半导体", "芯片"], ("512480", "半导体ETF")),
    (["消费电子", "光学光电子", "电子化学品", "其他电子", "电子元件"], ("512480", "半导体ETF")),
    # 计算机 (THS: 软件开发/IT服务/计算机设备)
    (["软件", "计算机", "IT服务"], ("512720", "计算机ETF")),
    # 通信 (THS: 通信设备/通信服务)
    (["通信"], ("515880", "通信ETF")),
    # 传媒 (THS: 出版/广告/影视/游戏/数字媒体)
    (["传媒", "出版", "广告", "影视", "游戏", "数字媒体"], ("512980", "传媒ETF")),
    # 互联网 / 商贸
    (["互联网", "电商"], ("159928", "消费ETF")),
    (["商贸", "零售", "贸易"], ("159928", "消费ETF")),
    (["旅游", "酒店", "景点", "餐饮"], ("159928", "消费ETF")),
    (["家居用品", "文娱用品", "个护用品", "化妆品"], ("159928", "消费ETF")),
    # 食品饮料 (THS: 白酒/饮料制造/乳业/食品加工/调味品)
    (["白酒"], ("512690", "酒ETF")),
    (["饮料", "乳业", "食品加工", "调味", "肉制品"], ("515170", "食品饮料ETF")),
    # 纺织服饰 (THS: 服装家纺/纺织制造/饰品)
    (["服装", "纺织", "饰品", "化纤"], ("159771", "纺织服装ETF")),
    # 家电 (THS: 白色家电/黑色家电/厨卫电器/小家电/照明设备)
    (["家电", "厨卫", "小家电", "照明"], ("159996", "家电ETF")),
    # 汽车 (THS: 乘用车/商用车/汽车零部件/汽车服务/摩托车)
    (["汽车", "乘用车", "商用车", "摩托车"], ("516110", "汽车ETF")),
    # 国防军工 (THS: 航空/航天/船舶/兵器/军工电子)
    (["军工", "国防", "航空", "航天", "船舶", "兵器"], ("512660", "军工ETF")),
    # 机械 (THS: 通用设备/专用设备/工程机械/自动化设备/轨交设备)
    (["机械", "通用设备", "专用设备", "自动化设备", "轨交设备"], ("562500", "机械ETF")),
    # 农林牧渔
    (["农", "种植", "养殖", "渔", "饲料", "动物"], ("159825", "农业ETF")),
    # 交通运输 (THS: 公路/铁路/航空机场/物流/港口/航运)
    (["运输", "物流", "港口", "铁路", "航运", "公路", "机场"], ("159666", "运输ETF")),
    # 新能源相关 (THS: 风电设备/光伏设备/电池/电网设备/电机/其他电源设备)
    (["风电", "光伏", "电池", "电网", "电机", "电源"], ("515790", "新能源ETF")),
]


def _resolve_etf_for_board(board_name: str) -> tuple[str, str] | None:
    """THS 板块名 → 兜底 ETF.

    Cascade: (1) 直接在 INDUSTRY_TO_ETF 里 (2) 关键词包含匹配 _BOARD_KEYWORD_ETF
    """
    if not board_name:
        return None
    if board_name in INDUSTRY_TO_ETF:
        return INDUSTRY_TO_ETF[board_name]
    for keywords, etf in _BOARD_KEYWORD_ETF:
        if any(k in board_name for k in keywords):
            return etf
    return None


async def _resolve_held_boards(held_codes: list[str]) -> set[str]:
    if not held_codes:
        return set()
    from services.market_data import _lookup_industry
    boards_list = await _load_ths_boards(force=False)
    if not boards_list:
        return set()
    industries = await asyncio.gather(*(
        asyncio.to_thread(_lookup_industry, c) for c in held_codes
    ), return_exceptions=True)
    held: set[str] = set()
    for ind in industries:
        if isinstance(ind, Exception) or not ind:
            continue
        board = _resolve_ths_board(ind, boards_list)
        if board:
            held.add(board)
    return held


async def scan_sectors(held_codes: list[str], force: bool = False) -> dict:
    held_boards = await _resolve_held_boards(held_codes)
    rows = await _load_summary(force=force)
    if not rows:
        return {"sectors": [], "total": 0, "held_boards": sorted(held_boards), "kline_top_n": 0}

    await _enrich_kline(rows, top_n=_KLINE_TOP_N, must_include=held_boards)

    for r in rows:
        r["held"] = r["name"] in held_boards
        etf = _resolve_etf_for_board(r["name"])
        r["etf_code"] = etf[0] if etf else None
        r["etf_name"] = etf[1] if etf else None
        r.setdefault("change_5d", None)
        r.setdefault("change_30d", None)
        r.setdefault("kline_tail", [])

    # Sort: 5d desc (None last), tiebreak 1d desc
    def sort_key(r: dict):
        c5 = r.get("change_5d")
        return (-(c5 if c5 is not None else -999), -r["change_1d"])
    rows.sort(key=sort_key)

    return {
        "sectors": rows,
        "total": len(rows),
        "held_boards": sorted(held_boards),
        "kline_top_n": _KLINE_TOP_N,
    }
