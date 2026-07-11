import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

// 组合净值曲线: TWR(时间加权, 出入金已剥离) vs 沪深300, 同起点归一 100。
// 纯客观展示, 不构成任何买卖建议。
const PERIODS = [
  { key: 60, label: '3月' },
  { key: 120, label: '半年' },
  { key: 250, label: '一年' },
]

const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v + '%'
const colorOf = (v) => v == null ? 'text-text-dim' : v >= 0 ? 'text-bear-bright' : 'text-bull-bright'

function Chart({ dates, twr, bench }) {
  const W = 680, H = 190, ML = 6, MR = 40, MT = 8, MB = 16
  const all = [...twr, ...bench.filter(v => v != null), 100]
  const lo = Math.min(...all), hi = Math.max(...all)
  const pad = Math.max((hi - lo) * 0.08, 0.5)
  const y0 = lo - pad, y1 = hi + pad
  const px = (i) => ML + (W - ML - MR) * i / Math.max(dates.length - 1, 1)
  const py = (v) => MT + (H - MT - MB) * (1 - (v - y0) / (y1 - y0))
  const line = (arr) => arr.map((v, i) => v == null ? null : `${px(i).toFixed(1)},${py(v).toFixed(1)}`)
    .filter(Boolean).join(' ')
  // x 轴取 4 个日期刻度
  const ticks = [0, 1, 2, 3].map(k => Math.round(k * (dates.length - 1) / 3))
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 'auto' }}>
      {/* 基线 100 */}
      <line x1={ML} y1={py(100)} x2={W - MR} y2={py(100)}
        stroke="var(--color-border-med)" strokeDasharray="4 4" strokeWidth="1" />
      <text x={W - MR + 4} y={py(100) + 3.5} fontSize="10" fill="var(--color-text-muted)">100</text>
      {/* 基准 */}
      <polyline points={line(bench)} fill="none" stroke="var(--color-text-muted)"
        strokeWidth="1.4" opacity="0.7" />
      {/* 组合 TWR */}
      <polyline points={line(twr)} fill="none" stroke="#c8a876" strokeWidth="2" />
      {/* 端点值 */}
      <text x={W - MR + 4} y={py(twr[twr.length - 1]) + 3.5} fontSize="10.5" fontWeight="600"
        fill="#c8a876">{twr[twr.length - 1].toFixed(1)}</text>
      {(() => { const b = bench[bench.length - 1]; return b == null ? null : (
        <text x={W - MR + 4} y={py(b) + (Math.abs(py(b) - py(twr[twr.length - 1])) < 11 ? 14 : 3.5)}
          fontSize="10" fill="var(--color-text-muted)">{b.toFixed(1)}</text>) })()}
      {ticks.map(i => (
        <text key={i} x={px(i)} y={H - 3} fontSize="9.5" fill="var(--color-text-muted)"
          textAnchor={i === 0 ? 'start' : i === dates.length - 1 ? 'end' : 'middle'}>
          {dates[i]?.slice(5)}
        </text>
      ))}
    </svg>
  )
}

export default function PortfolioCurve() {
  const [days, setDays] = useState(60)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(false)

  useEffect(() => {
    let dead = false
    setLoading(true); setErr(false)
    fetchJSON(`/api/portfolio/curve?days=${days}`)
      .then(d => { if (!dead) { if (d.error) setErr(d.error); else setData(d) } })
      .catch(() => { if (!dead) setErr(true) })
      .finally(() => { if (!dead) setLoading(false) })
    return () => { dead = true }
  }, [days])

  const m = data?.metrics
  return (
    <div className="mb-3">
      <div className="flex items-center gap-2 mb-1.5">
        <div className="text-[11px] text-text-muted tracking-wider">组合净值曲线（TWR · 出入金已剥离）</div>
        <div className="ml-auto flex gap-1">
          {PERIODS.map(p => (
            <button key={p.key} onClick={() => setDays(p.key)}
              className={`text-[10.5px] px-2 py-0.5 rounded ${days === p.key ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
              {p.label}
            </button>
          ))}
        </div>
      </div>
      {loading && !data && <SkeletonCard bare rows={4} label="逐日重建组合市值中…（首算约 1 分钟, 之后 1 小时缓存）" />}
      {!loading && err && <div className="text-[11.5px] text-text-dim py-3">{typeof err === 'string' ? err : '曲线暂不可达'}</div>}
      {data && !err && (
        <>
          <div className="flex items-center gap-4 mb-1 flex-wrap">
            <span className="text-[11px] text-text-muted">区间收益 <span className={`font-mono font-semibold text-[13px] ${colorOf(m['区间收益%'])}`}>{fmtPct(m['区间收益%'])}</span></span>
            <span className="text-[11px] text-text-muted">最大回撤 <span className="font-mono font-semibold text-[13px] text-text">{m['最大回撤%']}%</span></span>
            <span className="text-[11px] text-text-muted">沪深300 <span className={`font-mono text-[12px] ${colorOf(m['基准收益%'])}`}>{fmtPct(m['基准收益%'])}</span></span>
            <span className="text-[11px] text-text-muted">超额 <span className={`font-mono font-semibold text-[13px] ${colorOf(m['超额%'])}`}>{fmtPct(m['超额%'])}</span></span>
            <span className="text-[10px] text-text-dim ml-auto">起点 {m['起点']}</span>
          </div>
          <Chart dates={data.dates} twr={data.twr} bench={data.bench.series} />
          <div className="flex items-center gap-3 mt-0.5">
            <span className="text-[9.5px] text-text-dim"><span className="inline-block w-3 h-[2px] align-middle mr-1" style={{ background: '#c8a876' }} />我的组合</span>
            <span className="text-[9.5px] text-text-dim"><span className="inline-block w-3 h-[2px] align-middle mr-1 bg-text-muted opacity-70" />沪深300</span>
            <span className="text-[9px] text-text-dim ml-auto">现金/理财/机器人按成本基线近似 · 不构成买卖建议</span>
          </div>
        </>
      )}
    </div>
  )
}
