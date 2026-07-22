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

export default function SentimentDetailModal({ summary, volume, onClose }) {
  const [d, setD] = useState(null)
  const [tab, setTab] = useState('ladder')   // ladder | sector | dt
  const [mkt, setMkt] = useState('两市')      // 量能市场: 两市/沪/深/创业/科创
  const [metric, setMetric] = useState('amt') // 量能口径: amt=成交额(亿元) | vol=成交量(亿股)
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
                <span className="text-[10px] text-text-muted ml-auto">
                  近14日{mkt}{metric === 'amt' ? '成交额(亿元)' : '成交量(亿股)'} · 较前一日放量红/缩量绿{v.markets_intraday ? ' · 末根今日盘中' : ''}
                </span>
              </div>
              {series.length > 1
                ? <VolBars series={series} intraday={v.markets_intraday} unit={unit} />
                : <div className="text-[11px] text-text-dim py-4 text-center">
                    成交额历史档案积累中(每个收盘日自动定格一格, 数据源恢复时一次回填两周)——先切「成交量」看, 量的序列是全的
                  </div>}
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
