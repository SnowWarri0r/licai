# 板块「为什么动」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** SectorOpportunities 每行可点「为什么动」→ LLM 用全球快讯合成「为什么动+持仓关系」(不荐买卖)。

**Architecture:** 后端 `POST /api/sector/why`（复用 `news_routes.market_news` 取全球快讯 + `call_claude`，hash 缓存）；前端 SectorOpportunities 行内展开。

**Tech Stack:** FastAPI + `services.llm_client.call_claude`；React + Vite。venv `./venv`；前端 `cd frontend && npm run build`；后端 :8888。

Spec：`docs/superpowers/specs/2026-06-05-sector-why-design.md`

---

### Task 1: 后端 `POST /api/sector/why` + 缓存

**Files:** Modify `api/sector_routes.py`; Test `tests/test_sector_why.py`

- [ ] **Step 1: 失败测试** `tests/test_sector_why.py`

```python
from fastapi.testclient import TestClient
import services.llm_client as llm
import api.news_routes as news
from run import app

client = TestClient(app)


async def _fake_market_news():
    return {"items": [{"source": "财联社", "title": "铜价隔夜下跌", "content": "", "time": "2026-06-05 09:00"}]}


def test_why_returns_two_parts(monkeypatch):
    calls = {"n": 0}
    def fake(user_prompt, system=None, model=None, max_tokens=500):
        calls["n"] += 1
        return '{"why":"铜价下跌拖累","relation":"你持有有色股受影响"}'
    monkeypatch.setattr(llm, "call_claude", fake)
    monkeypatch.setattr(news, "market_news", _fake_market_news)
    body = {"market": "A", "name": "有色金属", "change_1d": -2.3, "change_5d": -5.1, "held": True, "leader": "洛阳钼业"}
    r = client.post("/api/sector/why", json=body)
    assert r.status_code == 200
    d = r.json()
    assert d["why"] and d["relation"]
    assert calls["n"] == 1
    r2 = client.post("/api/sector/why", json=body)
    assert r2.json().get("cached") is True
    assert calls["n"] == 1  # 同小时桶命中缓存


def test_why_llm_error_graceful(monkeypatch):
    monkeypatch.setattr(news, "market_news", _fake_market_news)
    def boom(*a, **k): raise RuntimeError("no creds")
    monkeypatch.setattr(llm, "call_claude", boom)
    r = client.post("/api/sector/why", json={"market": "US", "name": "信息技术xyz", "held": False})
    assert r.status_code == 200 and r.json().get("error")


def test_why_non_json_fallback(monkeypatch):
    monkeypatch.setattr(news, "market_news", _fake_market_news)
    monkeypatch.setattr(llm, "call_claude", lambda *a, **k: "就是一段话不是JSON")
    r = client.post("/api/sector/why", json={"market": "HK", "name": "金融abc", "held": False})
    assert r.status_code == 200 and r.json()["why"]
```

- [ ] **Step 2: 跑确认失败** `./venv/bin/python -m pytest tests/test_sector_why.py -q`（404）

- [ ] **Step 3: 实现（`api/sector_routes.py`）。** 顶部加 import（缺则加）：`import hashlib, json as _json, asyncio`、`from datetime import datetime`、`from pydantic import BaseModel`、`from typing import Optional`、`import services.llm_client as _llm`、`from database import get_all_holdings`、`import api.news_routes as _news`（注意：测试 monkeypatch `api.news_routes.market_news`，所以必须 `import api.news_routes as _news` 后调 `_news.market_news()`，不能 `from ... import market_news`）。然后：

```python
_WHY_CACHE: dict[str, dict] = {}


class WhyIn(BaseModel):
    market: str
    name: str
    change_1d: Optional[float] = None
    change_5d: Optional[float] = None
    held: bool = False
    leader: Optional[str] = None


_WHY_SYS = (
    "你是板块异动解读助手。只解释板块为什么动, 严禁任何操作建议(买入/卖出/加仓/减仓/目标价/仓位都不许)。"
    "用简体中文输出严格 JSON, 两个键:\n"
    '{"why":"这个板块近期为什么动(1-2句, 结合快讯)","relation":"跟用户持仓/关注什么关系(没有就写\'与你当前持仓无直接关系\')"}'
    "\n只输出 JSON。料不足就直说不确定, 不要编造具体数字或事件。"
)

_MARKET_CN = {"A": "A股", "HK": "港股", "US": "美股"}


@router.post("/why")
async def sector_why(data: WhyIn):
    from datetime import datetime as _dt
    hour = _dt.now().strftime("%Y-%m-%d-%H")
    key = hashlib.sha1(f"{data.market}|{data.name}|{hour}".encode("utf-8")).hexdigest()
    if key in _WHY_CACHE:
        return {**_WHY_CACHE[key], "cached": True}
    try:
        mn = await _news.market_news()
        heads = [it.get("title", "") for it in (mn.get("items") or [])][:60]
    except Exception:
        heads = []
    news_block = "\n".join(f"- {h}" for h in heads if h) or "(近期无可用快讯)"
    try:
        holdings = await get_all_holdings()
        hold_desc = ", ".join(f"{h['stock_code']}({h.get('stock_name','')})" for h in holdings) or "(无持仓信息)"
    except Exception:
        hold_desc = "(无持仓信息)"
    moves = []
    if data.change_1d is not None: moves.append(f"1日 {data.change_1d:+.2f}%")
    if data.change_5d is not None: moves.append(f"5日 {data.change_5d:+.2f}%")
    user_prompt = (
        f"用户持仓: {hold_desc}\n\n"
        f"市场: {_MARKET_CN.get(data.market, data.market)}  板块: {data.name}"
        + (f"  领涨股: {data.leader}" if data.leader else "")
        + (f"  近期涨跌: {', '.join(moves)}" if moves else "")
        + "\n\n近期全球财经快讯(标题):\n" + news_block
        + "\n\n请据此按要求输出 JSON。"
    )
    try:
        raw = await asyncio.to_thread(_llm.call_claude, user_prompt, _WHY_SYS, "claude-sonnet-4-20250514", 500)
    except Exception:
        return {"why": "", "relation": "", "error": "解读暂不可用", "cached": False}
    parsed = None
    try:
        s = raw.strip(); i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            parsed = _json.loads(s[i:j+1])
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        parsed = {"why": raw.strip()[:300], "relation": ""}
    out = {"why": str(parsed.get("why") or "").strip(), "relation": str(parsed.get("relation") or "").strip()}
    _WHY_CACHE[key] = out
    return {**out, "cached": False}
```
（确认 `router = APIRouter(prefix="/api/sector", ...)` 已存在 → 路径写 `/why`。）

- [ ] **Step 4: 跑确认通过** `./venv/bin/python -m pytest tests/test_sector_why.py -q`（3 passed），再 `./venv/bin/python -m pytest tests/ -q` 全过。

- [ ] **Step 5: Commit**
```bash
cd ~/stock-trading-assistant
git add api/sector_routes.py tests/test_sector_why.py
git commit -m "$(printf 'feat: POST /api/sector/why 板块为什么动 LLM 解读 (快讯合成+缓存+降级)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: SectorOpportunities 行内「为什么动」+ sw bump

**Files:** Modify `frontend/src/components/SectorOpportunities.jsx`、`frontend/public/sw.js`

- [ ] **Step 1: 读组件**，找到行渲染 `<div key={r.name} className="licai-opp-row ...">`（约 line 233）以及它所在的 `.map(r => ...)`。行内有 `r.name/r.held/r.change_1d/5d/30d/r.leader/r.symbol`。`market` 变量在组件作用域内可用。

- [ ] **Step 2: 加状态 + fetch（组件函数体内, 靠近其它 useState）**
```jsx
  const [whyOpen, setWhyOpen] = useState(null)   // 当前展开的 r.name
  const [whyData, setWhyData] = useState({})      // { 'market:name': {why,relation,error,loading} }
  const toggleWhy = (r) => {
    if (whyOpen === r.name) { setWhyOpen(null); return }
    setWhyOpen(r.name)
    const k = `${market}:${r.name}`
    if (whyData[k]) return
    setWhyData(d => ({ ...d, [k]: { loading: true } }))
    fetch('/api/sector/why', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ market, name: r.name, change_1d: r.change_1d, change_5d: r.change_5d, held: !!r.held, leader: r.leader || null }),
    }).then(res => res.json())
      .then(j => setWhyData(d => ({ ...d, [k]: j })))
      .catch(() => setWhyData(d => ({ ...d, [k]: { error: '解读暂不可用' } })))
  }
```
（确认 `useState` 已 import；已是。）

- [ ] **Step 3: 行渲染加按钮 + 展开区。** 把每行的单个 `<div key={r.name} className="licai-opp-row ...">...</div>` 包成一个 Fragment, 在行尾(操作列或名称行右侧)加按钮, 行下方条件渲染展开区。最小改法：把 `<div key={r.name} ...>` 改为 `<div key={r.name}>` 包裹原行 + 展开区：
```jsx
        <div key={r.name}>
          <div className="licai-opp-row px-3 md:px-5 py-2 items-center text-[11.5px]">
            {/* …原有列保持不变… 在名称那一格的 span 后面加一个按钮… */}
            {/* 找到名称格: <span className="text-text-bright font-semibold truncate">{r.name}</span> 后面加: */}
            <button onClick={() => toggleWhy(r)}
              className="ml-1.5 text-[10px] px-1 py-[1px] rounded border border-border-med text-text-dim hover:text-accent hover:border-accent cursor-pointer shrink-0">
              为什么动
            </button>
          </div>
          {whyOpen === r.name && (() => {
            const w = whyData[`${market}:${r.name}`]
            return (
              <div className="px-3 md:px-5 pb-2.5 -mt-1">
                <div className="rounded-lg border border-accent/20 bg-accent/5 p-2.5 space-y-1">
                  {!w || w.loading ? (
                    <div className="text-[11px] text-text-dim animate-pulse">解读生成中…</div>
                  ) : w.error ? (
                    <div className="text-[11px] text-text-dim">解读暂不可用</div>
                  ) : (
                    <>
                      {w.why && <div className="text-[11.5px] text-text"><span className="text-accent">为什么动 · </span>{w.why}</div>}
                      {w.relation && <div className="text-[11.5px] text-text"><span className="text-accent">跟你的关系 · </span>{w.relation}</div>}
                    </>
                  )}
                </div>
              </div>
            )
          })()}
        </div>
```
注意：原来 `<div key={r.name} className="licai-opp-row ...">` 自带 key；改造后 key 放到外层 `<div key={r.name}>`，内层行 `<div className="licai-opp-row ...">` 去掉 key。务必保持原有各列 JSX 不变, 只在名称 span 后插按钮、行后插展开区。READ 现有行 JSX 完整结构再改, 避免错位。

- [ ] **Step 4: sw bump** `frontend/public/sw.js` `CACHE_NAME` v106→v107。

- [ ] **Step 5: build + 验证**
```bash
cd ~/stock-trading-assistant
./venv/bin/python -m pytest tests/ -q
cd frontend && npm run build 2>&1 | tail -3
cd ~/stock-trading-assistant
pkill -f "run.py" 2>/dev/null; sleep 1.5; nohup ./venv/bin/python run.py > /tmp/lb.log 2>&1 &
for i in $(seq 1 25); do sleep 1; curl -s localhost:8888/api/sector/scan >/dev/null 2>&1 && break; done
curl -s localhost:8888/sw.js | grep -o "licai-v[0-9]*"
curl -s -X POST localhost:8888/api/sector/why -H 'Content-Type: application/json' -d '{"market":"A","name":"有色金属","change_1d":-2.0,"held":true,"leader":"洛阳钼业"}' | python3 -c "import sys,json;d=json.load(sys.stdin);print('why keys:',sorted(d.keys()),'has:',bool(d.get('why') or d.get('error')))"
```
Expected：测试全过；build ✓；sw v107；why 端点返回 why/relation(或 error)+cached。

- [ ] **Step 6: Commit**
```bash
git add frontend/src/components/SectorOpportunities.jsx frontend/public/sw.js
git commit -m "$(printf 'feat: 板块行内「为什么动」解读展开 + sw bump\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Self-Review 记录
- Spec 覆盖：why 端点+缓存+降级+无建议(T1)、行内展开按钮(T2)。
- monkeypatch 生效：端点用 `_llm.call_claude` 和 `_news.market_news()` 模块属性调用。
- 无建议铁律在 `_WHY_SYS`。容错：LLM 抛错 error 不 5xx；非 JSON 兜底进 why；快讯空仍可调。
