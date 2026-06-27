import { useState, useEffect, useRef } from 'react'
import { fetchJSON } from '../hooks/useApi'

// 极简 markdown 渲染 (## 标题 / **粗** / - 列表 / 段落), 不引依赖
function renderInline(text, kp) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((p, i) =>
    p.startsWith('**') && p.endsWith('**')
      ? <strong key={`${kp}-${i}`} className="text-text-bright">{p.slice(2, -2)}</strong>
      : <span key={`${kp}-${i}`}>{p}</span>)
}
// 表格行: 以 | 开头/结尾且含分隔; 分隔行: |---|:--|... (全是 - 和 : 和 |)
const isTableRow = (t) => t.startsWith('|') && t.indexOf('|', 1) > 0
const isTableSep = (t) => /^\|?[\s:|-]+\|[\s:|-]*$/.test(t) && t.includes('-')
const splitCells = (t) => t.replace(/^\||\|$/g, '').split('|').map(c => c.trim())

function MiniMarkdown({ text }) {
  // 模型偶尔内联 <cite index="x">...</cite> 引用标签, 去掉只留文字(后端已剥, 这里兜底)
  const lines = (text || '').replace(/<\/?cite[^>]*>/g, '').split('\n')
  const out = []
  let i = 0
  while (i < lines.length) {
    const t = lines[i].trim()
    // 表格: 连续的 | 行 (第二行是分隔行)
    if (isTableRow(t) && i + 1 < lines.length && isTableSep(lines[i + 1].trim())) {
      const header = splitCells(t)
      const rows = []
      let j = i + 2
      while (j < lines.length && isTableRow(lines[j].trim())) { rows.push(splitCells(lines[j].trim())); j++ }
      out.push(
        <div key={i} className="my-2 overflow-x-auto">
          <table className="text-[11.5px] border-collapse w-full">
            <thead><tr>{header.map((h, k) => (
              <th key={k} className="text-left font-semibold text-text-bright px-2 py-1 border-b border-border bg-surface-3 whitespace-nowrap">{renderInline(h, `h${i}-${k}`)}</th>
            ))}</tr></thead>
            <tbody>{rows.map((r, ri) => (
              <tr key={ri} className="border-b border-border-subtle">{r.map((c, k) => (
                <td key={k} className="px-2 py-1 text-text-dim whitespace-nowrap">{renderInline(c, `c${i}-${ri}-${k}`)}</td>
              ))}</tr>
            ))}</tbody>
          </table>
        </div>
      )
      i = j
      continue
    }
    if (!t) { out.push(<div key={i} className="h-1.5" />) }
    else if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) out.push(<hr key={i} className="my-2.5 border-0 border-t border-border-subtle" />)
    else if (t.startsWith('## ')) out.push(<div key={i} className="text-[12.5px] font-semibold text-accent mt-2 mb-0.5">{renderInline(t.slice(3), i)}</div>)
    else if (t.startsWith('### ')) out.push(<div key={i} className="text-[12px] font-semibold text-text-bright mt-1.5">{renderInline(t.slice(4), i)}</div>)
    else if (t.startsWith('# ')) out.push(<div key={i} className="text-[13px] font-semibold text-accent mt-2 mb-0.5">{renderInline(t.slice(2), i)}</div>)
    else if (t.startsWith('> ')) out.push(<div key={i} className="text-[12px] text-text-muted border-l-2 border-accent/40 pl-2 my-1 italic">{renderInline(t.slice(2), i)}</div>)
    else if (t.startsWith('- ') || t.startsWith('• ') || t.startsWith('* ')) out.push(<div key={i} className="flex gap-1.5 text-[12px] leading-relaxed"><span className="text-accent shrink-0">·</span><span className="text-text-dim">{renderInline(t.slice(2), i)}</span></div>)
    else if (/^\d+\.\s/.test(t)) { const m = t.match(/^(\d+)\.\s+(.*)$/); out.push(<div key={i} className="flex gap-1.5 text-[12px] leading-relaxed"><span className="text-accent shrink-0 font-medium">{m[1]}.</span><span className="text-text-dim">{renderInline(m[2], i)}</span></div>) }
    else out.push(<div key={i} className="text-[12px] text-text-dim leading-relaxed">{renderInline(t, i)}</div>)
    i++
  }
  return <div>{out}</div>
}

// 能力展示型推荐问题 (page 模式空态用), 覆盖 市场风格/资金主线/政策/基本面/同行/筹码
const MARKET_SUGGESTIONS = [
  '这周市场什么风格,资金主线在哪',
  '现在量化资金在冲哪个概念',
  '最近有什么政策面/国家调控影响市场',
  '资金人气榜上抱团方向是什么',
]

export default function StockAsk({ page = false }) {
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState([])   // [{q, steps, thought, answer, typed, done, err}]
  const [holdings, setHoldings] = useState([])
  const abortRef = useRef(null)
  const typer = useRef(null)
  const scrollBox = useRef(null)
  const follow = useRef(true)   // 是否跟随滚到底; 用户往上拖就关掉, 拖回底部再开

  useEffect(() => {
    fetchJSON('/api/portfolio').then(d => {
      const hs = Array.isArray(d) ? d : (d.holdings || d.positions || [])
      // 只留当前在持(shares>0); 已清仓的票不该出现在"我的持仓"快捷入口
      setHoldings(hs.filter(h => (h.stock_name || h.stock_code) && Number(h.shares) > 0).slice(0, 8))
    }).catch(() => {})
    return () => { abortRef.current?.abort(); clearInterval(typer.current) }
  }, [])

  const patchLast = (fn) => setHistory(h => h.map((it, i) => i === h.length - 1 ? fn(it) : it))

  // 用户手动滚动: 贴近底部(<48px)就重新开启跟随, 往上拖就停跟随
  const onScroll = () => {
    const el = scrollBox.current
    if (!el) return
    follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }
  // 每次内容变化(打字机每跳一下也会触发)后, 若处于跟随态就贴到底
  useEffect(() => {
    const el = scrollBox.current
    if (el && follow.current) el.scrollTop = el.scrollHeight
  }, [history])

  const typewriter = (full) => {
    clearInterval(typer.current)
    let n = 0
    typer.current = setInterval(() => {
      n = Math.min(full.length, n + 3)   // 每 tick 3 字
      patchLast(it => ({ ...it, typed: full.slice(0, n) }))   // history 变 → 上面 effect 跟随滚动
      if (n >= full.length) { clearInterval(typer.current); patchLast(it => ({ ...it, done: true })) }
    }, 16)
  }

  const handleEv = (ev) => {
    if (ev.type === 'step') patchLast(it => ({ ...it, steps: [...it.steps, { label: ev.label, arg: ev.arg }] }))
    else if (ev.type === 'thought') patchLast(it => ({ ...it, thought: ev.text }))
    else if (ev.type === 'answer') { patchLast(it => ({ ...it, answer: ev.text })); typewriter(ev.text || '') }
    else if (ev.type === 'error') patchLast(it => ({ ...it, err: ev.error, done: true }))
  }

  const ask = async (question) => {
    const text = (question ?? q).trim()
    if (!text || loading) return
    // 把已完成的历史轮次(最近4轮)作为上下文带给后端, 支持追问("它/明天呢")
    const hist = history.filter(it => it.answer && !it.err).slice(-4)
      .flatMap(it => [{ role: 'user', content: it.q }, { role: 'assistant', content: it.answer }])
    setQ(''); setLoading(true)
    follow.current = true
    setHistory(h => [...h, { q: text, steps: [], thought: '', answer: null, typed: '', done: false }])
    abortRef.current?.abort()
    const ctrl = new AbortController(); abortRef.current = ctrl
    try {
      const resp = await fetch('/api/ask/stock/stream', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text, history: hist }), signal: ctrl.signal,
      })
      const reader = resp.body.getReader(); const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const parts = buf.split('\n\n'); buf = parts.pop()   // 留下不完整的最后一段
        for (const p of parts) {
          const line = p.split('\n').find(l => l.startsWith('data: '))
          if (!line) continue
          try { handleEv(JSON.parse(line.slice(6))) } catch { /* skip */ }
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') patchLast(it => it.answer == null ? { ...it, err: '连接中断', done: true } : it)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-3">
        <h3 className={`${page ? 'text-[16px]' : 'text-[14px]'} font-semibold text-text-bright m-0`}>问问市场</h3>
        <span className="text-[10.5px] text-text-muted">
          {page ? '挂了19个数据工具的AI · 个股资金流/基本面/同行/筹码 · 市场风格/资金主线/政策面' : '个股涨跌/消息 · 这周市场什么风格 · 资金主线'}
        </span>
      </div>

      {history.length === 0 && (
        <div className="flex flex-col gap-2 mb-3">
          {page && (
            <div className="flex flex-wrap gap-1.5">
              {MARKET_SUGGESTIONS.map((s, i) => (
                <button key={i} onClick={() => ask(s)}
                  className="text-[11px] px-2.5 py-1 rounded-full border border-accent/30 bg-accent/8 text-accent/90 hover:bg-accent/15 hover:border-accent/50">
                  {s}
                </button>
              ))}
            </div>
          )}
          {holdings.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {holdings.map((h, i) => (
                <button key={i} onClick={() => ask(`${h.stock_name || h.stock_code}最近为什么涨跌`)}
                  className="text-[11px] px-2 py-0.5 rounded-full border border-border bg-surface-3 text-text-dim hover:text-text hover:border-accent/40">
                  {h.stock_name || h.stock_code} ↗
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div ref={scrollBox} onScroll={onScroll} className={`space-y-3 mb-3 ${history.length ? `${page ? 'max-h-[82vh] min-h-[60vh]' : 'max-h-[58vh]'} overflow-y-auto pr-1` : ''}`}>
        {history.map((it, i) => (
          <div key={i}>
            <div className="text-[12px] text-text-bright bg-surface-3 rounded-lg px-3 py-1.5 inline-block">{it.q}</div>
            <div className="mt-2 px-3 py-2.5 rounded-lg bg-accent/8 border border-accent/25">
              {/* 步骤实时流 */}
              {it.steps.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 mb-2">
                  {it.steps.map((s, j) => (
                    <span key={j} className="text-[10.5px] px-1.5 py-0.5 rounded bg-surface-3 text-text-dim border border-border-subtle">
                      {s.label}{s.arg ? <span className="text-text-muted ml-0.5">{s.arg}</span> : ''}
                      <span className="text-accent ml-1">{(it.answer != null || it.done) ? '✓' : '…'}</span>
                    </span>
                  ))}
                </div>
              )}
              {it.thought && it.answer == null && <div className="text-[11px] text-text-muted italic mb-1.5">{it.thought}</div>}
              {/* 答案 / loading / 错误 */}
              {it.err
                ? <div className="text-[11.5px] text-bull-bright">出错: {it.err}</div>
                : it.answer == null
                  ? (it.steps.length === 0 && <div className="text-[11.5px] text-text-dim">分析中…</div>)
                  : <div className="relative">
                      <MiniMarkdown text={it.typed} />
                      {!it.done && <span className="inline-block w-1.5 h-3.5 bg-accent/70 align-middle animate-pulse ml-0.5" />}
                    </div>}
            </div>
          </div>
        ))}
      </div>

      <div className="flex gap-2">
        <input value={q} onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing && e.keyCode !== 229) ask() }} disabled={loading}
          placeholder="例: 这周市场什么风格 / 洛阳钼业今天为什么涨 / 资金主线在哪"
          className="flex-1 text-[12px] px-3 py-2 rounded-lg bg-surface-3 border border-border text-text placeholder:text-text-muted focus:border-accent/50 outline-none disabled:opacity-50" />
        <button onClick={() => ask()} disabled={loading || !q.trim()}
          className="text-[12px] px-3.5 py-2 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed">
          {loading ? '分析中' : '问'}
        </button>
      </div>
      <div className="text-[10px] text-text-muted pt-2.5 mt-2 border-t border-border-subtle">
        Agent 自取行情/走势/新闻/大盘情绪后客观解读 · 纯解读不构成任何买卖建议
      </div>
    </div>
  )
}
