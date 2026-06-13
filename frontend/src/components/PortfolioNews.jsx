import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import NewsDetailModal from './NewsDetailModal'

const KIND_LABEL = {
  news: { label: '新闻', color: '#85a0b4' },
  notice: { label: '公告', color: '#d4a05c' },
  market: { label: '市场', color: '#7a9b8e' },
}

const SOURCES = ['portfolio', 'market', 'smallmetal']
const SOURCE_LABEL = { portfolio: '持仓相关', market: '全市场要闻', smallmetal: '小金属' }
const SOURCE_API = {
  portfolio: '/api/news/portfolio',
  market: '/api/news/market',
  smallmetal: '/api/news/small-metal',
}

// 简化时间显示: "2026-05-20 09:30:00" → "今天 09:30" / "昨天 09:30" / "05-18 09:30"
function fmtTime(s) {
  if (!s) return '--'
  const ts = s.slice(0, 16)
  const today = new Date().toISOString().slice(0, 10)
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10)
  if (ts.startsWith(today)) return '今天 ' + ts.slice(11)
  if (ts.startsWith(yesterday)) return '昨天 ' + ts.slice(11)
  return ts.slice(5).replace('T', ' ')
}

const LEVEL_META = {
  good: { label: '利好', icon: '🟢', color: '#cf5c5c' },   // A 股口径红 = 涨
  bad:  { label: '利空', icon: '🔴', color: '#5fa86c' },   // 绿 = 跌
  watch:{ label: '关注', icon: '🟡', color: '#d4a05c' },
}

function DigestCard() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [collapsed, setCollapsed] = useState(false)

  const load = useCallback(async (force = false) => {
    setLoading(true); setErr('')
    try {
      const d = await fetchJSON(`/api/news/digest${force ? '?force=true' : ''}`)
      setData(d)
      if (d.error) setErr(d.error)
    } catch (e) { setErr(e?.message || '加载失败') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load(false) }, [load])

  return (
    <div className="border-b border-border bg-accent/5">
      <div className="px-3 md:px-5 py-2.5 flex items-baseline justify-between gap-2 flex-wrap">
        <div className="flex items-baseline gap-2">
          <span className="text-[12px] text-text-bright font-semibold">🤖 LLM 摘要</span>
          {data?.generated_at && (
            <span className="text-[10.5px] text-text-muted font-mono">{data.generated_at.slice(5, 16)}</span>
          )}
          {data?.input_count && (
            <span className="text-[10.5px] text-text-muted">· 从 {data.input_count} 条素材</span>
          )}
        </div>
        <div className="flex gap-1 items-center">
          <button onClick={() => setCollapsed(c => !c)}
            className="px-2 py-[2px] rounded text-[10.5px] cursor-pointer border border-border-med text-text-dim hover:text-accent">
            {collapsed ? '展开' : '收起'}
          </button>
          <button onClick={() => load(true)} disabled={loading}
            className="px-2 py-[2px] rounded text-[10.5px] cursor-pointer border border-accent/40 text-accent hover:bg-accent/10 disabled:opacity-50">
            {loading ? '...' : '↻ 重算'}
          </button>
        </div>
      </div>
      {!collapsed && (
        <div className="px-3 md:px-5 pb-3">
          {loading && !data ? (
            <div className="text-[11px] text-text-dim">LLM 摘要生成中... (约 10-30 秒)</div>
          ) : err && !data?.highlights?.length ? (
            <div className="text-[11px] text-bear">{err}</div>
          ) : data?.summary || data?.highlights?.length ? (
            <>
              {data.summary && (
                <div className="text-[12px] text-text leading-snug mb-2">{data.summary}</div>
              )}
              {data.highlights?.length > 0 && (
                <div className="space-y-1.5">
                  {data.highlights.map((h, i) => {
                    const meta = LEVEL_META[h.level] || { label: '关注', icon: '⚪', color: '#888' }
                    return (
                      <div key={i} className="text-[11.5px] leading-snug flex gap-2 items-baseline">
                        <span style={{ color: meta.color }} className="font-semibold text-[10.5px] whitespace-nowrap">
                          [{meta.label}]
                        </span>
                        <span className="flex-1">
                          <span className="text-text">{h.title}</span>
                          {h.related && (
                            <span className="ml-2 text-[10.5px] font-mono text-text-muted">{h.related}</span>
                          )}
                          {h.impact && (
                            <span className="ml-2 text-[10.5px] text-text-muted">→ {h.impact}</span>
                          )}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </>
          ) : (
            <div className="text-[11px] text-text-dim">暂无摘要</div>
          )}
        </div>
      )}
    </div>
  )
}

export default function PortfolioNews() {
  const [cache, setCache] = useState({})  // source -> data
  const [source, setSource] = useState(() => {
    const s = localStorage.getItem('newsSource')
    return SOURCES.includes(s) ? s : 'portfolio'
  })
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [filter, setFilter] = useState('all')  // all | news | notice
  const [codeFilter, setCodeFilter] = useState('')
  const [detail, setDetail] = useState(null)

  const fetchSource = useCallback(async (s, force = false) => {
    if (!force && cache[s]) return
    setLoading(true); setErr('')
    try {
      const d = await fetchJSON(SOURCE_API[s])
      setCache(prev => ({ ...prev, [s]: d }))
    } catch (e) { setErr(e?.message || '加载失败') }
    finally { setLoading(false) }
  }, [cache])

  useEffect(() => { fetchSource(source) }, [source, fetchSource])

  const pickSource = (s) => {
    setSource(s); localStorage.setItem('newsSource', s)
    setFilter('all'); setCodeFilter('')
  }

  const reload = () => fetchSource(source, true)

  const active = cache[source]
  const data = cache.portfolio

  if (loading && !active) {
    return (
      <section className="rounded-xl border border-border bg-surface/60 px-3 md:px-5 py-3 text-[12px] text-text-dim">
        资讯加载中... (首次拉取 akshare 可能要 5-10 秒)
      </section>
    )
  }
  if (err) {
    return (
      <section className="rounded-xl border border-border bg-surface/60 px-3 md:px-5 py-3 text-[12px] text-bear">{err}</section>
    )
  }

  let items = (active?.items || [])
  if (source === 'portfolio') {
    if (filter !== 'all') items = items.filter(x => x.kind === filter)
    if (codeFilter) items = items.filter(x => x.code === codeFilter)
  }

  const codes = source === 'portfolio' ? (data?.tracked_codes || []) : []
  const counts = (active?.items || []).reduce((a, x) => {
    a[x.kind] = (a[x.kind] || 0) + 1
    return a
  }, {})

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-baseline justify-between flex-wrap gap-2"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2 flex-wrap">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">资讯</h3>
          {/* 数据源切换: 持仓 vs 市场 */}
          <div className="flex gap-0.5">
            {SOURCES.map(s => (
              <button key={s} onClick={() => pickSource(s)}
                className="px-2 py-[3px] rounded text-[11px] cursor-pointer"
                style={{
                  border: '1px solid',
                  borderColor: source === s ? 'var(--color-accent)' : 'var(--color-border-med)',
                  color: source === s ? 'var(--color-accent)' : 'var(--color-text-dim)',
                  background: source === s ? 'var(--color-accent)1a' : 'transparent',
                  fontWeight: source === s ? 600 : 400,
                }}>
                {SOURCE_LABEL[s]}
              </button>
            ))}
          </div>
          <span className="text-[11px] text-text-dim">
            {source === 'portfolio' ? (
              <>跟踪 {codes.length} 只 A 股 · 新闻 {counts.news || 0} · 公告 {counts.notice || 0}</>
            ) : (
              <>{active?.count || 0} 条 · 财联社 + 东财 + 同花顺</>
            )}
          </span>
        </div>
        <div className="flex gap-1 items-center">
          {source === 'portfolio' && (
            <>
              <button onClick={() => setFilter('all')}
                className="px-2 py-[2px] rounded text-[10.5px] cursor-pointer"
                style={{ border: '1px solid', borderColor: filter === 'all' ? 'var(--color-accent)' : 'var(--color-border-med)',
                  color: filter === 'all' ? 'var(--color-accent)' : 'var(--color-text-dim)' }}>
                全部
              </button>
              <button onClick={() => setFilter('news')}
                className="px-2 py-[2px] rounded text-[10.5px] cursor-pointer"
                style={{ border: '1px solid', borderColor: filter === 'news' ? 'var(--color-accent)' : 'var(--color-border-med)',
                  color: filter === 'news' ? 'var(--color-accent)' : 'var(--color-text-dim)' }}>
                新闻
              </button>
              <button onClick={() => setFilter('notice')}
                className="px-2 py-[2px] rounded text-[10.5px] cursor-pointer"
                style={{ border: '1px solid', borderColor: filter === 'notice' ? 'var(--color-accent)' : 'var(--color-border-med)',
                  color: filter === 'notice' ? 'var(--color-accent)' : 'var(--color-text-dim)' }}>
                公告
              </button>
              <span className="mx-1 text-text-muted">·</span>
              <select value={codeFilter} onChange={e => setCodeFilter(e.target.value)}
                className="bg-bg border border-border rounded px-1.5 py-[2px] text-[10.5px] text-text">
                <option value="">所有股票</option>
                {codes.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </>
          )}
          <button onClick={reload}
            className="px-2 py-[2px] rounded text-[10.5px] cursor-pointer border border-border-med text-text-dim hover:text-accent hover:border-accent">
            ↻ 刷新
          </button>
        </div>
      </div>

      <DigestCard />

      <div className="max-h-[640px] overflow-y-auto">
        {items.length === 0 ? (
          <div className="text-center text-text-dim text-[11.5px] py-6">无匹配条目</div>
        ) : items.map((it, i) => {
          const meta = KIND_LABEL[it.kind] || { label: it.kind, color: '#888' }
          return (
            <div key={i} role="button" tabIndex={0}
              onClick={() => setDetail(it)}
              onKeyDown={e => { if (e.key === 'Enter') setDetail(it) }}
              className="block px-3 md:px-5 py-2.5 border-b border-border-subtle transition-colors hover:bg-surface-2/40 cursor-pointer">
              <div className="flex items-baseline gap-2 mb-0.5 flex-wrap">
                <span className="text-[9.5px] px-1 py-[1px] rounded font-medium tracking-wider"
                  style={{ background: meta.color + '20', color: meta.color, border: '1px solid ' + meta.color + '40' }}>
                  {meta.label}
                </span>
                {it.code && <span className="text-[11px] font-mono text-text-muted">{it.code}</span>}
                {it.name && <span className="text-[11px] text-text-dim">{it.name}</span>}
                {it.kind === 'notice' && it.type && (
                  <span className="text-[10px] text-text-muted">· {it.type}</span>
                )}
                <span className="text-[10.5px] text-text-muted ml-auto font-mono">{fmtTime(it.time)}</span>
                {it.source && (
                  <span className="text-[10px] text-text-muted">{it.source}</span>
                )}
              </div>
              <div className="text-[12px] text-text leading-snug">{it.title}</div>
              {it.content && (
                <div className="text-[10.5px] text-text-muted leading-snug mt-0.5 line-clamp-2">{it.content}</div>
              )}
            </div>
          )
        })}
      </div>

      <div className="px-3 md:px-5 py-2 bg-surface-2/40 text-[10.5px] text-text-muted leading-relaxed">
        {source === 'portfolio'
          ? '持仓股新闻 + 公告 (akshare 东财). 5min 缓存. 点条目查看解读.'
          : '全市场要闻 (akshare 财联社 + 东财 + 同花顺). 5min 缓存. 部分源不带链接. 点条目查看解读.'}
        仅展示信息, 不做"该买/该卖"判断 — 信号识别交给你自己.
      </div>
      {detail && <NewsDetailModal item={detail} onClose={() => setDetail(null)} />}
    </section>
  )
}
