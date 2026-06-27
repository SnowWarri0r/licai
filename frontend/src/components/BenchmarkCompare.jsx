import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'
import { fmtMoney } from '../helpers'

const SYMBOLS = [
  { v: 'sh000300', label: '沪深 300' },
  { v: 'sh000001', label: '上证指数' },
  { v: 'sz399006', label: '创业板指' },
  { v: 'sh000688', label: '科创 50' },
]
const WINDOWS = [
  { v: 0,    label: '全部' },
  { v: 365,  label: '1 年' },
  { v: 180,  label: '6 月' },
  { v: 90,   label: '3 月' },
  { v: 30,   label: '1 月' },
]

const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
const fmtSigned = (v) => v == null ? '--' : (v >= 0 ? '+' : '−') + '¥' + fmtMoney(Math.abs(v))
const colorOf = (v) => v == null ? 'text-text-dim' : v >= 0 ? 'text-bear-bright' : 'text-bull-bright'

export default function BenchmarkCompare() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [symbol, setSymbol] = useState(() => localStorage.getItem('benchSymbol') || 'sh000300')
  const [windowDays, setWindowDays] = useState(() => parseInt(localStorage.getItem('benchWindow') || '0'))

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const d = await fetchJSON(`/api/portfolio/benchmark?symbol=${symbol}&days=${windowDays}`)
      setData(d)
    } catch (e) { setErr(e?.message || '加载失败') }
    finally { setLoading(false) }
  }, [symbol, windowDays])

  useEffect(() => { load() }, [load])

  const pickSymbol = (v) => { setSymbol(v); localStorage.setItem('benchSymbol', v) }
  const pickWindow = (v) => { setWindowDays(v); localStorage.setItem('benchWindow', String(v)) }

  if (loading) return <SkeletonCard rows={4} label="基准对照加载中" />
  if (err) {
    return (
      <section className="rounded-xl border border-border bg-surface/60 px-3 md:px-5 py-3 text-[12px] text-bear">
        {err}
      </section>
    )
  }
  if (!data || data.action_count === 0) {
    return null
  }

  const u = data.user || {}
  const b = data.benchmark || {}
  const a = data.alpha || {}
  const alphaPos = (a.pnl_diff || 0) >= 0

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-baseline justify-between flex-wrap gap-2"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2 flex-wrap">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">跑赢基准对照</h3>
          <span className="text-[11px] text-text-dim">
            {data.action_count} 笔操作 · 起 {data.first_action_date || '--'}
          </span>
        </div>
        <div className="flex gap-1 items-center flex-wrap">
          <div className="flex gap-0.5">
            {SYMBOLS.map(s => (
              <button key={s.v} onClick={() => pickSymbol(s.v)}
                className="px-2 py-[2px] rounded text-[10.5px] cursor-pointer"
                style={{
                  border: '1px solid',
                  borderColor: symbol === s.v ? 'var(--color-accent)' : 'var(--color-border-med)',
                  color: symbol === s.v ? 'var(--color-accent)' : 'var(--color-text-dim)',
                  background: symbol === s.v ? 'var(--color-accent)1a' : 'transparent',
                }}>
                {s.label}
              </button>
            ))}
          </div>
          <span className="mx-1 text-text-muted">·</span>
          <div className="flex gap-0.5">
            {WINDOWS.map(w => (
              <button key={w.v} onClick={() => pickWindow(w.v)}
                className="px-2 py-[2px] rounded text-[10.5px] cursor-pointer"
                style={{
                  border: '1px solid',
                  borderColor: windowDays === w.v ? 'var(--color-accent)' : 'var(--color-border-med)',
                  color: windowDays === w.v ? 'var(--color-accent)' : 'var(--color-text-dim)',
                  background: windowDays === w.v ? 'var(--color-accent)1a' : 'transparent',
                }}>
                {w.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* α 主观结论 */}
      <div className={`px-3 md:px-5 py-2.5 ${alphaPos ? 'bg-bear/8' : 'bg-bull/8'} border-b border-border-subtle`}>
        <div className="flex items-baseline justify-between flex-wrap gap-2">
          <div>
            <span className="text-[12px] text-text">
              你的操作{alphaPos ? '跑赢' : '跑输'}基准
            </span>
            <span className={`ml-2 font-mono text-[15px] font-bold ${colorOf(a.pnl_diff)}`}>
              {fmtSigned(a.pnl_diff)}
            </span>
            <span className={`ml-1.5 font-mono text-[12px] ${colorOf(a.pct_diff)}`}>
              ({fmtPct(a.pct_diff)})
            </span>
          </div>
          <span className="text-[10.5px] text-text-muted">
            假设同金额同日期买基准, 与你的实际操作对比
          </span>
        </div>
        {!alphaPos && (
          <div className="text-[10.5px] text-text-dim mt-1">
            如果不动, 只买 {SYMBOLS.find(s => s.v === symbol)?.label || symbol}, 这段时间会多 ¥{fmtMoney(Math.abs(a.pnl_diff))}.
            操作产生了负贡献.
          </div>
        )}
      </div>

      {/* 双栏明细 */}
      <div className="grid grid-cols-2 gap-px bg-border-subtle">
        <div className="bg-surface px-3 md:px-5 py-3">
          <div className="text-[11px] text-text-dim mb-1.5">你的实际操作</div>
          <div className="grid grid-cols-2 gap-y-1 text-[11.5px]">
            <span className="text-text-muted">投入</span>
            <span className="text-right font-mono">¥{fmtMoney(u.buy_total)}</span>
            <span className="text-text-muted">已收回</span>
            <span className="text-right font-mono">¥{fmtMoney(u.sell_total)}</span>
            <span className="text-text-muted">当前持仓</span>
            <span className="text-right font-mono">¥{fmtMoney(u.current_mv)}</span>
            <span className="text-text-muted pt-1 border-t border-border-subtle">总 PnL</span>
            <span className={`text-right font-mono pt-1 border-t border-border-subtle ${colorOf(u.pnl)}`}>
              {fmtSigned(u.pnl)}
            </span>
            <span className="text-text-muted">回报率</span>
            <span className={`text-right font-mono font-semibold ${colorOf(u.return_pct)}`}>
              {fmtPct(u.return_pct)}
            </span>
          </div>
        </div>
        <div className="bg-surface px-3 md:px-5 py-3">
          <div className="text-[11px] text-text-dim mb-1.5">
            等额买 {SYMBOLS.find(s => s.v === symbol)?.label || symbol}
          </div>
          <div className="grid grid-cols-2 gap-y-1 text-[11.5px]">
            <span className="text-text-muted">投入</span>
            <span className="text-right font-mono">¥{fmtMoney(u.buy_total)}</span>
            <span className="text-text-muted">已收回 (同金额)</span>
            <span className="text-right font-mono">¥{fmtMoney(u.sell_total)}</span>
            <span className="text-text-muted">当前估值</span>
            <span className="text-right font-mono">¥{fmtMoney(b.current_mv)}</span>
            <span className="text-text-muted pt-1 border-t border-border-subtle">总 PnL</span>
            <span className={`text-right font-mono pt-1 border-t border-border-subtle ${colorOf(b.pnl)}`}>
              {fmtSigned(b.pnl)}
            </span>
            <span className="text-text-muted">回报率</span>
            <span className={`text-right font-mono font-semibold ${colorOf(b.return_pct)}`}>
              {fmtPct(b.return_pct)}
            </span>
          </div>
        </div>
      </div>

      <div className="px-3 md:px-5 py-2 bg-surface-2/40 text-[10.5px] text-text-muted leading-relaxed">
        模型: dollar-matched 等额对比. 你每次买/卖, 假设同金额同日期在基准上同向操作.
        手续费已含 (你的: 万1.854+5起+印花税; 基准默认 0). 仅 A 股, 不含港美股.
      </div>
    </section>
  )
}
