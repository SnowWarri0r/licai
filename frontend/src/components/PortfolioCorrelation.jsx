import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

// 在持标的日收益相关性矩阵(价格口径): 量化同源风险。纯客观统计, 不构成任何买卖建议。
// 色深 = |相关|; >0.8 意味着"名字不同, 涨跌是一回事"。

function cellStyle(v, isSelf) {
  if (v == null) return { color: 'var(--color-text-dim)' }
  const a = Math.min(Math.abs(v), 1)
  if (isSelf) return { color: 'var(--color-text-dim)' }
  // 正相关金色系, 负相关蓝灰系; 透明度随强度
  const base = v >= 0 ? '200,168,118' : '133,160,180'
  return {
    background: `rgba(${base},${(a * 0.55).toFixed(2)})`,
    color: a > 0.55 ? '#1a1b1f' : 'var(--color-text)',
    fontWeight: a >= 0.8 ? 700 : 400,
  }
}

const shortName = (s) => (s || '').replace(/(ETF|基金)?(国泰|华夏|大成|华安|易方达|博时|嘉实|天弘|摩根|鹏华)?$/, '') || s

export default function PortfolioCorrelation() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(false)

  useEffect(() => {
    let dead = false
    fetchJSON('/api/portfolio/correlation?days=60')
      .then(d => { if (!dead) { if (d.error) setErr(d.error); else setData(d) } })
      .catch(() => { if (!dead) setErr(true) })
      .finally(() => { if (!dead) setLoading(false) })
    return () => { dead = true }
  }, [])

  if (loading) return <SkeletonCard bare rows={3} label="相关性矩阵计算中…" />
  if (err) return <div className="text-[11px] text-text-dim py-2">{typeof err === 'string' ? err : '相关性暂不可达'}</div>
  if (!data) return null

  const { names, matrix, pairs } = data
  const topHigh = (pairs || []).filter(p => p.corr >= 0.8)
  return (
    <div className="mb-3">
      <div className="text-[11px] text-text-muted tracking-wider mb-1.5">
        持仓相关性（近{data.days}日 · 日收益 · 价格口径）
      </div>
      {topHigh.length > 0 && (
        <div className="text-[11px] text-text mb-1.5">
          <span className="text-accent">⚠ 高同源:</span>{' '}
          {topHigh.map(p => `${shortName(p.a)}×${shortName(p.b)} ${p.corr}`).join('、')}
          <span className="text-text-dim"> —— 相关性≥0.8, 分散是名义上的</span>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="text-[10px] font-mono border-collapse">
          <thead>
            <tr>
              <th className="pr-1.5"></th>
              {names.map(n => (
                <th key={n} className="px-1 pb-1 font-normal text-text-dim text-left align-bottom"
                  style={{ writingMode: 'vertical-rl', maxHeight: 72 }}>{shortName(n)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.map((row, i) => (
              <tr key={i}>
                <td className="pr-1.5 py-[1px] text-text-dim whitespace-nowrap text-right">{shortName(names[i])}</td>
                {row.map((v, j) => (
                  <td key={j} className="px-1 py-[1px] text-center rounded" style={cellStyle(v, i === j)}>
                    {i === j ? '—' : v == null ? '·' : v.toFixed(2)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-[9px] text-text-dim mt-1">
        &gt;0.8 涨跌基本一回事 · 0.4-0.8 中度同向 · &lt;0.2 基本独立 · 现金/理财/机器人无价格序列不参与 · 纯客观统计, 不构成买卖建议
      </div>
    </div>
  )
}
