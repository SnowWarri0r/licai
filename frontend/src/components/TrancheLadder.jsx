import { useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtPrice } from '../helpers'

function TrancheRow({ tranche, currentPrice, currentHealth, onExecute }) {
  const {
    id, idx, trigger_price, shares, requires_health, status,
    sell_target, executed_price, sold_back_price,
  } = tranche
  const [busy, setBusy] = useState(false)

  const distance = ((trigger_price - currentPrice) / currentPrice) * 100
  const reached = currentPrice <= trigger_price * 1.002
  const healthOK = requires_health === 'any'
    || (requires_health === 'yellow' && currentHealth !== 'red')
    || (requires_health === 'green' && currentHealth === 'green')

  const isExecuted = status === 'executed'
  const isSoldBack = !!sold_back_price
  const sellReached = sell_target && currentPrice >= sell_target * 0.998

  const handleExecute = async () => {
    if (!id) return
    const price = prompt(`档位 ${idx}：确认买入成交价`, trigger_price.toFixed(2))
    if (!price) return
    setBusy(true)
    try {
      await fetchJSON(`/api/unwind/tranches/${id}/execute`, {
        method: 'POST',
        body: JSON.stringify({ executed_price: parseFloat(price) }),
      })
      onExecute?.()
    } catch { alert('执行失败') } finally { setBusy(false) }
  }

  const handleSellBack = async () => {
    if (!id) return
    const defaultPrice = sell_target ? sell_target.toFixed(2) : currentPrice.toFixed(2)
    const price = prompt(`档位 ${idx}：确认卖出回收价`, defaultPrice)
    if (!price) return
    setBusy(true)
    try {
      await fetchJSON(`/api/unwind/tranches/${id}/sell-back`, {
        method: 'POST',
        body: JSON.stringify({ sold_price: parseFloat(price) }),
      })
      onExecute?.()
    } catch { alert('卖回失败') } finally { setBusy(false) }
  }

  const handleUndoSell = async () => {
    if (!id) return
    if (!confirm(`撤销档位 ${idx} 的卖回记录？`)) return
    setBusy(true)
    try {
      await fetchJSON(`/api/unwind/tranches/${id}/sell-back`, { method: 'DELETE' })
      onExecute?.()
    } catch { alert('撤销失败') } finally { setBusy(false) }
  }

  // Row background
  const rowBg = isSoldBack
    ? 'bg-surface-3/40'
    : isExecuted
      ? (sellReached ? 'bg-bull/15' : 'bg-accent/8')
      : reached && healthOK ? 'bg-bull/10' : 'bg-transparent'

  // Index badge
  const badgeClass = isSoldBack
    ? 'bg-surface-3 text-text-muted'
    : isExecuted
      ? 'bg-accent/25 text-accent'
      : reached && healthOK
        ? 'bg-bull text-bg'
        : 'bg-surface-3 text-text-dim'

  const reqColor = {
    green: 'text-bull',
    yellow: 'text-[var(--color-signal-moderate)]',
    any: 'text-text-muted',
  }[requires_health] || 'text-text-muted'

  // Leg indicators
  // Buy leg always displayed; sell leg has 4 states: not-yet-bought, bought-waiting, sell-ready, sold-done
  const buyLeg = isExecuted
    ? <span className="text-text-muted">买 {fmtPrice(executed_price)} ✓</span>
    : reached && healthOK
      ? <span className="text-bull font-semibold">买 {fmtPrice(trigger_price)} 触发</span>
      : <span className="text-bull">买 {fmtPrice(trigger_price)}</span>

  const sellLeg = isSoldBack
    ? <span className="text-text-muted">卖 {fmtPrice(sold_back_price)} ✓</span>
    : !sell_target
      ? <span className="text-text-muted">卖 --</span>
      : !isExecuted
        ? <span className="text-bear-bright opacity-50">卖 {fmtPrice(sell_target)}</span>
        : sellReached
          ? <span className="text-bear-bright font-semibold">卖 {fmtPrice(sell_target)} 触发</span>
          : <span className="text-bear-bright">卖 {fmtPrice(sell_target)}</span>

  // Action button
  let actionEl = null
  if (isSoldBack) {
    actionEl = (
      <button onClick={handleUndoSell} disabled={busy}
        className="text-[10px] text-text-muted hover:text-bear cursor-pointer underline-offset-2 hover:underline">
        撤销
      </button>
    )
  } else if (isExecuted) {
    actionEl = (
      <button onClick={handleSellBack} disabled={busy}
        className={`px-2 py-0.5 text-[10px] rounded font-semibold cursor-pointer ${
          sellReached
            ? 'bg-bear text-bg hover:opacity-90'
            : 'border border-bear/50 text-bear-bright hover:bg-bear/10'
        }`}>
        {busy ? '...' : sellReached ? '卖回成交' : '卖回'}
      </button>
    )
  } else if (reached && healthOK && id) {
    actionEl = (
      <button onClick={handleExecute} disabled={busy}
        className="px-2 py-0.5 text-[10px] rounded bg-bull text-bg font-semibold hover:opacity-90 cursor-pointer">
        {busy ? '...' : '买入成交'}
      </button>
    )
  } else {
    actionEl = <span className="text-[10px] text-text-muted">等待</span>
  }

  // Status text
  let statusText
  if (isSoldBack) {
    const profit = sold_back_price - executed_price
    statusText = <span className="text-text-muted">
      一轮完成 {profit >= 0 ? '+' : ''}{(profit * shares).toFixed(0)}
    </span>
  } else if (isExecuted) {
    statusText = sellReached
      ? <span className="text-bear-bright font-semibold">可卖回</span>
      : <span className="text-accent">已买·待卖</span>
  } else if (reached) {
    statusText = healthOK
      ? <span className="text-bull font-semibold">触发中</span>
      : <span className="text-[var(--color-signal-moderate)]">基本面锁</span>
  } else {
    statusText = <span className="text-text-muted">距 {distance.toFixed(1)}%</span>
  }

  return (
    <div
      className={`grid gap-3 items-center px-3 py-1.5 text-[12px] border-b border-border last:border-b-0 ${rowBg}`}
      style={{ gridTemplateColumns: '22px 1fr auto auto', opacity: isSoldBack ? 0.7 : 1 }}
    >
      <div className={`rounded flex items-center justify-center text-[10px] font-mono font-bold ${badgeClass}`}
        style={{ width: 22, height: 22 }}>
        {isSoldBack ? '✓' : isExecuted ? idx : idx}
      </div>

      <div>
        <div className="font-mono font-semibold">
          {buyLeg}
          <span className="text-text-muted mx-1.5">→</span>
          {sellLeg}
        </div>
        <div className="text-[10px] text-text-muted font-mono mt-0.5">
          ×{shares}股
          {requires_health !== 'any' && (
            <span className={`ml-2 ${reqColor}`}>
              需 {requires_health === 'green' ? '🟢' : '🟡+'}
            </span>
          )}
        </div>
      </div>

      <div className="text-[10px] font-mono text-right min-w-20">
        {statusText}
      </div>

      <div>{actionEl}</div>
    </div>
  )
}

export default function TrancheLadder({ tranches, currentPrice, currentHealth, onExecute }) {
  if (!tranches || tranches.length === 0) {
    return (
      <div className="text-[11px] text-text-muted py-4 text-center italic rounded-lg border border-border bg-surface-2/40">
        暂无档位，点击"生成解套计划"开始
      </div>
    )
  }
  const sorted = [...tranches].sort((a, b) => b.trigger_price - a.trigger_price)
  const bought = sorted.filter(t => t.status === 'executed').length
  const sold = sorted.filter(t => t.sold_back_price).length

  return (
    <div className="rounded-lg overflow-hidden bg-surface-2 border border-border">
      <div className="px-3 py-2 bg-surface-3 border-b border-border flex items-center justify-between">
        <span className="text-[11px] text-text font-bold tracking-wider uppercase">加仓档位</span>
        <span className="text-[10px] text-text-muted font-mono">
          共 {sorted.length} 档 · 已买 {bought} · 已卖回 {sold}
        </span>
      </div>
      {sorted.map(t => (
        <TrancheRow
          key={t.id || t.idx}
          tranche={t}
          currentPrice={currentPrice}
          currentHealth={currentHealth}
          onExecute={onExecute}
        />
      ))}
    </div>
  )
}
