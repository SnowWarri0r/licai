import { useEffect, useMemo, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'

const BUY = '#e5484d'    // 主动买(外盘) 红 —— A股约定 红=多
const SELL = '#30a46c'   // 主动卖(内盘) 绿

function fmtAmt(yuan) {
  if (!yuan) return '0'
  if (yuan >= 1e8) return (yuan / 1e8).toFixed(2) + '亿'
  if (yuan >= 1e4) return (yuan / 1e4).toFixed(1) + '万'
  return Math.round(yuan).toString()
}

// 分价表: 每个价位一行 —— 价格 | 成交额 | 买卖比例条 | 竞买率, 我成交过的价位高亮。
export default function PriceVolumeTable({ code, prevClose, decimals = 2 }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!code) return
    let alive = true
    setLoading(true); setErr('')
    fetchJSON(`/api/market/tdx/price-volume/${encodeURIComponent(code)}`)
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) setErr(e?.message || '加载失败') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [code])

  const rows = useMemo(() => {
    const levels = data?.market?.levels || []
    // 我的成交按价位归并到 decimals 精度
    const mine = {}
    for (const m of (data?.mine || [])) {
      const k = m.price.toFixed(decimals)
      const e = mine[k] || { buy: 0, sell: 0 }
      e.buy += m.buy_shares || 0; e.sell += m.sell_shares || 0
      mine[k] = e
    }
    const maxAmt = Math.max(1, ...levels.map(l => l.amount || 0))
    // 只叠加落在市场价区间内的我的成交 —— 自动滤掉拆分前/不同标度的历史价位噪音
    const prices = levels.map(l => l.price)
    const lo = prices.length ? Math.min(...prices) : -Infinity
    const hi = prices.length ? Math.max(...prices) : Infinity
    const usedMine = new Set()
    const out = levels.map(l => {
      const k = l.price.toFixed(decimals)
      const mineHit = mine[k]
      if (mineHit) usedMine.add(k)
      return { ...l, key: k, mine: mineHit, w: (l.amount || 0) / maxAmt }
    })
    // 我成交过、市场分价里没有、但仍落在当日价区间内的价位, 补上
    for (const [k, e] of Object.entries(mine)) {
      const p = parseFloat(k)
      if (!usedMine.has(k) && p >= lo && p <= hi) {
        out.push({ price: p, key: k, amount: 0, buy_vol: 0, sell_vol: 0, buy_ratio: 0, w: 0, mine: e })
      }
    }
    out.sort((a, b) => b.price - a.price)
    return out
  }, [data, decimals])

  if (loading) return <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">加载分价中…</div>
  if (err) return <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">{err}</div>
  if (!data?.enabled) return <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">TDX 未接入，分价表不可用（仅显示我的成交）</div>
  if (!rows.length) return <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">暂无分价数据</div>

  const priceColor = (p) => prevClose ? (p > prevClose ? BUY : p < prevClose ? SELL : 'var(--color-text)') : 'var(--color-text)'

  return (
    <div className="max-h-[420px] overflow-y-auto">
      <table className="w-full text-[11px] font-mono border-collapse">
        <thead className="sticky top-0 bg-surface-3 text-text-dim">
          <tr className="text-left">
            <th className="py-1 px-1.5 font-normal">价格</th>
            <th className="py-1 px-1.5 font-normal text-right">成交额</th>
            <th className="py-1 px-1.5 font-normal w-[42%]">买卖比例</th>
            <th className="py-1 px-1.5 font-normal text-right">竞买率</th>
            <th className="py-1 px-1.5 font-normal text-right">我的</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const tot = (r.buy_vol || 0) + (r.sell_vol || 0)
            const buyW = tot ? (r.buy_vol / tot) * 100 : 0
            return (
              <tr key={r.key} className="border-t border-border-subtle/40" style={{ background: r.mine ? 'rgba(200,168,118,.12)' : 'transparent' }}>
                <td className="py-[3px] px-1.5" style={{ color: priceColor(r.price) }}>{r.price.toFixed(decimals)}</td>
                <td className="py-[3px] px-1.5 text-right text-text">{fmtAmt(r.amount)}</td>
                <td className="py-[3px] px-1.5">
                  <div className="flex h-[9px] rounded-sm overflow-hidden" style={{ width: `${Math.max(3, r.w * 100)}%`, minWidth: 3 }}>
                    <div style={{ width: `${buyW}%`, background: BUY }} />
                    <div style={{ width: `${100 - buyW}%`, background: SELL }} />
                  </div>
                </td>
                <td className="py-[3px] px-1.5 text-right" style={{ color: r.buy_ratio >= 0.5 ? BUY : SELL }}>
                  {tot ? Math.round(r.buy_ratio * 100) + '%' : '—'}
                </td>
                <td className="py-[3px] px-1.5 text-right text-[10px]">
                  {r.mine ? (
                    <span>
                      {r.mine.buy ? <span style={{ color: BUY }}>买{Math.round(r.mine.buy)}</span> : null}
                      {r.mine.buy && r.mine.sell ? ' ' : null}
                      {r.mine.sell ? <span style={{ color: SELL }}>卖{Math.round(r.mine.sell)}</span> : null}
                    </span>
                  ) : <span className="text-text-muted">·</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="mt-2 flex gap-3 text-[10px] text-text-dim px-1">
        <span><span className="inline-block w-2 h-2 rounded-sm align-middle mr-1" style={{ background: BUY }} />主动买(外盘)</span>
        <span><span className="inline-block w-2 h-2 rounded-sm align-middle mr-1" style={{ background: SELL }} />主动卖(内盘)</span>
        <span className="text-accent"><span className="inline-block w-2 h-2 rounded-sm align-middle mr-1" style={{ background: 'rgba(200,168,118,.5)' }} />我成交过</span>
        <span className="text-text-muted ml-auto">全天逐笔聚合 · 仅供参考</span>
      </div>
    </div>
  )
}
