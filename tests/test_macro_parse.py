"""新浪宏观行情行解析: 昨收取错字段会让整块涨跌幅系统性偏小。

港股/美股的行里「今开」排在「昨收」前面, 原实现拿今开当昨收算涨跌幅, 实测恒生指数
被算成 +0.59%(真值 +1.34%)、纳斯达克 -0.45%(真值 -0.28%)。这里用实盘抓下来的行做回归,
判据是新浪自己在行内给出的涨跌幅字段。
"""
from services.market_data import _parse_macro_line

# 2026-08-17 实盘抓取
LINE_SH = ("上证指数,3930.1044,3927.1764,3982.6535,3983.5066,3924.4738,0,0,"
           "489834027,1112818620627,0,0,0,0")
LINE_HK = ("HSI,恒生指数,25303.410,25116.850,25578.270,25303.410,25453.230,336.381,"
           "1.339,0.00000,0.00000,210770397,12584632671,0.000")
LINE_US = ("纳斯达克,26729.1644,-0.28,2026-08-15 09:48:23,-73.8606,26851.1480,"
           "26862.1673,26661.9537,27190.2070,20690.2500,6405507373,7105167631,0,0.00")


def test_hk_prev_close_is_not_today_open():
    d = _parse_macro_line("hkHSI", LINE_HK)
    assert d["prev_close"] == 25116.85            # 昨收, 不是今开 25303.41
    assert abs(d["change_pct"] - 1.339) < 0.005   # 与新浪自带的涨跌幅字段吻合
    assert d["open"] == 25303.41


def test_hk_amount_is_thousands_of_hkd():
    """港股那两个大数字是「成交额(千元)」在前、「成交量(股)」在后, 顺序反了会把
    2107 亿的成交额显示成 125.8 亿。判据: fields[11]×1000 与腾讯分时末行的累计成交额相等。"""
    d = _parse_macro_line("hkHSI", LINE_HK)
    assert d["amount"] == 210770397 * 1000        # 腾讯累计额 210,770,397,254 HKD
    assert d["volume"] == 12584632671             # 成交量(股)


def test_us_has_volume_but_no_amount():
    """美股指数源里只有成交量(股), 没有成交额 —— 前端据此退一格显示成交量。"""
    d = _parse_macro_line("gb_ixic", LINE_US)
    assert d["volume"] == 6405507373
    assert "amount" not in d


def test_us_prev_close_backs_out_of_change():
    d = _parse_macro_line("gb_ixic", LINE_US)
    assert abs(d["change_pct"] - (-0.28)) < 0.005  # 新浪自带涨跌幅 -0.28
    assert abs(d["prev_close"] - 26803.03) < 0.01  # = 现价 - 涨跌额
    assert d["open"] == 26851.148                  # fields[5] 是今开


def test_a_index_carries_amount_and_ohl():
    d = _parse_macro_line("sh000001", LINE_SH)
    assert abs(d["change_pct"] - 1.413) < 0.005
    assert d["amount"] == 1112818620627.0          # 成交额(元), 放大图里显示成 1.11万亿
    assert d["volume"] == 489834027.0
    assert (d["open"], d["high"], d["low"]) == (3930.1044, 3983.5066, 3924.4738)


def test_zero_fields_are_dropped_not_zeroed():
    """盘前/停牌时高低量额是 0, 不能当成真值发给前端(会显示 今开 0.00)。"""
    d = _parse_macro_line("sh000001", "某指数,0,3927.1764,3982.6535,0,0,0,0,0,0")
    assert "open" not in d and "high" not in d and "amount" not in d
    assert d["price"] == 3982.6535 and d["prev_close"] == 3927.1764


def test_overseas_and_fx_unaffected():
    nk = _parse_macro_line("int_nikkei", "日经指数,44946.64,-408.35,-0.90")
    assert abs(nk["change_pct"] - (-0.90)) < 0.01
    assert "amount" not in nk
    fx = _parse_macro_line("fx_susdcnh", "20:00:00,7.1234,7.1240,0,0,7.1300,7.1310,7.1305")
    assert fx["price"] == 7.1234 and fx["prev_close"] == 7.13


# --- 腾讯分时(港股/美股指数): 源给累计值, 必须差分成逐分钟增量 ---
class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _fake_get(rows, code="hkHSI"):
    def _get(url, params=None, timeout=None):
        assert params and params.get("code") == code
        return _FakeResp({"data": {code: {"data": {"data": rows, "date": "20260817"}}}})
    return _get


def test_tencent_minute_diffs_cumulative_volume(monkeypatch):
    import services.market_data as md
    rows = [
        "0930 25303.410 238534 2385342863.120",
        "0931 25363.910 682009 6820085442.068",
        "0932 25334.170 998023 9980234870.355",
    ]
    monkeypatch.setattr(md._requests, "get", _fake_get(rows))
    d = md._minute_tencent("hkHSI")
    pts = d["points"]
    assert [p["time"] for p in pts] == ["09:30", "09:31", "09:32"]
    assert pts[0]["手"] == 238534                       # 首根就是它自己的累计值
    assert pts[1]["手"] == 682009 - 238534              # 之后是增量, 不是累计
    assert pts[2]["手"] == 998023 - 682009
    assert abs(pts[1]["额"] - (6820085442.068 - 2385342863.120)) < 1


def test_tencent_minute_us_rows_have_no_amount_column(monkeypatch):
    import services.market_data as md
    monkeypatch.setattr(md._requests, "get",
                        _fake_get(["1630 53700.10 100", "1631 53710.20 350"], code="usDJI"))
    d = md._minute_tencent("gb_dji")                    # gb_dji → usDJI 的映射
    assert [p["price"] for p in d["points"]] == [53700.10, 53710.20]
    assert d["points"][1]["手"] == 250 and d["points"][1]["额"] == 0.0


def test_tencent_minute_skips_junk_rows(monkeypatch):
    """收盘后源里会混进 "  0" 这种占位行, 混进去会画出一根 0 价的线。"""
    import services.market_data as md
    monkeypatch.setattr(md._requests, "get",
                        _fake_get(["  0", "0930 25303.410 238534 100.0", "bad row here"]))
    d = md._minute_tencent("hkHSI")
    assert len(d["points"]) == 1 and d["points"][0]["price"] == 25303.41


def test_tencent_minute_unsupported_symbol_is_none(monkeypatch):
    import services.market_data as md
    monkeypatch.setattr(md._requests, "get", _fake_get([]))
    assert md._minute_tencent("int_nikkei") is None     # 日经没有腾讯分时, 不发请求


# --- 环球指数(日经/KOSPI/FTSE): hq.sinajs 那几个符号停更了, 要用 gi.finance 交叉校验 ---
_GI_NKY = {"result": {"data": [
    ["08:00", "68882.0600", "0.0000", "0", "2026-08-17", "68713.8000"],
    ["08:01", "68951.8300", "0.0000", "0"],
    ["10:30", "69009.4400", "0.0000", "0"],
    ["11:30", "68900.0000", "0.0000", "0"],
    ["14:30", "69170.4100", "0.0000", "0"],
]}}


def _gi_only(payload, sym_ok="NKY"):
    """只放行 gi.finance/hq/min 的请求, 其余原样报错(确保测的是这条路径)。"""
    def _get(url, params=None, timeout=None, headers=None):
        assert "gi.finance.sina.com.cn/hq/min" in url and params["symbol"] == sym_ok
        class R:
            def json(self_inner):
                return payload
        return R()
    return _get


def test_gi_minute_derives_session_from_first_point(monkeypatch):
    """时段起点按当天首个分时点推(日经 08:00 北京时间), 不写死时钟 —— 夏令时切换才不用改表。"""
    import services.market_data as md
    monkeypatch.setattr(md._requests, "get", _gi_only(_GI_NKY))
    d = md._minute_sina_global("int_nikkei")
    assert d["date"] == "2026-08-17" and d["prev_close"] == 68713.8
    assert d["price"] == 69170.41                       # 末点即最新价
    assert d["session"]["am"] == [480, 630]             # 08:00-10:30
    assert d["session"]["pm"] == [690, 870]             # 11:30-14:30
    assert d["session"]["labels"] == ["08:00", "10:30/11:30", "14:30"]
    assert all(p["手"] == 0 for p in d["points"])       # 源不给成交量


def test_gi_minute_continuous_market_has_no_afternoon(monkeypatch):
    """伦敦无午休: pm=None, 且收盘时刻可以越过 24:00(冬令时落到北京时间次日)。"""
    import services.market_data as md
    payload = {"result": {"data": [["16:00", "10749.95", "0", "0", "2026-08-17", "10750.11"],
                                   ["21:00", "10739.40", "0", "0"]]}}
    monkeypatch.setattr(md._requests, "get", _gi_only(payload, sym_ok="UKX"))
    d = md._minute_sina_global("int_ftse")
    assert d["session"]["pm"] is None
    assert d["session"]["am"] == [960, 1470]            # 16:00 → 次日 00:30
    assert d["session"]["labels"] == ["16:00", "20:15", "00:30"]


def test_gi_minute_unknown_symbol(monkeypatch):
    import services.market_data as md
    monkeypatch.setattr(md._requests, "get", _gi_only(_GI_NKY))
    assert md._minute_sina_global("hkHSI") is None      # 只管日经/KOSPI/FTSE


def test_macro_replaces_stale_hq_quote(monkeypatch):
    """hq 报的日经点位与 gi 差 35% → 整块换成 gi; 差得少(KOSPI)则保留 hq 的官方收盘价。"""
    import services.market_data as md

    hq_body = ('var hq_str_int_nikkei="日经指数,44946.64,-408.35,-0.90";\n'
               'var hq_str_znb_KOSPI="首尔综合指数,6977.9400,164.60,2.42,2:27 AM,0,2026-08-14,"'
               ';\n')
    gi_kospi = {"result": {"data": [["08:00", "6995.67", "0", "0", "2026-08-14", "6813.3400"],
                                    ["14:30", "6970.07", "0", "0"]]}}

    def _get(url, params=None, timeout=None, headers=None):
        class R:
            text = hq_body
            encoding = "gbk"

            def json(self_inner):
                if params["symbol"] == "NKY":
                    return _GI_NKY
                if params["symbol"] == "KOSPI":
                    return gi_kospi
                return {"result": {"data": []}}
        return R()

    monkeypatch.setattr(md._requests, "get", _get)
    monkeypatch.setattr(md, "MACRO_SYMBOLS",
                        [("overseas_index", "int_nikkei", "日经225"),
                         ("overseas_index", "znb_KOSPI", "韩国KOSPI")])
    g = {it["symbol"]: it for it in md._fetch_macro_sina()["overseas_index"]}
    nk = g["int_nikkei"]
    assert nk["price"] == 69170.41 and nk["prev_close"] == 68713.8   # 换成 gi, 不再是 44946.64
    assert abs(nk["change_pct"] - 0.665) < 0.01
    ks = g["znb_KOSPI"]
    assert ks["price"] == 6977.94                                    # 官方收盘(含收盘竞价)保留
    assert ks["high"] == 6995.67 and ks["low"] == 6970.07             # 日内高低来自 gi 分时


def test_us_preopen_zeros_backfilled_from_daily(monkeypatch):
    """美股盘前新浪把涨跌与开高低清零, 直接算是「昨收=现价 +0.00%」(看着像平盘, 实际没开盘)。
    这段窗口应改用日K倒数两根补出上一交易日涨跌。"""
    import services.market_data as md
    zeroed = ('var hq_str_gb_dji="道琼斯,53732.4102,0.00,2026-08-17 21:10:02,0.0000,0.0000,'
              '0.0000,0.0000,54744.3281,44579.0312,0,444937283,0,0.00";\n')

    class R:
        text = zeroed
        encoding = "gbk"

    monkeypatch.setattr(md._requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(md, "MACRO_SYMBOLS", [("us_index", "gb_dji", "道琼斯")])
    monkeypatch.setattr(md, "_kline_for_symbol",
                        lambda sym, n=30: [{"date": "2026-08-13", "close": 53839.99},
                                           {"date": "2026-08-14", "close": 53732.41}])
    it = md._fetch_macro_sina()["us_index"][0]
    assert it["prev_close"] == 53839.99
    assert abs(it["change_pct"] - (-0.2)) < 0.01        # 不再是 0.00%


# --- 美股指数分时: 腾讯对指数只回一个收盘快照点, 改用 Yahoo 的 1 分钟线 ---
def _yahoo_payload(gmtoffset=-14400):
    # 09:30 / 09:31 / 09:32 美东(EDT) 对应的 UTC 秒
    base = 1787059800          # 2026-08-18 09:30 EDT
    return {"chart": {"result": [{
        "meta": {"gmtoffset": gmtoffset, "exchangeTimezoneName": "America/New_York",
                 "chartPreviousClose": 53459.8},
        "timestamp": [base, base + 60, base + 120, base + 180],
        "indicators": {"quote": [{"close": [53272.98, None, 53300.5, 53310.0],
                                  "volume": [0, 1329031, 1134685, None]}]},
    }]}}


def test_yahoo_minute_converts_to_exchange_local_time(monkeypatch):
    import services.market_data as md

    class R:
        def json(self_inner):
            return _yahoo_payload()

    monkeypatch.setattr(md._requests, "get", lambda *a, **k: R())
    d = md._minute_yahoo("gb_dji")
    # close 为 None 的那根跳过(停牌/无成交的空槽), 其余按美东时刻落位
    assert [p["time"] for p in d["points"]] == ["09:30", "09:32", "09:33"]
    assert d["date"] == "2026-08-18"
    assert d["prev_close"] == 53459.8
    assert d["points"][1]["price"] == 53300.5
    assert d["points"][2]["手"] == 0.0            # volume 缺失按 0, 不让整根丢掉


def test_yahoo_minute_follows_dst_offset(monkeypatch):
    """冬令时 gmtoffset 变成 -18000, 时刻要跟着源给的偏移走, 不写死。"""
    import services.market_data as md

    class R:
        def json(self_inner):
            return _yahoo_payload(gmtoffset=-18000)

    monkeypatch.setattr(md._requests, "get", lambda *a, **k: R())
    d = md._minute_yahoo("gb_ixic")
    assert [p["time"] for p in d["points"]] == ["08:30", "08:32", "08:33"]


def test_yahoo_minute_only_us_indices(monkeypatch):
    import services.market_data as md
    called = []
    monkeypatch.setattr(md._requests, "get", lambda *a, **k: called.append(1))
    assert md._minute_yahoo("hkHSI") is None
    assert md._minute_yahoo("sh000001") is None
    assert not called                              # 不是美股指数就别发请求
