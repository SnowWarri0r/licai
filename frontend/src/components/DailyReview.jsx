import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

// 收盘 AI 复盘日报: 持仓今日归因 + 板块 + 全球大事 + 明日关注
const pctColor = (s) => {
  const v = parseFloat(s)
  if (isNaN(v) || v === 0) return 'text-text-dim'
  return v > 0 ? 'text-bear-bright' : 'text-bull-bright'  // A股: 涨红 跌绿
}

// 大盘全景: 今天全市场客观定格(情绪广度 + 主线 + 机构龙虎榜)。客观呈现,非买卖信号。
function MarketPanorama() {
  const [m, setM] = useState(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    fetchJSON('/api/market/eod-review').then(d => alive && setM(d)).catch(() => {}).finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [])
  if (loading) return <SkeletonCard bare rows={3} label="大盘全景加载中" />
  if (!m || (!m['情绪'] && !m['主线'])) return null
  const s = m['情绪'] || {}, mx = m['主线'] || {}, inst = m['机构龙虎榜'] || {}
  const st = mx['风格'] || {}
  return (
    <div className="bg-surface-3 rounded-lg px-3 py-2.5 space-y-2">
      <div className="text-[11px] text-text-muted tracking-wider">大盘全景 · {m.date} 周{m.weekday}</div>
      {/* 情绪与广度 */}
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[12px] font-mono">
        <span>涨停 <span className="text-bear-bright font-semibold">{s['涨停'] ?? '--'}</span></span>
        <span>跌停 <span className="text-bull-bright font-semibold">{s['跌停'] ?? '--'}</span></span>
        <span className="text-text-dim">炸板率 {s['炸板率%'] ?? '--'}%</span>
        {s['最高连板'] != null && <span className="text-text-dim">最高 {s['最高连板']} 连板</span>}
        {s['上涨家数'] != null && <span><span className="text-bear-bright">{s['上涨家数']}</span><span className="text-text-muted">涨</span> / <span className="text-bull-bright">{s['下跌家数']}</span><span className="text-text-muted">跌</span></span>}
        {s['赚钱效应'] && <span className="text-accent">{s['赚钱效应']}</span>}
      </div>
      {/* 今日主线: 板块 / 概念扎堆 */}
      {(mx['板块扎堆'] || mx['概念扎堆']) && (
        <div className="space-y-1">
          {mx['板块扎堆']?.length > 0 && (
            <div className="flex flex-wrap items-baseline gap-1.5 text-[11px]">
              <span className="text-text-muted shrink-0">主线板块</span>
              {mx['板块扎堆'].slice(0, 6).map((b, i) => (
                <span key={i} className="bg-surface-2 rounded px-1.5 py-0.5 text-text">{b['行业']}<span className="text-text-muted ml-1">{b['上榜数']}</span></span>
              ))}
            </div>
          )}
          {mx['概念扎堆']?.length > 0 && (
            <div className="flex flex-wrap items-baseline gap-1.5 text-[11px]">
              <span className="text-text-muted shrink-0">概念扎堆</span>
              {mx['概念扎堆'].slice(0, 8).map((c, i) => (
                <span key={i} className="bg-surface-2 rounded px-1.5 py-0.5 text-accent/90">{c['概念']}<span className="text-text-muted ml-1">{c['上榜数']}</span></span>
              ))}
            </div>
          )}
        </div>
      )}
      {/* 风格 */}
      {st['强势股均换手%'] != null && (
        <div className="text-[11px] text-text-dim">
          风格 · 强势股均换手 {st['强势股均换手%']}% · 小盘占 {st['小盘(<50亿)占比%']}% · 大盘占 {st['大盘(>500亿)占比%']}%
        </div>
      )}
      {/* 机构龙虎榜今日净买 */}
      {(inst['净买top'] || []).length > 0 && (
        <div className="flex flex-wrap items-baseline gap-1.5 text-[11px]">
          <span className="text-text-muted shrink-0">机构净买(龙虎榜)</span>
          {inst['净买top'].slice(0, 6).map((x, i) => (
            <span key={i} className="bg-surface-2 rounded px-1.5 py-0.5">
              <span className="text-text">{x.name}</span>
              <span className="text-bear-bright font-mono ml-1">{x['机构净买亿']}亿</span>
            </span>
          ))}
        </div>
      )}
      <div className="text-[10px] text-text-muted">客观定格,不预测明天、不构成买卖建议;机构龙虎榜为上榜日抽样滞后</div>
    </div>
  )
}

export default function DailyReview({ bare = false }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async (force = false) => {
    setLoading(true); setErr('')
    try {
      setData(await fetchJSON(`/api/news/daily-review${force ? '?force=true' : ''}`))
    } catch {
      setErr('复盘生成失败，稍后重试')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(false) }, [load])

  const has = data && (data.summary || (data.holdings || []).length || (data.global || []).length)

  return (
    <div className={bare ? '' : 'bg-surface-2 border border-border rounded-xl p-4 md:p-5'}>
      <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className={bare ? 'text-[11px] text-text-muted tracking-wider m-0' : 'text-[14px] font-semibold text-text-bright m-0'}>{bare ? '今日组合归因' : '今日复盘'}</h3>
          {data?.date && <span className="text-[11px] font-mono text-text-dim">{data.date}</span>}
          {data?.generated_at && <span className="text-[10px] text-text-muted">{data.generated_at} 生成</span>}
        </div>
        <button onClick={() => load(true)} disabled={loading}
          className="text-[11px] px-2.5 py-1 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med cursor-pointer disabled:opacity-40">
          {loading ? '生成中…' : '↻ 重新复盘'}
        </button>
      </div>

      {/* 大盘全景: 今天全市场发生了什么(独立取数, 不随 AI 复盘的成败) */}
      <div className="mb-3.5"><MarketPanorama /></div>

      {loading && !data && <SkeletonCard bare rows={5} label="AI 复盘生成中" />}
      {err && <div className="text-center py-4 text-bear text-[12px]">{err}</div>}

      {has && (
        <div className="space-y-3.5">
          {data.summary && (
            <div className="text-[12.5px] text-text-bright bg-surface-3 rounded-lg px-3 py-2">{data.summary}</div>
          )}

          {(data.holdings || []).length > 0 && (
            <div>
              <div className="text-[11px] text-text-muted mb-1.5 tracking-wider">持仓归因</div>
              <div className="space-y-1.5">
                {data.holdings.map((h, i) => (
                  <div key={i} className="flex items-baseline gap-2 text-[12px]">
                    <span className="text-text-bright font-medium min-w-[64px] shrink-0">{h.name}</span>
                    <span className={`font-mono text-[11.5px] min-w-[52px] shrink-0 ${pctColor(h.change)}`}>{h.change}</span>
                    <span className="text-text-dim">{h.why}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {(data.sectors || []).length > 0 && (
            <div>
              <div className="text-[11px] text-text-muted mb-1.5 tracking-wider">所属板块</div>
              <div className="flex flex-wrap gap-1.5">
                {data.sectors.map((s, i) => (
                  <span key={i} className="inline-flex items-center gap-1 bg-surface-3 rounded-md px-2 py-1 text-[11px]">
                    <span className="text-text">{s.name}</span>
                    <span className={`font-mono ${pctColor(s.change)}`}>{s.change}</span>
                    {s.note && <span className="text-text-muted">· {s.note}</span>}
                  </span>
                ))}
              </div>
            </div>
          )}

          {(data.global || []).length > 0 && (
            <div>
              <div className="text-[11px] text-text-muted mb-1.5 tracking-wider">全球大事</div>
              <ul className="space-y-1">
                {data.global.map((g, i) => (
                  <li key={i} className="text-[12px] text-text-dim flex gap-1.5">
                    <span className="text-text-muted shrink-0">·</span><span>{g}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(data.tomorrow || []).length > 0 && (
            <div>
              <div className="text-[11px] text-text-muted mb-1.5 tracking-wider">明日关注</div>
              <ul className="space-y-1">
                {data.tomorrow.map((t, i) => (
                  <li key={i} className="text-[12px] text-accent/90 flex gap-1.5">
                    <span className="shrink-0">→</span><span>{t}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="text-[10px] text-text-muted pt-1 border-t border-border-subtle">
            AI 复盘仅作资讯归因，不构成投资建议
          </div>
        </div>
      )}
    </div>
  )
}
