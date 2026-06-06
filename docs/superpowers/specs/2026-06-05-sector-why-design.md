# 板块「为什么动」(Sector Catalyst) — 设计

## 目标

跨市场板块异动看板（SectorOpportunities，A/港/美 + 未持仓筛选）已存在。补上唯一缺的
「为什么动」层：点某板块 → LLM 用近期全球快讯合成「为什么动 + 跟你持仓/关注的关系」，
B 形态，只解释、不荐买卖。

属于「跨市场机会雷达」方向；GS/JPM 贴链接摘要是后续第 2 项, 不在本次范围。

## 范围

**做：** 板块行加「为什么动」按钮 → 后端 `POST /api/sector/why` LLM 合成 → 行内展开两段解读 + 缓存。
**不做（YAGNI）：** 不改板块扫描器本身；不做 GS/JPM 抓取；不预跑全板块解读（只点开时调）；不打分。

## 铁律：只解释，不荐买卖

system prompt 禁任何操作建议（买/卖/加/减/目标价/仓位）。跟 `news interpret` 同原则。

## 现实约束

A 股板块料最足（全球快讯 + 板块领涨股）；港股/美股板块只能靠全球快讯里提到的主题，
LLM 合成，质量偏弱。接受这个差异, panel 文案不夸大。

## 交互

SectorOpportunities 每行右侧加一个「?」/「为什么动」小按钮。点击 → 该行下方**行内展开**
一块解读区：
- loading：skeleton「解读生成中…」
- 成功：两段「**为什么动** · …」「**跟你的关系** · …」
- 失败/无凭证：「解读暂不可用」
再点收起。展开结果按会话缓存, 重复点不重算。

## 架构

### 后端 `POST /api/sector/why`（加到 `api/sector_routes.py`）

- 入参：`{market: "A"|"HK"|"US", name: str, change_1d: float|None, change_5d: float|None, held: bool, leader: str|None}`
- 取素材：调现有 `market_news()`（财联社/东财/同花顺全球快讯, ~50-80 条, 已 5min 缓存）取标题列表。
  （A 股可选再带 `leader` 领涨股名；港美股只用全球快讯。）
- 调 `services.llm_client.call_claude`（用模块属性 `_llm.call_claude` 以便测试 monkeypatch）。
- system prompt：禁买卖建议；输出严格 JSON `{"why":"为什么动(1-2句)","relation":"跟用户持仓/关注板块关系(没有就说无直接关系)"}`。
- 持仓上下文：`get_all_holdings()` 拼进 prompt（让 relation 能落到具体持仓）。
- 缓存：进程内 dict, key = `sha1(market|name|YYYY-MM-DD-HH)`（按小时桶, 因快讯随盘变）, 命中返回 `cached:true`。
- 永不 5xx：LLM 抛错返回 `{"why":"","relation":"","error":"解读暂不可用"}`。
- 非 JSON 兜底：原文塞进 why。

**请求示例**
```
POST /api/sector/why
{ "market":"A", "name":"有色金属", "change_1d":-2.3, "change_5d":-5.1, "held":true, "leader":"洛阳钼业" }
```
**响应示例**
```json
{ "why":"铜价隔夜下跌叠加美元走强, 有色金属普遍承压。",
  "relation":"你持有洛阳钼业/紫金矿业等有色股, 直接受板块情绪影响。",
  "cached": false }
```
LLM 不可用：
```json
{ "why":"", "relation":"", "error":"解读暂不可用", "cached": false }
```

### 前端（`SectorOpportunities.jsx`）

- 每行加「为什么动」按钮 + 一个 `expandedKey` state（记录当前展开的 `r.name`）。
- 点击 → toggle 展开；首次展开时 `POST /api/sector/why`（带该行 market/name/change/held/leader），
  结果存组件内 Map（key=`market:name`）缓存。
- 展开区渲染 why/relation 两段（skeleton / error 兜底）。

## 容错

- `market_news()` 失败/空 → 仍调 LLM（prompt 注明「近期无可用快讯」），或直接返回 error；不崩。
- 港美股 relation 常为「无直接关系」属正常。

## 测试

**后端（pytest, monkeypatch `services.llm_client.call_claude` + `market_news`）：**
- 正常 → `{why,relation}` 两段；二次同入参（同小时桶）命中缓存, call_claude 只调一次。
- LLM 抛错 → 200 + error 字段, 不 5xx。
- 非 JSON → 兜底进 why。

**手动：** A 股有色板块点「为什么动」→ 出合理解读 + 关联持仓；断网 → 「解读暂不可用」。

## 文件清单

- `api/sector_routes.py` — 加 `POST /why` 端点 + 缓存（复用 `news_routes.market_news`）。
- `frontend/src/components/SectorOpportunities.jsx` — 行内「为什么动」按钮 + 展开区 + fetch。
- `frontend/public/sw.js` — 版本 bump。
- `tests/test_sector_why.py` — 新建。
