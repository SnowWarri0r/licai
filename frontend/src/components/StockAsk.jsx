import { useState, useEffect, useRef } from 'react'
import { fetchJSON } from '../hooks/useApi'

// 极简 markdown 渲染 (## 标题 / **粗** / - 列表 / 段落), 不引依赖
function renderInline(text, kp) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((p, i) =>
    p.startsWith('**') && p.endsWith('**')
      ? <strong key={`${kp}-${i}`} className="text-text-bright">{p.slice(2, -2)}</strong>
      : <span key={`${kp}-${i}`}>{p}</span>)
}
function MiniMarkdown({ text }) {
  const out = []
  ;(text || '').split('\n').forEach((ln, i) => {
    const t = ln.trim()
    if (!t) { out.push(<div key={i} className="h-1.5" />); return }
    if (t.startsWith('## ')) out.push(<div key={i} className="text-[12.5px] font-semibold text-accent mt-2 mb-0.5">{renderInline(t.slice(3), i)}</div>)
    else if (t.startsWith('### ')) out.push(<div key={i} className="text-[12px] font-semibold text-text-bright mt-1.5">{renderInline(t.slice(4), i)}</div>)
    else if (t.startsWith('- ') || t.startsWith('• ')) out.push(<div key={i} className="flex gap-1.5 text-[12px] leading-relaxed"><span className="text-accent shrink-0">·</span><span className="text-text-dim">{renderInline(t.slice(2), i)}</span></div>)
    else out.push(<div key={i} className="text-[12px] text-text-dim leading-relaxed">{renderInline(t, i)}</div>)
  })
  return <div>{out}</div>
}

export default function StockAsk() {
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState([])   // [{q, steps, thought, answer, typed, done, err}]
  const [holdings, setHoldings] = useState([])
  const esRef = useRef(null)
  const typer = useRef(null)
  const scrollBox = useRef(null)

  useEffect(() => {
    fetchJSON('/api/portfolio').then(d => {
      const hs = Array.isArray(d) ? d : (d.holdings || d.positions || [])
      setHoldings(hs.filter(h => (h.stock_name || h.stock_code)).slice(0, 8))
    }).catch(() => {})
    return () => { esRef.current?.close(); clearInterval(typer.current) }
  }, [])

  const patchLast = (fn) => setHistory(h => h.map((it, i) => i === h.length - 1 ? fn(it) : it))
  // 只在面板内部滚动容器里滚, 不动整个页面; 且仅当用户已贴近底部时才跟随(没在往上翻看就不抢)
  const scrollDown = () => setTimeout(() => {
    const el = scrollBox.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, 30)

  const typewriter = (full) => {
    clearInterval(typer.current)
    let n = 0
    typer.current = setInterval(() => {
      n = Math.min(full.length, n + 3)   // 每 tick 3 字
      patchLast(it => ({ ...it, typed: full.slice(0, n) }))
      if (n >= full.length) { clearInterval(typer.current); patchLast(it => ({ ...it, done: true })); scrollDown() }
    }, 16)
  }

  const ask = (question) => {
    const text = (question ?? q).trim()
    if (!text || loading) return
    setQ(''); setLoading(true)
    setHistory(h => [...h, { q: text, steps: [], thought: '', answer: null, typed: '', done: false }])
    scrollDown()
    esRef.current?.close()
    const es = new EventSource(`/api/ask/stock/stream?question=${encodeURIComponent(text)}`)
    esRef.current = es
    es.onmessage = (e) => {
      let ev; try { ev = JSON.parse(e.data) } catch { return }
      if (ev.type === 'step') { patchLast(it => ({ ...it, steps: [...it.steps, { label: ev.label, arg: ev.arg }] })); scrollDown() }
      else if (ev.type === 'thought') patchLast(it => ({ ...it, thought: ev.text }))
      else if (ev.type === 'answer') { patchLast(it => ({ ...it, answer: ev.text })); typewriter(ev.text || '') }
      else if (ev.type === 'error') { patchLast(it => ({ ...it, err: ev.error, done: true })); es.close(); setLoading(false) }
      else if (ev.type === 'done') { es.close(); setLoading(false) }
    }
    es.onerror = () => { es.close(); setLoading(false); patchLast(it => it.answer == null ? { ...it, err: '连接中断', done: true } : it) }
  }

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-3">
        <h3 className="text-[14px] font-semibold text-text-bright m-0">问问市场</h3>
        <span className="text-[10.5px] text-text-muted">个股涨跌/消息 · 这周市场什么风格 · 资金主线</span>
      </div>

      {holdings.length > 0 && history.length === 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {holdings.map((h, i) => (
            <button key={i} onClick={() => ask(`${h.stock_name || h.stock_code}最近为什么涨跌`)}
              className="text-[11px] px-2 py-0.5 rounded-full border border-border bg-surface-3 text-text-dim hover:text-text hover:border-accent/40">
              {h.stock_name || h.stock_code} ↗
            </button>
          ))}
        </div>
      )}

      <div ref={scrollBox} className={`space-y-3 mb-3 ${history.length ? 'max-h-[58vh] overflow-y-auto pr-1' : ''}`}>
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
