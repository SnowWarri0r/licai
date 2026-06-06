# 新闻详情 Panel（A 打底 + C 自动解读）— 设计

## 目标

现在新闻条目点击直接跳原文外链，我们抓到的正文片段和「为什么重要」的解读没被用上。
改成：点击在 app 内打开详情 panel，复用已抓素材 + 按需 LLM 解读，把信息留在产品里。
原文仍可一键打开。

属于更大的「跨市场机会雷达」方向里先落地的一块；机会雷达单独 spec，不在本次范围。

## 范围

**做：**
- 新闻条目点击 → app 内详情 panel（不再跳出）。
- Panel 显示已抓素材（A）+ 打开即自动 LLM 解读（C）+「原文 ↗」按钮。
- 解读结果缓存，同一条不重复调用。
- 接入 `PortfolioNews`、`MorningBriefing`、`UnwindView`（同一个 modal 组件）。

**不做（YAGNI）：**
- 不抓原文全文（反爬/付费墙/排版不稳，砍掉）。
- 不预跑全列表解读（只在点开单条时调用）。
- 不碰机会雷达 / 不改板块扫描。
- 不给打分 / 信号标签。

## 铁律：只解读，不荐买卖

解读 system prompt 必须禁止任何操作建议：买入/卖出/加仓/减仓/目标价/仓位
都不许出。只解释「讲了啥 / 为什么重要 / 跟持仓·关注板块什么关系」。
跟现有 `news_digest` 的无建议原则一致（见 `feedback_stock_assistant_no_recs_in_downtrend`）。

## 交互

1. 点新闻条目 → 打开 `NewsDetailModal`（居中弹窗，移动端全屏抽屉）。
2. Panel 立刻渲染 **A 块**（无需等待）：
   - 标题
   - 完整正文片段（现有 `content`，≤200 字，不再 line-clamp 截断）
   - 来源 · 时间 · 关联个股（code-name，若有）
   - 「原文 ↗」按钮（`window.open(url)`；无 url 时禁用并提示）
3. 同时自动触发 **C 解读**，上方区域显示 skeleton；返回后渲染三段：
   - 讲了啥
   - 为什么重要
   - 跟你持仓 / 关注板块的关系
4. ESC / 点遮罩 / × 关闭。

## 架构

### 后端：`POST /api/news/interpret`

- 入参：`{ title, content, code?, name?, source?, time? }`
- 取持仓上下文（A 股持仓 code+name，同 `news_digest` 的取法）拼进 prompt。
- 调 `services.llm_client.call_claude(user_prompt, system, model, max_tokens=600)`。
- system prompt：无建议铁律 + 输出固定 JSON `{ "what": "...", "why": "...", "relation": "..." }`。
- 解析 JSON；解析失败兜底成 `{what: 原始文本, why: "", relation: ""}`，不抛 500。
- **缓存**：进程内 dict，key = `sha1(title + content + code)`，TTL 24h（新闻发布后内容不变）。
  二次相同请求直接命中，不再调 LLM。
- 放在 `api/news_routes.py`（不新建 router）。

**请求示例**
```
POST /api/news/interpret
{ "title": "央行宣布降准 0.5 个百分点", "content": "...", "code": "601398", "name": "工商银行" }
```
**响应示例**
```json
{
  "what": "央行下调存款准备金率 0.5 个百分点，释放长期资金约 1 万亿元。",
  "why": "降准利好银行与高负债行业，整体提升市场流动性预期。",
  "relation": "你持有工商银行（601398），银行板块直接受益于流动性宽松。",
  "cached": false
}
```
LLM 不可用时：
```json
{ "what": "", "why": "", "relation": "", "error": "解读暂不可用", "cached": false }
```

### 前端：`NewsDetailModal.jsx`

- props：`item`（新闻对象）、`onClose`。
- mount 时 `POST /api/news/interpret`；组件内 + 模块级 Map 缓存（key=item.url||title），
  同会话重开秒显。
- 渲染：A 块（立即）+ C 块（loading skeleton → 三段 / 「解读暂不可用」）。
- 「原文 ↗」按钮：`window.open(item.url, '_blank', 'noopener')`。

### 接入点

- `PortfolioNews.jsx`：条目 `<a href target=_blank>` → `<button onClick={()=>setDetail(it)}>`；
  行尾保留一个小 ↗ 直接开原文（可选）；底部挂 `{detail && <NewsDetailModal .../>}`。
- `MorningBriefing.jsx` / `UnwindView.jsx`：新闻条目同款改造，复用 `NewsDetailModal`。

## 容错

- LLM 挂 / 无凭证 / 超时 → panel 照常显示 A 块 + 「解读暂不可用」+ 原文按钮，绝不挡阅读。
- 公告类（只有 title 没 content）→ 解读基于标题，panel 标注「仅标题，解读有限」。
- interpret 接口本身永不返回 5xx（内部兜底），前端只处理 `error` 字段。

## 测试

**后端（pytest，monkeypatch `call_claude`）：**
- 正常：stub 返回合法 JSON → 接口返回 `{what,why,relation}` 三段齐全。
- 缓存：同入参二次调用 → `call_claude` 只被调一次，第二次 `cached:true`。
- LLM 抛错 → 接口 200 且 `error` 字段存在，不 5xx。
- JSON 解析失败（stub 返回非 JSON）→ 兜底结构，不崩。

**手动：**
- 点开持仓相关新闻 → A 块即显 + 三段解读 + 原文可开。
- 断网 / 清空凭证 → 仍可读 A 块 + 原文。

## 文件清单

- `api/news_routes.py` — 加 `interpret` 端点 + 缓存。
- `services/llm_client.py` — 复用 `call_claude`，无改动。
- `frontend/src/components/NewsDetailModal.jsx` — 新建。
- `frontend/src/components/PortfolioNews.jsx` — 条目改 onClick + 挂 modal。
- `frontend/src/components/MorningBriefing.jsx` / `UnwindView.jsx` — 复用 modal。
- `frontend/public/sw.js` — 版本号 bump。
- `tests/test_news_interpret.py` — 新建。
