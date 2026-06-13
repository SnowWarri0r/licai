import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import MacroKlineModal from './MacroKlineModal'

const GROUP_META = {
  a_index:          { label: 'A 股大盘',   desc: '上证 / 深成 / 宽基' },
  hk_index:         { label: '港股',       desc: '外资情绪反应最直接, 涨幅 > A 股 = 外资认账' },
  us_index:         { label: '美股',       desc: '隔夜外盘, 风险偏好风向' },
  overseas_index:   { label: '海外指数',   desc: '日韩欧外围, KOSDAQ/日经异动看这里' },
  ai_tech:          { label: 'AI / 算力',  desc: 'AI 全产业链: 算力/芯片/光模块/云软件/AIGC/机器人/电力 + 上游金属' },
  fx:               { label: '汇率',       desc: 'USD/CNH 离岸对消息最敏感, 看中美/贸易看这条' },
  metal_base:       { label: '有色金属',   desc: '铜铝锌镍锡铅 — 工业/电力需求晴雨表 (算力扩张拉动铜)' },
  metal_precious:   { label: '贵金属',     desc: '金银避险情绪, 沪金沪银对国内更贴' },
  energy_new_black: { label: '新能源 / 黑色', desc: '碳酸锂/工业硅 = 新能源链, 螺纹/焦煤 = 地产基建' },
  energy_chem:      { label: '能化 / 农产品', desc: '原油 PTA 甲醇等, 宏观成本与通胀风向' },
}

const GROUP_ORDER = ['a_index', 'hk_index', 'us_index', 'overseas_index', 'ai_tech', 'fx',
  'metal_base', 'metal_precious', 'energy_new_black', 'energy_chem']

function colorOfPct(pct) {
  if (pct == null) return 'text-text-dim'
  if (pct > 1.5) return 'text-bear-bright'
  if (pct > 0) return 'text-bear'
  if (pct < -1.5) return 'text-bull-bright'
  if (pct < 0) return 'text-bull'
  return 'text-text'
}

// 汇率 / 国际商品: 价格小数位多一些. 国内期货取整.
function fmtPrice(sym, p) {
  if (p == null) return '--'
  if (sym.startsWith('fx_')) return p.toFixed(4)
  if (sym.startsWith('hf_')) return p.toFixed(2)
  if (sym.startsWith('nf_')) return p >= 1000 ? Math.round(p).toLocaleString() : p.toFixed(2)
  return p >= 1000 ? Math.round(p).toLocaleString() : p.toFixed(2)
}

function fmtPct(pct) {
  if (pct == null) return '--'
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

function Sparkline({ data, width = 80, height = 28, pct = 0 }) {
  if (!data || data.length < 2) {
    return <div style={{ width, height }} className="flex items-center justify-center text-[9.5px] text-text-muted">无 K 线</div>
  }
  const closes = data.map(d => d.close).filter(c => c > 0)
  if (closes.length < 2) {
    return <div style={{ width, height }} className="flex items-center justify-center text-[9.5px] text-text-muted">--</div>
  }
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const range = max - min || 1
  const stepX = width / (closes.length - 1)
  const points = closes.map((c, i) => {
    const x = i * stepX
    const y = height - ((c - min) / range) * height
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  // 段尾比段首高 = 上涨, A 股口径红涨绿跌
  const isUp = closes[closes.length - 1] >= closes[0]
  const stroke = isUp ? '#cf5c5c' : '#5fa86c'
  // 渐变填充背景
  const fillId = `sparkfill-${stroke.slice(1)}`
  return (
    <svg width={width} height={height} className="shrink-0">
      <defs>
        <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.25" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polyline points={`0,${height} ${points} ${width},${height}`} fill={`url(#${fillId})`} stroke="none" />
      <polyline points={points} fill="none" stroke={stroke} strokeWidth="1.3" />
    </svg>
  )
}

function MacroChip({ item, onClick }) {
  const pct = item.change_pct
  const k = item.kline || []
  let periodPct = null
  if (k.length >= 2 && k[0].close > 0) {
    periodPct = ((k[k.length - 1].close / k[0].close) - 1) * 100
  }
  return (
    <button
      onClick={() => onClick?.(item)}
      className="flex flex-col gap-1 px-2.5 py-1.5 rounded-md bg-surface-3 border border-border-subtle min-w-[178px] cursor-pointer text-left
                 hover:bg-surface-2 hover:border-accent/40 transition-colors">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] text-text whitespace-nowrap">{item.name}</span>
        <span className="text-[11.5px] font-mono text-text-bright">{fmtPrice(item.symbol, item.price)}</span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <Sparkline data={k} pct={periodPct ?? 0} />
        <div className="flex flex-col items-end gap-0">
          <span className={`text-[11px] font-mono ${colorOfPct(pct)}`}>{fmtPct(pct)}</span>
          {periodPct != null && (
            <span className={`text-[9.5px] font-mono ${colorOfPct(periodPct)}`} title="30 日涨跌幅">
              30d {fmtPct(periodPct)}
            </span>
          )}
        </div>
      </div>
    </button>
  )
}

export default function MacroDashboard() {
  const [data, setData] = useState({})
  const [loading, setLoading] = useState(true)
  const [updated, setUpdated] = useState(null)
  const [openItem, setOpenItem] = useState(null)

  const reload = useCallback(async () => {
    try {
      const d = await fetchJSON('/api/market/macro?with_kline=true')
      setData(d || {})
      setUpdated(new Date())
    } catch {}
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    reload()
    const t = setInterval(reload, 30000)
    return () => clearInterval(t)
  }, [reload])

  const totalCount = Object.values(data).reduce((s, arr) => s + (arr?.length || 0), 0)

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-baseline justify-between flex-wrap gap-2"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">宏观仪表盘</h3>
          <span className="text-[11px] text-text-dim">
            {loading ? '加载中...' : `${totalCount} 个指标`}
          </span>
        </div>
        <span className="text-[10.5px] text-text-muted font-mono">
          {updated ? `更新 ${updated.toLocaleTimeString('zh-CN', { hour12: false })}` : '--'}
        </span>
      </div>

      {GROUP_ORDER.map(g => {
        const items = data[g]
        if (!items || items.length === 0) return null
        const meta = GROUP_META[g]
        return (
          <div key={g} className="px-3 md:px-5 py-2.5 border-b border-border-subtle last:border-b-0">
            <div className="flex items-baseline justify-between mb-1.5 flex-wrap gap-1">
              <span className="text-[11.5px] text-text-bright font-semibold tracking-wider">{meta.label}</span>
              <span className="text-[10.5px] text-text-muted">{meta.desc}</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {items.map(it => <MacroChip key={it.symbol} item={it} onClick={setOpenItem} />)}
            </div>
          </div>
        )
      })}

      <div className="px-3 md:px-5 py-2 bg-surface-2/40 text-[10.5px] text-text-muted leading-relaxed">
        红 = 涨, 绿 = 跌 (A 股口径). 点 chip 看 60 日大图. Sparkline 是过去 30 个交易日收盘.
        美股是前一交易日收盘 (隔夜外盘). 汇率 K 线源不稳, 失败会显示"数据源限频".
      </div>

      {openItem && (
        <MacroKlineModal item={openItem} onClose={() => setOpenItem(null)} />
      )}
    </section>
  )
}
