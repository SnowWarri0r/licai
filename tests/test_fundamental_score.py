"""Tests for fundamental health scorer."""
from services.fundamental_score import classify_health, compute_score


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


def test_compute_score_all_positive():
    score = compute_score(
        sector_5d_perf=0.05,
        futures_5d_perf=0.03,
        llm_sentiment=0.8,
        announcement_score=1.0,
    )
    # 0.3*0.05 + 0.2*0.03 + 0.3*0.8 + 0.2*1.0 = 0.461
    assert 0.45 < score < 0.47


def test_compute_score_all_negative():
    score = compute_score(
        sector_5d_perf=-0.08,
        futures_5d_perf=-0.05,
        llm_sentiment=-0.7,
        announcement_score=-1.0,
    )
    # -0.444
    assert -0.45 < score < -0.43


def test_compute_score_missing_llm_defaults_to_zero():
    score = compute_score(sector_5d_perf=0.02, futures_5d_perf=0.01)
    # 0.008
    assert 0.005 < score < 0.01
