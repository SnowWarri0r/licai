"""穿透敞口(同源风险)。

替掉的是一段关键词正则: 股票名里带「白银/黄金/有色/铜铝锌镍」才认得出, 于是
「兴业银锡」(银锡矿)一条都报不出来 —— 而这个账本里 山东黄金+兴业银锡 合起来是
总资产的 38.6%。这里测的都是那套关键词结构上不可能做到的事。
"""
from services.exposure import _fund_family, _key_of, _warnings


# ── 份额类别归并 ────────────────────────────────────────

def test_share_classes_collapse_to_one_fund():
    """A/C 份额是同一个投资组合, 只是费率不同。不并的话会报出"两只基金重叠 45%" ——
    那不是发现, 是废话; 而且同一个底层标的的"来源数"会被算成两个。"""
    same = ["易方达全球成长精选混合(QDII)人民币A", "易方达全球成长精选混合(QDII)人民币C"]
    assert len({_fund_family(n) for n in same}) == 1
    assert _fund_family("大成纳斯达克100ETF联接(QDII)A") == "大成纳斯达克100ETF联接(QDII)"
    assert _fund_family("摩根标普500指数(QDII)人民币A") == "摩根标普500指数(QDII)"


def test_different_funds_stay_separate():
    """名字相近但确实是两只基金 —— 归并过度会把真正的撞车藏起来。"""
    a = _fund_family("易方达信息行业精选股票C")
    b = _fund_family("易方达信息产业混合C")
    assert a != b


def test_etf_names_not_mangled():
    """ETF/LOF 结尾是 F, 不能被当成份额后缀吃掉。"""
    for n in ("半导体ETF南方", "纳斯达克ETF华安", "科创50ETF华夏", "日经225ETF华安"):
        assert _fund_family(n) == n


# ── 标的归并键 ──────────────────────────────────────────

def test_a_share_keyed_by_code():
    assert _key_of({"code": "600547", "name": "山东黄金", "market": "CN_SH"}) == "CN:600547"


def test_overseas_keyed_by_name():
    """同一家公司在不同基金的报表里代码写法不一(台积电 = TSM 的 ADR 或 2330 的台股),
    名字反而一致 —— 实测易方达全球成长同时持有这两种, 敞口必须合成一个。"""
    us = _key_of({"code": "TSM", "name": "台积电", "market": "US"})
    tw = _key_of({"code": "2330", "name": "台积电", "market": "US"})
    assert us == tw


# ── 结论文案 ────────────────────────────────────────────

def _ind(name, pct, direct=0.0, indirect=0.0, members=None, n=2):
    mv = (direct + indirect) or pct * 1000
    return {"industry": name, "pct": pct, "mv": mv, "direct_mv": direct,
            "indirect_mv": indirect, "members": members or ["甲", "乙"], "n": n}


def test_industry_concentration_reported():
    """这一条正是关键词那套的死角: 两只票一个带"黄金"一个不带, 靠行业表才归得到一起。"""
    ws = _warnings([], [], [_ind("贵金属", 38.6, direct=50300, members=["山东黄金", "兴业银锡"])], 130000)
    assert any(w["kind"] == "industry" and "贵金属" in w["text"] for w in ws)
    assert any("山东黄金" in w["text"] and "兴业银锡" in w["text"] for w in ws)


def test_industry_below_line_is_quiet():
    assert _warnings([], [], [_ind("通信设备", 1.9, indirect=2500)], 130000) == []


def test_overseas_and_unknown_industry_not_warned():
    """海外底层拿不到 A 股行业表, 未归行业的不能拿来当"行业集中度"报。"""
    for name in ("海外(US·未归行业)", "未知行业"):
        assert _warnings([], [], [_ind(name, 60.0, indirect=78000)], 130000) == []


def test_single_holding_needs_two_sources():
    """只直持、没被基金间接持有 → 不是"同源", 那是普通集中度(另有规则管)。"""
    one = {"name": "山东黄金", "code": "600547", "pct": 19.6, "total_mv": 25500,
           "direct_mv": 25500, "indirect_mv": 0, "n_sources": 1, "via": []}
    assert [w for w in _warnings([one], [], [], 130000) if w["kind"] == "single_lookthrough"] == []


def test_single_holding_two_sources_reported():
    it = {"name": "中际旭创", "code": "300308", "pct": 1.0, "total_mv": 1300,
          "direct_mv": 0, "indirect_mv": 1300, "n_sources": 3,
          "via": [{"fund": "甲基金", "weight_pct": 5.5, "mv": 700},
                  {"fund": "乙基金", "weight_pct": 5.4, "mv": 600}]}
    ws = [w for w in _warnings([it], [], [], 130000) if w["kind"] == "single_lookthrough"]
    assert ws and "中际旭创" in ws[0]["text"] and "甲基金" in ws[0]["text"]


def test_fund_twins_reported_only_above_line():
    hi = {"a": "甲", "b": "乙", "overlap_pct": 36.0, "n_same": 8, "same": ["中际旭创"], "mv": 12500}
    lo = {"a": "甲", "b": "丙", "overlap_pct": 12.6, "n_same": 3, "same": ["中际旭创"], "mv": 8000}
    ws = _warnings([], [hi, lo], [], 130000)
    twins = [w for w in ws if w["kind"] == "fund_twins"]
    assert len(twins) == 1 and "36%" in twins[0]["text"]


def test_warnings_sorted_high_first():
    hi = {"a": "甲", "b": "乙", "overlap_pct": 45.0, "n_same": 9, "same": ["x"], "mv": 5700}
    ws = _warnings([], [hi], [_ind("贵金属", 30.0, direct=39000)], 130000)
    assert [w["level"] for w in ws] == sorted([w["level"] for w in ws], key=lambda l: {"high": 0, "med": 1, "low": 2}[l])


# ── 同源要归到"账本里的哪几行" ─────────────────────────

# 8-24 的真实账本: 山东黄金 2.56万 + 兴业银锡 2.54万, 东财行业都是「贵金属」。
# 名字里只有前者带"黄金"二字 —— 所以关键词那套只给山东黄金挂了 ↔同源, 银锡干干净净,
# 看着像两注不相干的仓, 实际是同一块。
_SDHJ = {"name": "山东黄金", "code": "600547", "market": "CN", "direct_mv": 25600.0,
         "indirect_mv": 0.0, "total_mv": 25600.0, "pct": 19.6, "via": []}
_XYYX = {"name": "兴业银锡", "code": "000426", "market": "CN", "direct_mv": 25400.0,
         "indirect_mv": 0.0, "total_mv": 25400.0, "pct": 19.5, "via": []}
_IMAP = {"600547": ("山东黄金", "贵金属", 1590.0), "000426": ("兴业银锡", "贵金属", 694.0)}


def _ind_of(industries, name):
    return next((g for g in industries if g["industry"] == name), None)


def test_two_metal_stocks_land_in_one_family_by_industry():
    """靠东财行业归并, 名字里有没有金属字样都不影响。"""
    from services.exposure import _by_industry
    g = _ind_of(_by_industry([_SDHJ, _XYYX], [], _IMAP, 130000.0, []), "贵金属")
    assert {h["code"] for h in g["holders"]} == {"600547", "000426"}
    assert [h["kind"] for h in g["holders"]] == ["A", "A"]
    assert g["n_holders"] == 2


def test_holder_is_the_fund_row_not_the_underlying_stock():
    """间接敞口对应的持仓行是那只基金 —— 界面要把角标打在基金那行上。"""
    from services.exposure import _by_industry
    it = {"name": "中际旭创", "code": "300308", "market": "CN", "direct_mv": 0.0,
          "indirect_mv": 1300.0, "total_mv": 1300.0, "pct": 1.0,
          "via": [{"fund": "易方达信息产业混合", "fund_code": "019018", "weight_pct": 5.5, "mv": 1300.0}]}
    g = _ind_of(_by_industry([it], [{"code": "019018", "codes": ["019018", "019019"]}],
                             {"300308": ("中际旭创", "通信设备", 900.0)}, 130000.0, []), "通信设备")
    assert [(h["kind"], h["code"]) for h in g["holders"]] == [("F", "019018")]
    # A/C 两个份额的代码都带上, 界面上不管展示的是哪一行都能对上
    assert g["holders"][0]["codes"] == ["019018", "019019"]


def test_unpenetrated_gold_fund_joins_the_family_without_faking_the_number():
    """华夏黄金ETF联接 拉不到明细(实测 0 条)。它偏偏就是纯黄金 —— 不挂进来, 这块的同源
    就少了最该在里面的那一行。但它的钱不能并进"穿透后合计", 那是量出来的数。"""
    from services.exposure import _by_industry
    un = [{"code": "008701", "name": "华夏黄金ETF联接C", "mv": 5000.0, "why": "拉不到季报股票明细"}]
    g = _ind_of(_by_industry([_SDHJ, _XYYX], [], _IMAP, 130000.0, un), "贵金属")
    assert [h["name"] for h in g["holders"] if h.get("guessed")] == ["华夏黄金ETF联接C"]
    assert g["mv"] == 51000.0                      # 只有量出来的两只股票
    assert g["unpenetrated"][0]["mv"] == 5000.0    # 猜出来的单独放


def test_guess_only_covers_names_that_pin_the_asset_down():
    from services.exposure import _guess_industry
    assert _guess_industry("华夏黄金ETF联接C") == "贵金属"
    assert _guess_industry("国投白银期货LOF") == "贵金属"
    # 「银行」里有个银字, 猜错比不猜更糟; 「有色/商品」太泛, 一样不猜
    assert _guess_industry("天弘中证银行ETF联接C") == ""
    assert _guess_industry("华宝标普油气") == ""


def test_industry_warning_carries_the_industry_name_for_dedupe():
    """前端要靠这个字段避免把同一个行业再报一遍(它自己那条是按行来讲的)。"""
    ws = _warnings([], [], [_ind("贵金属", 38.6, direct=50300, members=["山东黄金", "兴业银锡"])], 130000)
    assert [w.get("industry") for w in ws if w["kind"] == "industry"] == ["贵金属"]


def test_unpenetrated_is_spelled_out_in_the_warning():
    """"另有 X 万拉不到明细, 未计入" —— 不说的话这条结论看着就是全量。"""
    g = _ind("贵金属", 38.6, direct=50300, members=["山东黄金", "兴业银锡"])
    g["unpenetrated"] = [{"code": "008701", "name": "华夏黄金ETF联接C", "mv": 5000.0}]
    text = [w["text"] for w in _warnings([], [], [g], 130000) if w["kind"] == "industry"][0]
    assert "另有 0.50万" in text and "华夏黄金ETF联接C" in text and "未计入" in text
