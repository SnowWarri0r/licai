"""基金份额拆分: SPLIT 流水缩放 lot + raw/qfq 检测纯函数。"""
from services.external_ledger import compute_external_state
from services.external_assets import _split_factor_from_series


def _act(i, t, amount=0, shares=None, unit_price=None, date="2026-06-01"):
    return {"id": i, "action_type": t, "amount": amount, "shares": shares,
            "unit_price": unit_price, "trade_date": date, "status": "confirmed"}


def test_split_scales_lots_cost_unchanged():
    acts = [
        _act(1, "BUY", 31891.79, 18900, 1.6873, "2026-05-10"),
        _act(2, "SPLIT", 0, 2.0, None, "2026-07-06"),
    ]
    st = compute_external_state(acts, "FUND")
    assert abs(st["shares"] - 37800) < 1e-6            # 份额×2
    assert abs(st["cost_amount"] - 31891.79) < 0.01     # 成本不动
    assert st["realized_pnl"] == 0                      # 拆分不产生盈亏
    assert abs(st["lots"][0]["unit_price"] - 1.6873 / 2) < 1e-6


def test_split_then_partial_redeem_fifo_uses_scaled_unit():
    acts = [
        _act(1, "BUY", 10000, 2500, 4.0, "2026-05-10"),
        _act(2, "SPLIT", 0, 2.0, None, "2026-07-06"),
        # 拆分后 5000 份, 单价成本 2.0; 以 2.2 卖 1000 份 → realized = (2.2-2.0)*1000 = +200
        _act(3, "REDEEM", 2200, 1000, 2.2, "2026-07-07"),
    ]
    st = compute_external_state(acts, "FUND")
    assert abs(st["realized_pnl"] - 200) < 0.01
    assert abs(st["shares"] - 4000) < 1e-6
    assert abs(st["cost_amount"] - 8000) < 0.01


def test_split_only_scales_lots_before_split_date():
    acts = [
        _act(1, "BUY", 4000, 1000, 4.0, "2026-05-10"),
        _act(2, "SPLIT", 0, 2.0, None, "2026-07-06"),
        _act(3, "BUY", 2000, 1000, 2.0, "2026-07-07"),   # 拆分后按新价买入, 不受缩放
    ]
    st = compute_external_state(acts, "FUND")
    assert abs(st["shares"] - 3000) < 1e-6               # 2000(拆后) + 1000(新买)
    assert abs(st["cost_amount"] - 6000) < 0.01


def test_split_factor_detection():
    dates = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"]
    # 07-06 拆分 1拆3: raw 从 3.60 断崖到 1.21, qfq 平滑(当日实际涨 ~0.83%)
    raw = [3.55, 3.58, 3.60, 1.21]
    qfq = [1.1833, 1.1933, 1.2000, 1.2100]
    hit = _split_factor_from_series(dates, raw, qfq)
    assert hit is not None
    day, f = hit
    assert day == "2026-07-06" and f == 3.0              # 贴近整数取整

    # 无拆分的普通波动不误报
    assert _split_factor_from_series(dates, qfq, qfq) is None


def test_cycle_realized_dilutes_cost_same_day_rebuy():
    """日内卖光再买回: 亏损摊进新仓摊薄成本(不重置)。"""
    acts = [
        _act(1, "BUY", 7661.72, 5700, 1.3441, "2026-07-01"),
        _act(2, "REDEEM", 7005.3, 5700, 1.229, "2026-07-06"),   # 亏 -656.42, 卖光
        _act(3, "ADD", 4224.73, 3500, 1.2071, "2026-07-06"),    # 同日买回 → 周期延续
    ]
    st = compute_external_state(acts, "FUND")
    assert abs(st["cycle_realized"] - (-656.07)) < 0.01   # FIFO按lot单价配成本
    assert abs(st["diluted_cost"] - (4224.73 + 656.07)) < 0.01   # 亏损抬高摊薄成本
    assert abs(st["realized_pnl"] - (-656.07)) < 0.01            # 总已实现口径不变


def test_cycle_realized_resets_after_overnight_flat():
    """隔夜空仓 → 新周期: 老盈亏不再摊进成本, 但仍在 realized_pnl 总账里。"""
    acts = [
        _act(1, "BUY", 2424.2, 1400, 1.7316, "2026-06-03"),
        _act(2, "REDEEM", 2254.0, 1400, 1.61, "2026-06-12"),    # 亏 -170.2, 卖光
        _act(3, "BUY", 6616.13, 3800, 1.741, "2026-06-17"),     # 隔了几天再建仓 → 重置
    ]
    st = compute_external_state(acts, "FUND")
    assert st["cycle_realized"] == 0
    assert abs(st["diluted_cost"] - 6616.13) < 0.01              # 新周期摊薄 = lot 成本
    assert abs(st["realized_pnl"] - (-170.24)) < 0.01


def test_etf_xray_theme_classify_and_match():
    """主题分类(行业/宽基/风格/跨境)与成分匹配规则。"""
    from services.etf_xray import classify_theme, _matches
    assert classify_theme("通信ETF国泰")[1] == "行业主题"
    assert classify_theme("红利低波ETF华泰柏瑞")[1] == "风格策略"
    assert classify_theme("科创50ETF华夏")[1] == "宽基指数"
    assert classify_theme("黄金ETF华安")[1] == "跨境商品债"
    assert classify_theme("纳斯达克100ETF联接QDII")[1] == "跨境商品债"
    # 行业匹配: 同义词表 + 双向包含
    assert _matches("通信", "通信设备", "中兴通讯")
    assert not _matches("通信", "消费电子", "工业富联")
    assert _matches("家电", "白色家电", "美的集团")
    assert _matches("半导体设备", "半导体", "北方华创")
    assert _matches("创新药", "生物制品", "百济神州")


def test_etf_xray_compound_theme_matches_via_substring_key():
    """复合主题词(科创创新药)命中同义词表里的子串键(创新药)。"""
    from services.etf_xray import _matches
    assert _matches("科创创新药", "化学制药", "百利天恒")
    assert _matches("科创创新药", "生物制品", "君实生物")
    assert not _matches("科创创新药", "半导体", "寒武纪")


def test_etf_xray_industry_overrides_stock_name():
    """行业已知时行业说了算: 名字带主题词但行业偏题的股票判偏题(信维通信案)。"""
    from services.etf_xray import _matches
    assert not _matches("通信", "消费电子", "信维通信")
    assert not _matches("半导体", "汽车零部件", "XX半导体")
    # 行业查不到(港股/北交所)才允许名字兜底
    assert _matches("通信", "非A股/未知", "中国通信服务")
    assert not _matches("通信", "非A股/未知", "腾讯控股")


def test_curve_share_balance_split_and_final_scale():
    """曲线份额时间线: SPLIT 折算余额; 折算到现行标度后与前复权价同标度。"""
    from services.portfolio_curve import share_balance_series, adjust_to_final_scale
    dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
    acts = [
        {"id": 1, "action_type": "BUY", "shares": 100, "trade_date": "2026-01-05", "status": "confirmed"},
        {"id": 2, "action_type": "SPLIT", "shares": 2, "trade_date": "2026-01-07", "status": "confirmed"},
    ]
    bal = share_balance_series(acts, dates)
    assert bal == [100, 100, 200]
    assert adjust_to_final_scale(acts, dates, bal) == [200, 200, 200]


def test_curve_twr_ignores_flows():
    """TWR: 纯入金不产生收益; 无流量时等于市值涨幅。"""
    from services.portfolio_curve import twr_series, max_drawdown
    assert twr_series([100, 200, 300], [0, 100, 100]) == [100, 100.0, 100.0]
    tw = twr_series([100, 200], [0, 0])
    assert abs(tw[-1] - 200) < 1e-6
    assert max_drawdown([100, 120, 90, 110]) == -25.0


def test_structure_2b_rules():
    """2B法则: 冲过前高又收回其下=假突破; 击穿前低又收回其上=假破位。"""
    from services.stock_agent import _structure_scan
    # 2B顶: 前高10.0(idx~8), 末端冲到10.3后收盘跌回9.6
    closes = [9.0, 9.2, 9.4, 9.6, 9.8, 9.9, 9.95, 9.9, 10.0, 9.8, 9.6, 9.5,
              9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 10.0, 10.1, 10.25, 10.1, 9.9,
              9.75, 9.65, 9.6]
    highs = [c * 1.005 for c in closes]
    highs[20] = 10.3          # 突破前高 10.05
    lows = [c * 0.995 for c in closes]
    vols = [100] * len(closes)
    st = _structure_scan(closes, highs, lows, vols)
    assert "2B假突破" in st, st
    # 2B底: 前低9.0(中段), 末端下探8.8后收盘收回9.3
    closes2 = [10.8, 10.65, 10.5, 10.3, 10.1, 9.9, 9.6, 9.3, 9.1, 9.0, 9.1, 9.3, 9.5, 9.6,
               9.5, 9.4, 9.3, 9.2, 9.1, 9.0, 8.95, 8.85, 8.9, 9.1, 9.25, 9.3]
    highs2 = [c * 1.005 for c in closes2]
    lows2 = [c * 0.995 for c in closes2]
    lows2[21] = 8.8
    st2 = _structure_scan(closes2, highs2, lows2, [100] * len(closes2))
    assert "2B假破位" in st2, st2
