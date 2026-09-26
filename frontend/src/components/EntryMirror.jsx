import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'

const pctCls = (v) => v == null ? 'text-text-muted' : v > 0 ? 'text-bear' : v < 0 ? 'text-bull' : 'text-text-dim'
const pct = (v) => v == null ? '--' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`
const SHORT = {
  '买入价相对前一日收盘': '今天',
  '买入前一日在近 120 日区间的位置': '120日位置',
  '买入前 20 个交易日的涨跌': '前20日',
  '个股买入前一日的暴跌风险档': '暴跌风险',
}

// 买入前的镜子: 这只票现在的状态下, 你历史上同类买入之后 20 日的实际结果(与复盘页「买点回看」同口径)
export default function EntryMirror({ code }) {
  const [d, setD] = useState(null)
  useEffect(() => {
    if (!code) return
    let alive = true
    setD(null)
    fetchJSON(`/api/portfolio/entry-mirror/${encodeURIComponent(code)}`).then(x => alive && setD(x)).catch(() => {})
    return () => { alive = false }
  }, [code])
  if (!d?.available) return null
  const base = d.total?.mean20_pct
  return (
    <div className="mb-2 px-2.5 py-1.5 rounded-md bg-surface-3/70 border border-border-subtle flex items-baseline gap-x-3 gap-y-0.5 flex-wrap text-[11px]"
      title={`${d.note} 你全部买入之后 20 日平均 ${pct(base)}, 赚钱的占 ${Math.round((d.total?.win20 || 0) * 100)}%。`}>
      <span className="text-text-muted shrink-0">按现在的状态, 你以前同类买入之后 20 日</span>
      {d.matches.map(m => {
        const few = m.n_done < 5
        const worse = !few && base != null && m.mean20_pct != null && m.mean20_pct < base - 2
        const vsIdx = m.metric !== '绝对涨跌'
        return (
          <span key={m.dim} className={`font-mono ${few ? 'opacity-50' : ''}`}
            title={`${m.dim}「${m.label}」: ${m.n} 次(已满 20 日 ${m.n_done} 次), 之后 20 日${vsIdx ? '相对中证1000' : ''}平均 ${pct(m.mean20_pct)}, 中位 ${pct(m.median20_pct)}, ${vsIdx ? '跑赢' : '赚钱'}的占 ${Math.round((m.win20 || 0) * 100)}%${few ? '。样本太少, 只是个例' : ''}`}>
            <span className="text-text-dim">{SHORT[m.dim] || m.dim}</span>
            <span className={worse ? 'text-accent' : 'text-text'}>「{m.label}」</span>
            <span className="text-text-muted">{m.n_done}次 </span>
            <span className={pctCls(m.mean20_pct)}>{pct(m.mean20_pct)}</span>
            <span className="text-text-muted"> {vsIdx ? '跑赢' : '赚'}{Math.round((m.win20 || 0) * 100)}%</span>
          </span>
        )
      })}
      <span className="text-text-muted ml-auto shrink-0">全部买入 <span className={`font-mono ${pctCls(base)}`}>{pct(base)}</span></span>
    </div>
  )
}
