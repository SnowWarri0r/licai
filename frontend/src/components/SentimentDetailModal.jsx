import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { fetchJSON } from '../hooks/useApi'

// A股: 涨红跌绿
const up = 'text-bear-bright', down = 'text-bull-bright'

// 量能红绿窄柱: 每日 vs 前一日, 放量红 / 缩量绿。
// trend 含一根参照日(首位), 只画后面每根(都有前一日可比, 不出灰柱)。
function VolBars({ trend }) {
  if (!trend || trend.length < 2) return null
  const shown = trend.slice(1)
  const vols = shown.map(t => t.vol)
  const max = Math.max(...vols), min = Math.min(...vols), span = max - min || 1
  return (
    <div className="flex items-end gap-3" style={{ height: 96 }}>
      {shown.map((t, i) => {
        const h = Math.round(16 + ((t.vol - min) / span) * 56)
        const prev = trend[i].vol   // 前一日(原数组里的前一个)
        const color = t.vol > prev ? 'bg-bear-bright' : t.vol < prev ? 'bg-bull-bright' : 'bg-text-dim'
        return (
          <div key={i} className="flex flex-col items-center justify-end gap-1 h-full" style={{ width: 34 }}>
            <span className="text-[10px] text-text-dim font-mono">{t.vol}</span>
            <div className={`rounded-t ${color}`} style={{ height: h, width: 22 }} />
            <span className="text-[9.5px] text-text-muted">{t.date}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function SentimentDetailModal({ summary, volume, onClose }) {
  const [d, setD] = useState(null)
  const [tab, setTab] = useState('ladder')   // ladder | sector | dt
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchJSON('/api/market/sentiment-detail').then(setD).catch(() => {}).finally(() => setLoading(false))
  }, [])

  // 打开时锁住底下页面滚动 + Esc 关闭
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => { document.body.style.overflow = prev; window.removeEventListener('keydown', onKey) }
  }, [onClose])

  const v = volume || {}
  const zt = d?.zt || []
  // 连板梯队: 按连板数分组(>=2), 降序
  const byLb = {}
  zt.forEach(s => { if (s.lb >= 2) (byLb[s.lb] = byLb[s.lb] || []).push(s) })
  const lbLevels = Object.keys(byLb).map(Number).sort((a, b) => b - a)
  // 板块: 按数量降序
  const bySec = {}
  zt.forEach(s => { (bySec[s.sector] = bySec[s.sector] || []).push(s) })
  const secList = Object.entries(bySec).sort((a, b) => b[1].length - a[1].length)

  const Chip = ({ s, color }) => (
    <span className="inline-flex items-baseline gap-1 text-[11.5px] bg-surface-3 rounded px-1.5 py-0.5">
      <span className="text-text-bright">{s.name}</span>
      <span className={`font-mono text-[10px] ${color}`}>{s.pct > 0 ? '+' : ''}{s.pct}%</span>
    </span>
  )

  return createPortal(
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5 w-[820px] max-w-[95vw] max-h-[90vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}>
        {/* 头 */}
        <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="text-[15px] font-semibold text-text-bright m-0">市场情绪明细</h3>
            {summary?.mood && <span className="text-[12px]" style={{ color: summary.moodColor }}>{summary.mood}</span>}
            <span className="text-[11px] text-text-muted font-mono">{d?.date || ''}</span>
          </div>
          <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>

        {/* 关键指标 */}
        <div className="grid grid-cols-3 md:grid-cols-6 gap-2 mb-3 text-[11px]">
          {[
            ['涨停', summary?.n_zt, up],
            ['跌停', summary?.n_dt, down],
            ['炸板率', summary?.zbl_rate != null ? `${summary.zbl_rate}%` : '--', 'text-text-bright'],
            ['最高连板', summary?.max_lianban ? `${summary.max_lianban}板` : '--', 'text-accent'],
            ['赚钱效应', summary?.money_effect != null ? `${summary.money_effect > 0 ? '+' : ''}${summary.money_effect}%` : '--', summary?.money_effect > 0 ? up : down],
            ['两市量', v.amount_wy != null ? `${v.amount_wy}万亿` : '--', 'text-text-bright'],
          ].map(([l, val, cls], i) => (
            <div key={i} className="bg-surface-3 rounded-md px-2 py-1.5">
              <div className="text-text-dim text-[10px] mb-0.5">{l}</div>
              <div className={`font-mono font-semibold ${cls}`}>{val ?? '--'}</div>
            </div>
          ))}
        </div>

        {/* 量能红绿柱 */}
        {(v.trend || []).length > 1 && (
          <div className="mb-4 px-3 py-3 rounded-lg bg-surface-3/50 border border-border-subtle">
            <div className="text-[10.5px] text-text-muted mb-2">近6日沪市成交量(亿股) · 放量红 / 缩量绿</div>
            <VolBars trend={v.trend} />
          </div>
        )}

        {/* 子tab */}
        <div className="flex gap-1 mb-3 border-b border-border-subtle">
          {[['ladder', '连板梯队'], ['sector', '板块热点'], ['dt', `跌停 ${d?.n_dt || 0}`]].map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)}
              className={`text-[12px] px-3 py-1.5 -mb-px border-b-2 ${tab === k ? 'border-accent text-text-bright font-medium' : 'border-transparent text-text-dim hover:text-text'}`}>
              {label}
            </button>
          ))}
        </div>

        {loading && <div className="text-center py-6 text-text-dim text-[12px]">明细加载中…</div>}

        {/* 连板梯队: 每个高度的具体股票 */}
        {!loading && tab === 'ladder' && (
          <div className="space-y-2.5">
            {lbLevels.length === 0 && <div className="text-text-dim text-[12px]">今日无 2 板以上个股</div>}
            {lbLevels.map(lb => (
              <div key={lb} className="flex gap-2">
                <span className="text-[12px] font-semibold text-accent shrink-0 w-12">{lb}板</span>
                <div className="flex flex-wrap gap-1.5">
                  {byLb[lb].map((s, i) => <Chip key={i} s={s} color={up} />)}
                </div>
              </div>
            ))}
            <div className="text-[11px] text-text-muted pt-1">首板(1板)共 {zt.filter(s => s.lb <= 1).length} 只</div>
          </div>
        )}

        {/* 板块热点: 每个行业的具体股票 */}
        {!loading && tab === 'sector' && (
          <div className="space-y-2.5">
            {secList.map(([sec, stocks], i) => (
              <div key={i} className="flex gap-2">
                <span className="text-[12px] text-text-bright shrink-0 w-20 truncate">{sec}<span className="text-accent font-mono ml-1">{stocks.length}</span></span>
                <div className="flex flex-wrap gap-1.5">
                  {stocks.map((s, j) => <Chip key={j} s={s} color={up} />)}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 跌停 */}
        {!loading && tab === 'dt' && (
          <div className="flex flex-wrap gap-1.5">
            {(d?.dt || []).length === 0 && <div className="text-text-dim text-[12px]">今日无跌停</div>}
            {(d?.dt || []).map((s, i) => <Chip key={i} s={s} color={down} />)}
          </div>
        )}

        <div className="text-[10px] text-text-muted pt-3 mt-3 border-t border-border-subtle">
          纯客观情绪数据 · 不构成任何买卖建议
        </div>
      </div>
    </div>,
    document.body
  )
}
