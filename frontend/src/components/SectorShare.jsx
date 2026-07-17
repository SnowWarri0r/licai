import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'

const pctCls = (v) => v == null ? 'text-text-dim' : v > 0 ? 'text-bear' : v < 0 ? 'text-bull' : 'text-text-dim'
const fmtD = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}`

// 板块成交额份额: 资金往哪聚拢/从哪撤离(份额迁移比涨跌幅更接近资金真实走向)
export default function SectorShare() {
  const [d, setD] = useState(null)
  const [showAll, setShowAll] = useState(false)

  useEffect(() => {
    fetchJSON('/api/market/sector-share').then(setD).catch(() => {})
  }, [])

  const rows = d?.rows || []
  if (!rows.length) return null
  const shown = showAll ? rows : rows.slice(0, 15)
  const maxShare = rows[0]?.share_pct || 1
  const hasD1 = rows.some(r => r.d1 != null)

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-3 flex-wrap">
        <h3 className="text-[14px] font-semibold text-text-bright m-0">板块成交份额</h3>
        <span className="text-[10.5px] text-text-muted">占全市场成交额比重 · 两市板块合计 {d.total_yi?.toLocaleString()} 亿</span>
        {!hasD1 && <span className="text-[10px] text-text-dim">份额变化列从明天起随每日收盘档案出现</span>}
      </div>

      <div className="grid grid-cols-[minmax(64px,auto)_1fr_auto_auto_auto] gap-x-3 gap-y-0 text-[11.5px] items-center">
        <div className="text-[10px] text-text-muted pb-1">板块</div>
        <div className="text-[10px] text-text-muted pb-1">份额</div>
        <div className="text-[10px] text-text-muted pb-1 text-right">今日</div>
        <div className="text-[10px] text-text-muted pb-1 text-right" title={`较 ${d.baseline?.d1 || '上一档案日'} 份额变化(百分点)`}>较昨日</div>
        <div className="text-[10px] text-text-muted pb-1 text-right" title={`较 ${d.baseline?.d5 || '5个交易日前'} 份额变化(百分点)`}>较5日</div>
        {shown.map(r => (
          <div key={r.board} className="contents">
            <div className="text-text py-[3px] truncate">{r.board}</div>
            <div className="py-[3px]">
              <div className="flex items-center gap-1.5">
                <div className="h-[9px] rounded-sm shrink-0" style={{
                  width: `${Math.max(2, r.share_pct / maxShare * 100)}%`,
                  maxWidth: '82%',
                  background: r.pct >= 0 ? 'rgba(207,92,92,0.55)' : 'rgba(95,168,108,0.55)',
                }} />
                <span className="font-mono text-[10.5px] text-text-dim shrink-0">{r.share_pct}%</span>
              </div>
            </div>
            <div className={`py-[3px] font-mono text-right ${pctCls(r.pct)}`}>{r.pct >= 0 ? '+' : ''}{r.pct}%</div>
            <div className={`py-[3px] font-mono text-right ${pctCls(r.d1)}`}>{fmtD(r.d1)}</div>
            <div className={`py-[3px] font-mono text-right ${pctCls(r.d5)}`}>{fmtD(r.d5)}</div>
          </div>
        ))}
      </div>

      <div className="flex items-baseline justify-between mt-2 pt-2 border-t border-border-subtle">
        <span className="text-[10px] text-text-muted leading-relaxed">
          份额升=资金聚拢, 份额降=退潮; 份额升但板块跌=放量分歧 · 条色=今日涨跌 · 纯客观数据, 非买卖建议
        </span>
        <button onClick={() => setShowAll(v => !v)} className="text-[10.5px] text-accent shrink-0 ml-2">
          {showAll ? '收起' : `全部 ${rows.length} 个`}
        </button>
      </div>
    </div>
  )
}
