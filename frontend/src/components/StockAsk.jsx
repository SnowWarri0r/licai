import { useState, useEffect, useRef } from 'react'
import { fetchJSON } from '../hooks/useApi'

const TOOL_CN = {
  resolve_stock: '解析代码', get_quote: '行情', get_trend: '走势',
  get_news: '新闻', get_holdings: '持仓', get_market_sentiment: '大盘情绪',
}

// 极简 markdown 渲染 (## 标题 / **粗** / - 列表 / 段落), 不引依赖
function renderInline(text, keyPrefix) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return parts.map((p, i) =>
    p.startsWith('**') && p.endsWith('**')
      ? <strong key={`${keyPrefix}-${i}`} className="text-text-bright">{p.slice(2, -2)}</strong>
      : <span key={`${keyPrefix}-${i}`}>{p}</span>
  )
}
function MiniMarkdown({ text }) {
  const lines = (text || '').split('\n')
  const out = []
  lines.forEach((ln, i) => {
    const t = ln.trim()
    if (!t) { out.push(<div key={i} className="h-1.5" />); return }
    if (t.startsWith('## ')) {
      out.push(<div key={i} className="text-[12.5px] font-semibold text-accent mt-2 mb-0.5">{renderInline(t.slice(3), i)}</div>)
    } else if (t.startsWith('### ')) {
      out.push(<div key={i} className="text-[12px] font-semibold text-text-bright mt-1.5">{renderInline(t.slice(4), i)}</div>)
    } else if (t.startsWith('- ') || t.startsWith('• ')) {
      out.push(<div key={i} className="flex gap-1.5 text-[12px] leading-relaxed"><span className="text-accent shrink-0">·</span><span className="text-text-dim">{renderInline(t.slice(2), i)}</span></div>)
    } else {
      out.push(<div key={i} className="text-[12px] text-text-dim leading-relaxed">{renderInline(t, i)}</div>)
    }
  })
  return <div>{out}</div>
}

export default function StockAsk() {
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState([])   // [{q, answer, tools, err}]
  const [holdings, setHoldings] = useState([])
  const bottomRef = useRef(null)

  useEffect(() => {
    fetchJSON('/api/portfolio').then(d => {
      const hs = Array.isArray(d) ? d : (d.holdings || d.positions || [])
      setHoldings(hs.filter(h => (h.stock_name || h.stock_code)).slice(0, 8))
    }).catch(() => {})
  }, [])

  const ask = async (question) => {
    const text = (question ?? q).trim()
    if (!text || loading) return
    setQ('')
    setLoading(true)
    setHistory(h => [...h, { q: text, answer: null, tools: null }])
    try {
      const r = await fetchJSON('/api/ask/stock', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text }),
      })
      setHistory(h => h.map((it, i) => i === h.length - 1
        ? { ...it, answer: r.answer || '', tools: r.tools_used || [], err: r.error } : it))
    } catch (e) {
      setHistory(h => h.map((it, i) => i === h.length - 1 ? { ...it, err: String(e), answer: '' } : it))
    } finally {
      setLoading(false)
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
    }
  }

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-3">
        <h3 className="text-[14px] font-semibold text-text-bright m-0">问问个股</h3>
        <span className="text-[10.5px] text-text-muted">为什么涨/跌 · 最近消息 · 跟我持仓的关系</span>
      </div>

      {/* 持仓快捷 chips */}
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

      {/* 对话历史 */}
      <div className="space-y-3 mb-3">
        {history.map((it, i) => (
          <div key={i}>
            <div className="text-[12px] text-text-bright bg-surface-3 rounded-lg px-3 py-1.5 inline-block">{it.q}</div>
            <div className="mt-2 px-3 py-2.5 rounded-lg bg-accent/8 border border-accent/25">
              {it.answer === null
                ? <div className="text-[11.5px] text-text-dim">分析中… <span className="text-text-muted">(Agent 取数据要 15–40 秒)</span></div>
                : it.err
                  ? <div className="text-[11.5px] text-bull-bright">出错: {it.err}</div>
                  : <>
                      {it.tools?.length > 0 && (
                        <div className="text-[10px] text-text-muted mb-1.5">查了: {[...new Set(it.tools)].map(t => TOOL_CN[t] || t).join(' · ')}</div>
                      )}
                      <MiniMarkdown text={it.answer} />
                    </>}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* 输入 */}
      <div className="flex gap-2">
        <input value={q} onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') ask() }}
          disabled={loading}
          placeholder="例: 洛阳钼业今天为什么大涨 / 中钨高新最近什么消息"
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
