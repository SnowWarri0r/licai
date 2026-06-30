import { useState, useEffect, useRef } from 'react'
import { fetchJSON } from '../hooks/useApi'
import ProKline from './ProKline'
import { MiniMarkdown, SourcesBlock, streamAnalysis } from './askShared'

const TABS = [
  { key: 'gainers', label: '涨幅榜' },
  { key: 'by_amount', label: '成交额榜' },
]

function pctColor(v) {
  if (v > 0) return 'text-bear'
  if (v < 0) return 'text-bull'
  return 'text-text-dim'
}

// 右侧面板: 选中股票先看 K线; 想问再在底部输入框问(可选), 才跑 AI 分析
function StockPanel({ stock }) {
  const [q, setQ] = useState('')
  const [asking, setAsking] = useState(false)
  const [answer, setAnswer] = useState('')
  const [charts, setCharts] = useState([])
  const [sources, setSources] = useState([])
  const [steps, setSteps] = useState([])
  const abortRef = useRef(null)

  // 切换股票: 清空问答、停掉进行中的请求
  useEffect(() => {
    setQ(''); setAnswer(''); setCharts([]); setSources([]); setSteps([]); setAsking(false)
    return () => abortRef.current?.abort()
  }, [stock])

  const ask = () => {
    const question = q.trim()
    if (!question || asking || !stock) return
    abortRef.current?.abort()
    const ctrl = new AbortController(); abortRef.current = ctrl
    setAsking(true); setAnswer(''); setCharts([]); setSources([]); setSteps([])
    streamAnalysis(`${stock.name}(${stock.code}): ${question}`, {
      signal: ctrl.signal,
      onStep: (e) => setSteps(s => [...s, { label: e.label }]),
      onChart: (e) => setCharts(c => [...c, e.url]),
      onSource: (arr) => setSources(s => [...s, ...arr]),
      onAnswer: (t) => setAnswer(t),
      onError: () => setAsking(false),
      onDone: () => setAsking(false),
    })
  }

  if (!stock) {
    return (
      <div className="h-full flex items-center justify-center text-center px-6">
        <div className="text-text-muted text-[13px] leading-relaxed">
          点左侧任意一只股票看 K 线<br />
          <span className="text-[11px] text-text-dim">想问什么(为什么涨/量价/消息)在下面输入框问</span>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-baseline gap-2 px-4 py-2 border-b border-border-subtle shrink-0">
        <span className="text-[14px] font-semibold text-text-bright">{stock.name}</span>
        <span className="text-[11px] font-mono text-text-muted">{stock.code}</span>
        <span className={`text-[13px] font-mono font-semibold ${pctColor(stock.pct)}`}>
          {stock.pct >= 0 ? '+' : ''}{stock.pct}%
        </span>
        {stock['行业'] && <span className="text-[10.5px] text-text-dim ml-1">{stock['行业']}</span>}
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2 min-h-0">
        <ProKline code={stock.code} />

        {(asking || answer || steps.length > 0) && (
          <div className="mt-3 pt-3 border-t border-border-subtle">
            {asking && !answer && (
              <div className="flex flex-wrap gap-1.5 mb-1 items-center">
                <span className="text-[11px] text-text-dim">分析中…</span>
                {steps.slice(-6).map((s, i) => (
                  <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-surface-3 text-text-dim border border-border-subtle">{s.label}</span>
                ))}
              </div>
            )}
            {charts.map((src, k) => (
              <a key={k} href={src} target="_blank" rel="noreferrer" className="block mb-2">
                <img src={src} alt="K线图" loading="lazy" className="w-full rounded-lg border border-border-subtle" />
              </a>
            ))}
            {answer && <MiniMarkdown text={answer} sources={sources} />}
            {answer && <SourcesBlock sources={sources} />}
            {answer && <div className="mt-2 pt-2 border-t border-border-subtle text-[10px] text-text-muted">仅客观分析，不构成买卖建议</div>}
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-border px-3 py-2 flex gap-2">
        <input value={q} onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) ask() }}
          disabled={asking}
          placeholder={`想问点 ${stock.name} 什么?(可选) 例: 今天为什么涨 / 量价怎么看`}
          className="flex-1 text-[12px] px-3 py-2 rounded-lg bg-surface-3 border border-border text-text placeholder:text-text-muted focus:border-accent/50 outline-none disabled:opacity-50" />
        <button onClick={ask} disabled={asking || !q.trim()}
          className="text-[12px] px-3.5 py-2 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed">
          {asking ? '分析中' : '问'}
        </button>
      </div>
    </div>
  )
}

export default function Rankings() {
  const [tab, setTab] = useState('gainers')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(false)
  const [selected, setSelected] = useState(null)

  const load = () => {
    setLoading(true); setErr(false)
    fetchJSON('/api/market/rankings?limit=100')
      .then(d => { if (d.error) { setErr(true) } else setData(d) })
      .catch(() => setErr(true))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const list = (data && data[tab]) || []

  return (
    <div className="bg-surface-2 border border-border rounded-xl overflow-hidden flex flex-col lg:flex-row h-[calc(100vh-11rem)] min-h-[480px]">
      <div className="lg:w-[420px] shrink-0 flex flex-col border-b lg:border-b-0 lg:border-r border-border min-h-0">
        <div className="flex items-center gap-1 px-3 py-2 border-b border-border-subtle">
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`text-[12px] px-2.5 py-1 rounded border ${tab === t.key ? 'bg-accent/20 text-accent border-accent/40' : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
              {t.label}
            </button>
          ))}
          <span className="ml-auto text-[10px] text-text-muted">{data?.as_of ? data.as_of.slice(5) : ''}</span>
          <button onClick={load} title="刷新" className="text-[10.5px] px-1.5 py-0.5 rounded border border-border text-text-dim hover:text-text">刷新</button>
        </div>

        <div className="flex-1 overflow-y-auto min-h-0">
          {loading && <div className="text-center py-8 text-text-dim text-[12px]">加载榜单…</div>}
          {err && <div className="text-center py-8 text-text-dim text-[12px]">榜单源暂不可达（东财抖动），<button onClick={load} className="text-accent">重试</button></div>}
          {!loading && !err && list.map((r, i) => {
            const active = selected?.code === r.code
            return (
              <button key={r.code} onClick={() => setSelected(r)}
                className={`w-full flex items-center gap-2 px-3 py-1.5 text-left border-b border-border-subtle/60 ${active ? 'bg-accent/15' : 'hover:bg-surface-3/60'}`}>
                <span className="text-[10px] font-mono text-text-muted w-5 shrink-0 text-right">{i + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="text-[12.5px] text-text-bright truncate">{r.name}</span>
                    {r.is_st && <span className="text-[8.5px] px-1 rounded bg-bear/15 text-bear-bright shrink-0">ST</span>}
                  </span>
                  <span className="text-[10px] text-text-muted font-mono">{r.code} · {r['行业'] || '—'}</span>
                </span>
                <span className="text-right shrink-0">
                  <span className={`block text-[12.5px] font-mono font-semibold ${pctColor(r.pct)}`}>{r.pct >= 0 ? '+' : ''}{r.pct}%</span>
                  <span className="block text-[10px] text-text-muted font-mono">
                    {tab === 'by_amount' ? `${r['成交额亿']}亿` : `量比${r['量比'] ?? '—'}`}
                  </span>
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="flex-1 min-h-0 min-w-0">
        <StockPanel stock={selected} />
      </div>
    </div>
  )
}
