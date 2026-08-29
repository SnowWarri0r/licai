from services.chart_render import flat_bar_colors, _UP, _DOWN, _FLAT


def test_flat_bar_colors_limit_up_is_red():
    """一字涨停(开=收=高=低)在 mplfinance 里落到 open<close 的 else 分支被画成绿柱。

    实测蓝盾光电(300862) 2026-08-10~08-13 四个一字板全是绿的。这里按昨收判回红。
    """
    #        昨收    一字涨停           一字跌停
    opens = [20.00, 22.00, 24.20, 21.78]
    closes = [20.00, 22.00, 24.20, 21.78]
    got = flat_bar_colors(opens, closes, start=1)
    assert got == [_UP, _UP, _DOWN]


def test_flat_bar_colors_leaves_normal_candles_alone():
    """有实体的 K 线不覆盖颜色, 交回 mplfinance 按 开/收 判定。"""
    opens = [10.0, 10.0, 11.0]
    closes = [10.0, 11.0, 10.5]
    assert flat_bar_colors(opens, closes, start=0) == [None, None, None]


def test_flat_bar_colors_flat_day_is_neutral():
    """开=收且与昨收持平(平盘/停牌): 既不是涨也不是跌, 用中性灰而不是默认的绿。"""
    opens = [10.0, 10.0]
    closes = [10.0, 10.0]
    assert flat_bar_colors(opens, closes, start=1) == [_FLAT]
    # 序列第一根没有昨收可比, 不覆盖
    assert flat_bar_colors(opens, closes, start=0)[0] is None


def test_flat_bar_colors_length_matches_display_window():
    """覆盖色列表必须和展示窗口等长——mplfinance 对 marketcolor_overrides 长度不符会直接抛错。"""
    opens = [10.0] * 8
    closes = [10.0] * 8
    assert len(flat_bar_colors(opens, closes, start=3)) == 5


# ── 量柱那一栏画的是什么, 得写在图上 ──────────────────────

def _bars(n=30, vol=50000.0):
    """升序日线: (date, close, high, low, vol, open)。"""
    out = []
    for i in range(n):
        c = 100.0 + i
        out.append((f"2026-08-{(i % 28) + 1:02d}", c, c + 1, c - 1, vol + i, c - 0.5))
    return out


def test_volume_panel_is_labeled():
    """下面那栏原来一个字都没有 —— 看图的人和模型只能猜是成交量还是成交额, 而两者差着
    "手×股价"的量级(实测茅台一天 2.2万手 / 28亿元)。图要喂给模型看, 所以口径必须画在轴上。

    判据: 换个标签, 出来的 PNG 就得不一样(说明标签真画进去了, 不是被忽略)。
    """
    from services.chart_render import render_trend_chart
    a = render_trend_chart(_bars(), code="600519", name="贵州茅台")
    b = render_trend_chart(_bars(), code="600519", name="贵州茅台", vol_label="成交额(元)")
    assert a and b
    assert a != b


def test_default_label_is_shares_not_turnover():
    """A股各源的成交量都已归一到「手」(新浪那条除以过 100), 所以默认标手。"""
    import inspect
    from services.chart_render import render_trend_chart
    assert inspect.signature(render_trend_chart).parameters["vol_label"].default == "成交量(手)"
