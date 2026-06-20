import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'

// A股 涨红跌绿: pct → 背景色(强度按幅度), 文字色
function cellBg(pct) {
  if (pct == null) return 'transparent'
  const a = Math.min(Math.abs(pct) / 6, 1) * 0.85 + 0.08
  return pct > 0 ? `rgba(207,92,92,${a})` : pct < 0 ? `rgba(95,168,108,${a})` : 'rgba(120,130,140,0.15)'
}
const pctColor = (v) => v == null ? 'text-text-dim' : v > 0 ? 'text-bear-bright' : v < 0 ? 'text-bull-bright' : 'text-text-dim'

export default function SectorMatrix() {
  const [days, setDays] = useState(10)
  const [m, setM] = useState(null)
  const [ai, setAi] = useState(null)
  const [loading, setLoading] = useState(true)
  const [aiLoading, setAiLoading] = useState(false)

  const load = useCallback((dy, force = false) => {
    setLoading(true)
    fetchJSON(`/api/sector/matrix?days=${dy}${force ? '&force=true' : ''}`).then(setM).catch(() => {}).finally(() => setLoading(false))
    setAiLoading(true)
    fetchJSON(`/api/sector/trend-ai?days=${dy}${force ? '&force=true' : ''}`).then(setAi).catch(() => {}).finally(() => setAiLoading(false))
  }, [])

  useEffect(() => { load(days) }, [days, load])

  if (loading && !m) return <div className="bg-surface-2 border border-border rounded-xl p-5 text-center text-text-dim text-[12px]">板块矩阵计算中…<span className="text-text-muted">（首次约 20–40 秒）</span></div>
  if (!m || !(m.rows || []).length) return null

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">板块趋势矩阵</h3>
          <span className="text-[10.5px] text-text-muted">近 {m.days} 日 · 红涨绿跌 · 资金/动能</span>
        </div>
        <div className="flex gap-1 items-center">
          {[10, 20].map(dy => (
            <button key={dy} onClick={() => setDays(dy)}
              className={`text-[11px] px-2 py-0.5 rounded border ${days === dy ? 'bg-accent/20 text-accent border-accent/40' : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
              {dy}日
            </button>
          ))}
          <button onClick={() => load(days, true)} className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text">刷新</button>
        </div>
      </div>

      {/* AI 趋势分析 */}
      {aiLoading && !ai && <div className="text-[11.5px] text-text-dim mb-3">AI 分析板块趋势中…</div>}
      {ai && ai.summary && (
        <div className="mb-3 px-3 py-2.5 rounded-lg bg-accent/10 border border-accent/30">
          <div className="text-[12.5px] text-text-bright leading-relaxed mb-1.5">{ai.summary}</div>
          <div className="space-y-1">
            {(ai.trends || []).map((t, i) => (
              <div key={i} className="text-[11.5px] leading-relaxed flex gap-1.5">
                <span className="text-accent shrink-0 font-medium">{t.type}</span>
                <span className="text-text-dim">{t.detail}</span>
              </div>
            ))}
          </div>
          {ai.holdings_note && <div className="text-[11px] text-info mt-1.5 leading-relaxed">持仓: {ai.holdings_note}</div>}
        </div>
      )}

      {/* 热力矩阵 */}
      <div className="overflow-x-auto -mx-1 px-1">
        <table className="border-collapse text-[10px]" style={{ minWidth: 560 }}>
          <thead>
            <tr className="text-text-muted">
              <th className="text-left font-normal sticky left-0 bg-surface-2 pr-2 z-10">板块</th>
              <th className="font-normal px-1 text-right">今日</th>
              <th className="font-normal px-1 text-right">累计</th>
              <th className="font-normal px-1 text-right">净流入</th>
              {(m.dates || []).map((d, i) => (
                <th key={i} className="font-normal px-0.5 text-center" style={{ minWidth: 30 }}>{d}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {m.rows.map((r, ri) => (
              <tr key={ri}>
                <td className="text-text-bright whitespace-nowrap sticky left-0 bg-surface-2 pr-2 z-10 py-0.5">
                  {r.name}{r.streak >= 2 && <span className="text-bear-bright ml-1">↑{r.streak}</span>}
                </td>
                <td className={`px-1 text-right font-mono ${pctColor(r.today_pct)}`}>{r.today_pct >= 0 ? '+' : ''}{r.today_pct}</td>
                <td className={`px-1 text-right font-mono font-semibold ${pctColor(r.cum_pct)}`}>{r.cum_pct >= 0 ? '+' : ''}{r.cum_pct}</td>
                <td className={`px-1 text-right font-mono ${pctColor(r.net_inflow)}`}>{r.net_inflow >= 0 ? '+' : ''}{r.net_inflow}亿</td>
                {(r.daily || []).map((c, ci) => (
                  <td key={ci} className="text-center font-mono" title={`${c.date} ${c.pct >= 0 ? '+' : ''}${c.pct}%`}
                    style={{ background: cellBg(c.pct), color: Math.abs(c.pct) > 2 ? '#fff' : 'var(--color-text-dim)' }}>
                    {c.pct >= 0 ? '+' : ''}{c.pct.toFixed(1)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="text-[10px] text-text-muted pt-2.5 mt-2 border-t border-border-subtle">
        同花顺行业 · 按近 {m.days} 日累计涨幅排序 · ↑N=连涨天数 · 纯客观, 不构成买卖建议
      </div>
    </div>
  )
}
