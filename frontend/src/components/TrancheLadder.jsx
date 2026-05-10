import { useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtPrice } from '../helpers'

function TrancheRow({ tranche, currentPrice, onChange }) {
  const {
    id, idx, trigger_price, shares, status,
    executed_price, source,
  } = tranche
  const [busy, setBusy] = useState(false)

  // 减仓档位: trigger >= current 才触发
  const distance = ((trigger_price - currentPrice) / currentPrice) * 100
  const reached = currentPrice >= trigger_price * 0.998
  const isExecuted = status === 'executed'

  const handleExecute = async () => {
    if (!id) return
    const price = prompt(`档位 ${idx}: 确认卖出成交价`, currentPrice.toFixed(2))
    if (!price) return
    setBusy(true)
    try {
      await fetchJSON(`/api/unwind/tranches/${id}/execute`, {
        method: 'POST',
        body: JSON.stringify({ executed_price: parseFloat(price) }),
      })
      onChange?.()
    } catch (e) { alert('成交记录失败: ' + (e?.message || '')) }
    finally { setBusy(false) }
  }

  const handleUndo = async () => {
    if (!id) return
    if (!confirm(`撤销档位 ${idx} 的卖出记录? (会删除对应 REDUCE 流水)`)) return
    setBusy(true)
    try {
      await fetchJSON(`/api/unwind/tranches/${id}/execute`, { method: 'DELETE' })
      onChange?.()
    } catch (e) { alert('撤销失败: ' + (e?.message || '')) }
    finally { setBusy(false) }
  }

  const rowBg = isExecuted
    ? 'bg-bull/10'
    : reached ? 'bg-bear/10' : 'bg-transparent'

  const badgeClass = isExecuted
    ? 'bg-bull text-bg'
    : reached ? 'bg-bear text-bg' : 'bg-surface-3 text-text-dim'

  let actionEl
  if (isExecuted) {
    actionEl = (
      <button onClick={handleUndo} disabled={busy}
        className="text-[10px] text-text-muted hover:text-bear cursor-pointer underline-offset-2 hover:underline">
        撤销
      </button>
    )
  } else if (reached && id) {
    actionEl = (
      <button onClick={handleExecute} disabled={busy}
        className="px-2 py-0.5 text-[10px] rounded bg-bear text-bg font-semibold hover:opacity-90 cursor-pointer">
        {busy ? '...' : '减仓成交'}
      </button>
    )
  } else {
    actionEl = <span className="text-[10px] text-text-muted">等待</span>
  }

  let statusText
  if (isExecuted) {
    const proceeds = (executed_price || trigger_price) * shares
    statusText = <span className="text-bull">已卖 ¥{proceeds.toFixed(0)}</span>
  } else if (reached) {
    statusText = <span className="text-bear-bright font-semibold">触发中</span>
  } else {
    statusText = <span className="text-text-muted">距 +{distance.toFixed(1)}%</span>
  }

  return (
    <div
      className={`grid gap-3 items-center px-3 py-1.5 text-[12px] border-b border-border last:border-b-0 ${rowBg}`}
      style={{ gridTemplateColumns: '22px 1fr auto auto', opacity: isExecuted ? 0.85 : 1 }}
    >
      <div className={`rounded flex items-center justify-center text-[10px] font-mono font-bold ${badgeClass}`}
        style={{ width: 22, height: 22 }}>
        {isExecuted ? '✓' : idx}
      </div>

      <div>
        <div className="font-mono font-semibold">
          <span className={isExecuted ? 'text-text-muted' : 'text-bear-bright'}>
            卖 {fmtPrice(executed_price || trigger_price)}
          </span>
          <span className="text-text-muted mx-1.5">·</span>
          <span className="text-text">×{shares}股</span>
        </div>
        {source && (
          <div className="text-[10px] text-text-muted font-mono mt-0.5">{source}</div>
        )}
      </div>

      <div className="text-[10px] font-mono text-right min-w-20">
        {statusText}
      </div>

      <div>{actionEl}</div>
    </div>
  )
}

export default function TrancheLadder({ tranches, currentPrice, onExecute }) {
  if (!tranches || tranches.length === 0) {
    return (
      <div className="text-[11px] text-text-muted py-4 text-center italic rounded-lg border border-border bg-surface-2/40">
        暂无档位, 点击"生成减仓阶梯"开始
      </div>
    )
  }
  // 减仓档位低价在前 (从浅反弹到 TVM 解套)
  const sorted = [...tranches].sort((a, b) => a.trigger_price - b.trigger_price)
  const sold = sorted.filter(t => t.status === 'executed').length
  const remainingShares = sorted
    .filter(t => t.status !== 'executed')
    .reduce((sum, t) => sum + (t.shares || 0), 0)

  return (
    <div className="rounded-lg overflow-hidden bg-surface-2 border border-border">
      <div className="px-3 py-2 bg-surface-3 border-b border-border flex items-center justify-between">
        <span className="text-[11px] text-text font-bold tracking-wider uppercase">减仓阶梯</span>
        <span className="text-[10px] text-text-muted font-mono">
          共 {sorted.length} 档 · 已卖 {sold} · 待卖 {remainingShares}股
        </span>
      </div>
      {sorted.map(t => (
        <TrancheRow
          key={t.id || t.idx}
          tranche={t}
          currentPrice={currentPrice}
          onChange={onExecute}
        />
      ))}
    </div>
  )
}
