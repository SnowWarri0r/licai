import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'

// 情绪 → 色温 (A股 红暖绿冷)
const MOOD_COLOR = {
  '情绪高潮': '#cf5c5c', '回暖/进攻': '#d98a6a',
  '分歧/震荡': '#d4a05c', '退潮/亏钱效应': '#5fa86c', '数据不足': '#85a0b4',
}
const pctColor = (v) => v == null ? 'text-text-dim' : v > 0 ? 'text-bear-bright' : v < 0 ? 'text-bull-bright' : 'text-text-dim'

export default function SentimentThermometer() {
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchJSON('/api/market/sentiment').then(setD).catch(() => {}).finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="text-center py-4 text-text-dim text-[12px]">市场情绪加载中…</div>
  if (!d || !d.n_zt) return null
  const c = MOOD_COLOR[d.mood] || '#85a0b4'

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">市场情绪温度计</h3>
          <span className="text-[10.5px] text-text-muted">爱在冰川式 · 打板情绪</span>
        </div>
        <span className="text-[10.5px] text-text-muted">{d.date}</span>
      </div>

      {/* 情绪定性 */}
      <div className="mb-3 px-3 py-2.5 rounded-lg" style={{ background: c + '1a', border: `1px solid ${c}55` }}>
        <div className="flex items-baseline gap-2">
          <span className="text-[16px] font-bold" style={{ color: c }}>{d.mood}</span>
          {d.money_effect != null && (
            <span className="text-[11px] text-text-dim">赚钱效应 <span className={pctColor(d.money_effect)}>{d.money_effect > 0 ? '+' : ''}{d.money_effect}%</span></span>
          )}
        </div>
        {d.mood_desc && <div className="text-[11.5px] text-text-dim mt-1 leading-relaxed">{d.mood_desc}</div>}
      </div>

      {/* 指标 grid */}
      <div className="grid grid-cols-3 md:grid-cols-6 gap-2 mb-3 text-[11px]">
        {[
          ['涨停', d.n_zt, 'text-bear-bright'],
          ['跌停', d.n_dt, 'text-bull-bright'],
          ['炸板', `${d.n_zb}`, 'text-text-bright'],
          ['炸板率', `${d.zbl_rate}%`, d.zbl_rate >= 40 ? 'text-bull-bright' : 'text-text-bright'],
          ['最高连板', `${d.max_lianban}板`, 'text-accent'],
          ['昨涨停红盘', d.red_rate != null ? `${d.red_rate}%` : '--', d.red_rate >= 50 ? 'text-bear-bright' : 'text-bull-bright'],
        ].map(([label, val, cls], i) => (
          <div key={i} className="bg-surface-3 rounded-md px-2 py-1.5">
            <div className="text-text-dim text-[10px] mb-0.5">{label}</div>
            <div className={`font-mono font-semibold ${cls}`}>{val}</div>
          </div>
        ))}
      </div>

      {/* 连板梯队 + 空间龙头 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px]">
        {(d.ladder || []).length > 0 && (
          <div className="flex items-center gap-1.5">
            <span className="text-text-muted text-[10.5px]">连板梯队</span>
            {d.ladder.map((l, i) => (
              <span key={i} className="font-mono text-text-dim">{l.lb}板<span className="text-accent">×{l.count}</span></span>
            ))}
          </div>
        )}
        {(d.leaders || []).length > 0 && (
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="text-text-muted text-[10.5px]">空间龙头</span>
            <span className="text-text-bright truncate">{d.leaders.join(' / ')}</span>
          </div>
        )}
      </div>

      <div className="text-[10px] text-text-muted pt-2.5 mt-2 border-t border-border-subtle">
        纯客观情绪指标，看市场是高潮还是退潮 · 不构成任何买卖建议
      </div>
    </div>
  )
}
