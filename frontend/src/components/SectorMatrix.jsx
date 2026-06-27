import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

// A股 涨红跌绿: pct → 背景色(强度按幅度), 文字色
function cellBg(pct) {
  if (pct == null) return 'transparent'
  const a = Math.min(Math.abs(pct) / 6, 1) * 0.85 + 0.08
  return pct > 0 ? `rgba(207,92,92,${a})` : pct < 0 ? `rgba(95,168,108,${a})` : 'rgba(120,130,140,0.15)'
}
const pctColor = (v) => v == null ? 'text-text-dim' : v > 0 ? 'text-bear-bright' : v < 0 ? 'text-bull-bright' : 'text-text-dim'

// 客户端缓存: 刷新页面立刻显示上次结果, 不再每次都转圈重拉 (后端本就缓存, 这里只去掉前端闪烁)
const CKEY = (dy) => `sectorMatrix_${dy}`
const readCache = (dy) => { try { return JSON.parse(localStorage.getItem(CKEY(dy)) || 'null') } catch { return null } }
const writeCache = (dy, m, ai) => { try { localStorage.setItem(CKEY(dy), JSON.stringify({ m, ai })) } catch {} }

export default function SectorMatrix() {
  const [days, setDays] = useState(10)
  const cached = readCache(10)
  const [m, setM] = useState(cached?.m || null)
  const [ai, setAi] = useState(cached?.ai || null)
  const [loading, setLoading] = useState(!cached)
  const [aiLoading, setAiLoading] = useState(!cached?.ai)

  const load = useCallback((dy, force = false) => {
    const cache = readCache(dy)
    // 有缓存先秒显, 不显示 loading; 无缓存才转圈
    if (cache?.m) setM(cache.m)
    if (cache?.ai) setAi(cache.ai)
    setLoading(!cache?.m || force)
    setAiLoading(!cache?.ai || force)
    let nm = cache?.m || null, na = cache?.ai || null
    fetchJSON(`/api/sector/matrix?days=${dy}${force ? '&force=true' : ''}`)
      .then(r => { nm = r; setM(r); writeCache(dy, nm, na) }).catch(() => {}).finally(() => setLoading(false))
    fetchJSON(`/api/sector/trend-ai?days=${dy}${force ? '&force=true' : ''}`)
      .then(r => { na = r; setAi(r); writeCache(dy, nm, na) }).catch(() => {}).finally(() => setAiLoading(false))
  }, [])

  useEffect(() => { load(days) }, [days, load])

  if (loading && !m) return <SkeletonCard rows={6} label="板块矩阵计算中(首次约20-40秒)" />
  if (!m || !(m.rows || []).length) return null

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">板块趋势矩阵</h3>
          <span className="text-[10.5px] text-text-muted">近 {m.days} 日 · 红涨绿跌 · 资金/动能</span>
          {m.intraday && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-bear/15 text-bear-bright">含今日盘中 {m.today}</span>
          )}
        </div>
        <div className="flex gap-1 items-center">
          {[10, 20].map(dy => (
            <button key={dy} onClick={() => setDays(dy)}
              className={`text-[11px] px-2 py-0.5 rounded border ${days === dy ? 'bg-accent/20 text-accent border-accent/40' : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
              {dy}日
            </button>
          ))}
          <button onClick={() => load(days, true)} disabled={loading || aiLoading}
            className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text disabled:opacity-40 disabled:cursor-wait">
            {(loading || aiLoading) ? '刷新中…' : '刷新'}
          </button>
        </div>
      </div>

      {/* AI 趋势分析 */}
      {aiLoading && !ai?.summary && <div className="text-[11.5px] text-text-dim mb-3">AI 分析板块趋势中…<span className="text-text-muted">(Opus 推理约 10–20 秒)</span></div>}
      {ai && ai.summary && (
        <div className={`mb-3 px-3 py-2.5 rounded-lg bg-accent/10 border border-accent/30 transition-opacity ${aiLoading ? 'opacity-50' : ''}`}>
          <div className="flex items-baseline justify-between gap-2 mb-1.5">
            <span className="text-[11px] text-accent font-medium">AI 趋势分析{aiLoading && <span className="ml-1 text-text-muted">· 重新分析中…</span>}</span>
          </div>
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

      {/* 热力矩阵 — 铺满整宽, 日格等分剩余空间 */}
      <div className="overflow-x-auto -mx-1 px-1">
        <table className="w-full border-collapse text-[11.5px]" style={{ tableLayout: 'fixed' }}>
          <colgroup>
            <col style={{ width: 104 }} />
            <col style={{ width: 56 }} />
            <col style={{ width: 56 }} />
            <col style={{ width: 64 }} />
            {(m.dates || []).map((_, i) => <col key={i} />)}
          </colgroup>
          <thead>
            <tr className="text-text-muted text-[10.5px]">
              <th className="text-left font-normal sticky left-0 bg-surface-2 pr-2 z-10 pb-1">板块</th>
              <th className="font-normal px-1 text-right pb-1">今日</th>
              <th className="font-normal px-1 text-right pb-1">累计</th>
              <th className="font-normal px-1 text-right pb-1">净流入</th>
              {(m.dates || []).map((d, i) => (
                <th key={i} className={`text-center pb-1 ${m.intraday && d === m.today ? 'text-bear-bright font-semibold' : 'font-normal'}`}>{d}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {m.rows.map((r, ri) => (
              <tr key={ri} className="border-t border-border-subtle/30">
                <td className="text-text-bright whitespace-nowrap truncate sticky left-0 bg-surface-2 pr-2 z-10 py-1.5">
                  {r.name}{r.streak >= 2 && <span className="text-bear-bright ml-1 text-[10px]">↑{r.streak}</span>}
                </td>
                <td className={`px-1 text-right font-mono ${pctColor(r.today_pct)}`}>{r.today_pct >= 0 ? '+' : ''}{r.today_pct}</td>
                <td className={`px-1 text-right font-mono font-semibold ${pctColor(r.cum_pct)}`}>{r.cum_pct >= 0 ? '+' : ''}{r.cum_pct}</td>
                <td className={`px-1 text-right font-mono ${pctColor(r.net_inflow)}`}>{r.net_inflow >= 0 ? '+' : ''}{r.net_inflow}亿</td>
                {(r.daily || []).map((c, ci) => (
                  <td key={ci} className="text-center font-mono px-1 py-1.5 rounded-sm" title={`${c.date} ${c.pct >= 0 ? '+' : ''}${c.pct}%`}
                    style={{ background: cellBg(c.pct), color: Math.abs(c.pct) > 1.5 ? '#fff' : 'var(--color-text-dim)' }}>
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
