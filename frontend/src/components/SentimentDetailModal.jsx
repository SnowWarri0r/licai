import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { fetchJSON } from '../hooks/useApi'

// A股: 涨红跌绿
const up = 'text-bear-bright', down = 'text-bull-bright'

// 量能红绿窄柱: 每日 vs 前一日, 放量红 / 缩量绿。
// series: [{date, v}] 含一根参照日(首位), 只画后面每根(都有前一日可比, 不出灰柱)。
function VolBars({ series, intraday, unit }) {
  if (!series || series.length < 2) return null
  const shown = series.slice(1)
  const vals = shown.map(t => t.v)
  const max = Math.max(...vals), min = Math.min(...vals), span = max - min || 1
  const n = shown.length
  const step = n > 10 ? 3 : n > 7 ? 2 : 1   // 日期标签密时隔位显示, 防重叠
  return (
    <div className="flex items-end gap-1" style={{ height: 100 }}>
      {shown.map((t, i) => {
        const h = Math.round(14 + ((t.v - min) / span) * 54)
        const prev = series[i].v   // 前一日(原数组里的前一个)
        const isToday = intraday && i === n - 1   // 末根=今日实时盘中
        const color = t.v > prev ? 'bg-bear-bright' : t.v < prev ? 'bg-bull-bright' : 'bg-text-dim'
        const showDate = (n - 1 - i) % step === 0   // 从最新往前隔位, 保证最新一根有标签
        return (
          <div key={i} className="flex-1 flex flex-col items-center justify-end gap-1 h-full min-w-0" title={`${t.date}${isToday ? ' 今日盘中' : ''}: ${t.v}${unit}`}>
            <span className={`text-[8.5px] font-mono leading-none ${isToday ? 'text-bear-bright font-semibold' : 'text-text-dim'}`}>{t.v}</span>
            <div className={`w-full max-w-[20px] rounded-t ${color}`} style={{ height: h, outline: isToday ? '1px solid var(--color-accent)' : 'none', outlineOffset: 1 }} />
            <span className={`text-[8.5px] leading-none h-2.5 ${isToday ? 'text-accent font-semibold' : 'text-text-muted'}`}>{showDate ? t.date : ''}</span>
          </div>
        )
      })}
    </div>
  )
}

// 当日分时累计曲线 + 昨日同期对照 + 昨日全天参照线。SVG 只画线, 文字走 HTML 免拉伸/裁切。
function IntradayLine({ intra, metric, unit }) {
  const points = intra?.points || []
  if (points.length < 2) return null
  const prev = intra?.prev_points || []
  const val = p => (metric === 'amt' ? p.amt : p.vol)
  const prevFull = intra?.prev_full && (metric === 'amt' ? intra.prev_full.amt : intra.prev_full.vol)
  const proj = intra?.projected && (metric === 'amt' ? intra.projected.amt : intra.projected.vol)
  const today = val(points[points.length - 1])
  // y 轴上界取 今日/昨日/昨日全天/预测 的最大, 保证参照线与预测终点落在框内
  const max = Math.max(today, ...points.map(val), ...prev.map(val), prevFull || 0, (proj && !intra?.projected?.final) ? proj : 0) * 1.06 || 1
  const W = 560, H = 120, padB = 4, padT = 6
  const n = Math.max(points.length, prev.length, 2)
  const x = i => (i / (n - 1)) * W
  const y = v => padT + (1 - v / max) * (H - padT - padB)
  const path = arr => arr.map((p, i) => `${x(i).toFixed(1)},${y(val(p)).toFixed(1)}`).join(' ')
  const todayLine = path(points)
  const area = `0,${H - padB} ${todayLine} ${x(points.length - 1)},${H - padB}`
  const fmt = v => (v >= 10000 ? `${(v / 10000).toFixed(2)}万亿` : `${v}${unit}`)
  const marks = ['09:30', '10:30', '11:30/13:00', '14:00', '15:00']
  return (
    <div>
      <div className="flex items-baseline gap-3 mb-1 text-[10.5px] flex-wrap">
        <span className="text-text-muted">今日累计 <b className="text-accent font-mono">{fmt(today)}</b></span>
        {prevFull != null && <span className="text-text-muted">昨日全天 <b className="text-text-dim font-mono">{fmt(prevFull)}</b></span>}
        {proj != null && !intra.projected.final && (
          <span className="text-text-muted">
            预计全天 <b className="text-bear-bright font-mono">~{fmt(proj)}</b>
            {prevFull != null && <span className={proj >= prevFull ? 'text-bear ml-1' : 'text-bull ml-1'}>{proj >= prevFull ? '↑' : '↓'}较昨日{Math.abs(Math.round((proj / prevFull - 1) * 100))}%</span>}
            {intra.projected.basis && <span className="text-text-dim ml-1">({intra.projected.basis})</span>}
          </span>
        )}
      </div>
      <div className="flex">
        {/* Y 轴刻度(HTML, 免 SVG 拉伸): 4 档, 顶=上界 */}
        <div className="relative shrink-0 w-9 mr-1" style={{ height: 120 }}>
          {[0, 1, 2, 3].map(k => {
            const gv = max * (1 - k / 3)
            return (
              <span key={k} className="absolute right-0 text-[8px] text-text-muted font-mono leading-none"
                style={{ top: `${(k / 3) * 100}%`, transform: 'translateY(-2px)' }}>
                {gv >= 10000 ? (gv / 10000).toFixed(1) + '万亿' : Math.round(gv)}
              </span>
            )
          })}
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} className="flex-1" style={{ height: 120 }} preserveAspectRatio="none">
          {/* Y 网格线 */}
          {[0, 1, 2, 3].map(k => <line key={k} x1="0" y1={padT + (k / 3) * (H - padT - padB)} x2={W} y2={padT + (k / 3) * (H - padT - padB)} stroke="#ffffff" strokeOpacity="0.05" strokeWidth="0.6" />)}
          <polygon points={area} fill="#c8a87620" />
          {prevFull != null && <line x1="0" y1={y(prevFull)} x2={W} y2={y(prevFull)} stroke="#8a8f99" strokeWidth="0.9" strokeDasharray="5 3" />}
          {prev.length > 1 && <polyline points={path(prev)} fill="none" stroke="#8a8f99" strokeWidth="1" strokeDasharray="3 3" opacity="0.7" />}
          <polyline points={todayLine} fill="none" stroke="#c8a876" strokeWidth="1.6" />
          {/* 盘中预测: 从今日末点虚线延伸到 15:00 的预测终点 */}
          {proj != null && !intra.projected.final && (
            <>
              <line x1={x(points.length - 1)} y1={y(today)} x2={x(n - 1)} y2={y(proj)}
                stroke="#cf5c5c" strokeWidth="1.4" strokeDasharray="4 3" />
              <circle cx={x(n - 1)} cy={y(proj)} r="2.4" fill="#cf5c5c" />
            </>
          )}
        </svg>
      </div>
      <div className="flex justify-between text-[8.5px] text-text-muted mt-0.5 pl-10">
        {marks.map((m, i) => <span key={i}>{m}</span>)}
      </div>
      <div className="flex items-center gap-3 text-[9px] text-text-muted mt-1 pl-10 flex-wrap">
        <span><span style={{ color: '#c8a876' }}>—</span> 今日累计</span>
        <span><span style={{ color: '#8a8f99' }}>--</span> 昨日同期</span>
        <span><span style={{ color: '#8a8f99' }}>┈</span> 昨日全天 {prevFull != null ? fmt(prevFull) : ''}</span>
        {proj != null && !intra.projected.final && <span><span style={{ color: '#cf5c5c' }}>--</span> 预测全天</span>}
        <span className="text-text-dim">纵轴={unit}</span>
      </div>
    </div>
  )
}

export default function SentimentDetailModal({ summary, volume, onClose }) {
  const [d, setD] = useState(null)
  const [tab, setTab] = useState('ladder')   // ladder | sector | dt
  const [mkt, setMkt] = useState('两市')      // 量能市场: 两市/沪/深/创业/科创
  const [metric, setMetric] = useState('amt') // 量能口径: amt=成交额(亿元) | vol=成交量(亿股)
  const [view, setView] = useState('daily')   // daily=近14日 | intraday=当日分时累计
  const [loading, setLoading] = useState(true)
  const [vol, setVol] = useState(null)       // 独立量能接口(实时读数+近14日), 不吃 sentiment 5min 缓存
  const [intra, setIntra] = useState(null)    // 当日分时累计(逐分钟量额)

  useEffect(() => {
    fetchJSON('/api/market/sentiment-detail').then(setD).catch(() => {}).finally(() => setLoading(false))
  }, [])

  // 量能: 打开即拉独立端点; 盘中(intraday)每 60s 刷新当前实时量额
  useEffect(() => {
    let alive = true, timer = null
    const pull = () => fetchJSON('/api/market/volume').then(r => {
      if (!alive || !r) return
      setVol(r)
      if (r.intraday) timer = setTimeout(pull, 60000)
    }).catch(() => {})
    pull()
    return () => { alive = false; if (timer) clearTimeout(timer) }
  }, [])

  // 分时视图: 按市场拉当日逐分钟累计; 盘中每 60s 刷新
  useEffect(() => {
    if (view !== 'intraday') return
    let alive = true, timer = null
    const pull = () => fetchJSON(`/api/market/volume-intraday?market=${encodeURIComponent(mkt)}`).then(r => {
      if (!alive || !r) return
      setIntra(r)
      if (vol?.intraday) timer = setTimeout(pull, 60000)
    }).catch(() => {})
    pull()
    return () => { alive = false; if (timer) clearTimeout(timer) }
  }, [view, mkt, vol?.intraday])

  // 打开时锁住底下页面滚动 + Esc 关闭
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => { document.body.style.overflow = prev; window.removeEventListener('keydown', onKey) }
  }, [onClose])

  const v = { ...(volume || {}), ...(vol || {}) }   // 独立端点覆盖 sentiment 内嵌口径
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

        {/* 量能红绿柱: 两市/沪/深/创业/科创 × 成交额/成交量 */}
        {(() => {
          const mks = v.markets || {}
          const names = Object.keys(mks)
          // 新接口缺席时退回旧的单沪市量序列
          if (!names.length) {
            return (v.trend || []).length > 1 && (
              <div className="mb-4 px-3 py-3 rounded-lg bg-surface-3/50 border border-border-subtle">
                <div className="text-[10.5px] text-text-muted mb-2">近14日沪市成交量(亿股) · 每根较<b className="text-text-dim">前一日</b>放量红/缩量绿{v.intraday ? ' · 末根今日盘中' : ''}</div>
                <VolBars series={(v.trend || []).map(t => ({ date: t.date, v: t.vol }))} intraday={v.intraday} unit="亿股" />
              </div>
            )
          }
          const trend = (mks[mkt] || mks['两市'] || {}).trend || []
          const series = trend.map(t => ({ date: t.date, v: metric === 'amt' ? t.amt : t.vol })).filter(t => t.v != null)
          const unit = metric === 'amt' ? '亿元' : '亿股'
          const rt = (v.realtime || {})[mkt]
          const rtVal = rt && (metric === 'amt' ? rt.amt : rt.vol)
          const live = v.intraday   // 盘中实时 / 收盘后为当日定格
          return (
            <div className="mb-4 px-3 py-3 rounded-lg bg-surface-3/50 border border-border-subtle">
              <div className="flex items-center gap-1 mb-2 flex-wrap">
                {names.map(k => (
                  <button key={k} onClick={() => setMkt(k)}
                    className={`text-[10.5px] px-1.5 py-0.5 rounded ${mkt === k ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>{k}</button>
                ))}
                <span className="text-text-muted mx-1">·</span>
                {[['amt', '成交额'], ['vol', '成交量']].map(([k, lb]) => (
                  <button key={k} onClick={() => setMetric(k)}
                    className={`text-[10.5px] px-1.5 py-0.5 rounded ${metric === k ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>{lb}</button>
                ))}
                <span className="text-text-muted mx-1">·</span>
                {[['daily', '近14日'], ['intraday', '当日分时']].map(([k, lb]) => (
                  <button key={k} onClick={() => setView(k)}
                    className={`text-[10.5px] px-1.5 py-0.5 rounded ${view === k ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>{lb}</button>
                ))}
              </div>
              {/* 当前实时读数(盘中随分钟刷新, 收盘后为定格) */}
              {rtVal != null && (
                <div className="flex items-baseline gap-2 mb-2">
                  <span className="text-[17px] font-mono font-semibold text-text-bright">{rtVal >= 10000 ? `${(rtVal / 10000).toFixed(2)}万亿` : `${rtVal}${unit === '亿元' ? '亿元' : '亿股'}`}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${live ? 'bg-bear/15 text-bear-bright' : 'bg-surface-2 text-text-dim'}`}>{live ? '盘中实时' : '今日收盘'}</span>
                  <span className="text-[10.5px] text-text-muted">{mkt}{metric === 'amt' ? '成交额' : '成交量'}</span>
                </div>
              )}
              {view === 'daily' ? (
                <>
                  <div className="text-[10px] text-text-muted mb-1.5">
                    近14日{metric === 'amt' ? '成交额(亿元)' : '成交量(亿股)'} · 较前一日放量红/缩量绿{live ? ' · 末根今日盘中' : ''}
                  </div>
                  {series.length > 1
                    ? <VolBars series={series} intraday={live} unit={unit} />
                    : <div className="text-[11px] text-text-dim py-3 text-center">暂无足够历史</div>}
                </>
              ) : (
                <>
                  {(intra?.points || []).length > 1
                    ? <IntradayLine intra={intra} metric={metric} unit={unit} />
                    : <div className="text-[11px] text-text-dim py-3 text-center">分时加载中…</div>}
                </>
              )}
            </div>
          )
        })()}
        {v.label && v.ratio != null && (
          <div className="text-[10px] text-text-muted -mt-2 mb-4 px-1">
            注: 头部「{v.label}{v.ratio > 0 ? '+' : ''}{v.ratio}%」是<b className="text-text-dim">沪市今日 较前5日均值</b>口径, 与柱色的"较前一日"基准不同。
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
