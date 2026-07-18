import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'
import SentimentDetailModal from './SentimentDetailModal'

// 情绪 → 色温 (A股 红暖绿冷)
const MOOD_COLOR = {
  '情绪高潮': '#cf5c5c', '回暖/进攻': '#d98a6a',
  '分歧/震荡': '#d4a05c', '退潮/亏钱效应': '#5fa86c', '数据不足': '#85a0b4',
}
const pctColor = (v) => v == null ? 'text-text-dim' : v > 0 ? 'text-bear-bright' : v < 0 ? 'text-bull-bright' : 'text-text-dim'

// 情绪周期时间轴: 近30交易日 涨停柱(上,红)/跌停柱(下,绿) + 赚钱效应折线(金)
function CycleStrip() {
  const [d, setD] = useState(null)
  useEffect(() => {
    fetchJSON('/api/market/sentiment-history?days=30').then(setD).catch(() => {})
  }, [])
  const s = d?.series || []
  if (s.length < 5) return null

  const W = 720, H = 120, P = { l: 8, r: 8, t: 14, b: 16 }
  const midY = P.t + (H - P.t - P.b) * 0.5
  const half = (H - P.t - P.b) / 2 - 2
  const n = s.length
  const step = (W - P.l - P.r) / n
  const bw = Math.max(4, Math.min(14, step * 0.42))
  const maxZt = Math.max(...s.map(r => r.n_zt || 0), 1)
  const maxDt = Math.max(...s.map(r => r.n_dt || 0), 1)
  const maxMe = Math.max(...s.map(r => Math.abs(r.money_effect ?? 0)), 1)
  const x = (i) => P.l + step * (i + 0.5)
  const meY = (v) => midY - (v / maxMe) * half
  const mePts = s.map((r, i) => r.money_effect == null ? null : `${x(i).toFixed(1)},${meY(r.money_effect).toFixed(1)}`)
    .filter(Boolean).join(' ')

  return (
    <div className="mb-3">
      <div className="flex items-baseline gap-2 mb-1">
        <span className="text-[10.5px] text-text-muted">情绪周期 · 近{n}个交易日</span>
        <span className="text-[9.5px] text-text-dim">
          <span className="text-bear">■</span>涨停(上) <span className="text-bull">■</span>跌停(下) <span style={{ color: '#c8a876' }}>—</span>赚钱效应
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full block" style={{ height: 'auto' }}>
        <line x1={P.l} x2={W - P.r} y1={midY} y2={midY} stroke="rgba(255,255,255,0.12)" strokeWidth="1" />
        {s.map((r, i) => {
          const hu = (r.n_zt / maxZt) * half
          const hd = (r.n_dt / maxDt) * half
          const op = r.partial ? 0.45 : 0.85
          return (
            <g key={r.date}>
              <title>{`${r.date}${r.partial ? '(盘中)' : ''}  涨停${r.n_zt} 跌停${r.n_dt} 炸板率${r.zbl_rate}% 最高${r.max_lb}板 赚钱效应${r.money_effect == null ? '—' : (r.money_effect > 0 ? '+' : '') + r.money_effect + '%'}`}</title>
              <rect x={x(i) - bw / 2} y={midY - hu} width={bw} height={Math.max(hu, 0.5)} fill="#cf5c5c" opacity={op} />
              <rect x={x(i) - bw / 2} y={midY} width={bw} height={Math.max(hd, 0.5)} fill="#5fa86c" opacity={op} />
              {r.max_lb >= 4 && (
                <text x={x(i)} y={midY - hu - 3} textAnchor="middle" fontSize="8.5" fill="#c8a876">{r.max_lb}板</text>
              )}
              {(i === 0 || i === n - 1 || i === Math.floor(n / 2)) && (
                <text x={x(i)} y={H - 4} textAnchor="middle" fontSize="8.5" fill="#6b7280">{r.date.slice(5)}</text>
              )}
            </g>
          )
        })}
        {mePts && <polyline points={mePts} fill="none" stroke="#c8a876" strokeWidth="1.6" opacity="0.9" />}
      </svg>
    </div>
  )
}

export default function SentimentThermometer() {
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)
  const [showDetail, setShowDetail] = useState(false)
  // 客户端缓存: 刷新页面秒显上次 AI 解读, 不再每次转圈重拉 (后端本就缓存)
  const cachedAi = (() => { try { return JSON.parse(localStorage.getItem('sentimentAi') || 'null') } catch { return null } })()
  const [ai, setAi] = useState(cachedAi)
  const [aiLoading, setAiLoading] = useState(!cachedAi)

  const loadAi = (force = false) => {
    setAiLoading(force || !cachedAi)
    fetchJSON(`/api/market/sentiment-ai${force ? '?force=true' : ''}`)
      .then(r => { setAi(r); try { if (r?.summary) localStorage.setItem('sentimentAi', JSON.stringify(r)) } catch {} })
      .catch(() => {}).finally(() => setAiLoading(false))
  }

  useEffect(() => {
    fetchJSON('/api/market/sentiment').then(setD).catch(() => {}).finally(() => setLoading(false))
    loadAi()
  }, [])

  if (loading) return <SkeletonCard rows={3} label="情绪加载中" />
  if (!d || !d.n_zt) return null
  const c = MOOD_COLOR[d.mood] || '#85a0b4'
  const v = d.volume || {}

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">市场情绪温度计</h3>
          <span className="text-[10.5px] text-text-muted">涨停/连板/量能</span>
        </div>
        <button onClick={() => setShowDetail(true)}
          className="text-[11px] px-2.5 py-1 rounded border border-accent/40 text-accent hover:bg-accent/10">
          看具体股票 →
        </button>
      </div>

      {/* 情绪定性 */}
      <div className="mb-3 px-3 py-2.5 rounded-lg" style={{ background: c + '1a', border: `1px solid ${c}55` }}>
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-[16px] font-bold" style={{ color: c }}>{d.mood}</span>
          {d.money_effect != null && (
            <span className="text-[11px] text-text-dim">赚钱效应 <span className={pctColor(d.money_effect)}>{d.money_effect > 0 ? '+' : ''}{d.money_effect}%</span></span>
          )}
          {v.amount_wy != null && (
            <span className="text-[11px] text-text-dim">· 两市 <span className="text-text-bright font-mono">{v.amount_wy}万亿</span>
              {v.label && <span className={`ml-1 ${v.ratio > 0 ? 'text-bear-bright' : v.ratio < 0 ? 'text-bull-bright' : 'text-text-dim'}`} title="今日沪市成交量 较前5个交易日均值">{v.label}<span className="text-text-muted">较5日均</span>{v.ratio != null ? `${v.ratio > 0 ? '+' : ''}${v.ratio}%` : ''}</span>}
            </span>
          )}
        </div>
        {d.mood_desc && <div className="text-[11.5px] text-text-dim mt-1 leading-relaxed">{d.mood_desc}</div>}
      </div>

      {/* AI 情绪解读 */}
      {aiLoading && !ai?.summary && <div className="text-[11.5px] text-text-dim mb-3">AI 分析市场情绪中…<span className="text-text-muted">(Opus 推理约 10–20 秒)</span></div>}
      {ai && ai.summary && (
        <div className={`mb-3 px-3 py-2.5 rounded-lg bg-accent/10 border border-accent/30 transition-opacity ${aiLoading ? 'opacity-50' : ''}`}>
          <div className="flex items-baseline justify-between gap-2 mb-1.5">
            <span className="text-[11px] text-accent font-medium">AI 情绪解读{aiLoading && <span className="ml-1 text-text-muted">· 重新分析中…</span>}</span>
            <button onClick={() => loadAi(true)} disabled={aiLoading}
              className="text-[10.5px] text-text-muted hover:text-text disabled:opacity-40 disabled:cursor-wait">
              {aiLoading ? '分析中…' : '重新分析'}
            </button>
          </div>
          <div className="text-[12.5px] text-text-bright leading-relaxed mb-1.5">{ai.summary}</div>
          {ai.cycle && (
            <div className="text-[11.5px] mb-1.5 flex gap-1.5">
              <span className="text-accent shrink-0 font-medium">周期</span>
              <span className="text-text-dim">{ai.cycle}</span>
            </div>
          )}
          <div className="space-y-1">
            {(ai.points || []).map((p, i) => (
              <div key={i} className="text-[11.5px] leading-relaxed flex gap-1.5">
                <span className="text-accent shrink-0 font-medium">{p.type}</span>
                <span className="text-text-dim">{p.detail}</span>
              </div>
            ))}
          </div>
          {ai.holdings_note && <div className="text-[11px] text-info mt-1.5 leading-relaxed">持仓: {ai.holdings_note}</div>}
        </div>
      )}

      <CycleStrip />

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

      {/* 连板梯队 + 板块热点 摘要 (点开看具体股票) */}
      <button onClick={() => setShowDetail(true)} className="w-full text-left">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] mb-1.5">
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
              <span className="text-text-muted text-[10.5px]">龙头</span>
              <span className="text-text-bright truncate">{d.leaders.join(' / ')}</span>
            </div>
          )}
        </div>
        {(d.hot_sectors || []).length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-text-muted text-[10.5px]">板块热点</span>
            {d.hot_sectors.slice(0, 5).map((h, i) => (
              <span key={i} className="text-[11px] bg-surface-3 rounded px-2 py-0.5">
                {h.name}<span className="text-accent font-mono ml-1">{h.count}</span>
              </span>
            ))}
            <span className="text-[10.5px] text-accent">点开看具体股票 →</span>
          </div>
        )}
      </button>

      <div className="text-[10px] text-text-muted pt-2.5 mt-2 border-t border-border-subtle">
        纯客观情绪指标，看市场是高潮还是退潮 · 不构成任何买卖建议
      </div>

      {showDetail && (
        <SentimentDetailModal
          summary={{ ...d, moodColor: c }}
          volume={v}
          onClose={() => setShowDetail(false)}
        />
      )}
    </div>
  )
}
