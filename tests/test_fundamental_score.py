"""Tests for fundamental health scorer."""
import pytest

from services.fundamental_score import (
    SECTOR_FULL_SCALE_PCT,
    classify_health,
    compute_score,
    norm_pct,
)


def test_classify_health_green():
    assert classify_health(0.7) == "green"
    assert classify_health(0.5) == "green"


def test_classify_health_yellow():
    assert classify_health(0.3) == "yellow"
    assert classify_health(-0.3) == "yellow"
    assert classify_health(0.0) == "yellow"


def test_classify_health_red():
    assert classify_health(-0.6) == "red"
    assert classify_health(-1.0) == "red"


# --- norm_pct: 百分数 → [-1, 1] ---

def test_norm_pct_maps_full_scale_to_one():
    assert norm_pct(SECTOR_FULL_SCALE_PCT, SECTOR_FULL_SCALE_PCT) == 1.0
    assert norm_pct(-SECTOR_FULL_SCALE_PCT, SECTOR_FULL_SCALE_PCT) == -1.0


def test_norm_pct_clamps_beyond_full_scale():
    # 板块 5 日 +12% 属极端行情, 截断到 1.0 而不是让它主导整个分数
    assert norm_pct(12.0, 5.0) == 1.0
    assert norm_pct(-30.0, 5.0) == -1.0


def test_norm_pct_passes_none_through():
    """None = 这路信号取不到, 必须一路透传给 compute_score, 不能变成 0.0。"""
    assert norm_pct(None, 5.0) is None


def test_norm_pct_scales_proportionally():
    assert norm_pct(2.5, 5.0) == 0.5
    assert norm_pct(-1.0, 5.0) == -0.2


# --- compute_score: 加权 + 缺失信号按剩余权重重新归一 ---

def test_compute_score_all_four_signals():
    score = compute_score(sector_perf=0.5, futures_perf=0.3,
                          news_sentiment=0.8, announcement_score=1.0)
    # 权重和恰为 1: 0.3*0.5 + 0.2*0.3 + 0.3*0.8 + 0.2*1.0 = 0.65
    assert score == pytest.approx(0.65)


def test_compute_score_all_negative():
    score = compute_score(sector_perf=-0.8, futures_perf=-0.5,
                          news_sentiment=-0.7, announcement_score=-1.0)
    # -0.24 - 0.10 - 0.21 - 0.20 = -0.75
    assert score == pytest.approx(-0.75)


def test_compute_score_renormalizes_over_available_signals():
    """行情两路取不到时, 情感两路要按 0.5 的存活权重重新归一 —— 否则分数被往黄灯拖。

    这是修过的 bug: 缺失信号当 0 用的话, 两路情感同时打满也只有
    0.3*1 + 0.2*1 = 0.5, 绿灯边界勉强够到, 稍弱一点就永远是黄灯。
    """
    score = compute_score(news_sentiment=1.0, announcement_score=1.0)
    assert score == pytest.approx(1.0)
    assert classify_health(score) == "green"

    score = compute_score(news_sentiment=-1.0, announcement_score=-1.0)
    assert score == pytest.approx(-1.0)
    assert classify_health(score) == "red"


def test_compute_score_single_signal_is_that_signal():
    assert compute_score(news_sentiment=0.6) == pytest.approx(0.6)
    assert compute_score(sector_perf=-0.9) == pytest.approx(-0.9)


def test_compute_score_missing_signal_is_not_treated_as_neutral():
    """「取不到」与「中性」必须给出不同的分。"""
    absent = compute_score(sector_perf=None, news_sentiment=1.0)
    neutral = compute_score(sector_perf=0.0, news_sentiment=1.0)
    assert absent == pytest.approx(1.0)      # 只剩新闻这一路, 归一后就是它本身
    assert neutral == pytest.approx(0.5)     # 0.3*0 + 0.3*1, 存活权重 0.6
    assert absent != neutral


def test_compute_score_no_signals_is_zero():
    assert compute_score() == 0.0


def test_percent_inputs_reach_the_bands():
    """回归: 行情信号必须先过 norm_pct, 否则它名义占一半权重、实际贡献万分之几。"""
    # 板块 5 日 -5%、商品当日 -5%, 情感两路中性 → 归一后 -1/-1 → -0.5, 恰在档位边界上
    # (classify_health 的红档是 < -0.5 而绿档是 >= 0.5, 边界归黄, 所以这里是黄不是红)
    score = compute_score(
        sector_perf=norm_pct(-5.0, SECTOR_FULL_SCALE_PCT),
        futures_perf=norm_pct(-5.0, SECTOR_FULL_SCALE_PCT),
        news_sentiment=0.0,
        announcement_score=0.0,
    )
    assert score == pytest.approx(-0.5)
    assert classify_health(score) == "yellow"

    # 再叠一点负面消息就跨进红灯 —— 归一之后行情信号是真能推动档位的
    worse = compute_score(
        sector_perf=norm_pct(-5.0, SECTOR_FULL_SCALE_PCT),
        futures_perf=norm_pct(-5.0, SECTOR_FULL_SCALE_PCT),
        news_sentiment=-0.5,
        announcement_score=0.0,
    )
    assert classify_health(worse) == "red"

    # 反向验证: 直接喂百分数(不归一)会把 -5% 读成 -5, 溢出 [-1,1] 语义
    wrong = compute_score(sector_perf=-5.0, futures_perf=-5.0,
                          news_sentiment=0.0, announcement_score=0.0)
    assert wrong == pytest.approx(-2.5)   # 越界, 说明归一这步不可省
