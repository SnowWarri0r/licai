import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

// 收盘 AI 复盘日报: 持仓今日归因 + 板块 + 全球大事 + 明日关注
const pctColor = (s) => {
  const v = parseFloat(s)
  if (isNaN(v) || v === 0) return 'text-text-dim'
  return v > 0 ? 'text-bear-bright' : 'text-bull-bright'  // A股: 涨红 跌绿
}

export default function DailyReview() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async (force = false) => {
    setLoading(true); setErr('')
    try {
      setData(await fetchJSON(`/api/news/daily-review${force ? '?force=true' : ''}`))
    } catch (e) {
      setErr('复盘生成失败，稍后重试')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(false) }, [load])

  const has = data && (data.summary || (data.holdings || []).length || (data.global || []).length)

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">今日复盘</h3>
          {data?.date && <span className="text-[11px] font-mono text-text-dim">{data.date}</span>}
          {data?.generated_at && <span className="text-[10px] text-text-muted">{data.generated_at} 生成</span>}
        </div>
        <button onClick={() => load(true)} disabled={loading}
          className="text-[11px] px-2.5 py-1 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med cursor-pointer disabled:opacity-40">
          {loading ? '生成中…' : '↻ 重新复盘'}
        </button>
      </div>

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
