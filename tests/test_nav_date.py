"""场外基金按哪天净值确认: 15:00 为界, 非交易日顺延。"""
import pytest

from services.external_assets import nav_date_for


@pytest.mark.parametrize("trade_date,trade_time,expected", [
    ("2026-09-22", "14:59", "2026-09-22"),     # 交易日 15:00 前 → 当日
    ("2026-09-22", "15:00", "2026-09-23"),     # 15:00 整已算收市后
    ("2026-09-22", "18:14", "2026-09-23"),     # 实测 017731 周二晚上赎回, 按周三净值
    ("2026-09-22", None, "2026-09-22"),        # 没有提交时间(定投/老记录) → 当日
    ("2026-09-25", None, "2026-09-28"),        # 中秋休市那天定投 → 顺延(旧逻辑查不到净值, 永久 pending)
    ("2026-09-26", "10:00", "2026-09-28"),     # 周六
    ("2026-09-30", "16:00", "2026-10-08"),     # 国庆前最后一个交易日收市后 → 节后第一天
    ("2026-10-03", "10:00", "2026-10-08"),     # 长假中
])
def test_nav_date_for(trade_date, trade_time, expected):
    assert nav_date_for(trade_date, trade_time) == expected
