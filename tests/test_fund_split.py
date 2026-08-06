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


def test_etf_xray_cloud_chain_counts_as_on_theme():
    """云计算是 IaaS→SaaS 一条链: 服务器 / IDC / 云网络 / 集成 / 云软件都算贴题。

    THEME_SYN 缺「云计算」键时只剩「主题词与行业名互相包含」的兜底, 字面对不上就
    全判偏题 —— 实测把 516510 云计算ETF易方达 打成 0.0%「偏离显著」, 是表缺条目
    不是基金挂羊头。同理补了 数据中心 / 算力 / 大数据 / 数字经济。
    """
    from services.etf_xray import _matches
    for ind in ("计算机设备", "通信服务", "通信设备", "IT服务", "软件开发"):
        assert _matches("云计算", ind, "中科曙光"), ind
    assert _matches("数据中心", "通信服务", "润泽科技")
    assert _matches("算力", "半导体", "寒武纪")
    # 仍要有鉴别力: 云计算 ETF 重仓白酒/银行照样判偏题
    assert not _matches("云计算", "白酒", "贵州茅台")
    assert not _matches("云计算", "银行", "招商银行")
    # 数字经济 ETF 前十大全是半导体设备 → 判偏题(实测 560800 匹配 0%)
    assert not _matches("数字经济", "半导体", "北方华创")


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


def test_curve_infers_untracked_cash_transfers():
    """现金/理财桶市值手填、转账不写流水 → 跳变必须补记成流量, 否则 TWR 当成盈亏。

    实测过的坑: 朝朝宝 2026-07-31 从 28490 转出到 3898, 曲线当天掉 6.4 个点。
    """
    from services.portfolio_curve import infer_cash_transfers, twr_series
    dates = ["d1", "d2", "d3"]

    # 1) 大额转出: 整笔判为流量, 当天收益归零
    vals, fl = [28490.0, 28490.0, 3898.0], {}
    assert infer_cash_transfers(vals, dates, fl) == ["d3"]
    assert abs(fl["d3"] - (-24592.0)) < 0.01
    tw = twr_series(vals, [fl.get(d, 0.0) for d in dates])
    assert abs(tw[-1] - 100.0) < 1e-6          # 转账不产生收益

    # 2) 带宽内的真实利息保留为收益, 不误判成转账
    vals, fl = [10080.0, 10082.0, 10085.0], {}
    assert infer_cash_transfers(vals, dates, fl) == []
    assert twr_series(vals, [0.0, 0.0, 0.0])[-1] > 100.0

    # 3) 已登记的存取流水不再重复补记
    vals, fl = [10000.0, 15000.0, 15000.0], {"d2": 5000.0}
    assert infer_cash_transfers(vals, dates, fl) == []
    assert fl == {"d2": 5000.0}

    # 4) 余额极小的桶用绝对下限兜底(2元→83元是入金, 不是 4000% 利息)
    vals, fl = [2.02, 83.0], {}
    assert infer_cash_transfers(vals, ["d1", "d2"], fl) == ["d2"]


def test_curve_accrues_interest_realized_at_redemption():
    """理财到期一次性结息: 赎回额超过本金的部分是应计利息, 必须摊回持有期。

    不摊的话账上只有本金 13876 却流出 14299, TWR 分母打成负数 → 收益蒸发。
    实测过的两笔: 中邮 +423.77 / 交银 +190.90 在曲线上完全看不见。
    """
    from services.portfolio_curve import (overdraw_accruals, cost_basis_series,
                                          accrued_on, twr_series, day_flows)
    acts = [
        {"id": 1, "action_type": "DEPOSIT", "amount": 20000.0, "trade_date": "2025-04-08"},
        {"id": 2, "action_type": "WITHDRAW", "amount": 6123.82, "trade_date": "2026-05-21"},
        {"id": 3, "action_type": "WITHDRAW", "amount": 14299.95, "trade_date": "2026-06-16"},
    ]
    # 部分赎回不算超额(本金还够); 只有最后清仓那笔透出 423.77
    bands = overdraw_accruals(acts)
    assert len(bands) == 1
    assert bands[0][0] == "2025-04-08" and bands[0][1] == "2026-06-16"
    assert abs(bands[0][2] - 423.77) < 0.01

    # 结息当天归零(钱已随本金流出), 前一天爬到接近满额
    assert accrued_on(bands, "2026-06-16") == 0.0
    assert accrued_on(bands, "2026-06-17") == 0.0
    assert accrued_on(bands, "2025-04-07") == 0.0
    assert 422.0 < accrued_on(bands, "2026-06-15") <= 423.77

    dates = ["2026-05-21", "2026-06-15", "2026-06-16"]
    vals = cost_basis_series(acts, dates, accrue=True)
    fl = day_flows(acts, dates)
    # 赎回前市值 ≈ 赎回金额(差额 ≤ 利息/持有天数), 于是赎回当天不产生假收益
    assert abs(vals[1] - 14299.95) < 2.0
    tw = twr_series(vals, [fl.get(d, 0.0) for d in dates])
    assert tw[1] > tw[0]          # 收益在爬升里逐日计入
    assert abs(tw[2] - tw[1]) < 1e-6   # 赎回当天 r=0

    # 没有超额赎回的资产完全不受影响
    plain = [{"id": 1, "action_type": "DEPOSIT", "amount": 100.0, "trade_date": "2026-01-01"}]
    assert overdraw_accruals(plain) == []
    assert cost_basis_series(plain, ["2026-01-01", "2026-01-02"], accrue=True) == [100.0, 100.0]
    # 默认关闭: 基金/ETF 的超额赎回是交易盈亏, 不能当利息再摊一遍
    assert cost_basis_series(acts, ["2026-06-15"]) == [13876.18]


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


def test_lhb_pick_latest_day_dedupe_and_sort():
    """龙虎榜日榜: 取最新披露日 + 同股多榜单口径去重(金额取绝对值最大, 原因合并) + 按净买额排序。"""
    from services.lhb_detail import _pick_latest_day
    recs = [
        {"上榜日": "2026-07-14", "代码": "000001", "名称": "老日期", "涨跌幅": 1, "收盘价": 10,
         "龙虎榜净买额": 5e8, "换手率": 3, "解读": "x", "上榜原因": "老"},
        {"上榜日": "2026-07-15", "代码": "000021", "名称": "深科技", "涨跌幅": -9.99, "收盘价": 47.26,
         "龙虎榜净买额": -3.9e8, "换手率": 11.66, "解读": "4家机构卖出", "上榜原因": "日跌幅偏离"},
        {"上榜日": "2026-07-15", "代码": "000021", "名称": "深科技", "涨跌幅": -9.99, "收盘价": 47.26,
         "龙虎榜净买额": -1.2e8, "换手率": 11.66, "解读": "4家机构卖出", "上榜原因": "日换手率达20%"},
        {"上榜日": "2026-07-15", "代码": "000566", "名称": "海南海药", "涨跌幅": 10.04, "收盘价": 5.15,
         "龙虎榜净买额": 7.8e7, "换手率": 8.38, "解读": "上海资金买入", "上榜原因": "日振幅"},
    ]
    day, rows = _pick_latest_day(recs)
    assert day == "2026-07-15"
    assert [r["code"] for r in rows] == ["000566", "000021"]      # 净买额降序, 老日期被丢弃
    sk = rows[1]
    assert sk["净买额亿"] == -3.9                                  # 取绝对值最大那条
    assert "日跌幅偏离" in sk["上榜原因"] and "日换手率达20%" in sk["上榜原因"]
    assert _pick_latest_day([]) == ("", [])


def test_exright_raw_price_on_inverse_transform():
    """除权逆变换: 前复权价 → 历史日真实标度。派息=加法, 送转=乘法, 多事件从近到远。"""
    import asyncio
    import time as _t
    from services import exright
    # 青岛银行案: 2026-06-24 每股派0.18, 无送转; 04-29 的前复权昨收 5.47 → 真实 5.65
    exright._ev_cache["T1"] = ([("2026-06-24", 0.18, 0.0)], _t.time())
    raw, n = asyncio.run(exright.raw_price_on("T1", 5.47, "2026-04-29"))
    assert (raw, n) == (5.65, 1)
    # 多事件: 06-24 派0.18 + 05-10 每股送转0.5 → 10 → ×1+0.18=10.18 → ×1.5=15.27
    exright._ev_cache["T2"] = ([("2026-05-10", 0.0, 0.5), ("2026-06-24", 0.18, 0.0)], _t.time())
    raw2, n2 = asyncio.run(exright.raw_price_on("T2", 10.0, "2026-05-01"))
    assert (raw2, n2) == (15.27, 2)
    # 目标日在事件之后: 不逆变换
    raw3, n3 = asyncio.run(exright.raw_price_on("T2", 10.0, "2026-06-25"))
    assert (raw3, n3) == (10.0, 0)
