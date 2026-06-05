# 新闻详情 Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 点新闻条目在 app 内打开详情 panel（复用已抓正文片段 + 按需 LLM 解读 + 原文按钮），不再直接跳外链。

**Architecture:** 后端加 `POST /api/news/interpret`（call_claude 出 `{what,why,relation}`，按 hash 缓存）；前端 `NewsDetailModal.jsx` 复用组件，PortfolioNews/MorningBriefing/UnwindView 的新闻条目改成点击开 panel。

**Tech Stack:** FastAPI + `services/llm_client.call_claude(user_prompt, system, model, max_tokens)`；React + Vite。venv `./venv`；前端 build `cd frontend && npm run build`（→ `../static`），后端 :8888。

参考 spec：`docs/superpowers/specs/2026-06-05-news-detail-panel-design.md`

---

## File Structure

- `api/news_routes.py` — 加 `POST /interpret` 端点 + 进程内缓存（router 已是 `/api/news`）。
- `services/llm_client.py` — 复用 `call_claude`，无改动。
- `frontend/src/components/NewsDetailModal.jsx`（新）— 详情 panel（A 块 + C 解读 + 原文按钮）。
- `frontend/src/components/PortfolioNews.jsx` — 条目从 `<a href>` 改成点击开 modal。
- `frontend/src/components/MorningBriefing.jsx` / `UnwindView.jsx` — 新闻条目复用 modal。
- `frontend/public/sw.js` — 版本 bump。
- `tests/test_news_interpret.py`（新）— 后端测试。

---

### Task 1: 后端 `POST /api/news/interpret` + 缓存

**Files:**
- Modify: `api/news_routes.py`
- Test: `tests/test_news_interpret.py`

- [ ] **Step 1: 写失败测试** `tests/test_news_interpret.py`

```python
import asyncio
from fastapi.testclient import TestClient
import services.llm_client as llm
from run import app  # FastAPI app with news_router included

client = TestClient(app)


def test_interpret_returns_three_parts(monkeypatch):
    calls = {"n": 0}
    def fake(user_prompt, system=None, model=None, max_tokens=600):
        calls["n"] += 1
        return '{"what":"讲了降准","why":"利好流动性","relation":"你持有银行股受益"}'
    monkeypatch.setattr(llm, "call_claude", fake)
    r = client.post("/api/news/interpret", json={"title": "央行降准", "content": "全文片段", "code": "601398", "name": "工商银行"})
    assert r.status_code == 200
    d = r.json()
    assert d["what"] and d["why"] and d["relation"]
    assert calls["n"] == 1
    # 同入参二次 → 命中缓存, call_claude 不再被调
    r2 = client.post("/api/news/interpret", json={"title": "央行降准", "content": "全文片段", "code": "601398", "name": "工商银行"})
    assert r2.status_code == 200 and r2.json().get("cached") is True
    assert calls["n"] == 1


def test_interpret_llm_error_graceful(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no creds")
    monkeypatch.setattr(llm, "call_claude", boom)
    r = client.post("/api/news/interpret", json={"title": "另一条新闻xyz", "content": "x"})
    assert r.status_code == 200
    assert r.json().get("error")


def test_interpret_non_json_fallback(monkeypatch):
    monkeypatch.setattr(llm, "call_claude", lambda *a, **k: "这不是JSON只是一段话")
    r = client.post("/api/news/interpret", json={"title": "标题abc", "content": "y"})
    assert r.status_code == 200
    d = r.json()
    assert d["what"]  # 兜底把原文塞进 what
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_news_interpret.py -q`
Expected: FAIL（接口不存在 / 404）。

- [ ] **Step 3: 实现端点（加到 `api/news_routes.py`，import 区加 `import hashlib`、`from pydantic import BaseModel`、`from services.llm_client import call_claude`、`import asyncio`、`from database import get_all_holdings`；若已 import 则不重复）**

```python
_INTERPRET_CACHE: dict[str, dict] = {}

class InterpretIn(BaseModel):
    title: str
    content: str | None = ""
    code: str | None = None
    name: str | None = None
    source: str | None = None
    time: str | None = None

_INTERPRET_SYS = (
    "你是 A 股资讯解读助手。只解释新闻, 严禁任何操作建议(买入/卖出/加仓/减仓/目标价/仓位都不许出)。"
    "用简体中文输出严格 JSON, 三个键:\n"
    '{"what":"这条新闻讲了什么(1-2句)","why":"为什么重要/影响面(1-2句)",'
    '"relation":"跟用户持仓或关注板块什么关系(没有就写\'与你当前持仓无直接关系\')"}'
    "\n只输出 JSON, 不要多余文字。"
)

@router.post("/interpret")
async def interpret_news(data: InterpretIn):
    key = hashlib.sha1(f"{data.title}|{data.content}|{data.code or ''}".encode("utf-8")).hexdigest()
    if key in _INTERPRET_CACHE:
        return {**_INTERPRET_CACHE[key], "cached": True}
    try:
        holdings = await get_all_holdings()
        hold_desc = ", ".join(f"{h['stock_code']}({h.get('stock_name','')})" for h in holdings) or "(无持仓信息)"
    except Exception:
        hold_desc = "(无持仓信息)"
    rel = f"[{data.code}{('-'+data.name) if data.name else ''}] " if data.code else ""
    user_prompt = (
        f"用户持仓: {hold_desc}\n\n"
        f"新闻标题: {rel}{data.title}\n"
        f"新闻正文: {data.content or '(无正文, 仅标题)'}\n\n请按要求输出 JSON。"
    )
    try:
        raw = await asyncio.to_thread(call_claude, user_prompt, _INTERPRET_SYS, "claude-sonnet-4-20250514", 600)
    except Exception as e:
        return {"what": "", "why": "", "relation": "", "error": "解读暂不可用", "cached": False}
    import json as _json
    parsed = None
    try:
        s = raw.strip()
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            parsed = _json.loads(s[i:j+1])
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        parsed = {"what": raw.strip()[:300], "why": "", "relation": ""}
    out = {
        "what": str(parsed.get("what") or "").strip(),
        "why": str(parsed.get("why") or "").strip(),
        "relation": str(parsed.get("relation") or "").strip(),
    }
    _INTERPRET_CACHE[key] = out
    return {**out, "cached": False}
```
（注意：`router = APIRouter(prefix="/api/news", ...)` 已存在；端点路径写 `/interpret`。测试 monkeypatch 的是 `services.llm_client.call_claude`，所以端点内必须以 `call_claude(...)` 通过模块属性调用 —— 用 `from services.llm_client import call_claude` 后 monkeypatch 对已绑定名无效。改为 `import services.llm_client as _llm` 并调用 `_llm.call_claude(...)`，确保 monkeypatch 生效。）

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_news_interpret.py -q`
Expected: 3 passed。再跑全量 `./venv/bin/python -m pytest tests/ -q` 确认没破坏。

- [ ] **Step 5: Commit**

```bash
cd /Users/lovart/stock-trading-assistant
git add api/news_routes.py tests/test_news_interpret.py
git commit -m "$(printf 'feat: POST /api/news/interpret LLM 新闻解读 (三段+缓存+降级)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: NewsDetailModal 组件

**Files:**
- Create: `frontend/src/components/NewsDetailModal.jsx`

- [ ] **Step 1: 写组件**

```jsx
import { useState, useEffect } from 'react'

const _cache = new Map()  // key=url||title → interpret 结果, 同会话重开秒显

export default function NewsDetailModal({ item, onClose }) {
  const [interp, setInterp] = useState(null)
  const [loading, setLoading] = useState(true)
  const cacheKey = item?.url || item?.title || ''

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    if (!item) return
    if (_cache.has(cacheKey)) { setInterp(_cache.get(cacheKey)); setLoading(false); return }
    setLoading(true)
    fetch('/api/news/interpret', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: item.title, content: item.content || '', code: item.code || null, name: item.name || null, source: item.source || null, time: item.time || null }),
    }).then(r => r.json()).then(d => { _cache.set(cacheKey, d); setInterp(d) })
      .catch(() => setInterp({ error: '解读暂不可用' }))
      .finally(() => setLoading(false))
  }, [cacheKey])

  if (!item) return null
  const hasUrl = !!item.url
  return (
    <div className="fixed inset-0 z-[220] flex items-center justify-center bg-black/70 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl w-[560px] max-w-[96vw] max-h-[88vh] overflow-y-auto p-5 space-y-3" onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-[14px] font-semibold text-text-bright m-0 leading-snug">{item.title}</h3>
          <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-1 cursor-pointer shrink-0">×</button>
        </div>
        <div className="text-[10.5px] text-text-muted flex flex-wrap gap-x-2 gap-y-0.5">
          {item.source && <span>{item.source}</span>}
          {item.time && <span>· {String(item.time).slice(0, 16)}</span>}
          {item.code && <span>· {item.code}{item.name ? `-${item.name}` : ''}</span>}
        </div>

        {/* C: 解读 */}
        <div className="rounded-lg border border-accent/20 bg-accent/5 p-3 space-y-1.5">
          {loading ? (
            <div className="text-[11.5px] text-text-dim animate-pulse">解读生成中…</div>
          ) : interp?.error ? (
            <div className="text-[11.5px] text-text-dim">解读暂不可用</div>
          ) : (
            <>
              {interp?.what && <div className="text-[12px] text-text"><span className="text-accent">讲了啥 · </span>{interp.what}</div>}
              {interp?.why && <div className="text-[12px] text-text"><span className="text-accent">为什么重要 · </span>{interp.why}</div>}
              {interp?.relation && <div className="text-[12px] text-text"><span className="text-accent">跟你的关系 · </span>{interp.relation}</div>}
            </>
          )}
        </div>

        {/* A: 正文片段 */}
        {item.content ? (
          <div className="text-[12px] text-text-dim leading-relaxed whitespace-pre-wrap">{item.content}</div>
        ) : (
          <div className="text-[11px] text-text-muted">仅标题，无正文片段。</div>
        )}

        <div className="flex justify-end pt-1">
          <button disabled={!hasUrl}
            onClick={() => hasUrl && window.open(item.url, '_blank', 'noopener')}
            className="px-3 py-1.5 rounded-md border border-accent/50 bg-accent/10 text-accent text-[12px] hover:bg-accent/20 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default">
            原文 ↗
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: build 确认无语法错**

Run: `cd /Users/lovart/stock-trading-assistant/frontend && npm run build 2>&1 | tail -3`
Expected: ✓ built（组件未被引用会被 tree-shake，但语法错会报错）。

- [ ] **Step 3: Commit**

```bash
cd /Users/lovart/stock-trading-assistant
git add frontend/src/components/NewsDetailModal.jsx
git commit -m "$(printf 'feat: NewsDetailModal 新闻详情 panel 组件\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 3: PortfolioNews 接入 panel

**Files:**
- Modify: `frontend/src/components/PortfolioNews.jsx`

- [ ] **Step 1: 改条目点击 → 开 modal**

读文件确认结构。当前新闻列表项是 `<a key={i} href={hasUrl ? it.url : '#'} target=... onClick=...>` 包住标题+正文（约 line 262-288）。改造：
1. import：顶部加 `import NewsDetailModal from './NewsDetailModal'`。
2. state：组件内加 `const [detail, setDetail] = useState(null)`（useState 已 import）。
3. 把列表项的 `<a ...>` 外层换成 `<div>`，点击改成 `onClick={() => setDetail(it)}`，保留原有 className/布局，去掉 href/target/rel：
```jsx
        <div key={i} role="button" tabIndex={0}
          onClick={() => setDetail(it)}
          onKeyDown={e => { if (e.key === 'Enter') setDetail(it) }}
          className={/* 沿用原 <a> 的 className */}>
```
   （把原 `<a>` 上的 className 原样搬到 `<div>`；闭合标签 `</a>` 改 `</div>`。行尾若想留个直达原文的小 ↗，可选加，但本任务不强制。）
4. 在组件 return 的最外层容器末尾（最后一个闭合标签前）挂：
```jsx
      {detail && <NewsDetailModal item={detail} onClose={() => setDetail(null)} />}
```

- [ ] **Step 2: build + 手动验证**

```bash
cd /Users/lovart/stock-trading-assistant/frontend && npm run build 2>&1 | tail -3
```
Expected: ✓ built。重启后端，打开页面新闻区点一条 → 弹 panel，解读三段加载，原文按钮可开。

- [ ] **Step 3: Commit**

```bash
cd /Users/lovart/stock-trading-assistant
git add frontend/src/components/PortfolioNews.jsx
git commit -m "$(printf 'feat: PortfolioNews 点击改为打开详情 panel\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 4: MorningBriefing / UnwindView 复用 + sw bump

**Files:**
- Modify: `frontend/src/components/MorningBriefing.jsx`、`frontend/src/components/UnwindView.jsx`、`frontend/public/sw.js`

- [ ] **Step 1: 两个组件接入同款 modal**

对 `MorningBriefing.jsx` 和 `UnwindView.jsx`：用 Grep 找它们渲染新闻条目的地方（找 `it.url` / `href` / `.title` 的列表）。若存在新闻条目跳外链：
1. import `NewsDetailModal`。
2. 加 `const [detail, setDetail] = useState(null)`（确认 useState 已 import；没有则加）。
3. 把新闻条目的外链点击改成 `onClick={() => setDetail(it)}`（同 Task 3 做法），return 末尾挂 `{detail && <NewsDetailModal item={detail} onClose={() => setDetail(null)} />}`。
若某组件其实没有可点击新闻条目（只是展示文本），则跳过该文件并在报告里说明 —— 不要硬塞。

- [ ] **Step 2: sw bump**

`frontend/public/sw.js`：`CACHE_NAME` 从 `licai-v105` 升到 `licai-v106`。

- [ ] **Step 3: build + 全量验证**

```bash
cd /Users/lovart/stock-trading-assistant
./venv/bin/python -m pytest tests/ -q
cd frontend && npm run build 2>&1 | tail -3
curl -s localhost:8888/sw.js | grep -o "licai-v[0-9]*"   # 重启后端后
```
Expected: 测试全过；build 成功；sw v106。

- [ ] **Step 4: 端到端手动验证**

重启后端，打开页面：PortfolioNews（及 MorningBriefing/UnwindView 若接入）点新闻 → panel 显示正文片段 + 三段解读 + 原文按钮；断网/无凭证时仍显示正文 + 「解读暂不可用」+ 原文。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MorningBriefing.jsx frontend/src/components/UnwindView.jsx frontend/public/sw.js
git commit -m "$(printf 'feat: MorningBriefing/UnwindView 新闻复用详情 panel + sw bump\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Self-Review 记录

- Spec 覆盖：interpret 端点+缓存+降级+无建议铁律(T1)、NewsDetailModal A块+C解读+原文(T2)、PortfolioNews 接入(T3)、MorningBriefing/UnwindView 复用+sw(T4)。
- 无建议铁律写进 `_INTERPRET_SYS`。
- monkeypatch 生效：端点用 `import services.llm_client as _llm; _llm.call_claude(...)`（测试 patch 模块属性）。
- 容错：LLM 抛错返回 error 字段不 5xx；非 JSON 兜底进 what；无正文标注；无 url 原文按钮 disabled。
