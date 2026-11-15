"""港美股按 GICS 子行业映射到申万二级, 让跨市场的同一行业在穿透里并成一堆。标的均为虚构。"""
from services import overseas_industry as oi
from services.exposure import _by_industry, _warnings


def _item(key, name, code, market, mv):
    return {"key": key, "name": name, "code": code, "market": market, "direct_mv": mv,
            "indirect_mv": 0.0, "total_mv": mv, "pct": 1.0, "via": []}


def test_overseas_chip_joins_ashare_semis():
    a = _item("CN:688999", "测试芯片A", "688999", "CN", 10000.0)
    u = _item("X:测试芯片U", "测试芯片U", "ZZCHIP", "US", 8000.0)
    imap = {"688999": ("测试芯片A", "半导体", 100.0)}
    ovs = {"X:测试芯片U": ("半导体", "半导体产品")}
    g = next(g for g in _by_industry([a, u], [], imap, 100000.0, [], ovs) if g["industry"] == "半导体")
    assert g["mv"] == 18000.0 and g["n"] == 2
    assert g["mapped"] == [{"name": "测试芯片U", "gics": "半导体产品"}]


def test_unmapped_subindustry_keeps_its_name_and_is_not_warned():
    u = _item("X:测试零售", "测试零售", "ZZRT", "US", 60000.0)
    inds = _by_industry([u], [], {}, 100000.0, [], {"X:测试零售": ("海外·多品类零售", "多品类零售")})
    assert inds[0]["industry"] == "海外·多品类零售" and "mapped" not in inds[0]
    assert _warnings([], [], inds, 100000.0) == []


def test_missing_lookup_falls_back_to_unclassified():
    u = _item("X:测试未知", "测试未知", "ZZUN", "HK", 5000.0)
    assert _by_industry([u], [], {}, 100000.0, [])[0]["industry"] == "海外(HK·未归行业)"


def test_label_for(monkeypatch):
    subs = {("US", "ZZCHIP"): "半导体产品", ("US", "ZZRT"): "多品类零售", ("HK", "09999"): None}
    monkeypatch.setattr(oi, "gics_sub", lambda c, m: subs.get((m, oi._bare(c))))
    assert oi.label_for("US.ZZCHIP", "US") == ("半导体", "半导体产品")
    assert oi.label_for("ZZRT", "US") == ("海外·多品类零售", "多品类零售")
    assert oi.label_for("HK.09999", "HK") == ("海外(HK·未归行业)", None)


def test_hk_uses_last_gics_level(monkeypatch):
    import services.foreign_f10 as f
    oi._cache.clear()
    monkeypatch.setattr(f, "hk_profile", lambda s: {"行业分类": "信息技术-半导体产品与设备-半导体产品与设备-半导体产品",
                                                   "industry": "半导体"})
    assert oi.gics_sub("HK.9998", "HK") == "半导体产品"
    oi._cache.clear()


def test_mapping_targets_exist_in_sw_level2():
    """映射目标必须是 A 股行业表里真实存在的申万二级名, 写错一个字就并不进去。"""
    sw = {"半导体", "软件开发", "IT服务Ⅱ", "通信设备", "元件", "游戏Ⅱ", "广告营销", "乘用车", "汽车零部件",
          "银行Ⅱ", "保险Ⅱ", "证券Ⅱ", "化学制药", "生物制品", "医疗器械", "医疗服务", "油气开采Ⅱ",
          "炼化及贸易", "非白酒", "酒店餐饮", "电力", "服装家纺", "贵金属", "工业金属", "煤炭开采",
          "航运港口", "物流", "航空机场"}
    assert set(oi.GICS_TO_SW.values()) <= sw
