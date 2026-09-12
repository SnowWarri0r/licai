import React, { useEffect, useState } from 'react'
import { isOnchainEtf } from '../../../helpers'
import { fetchJSON } from '../../../hooks/useApi'
import { DcaPanel } from './DcaPanel'

// Inline edit row for external asset
export function EditAssetRow({ asset, onDone, onCancel, brokers = [] }) {
  const isBot = asset.asset_type === 'BOT'
  const isFund = asset.asset_type === 'FUND'
  const isCrypto = asset.asset_type === 'CRYPTO'
  const isWealth = asset.asset_type === 'WEALTH'
  const isCash = asset.asset_type === 'CASH'
  const boundToOkx = !!asset.okx_algo_id

  // --- Smart-linked fields for FUND/CRYPTO ---
  // Field semantics (matching mainstream 公募基金 App convention):
  //   持有成本 = unit cost (RMB / 份)            — derived: cost_amount / shares
  //   持有份额 = total shares
  //   基金净值 = unit NAV (RMB / 份)              — from live quote, editable
  //   持有金额 = total market value              = shares × NAV + 待确认金额
  //   待确认金额 = pending settlement (no shares yet)
  //
  // DB columns: cost_amount (total), shares, manual_value (mv override), pending_amount
  // 优先官方公布净值 (跟主流基金 App 显示一致)，est_nav 仅作 fallback
  const liveNav = asset.quote?.nav ?? asset.quote?.est_nav ?? null
  const liveNavDate = asset.quote?.nav_date || asset.quote?.est_time || ''
  const initShares = asset.shares ?? ''
  const initTotalCost = asset.cost_amount ?? ''
  const initUnitCost = (parseFloat(initShares) > 0 && parseFloat(initTotalCost) > 0)
    ? (parseFloat(initTotalCost) / parseFloat(initShares)).toFixed(4) : ''
  const initNav = liveNav != null ? String(liveNav) : ''
  const initPending = asset.pending_amount ? String(asset.pending_amount) : ''
  const pendingNum = parseFloat(initPending) || 0
  const initMv = asset.manual_value != null
    ? String(asset.manual_value)
    : (parseFloat(initShares) > 0 && parseFloat(initNav) > 0
        ? (parseFloat(initShares) * parseFloat(initNav) + pendingNum).toFixed(2)
        : '')

  const [unitCost, setUnitCost] = useState(initUnitCost)
  const [shares, setShares] = useState(String(initShares ?? ''))
  const [nav, setNav] = useState(initNav ?? '')
  const [mv, setMv] = useState(initMv ?? '')
  const [pending, setPending] = useState(initPending)
  const [lockMv, setLockMv] = useState(asset.manual_value != null)
  // 申购费率以百分比展示 (DB 存小数, 0.0015 → "0.15")
  const [feeRatePct, setFeeRatePct] = useState(
    asset.purchase_fee_rate != null ? String(+(asset.purchase_fee_rate * 100).toFixed(4)) : ''
  )
  const [broker, setBroker] = useState(asset.broker || '')

  // WEALTH-only fields
  const [cost, setCost] = useState(String(initTotalCost ?? ''))  // also reused by BOT
  const [manualValue, setManualValue] = useState(asset.manual_value ?? '')
  const [annualYield, setAnnualYield] = useState(
    asset.annual_yield_rate != null ? (asset.annual_yield_rate * 100).toString() : ''
  )
  const [startDate, setStartDate] = useState(asset.start_date || '')

  const [busy, setBusy] = useState(false)
  const rootRef = React.useRef(null)
  useEffect(() => {
    rootRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  // Field-linkage. Only the source input keeps the user's raw string; derived
  // fields get fixed-precision strings.
  // Relationships (FUND/CRYPTO):
  //   mv = shares × nav + pending
  //   shares = (mv - pending) / nav     (when user types mv)
  //   total_cost = unitCost × shares    (computed at save time)
  const update = (field, value) => {
    const next = { unitCost, shares, nav, mv, pending }
    next[field] = value
    const numOf = (k) => parseFloat(next[k]) || 0
    const u = numOf('unitCost'), s = numOf('shares'), n = numOf('nav'), p = numOf('pending')

    if (field === 'unitCost') {
      // unit cost change doesn't affect shares/nav/mv/pending — just recomputes total at save
    } else if (field === 'shares') {
      if (n > 0) next.mv = (s * n + p).toFixed(2)
    } else if (field === 'nav') {
      if (s > 0) next.mv = (s * n + p).toFixed(2)
    } else if (field === 'mv') {
      const m = parseFloat(value) || 0
      if (n > 0) {
        const confirmedValue = m - p
        if (confirmedValue >= 0) next.shares = (confirmedValue / n).toFixed(4)
      }
    } else if (field === 'pending') {
      if (s > 0 && n > 0) next.mv = (s * n + parseFloat(value || 0)).toFixed(2)
    }
    setUnitCost(next.unitCost); setShares(next.shares); setNav(next.nav); setMv(next.mv); setPending(next.pending)
  }

  const save = async () => {
    setBusy(true)
    try {
      const payload = {}
      if (isFund || isCrypto) {
        const u = parseFloat(unitCost) || 0
        const s = parseFloat(shares) || 0
        // FUND/CRYPTO 的 cost/shares 是流水推算出来的. 只在用户"实质改了"时才回传 —
        // 否则单价是 4 位小数显示, 单价×份额 跟账本真值差几分钱, 后端会误判成本变了,
        // 注入一条莫名其妙的"利息/分红 (adjust)"流水。差值在阈值内就不传, 不动账本。
        const newCost = u > 0 && s > 0 ? Number((u * s).toFixed(4)) : (cost !== '' ? parseFloat(cost) : null)
        const newShares = shares !== '' ? parseFloat(shares) : null
        const curCost = asset.cost_amount != null ? Number(asset.cost_amount) : null
        const curShares = asset.shares != null ? Number(asset.shares) : null
        if (newShares != null && (curShares == null || Math.abs(newShares - curShares) > 1e-4)) {
          payload.shares = newShares
        }
        if (newCost != null && (curCost == null || Math.abs(newCost - curCost) > 0.5)) {
          payload.cost_amount = newCost
        }
        payload.manual_value = lockMv && mv !== '' ? parseFloat(mv) : null
        payload.pending_amount = pending !== '' ? parseFloat(pending) : 0
        // 申购费率: 百分比 → 小数. 留空写 null. 场内 ETF 走佣金无申购费, 不传。
        if (isFund && !isOnchainEtf(asset.code)) payload.purchase_fee_rate = feeRatePct !== '' ? parseFloat(feeRatePct) / 100 : null
        if (isFund && isOnchainEtf(asset.code)) payload.broker = broker || null
      } else if (isWealth) {
        payload.cost_amount = cost !== '' ? parseFloat(cost) : null
        payload.shares = null
        payload.manual_value = manualValue !== '' ? parseFloat(manualValue) : null
        payload.annual_yield_rate = annualYield !== '' ? parseFloat(annualYield) / 100 : null
        payload.start_date = startDate || null
      } else if (isCash) {
        // CASH: 当前余额映射到 cost_amount = manual_value = balance；可选年化用于估月息
        const balance = manualValue !== '' ? parseFloat(manualValue) : (cost !== '' ? parseFloat(cost) : null)
        payload.cost_amount = balance
        payload.manual_value = balance
        payload.shares = null
        payload.annual_yield_rate = annualYield !== '' ? parseFloat(annualYield) / 100 : null
      } else { // BOT
        payload.cost_amount = cost !== '' ? parseFloat(cost) : null
        payload.shares = null
        payload.manual_value = manualValue !== '' ? parseFloat(manualValue) : null
      }
      await fetchJSON(`/api/assets/${asset.id}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      onDone?.()
    } finally { setBusy(false) }
  }

  const unbindOkx = async () => {
    if (!confirm('解除 OKX 绑定？市值将改为手动维护。')) return
    setBusy(true)
    try {
      await fetchJSON(`/api/assets/${asset.id}`, {
        method: 'PUT',
        body: JSON.stringify({ okx_algo_id: '', okx_bot_type: '' }),
      })
      onDone?.()
    } finally { setBusy(false) }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1 text-[12px] text-text font-mono outline-none focus:border-accent'

  return (
    <div ref={rootRef}
      className="px-6 py-3 border-b-2 border-accent bg-accent/5 flex flex-wrap gap-3 items-end"
      style={{ animation: 'fade-up 0.2s ease-out' }}>
      <span className="text-[11px] text-accent font-semibold mr-2 basis-full">
        ✎ 编辑 <span className="text-text-bright">{asset.name}</span>
        {boundToOkx && <span className="ml-2 text-[10px] text-bull">🔗 OKX 同步中</span>}
        {(isFund || isCrypto) && (
          <span className="ml-2 text-[10px] text-text-muted font-normal">
            · 改任一字段，其余自动算
          </span>
        )}
      </span>

      {/* FUND / CRYPTO: 持有成本(单价) / 持有份额 / 基金净值(单价) / 持有金额(总) / 待确认金额 */}
      {(isFund || isCrypto) && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">持有成本 ¥/{isFund ? '份' : '个'}</label>
            <input type="number" step="0.0001" value={unitCost} onChange={e => update('unitCost', e.target.value)} className={`${inp} w-24`} placeholder="2.4856" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">{isFund ? '持有份额' : '持有数量'}</label>
            <input type="number" step="0.0001" value={shares} onChange={e => update('shares', e.target.value)} className={`${inp} w-28`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              {isFund ? '基金净值' : '单价'}
              {liveNav != null && (
                <span className="ml-1 text-[9.5px] text-text-muted">
                  实时 {liveNav}{liveNavDate ? ` · ${String(liveNavDate).slice(0, 10)}` : ''}
                </span>
              )}
            </label>
            <input type="number" step="0.0001" value={nav} onChange={e => update('nav', e.target.value)} className={`${inp} w-24`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">持有金额 ¥（含待确认）</label>
            <input type="number" step="0.01" value={mv} onChange={e => update('mv', e.target.value)} className={`${inp} w-32`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">待确认金额 ¥</label>
            <input type="number" step="0.01" value={pending} onChange={e => update('pending', e.target.value)} className={`${inp} w-28`} placeholder="0" />
          </div>
          {isFund && !isOnchainEtf(asset.code) && (
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">申购费率 %</label>
              <input type="number" step="0.01" value={feeRatePct} onChange={e => setFeeRatePct(e.target.value)}
                className={`${inp} w-24`} placeholder="C类填0" title="定投批量确认按此费率内扣算份额。A类填折后实际费率(如0.15), C类/无申购费填0或留空" />
            </div>
          )}
          {isFund && isOnchainEtf(asset.code) && (
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">券商</label>
              <select className={`${inp} w-28`} value={broker} onChange={e => setBroker(e.target.value)}>
                <option value="">默认</option>
                {brokers.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
              </select>
            </div>
          )}
          <label className="flex items-center gap-1.5 text-[11px] text-text-dim cursor-pointer select-none ml-1">
            <input type="checkbox" checked={lockMv} onChange={e => setLockMv(e.target.checked)} />
            锁定市值
          </label>
          {parseFloat(unitCost) > 0 && parseFloat(shares) > 0 && (
            <div className="basis-full text-[10px] text-text-muted pt-1">
              累计本金 ≈ ¥{(parseFloat(unitCost) * parseFloat(shares)).toFixed(2)}
              （= 持有成本 × 持有份额）
            </div>
          )}
        </>
      )}

      {/* WEALTH: cost + 起投日 + 年化/手动总额二选一 */}
      {isWealth && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">本金 ¥</label>
            <input type="number" step="0.01" value={cost} onChange={e => setCost(e.target.value)} className={`${inp} w-28`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">起投日</label>
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className={`${inp} w-36`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">年化 % (二选一)</label>
            <input type="number" step="0.001" value={annualYield} onChange={e => setAnnualYield(e.target.value)} className={`${inp} w-24`} placeholder="2.15" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">当前余额 ¥ (App 显示数)</label>
            <input type="number" step="0.01" value={manualValue} onChange={e => setManualValue(e.target.value)} className={`${inp} w-36`} placeholder="去 App 抄数" />
          </div>
        </>
      )}

      {/* CASH: 当前余额 + 可选 7日年化 (用于估月利息) */}
      {isCash && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">当前余额 ¥（直接抄你 App 上看到的数）</label>
            <input type="number" step="0.01" autoFocus value={manualValue}
              onChange={e => { setManualValue(e.target.value); setCost(e.target.value) }}
              className={`${inp} w-44`} placeholder="3000" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">7日年化 % (可选,用于估月利息)</label>
            <input type="number" step="0.001" value={annualYield}
              onChange={e => setAnnualYield(e.target.value)}
              className={`${inp} w-24`} placeholder="1.17" />
          </div>
        </>
      )}

      {/* BOT: cost + 当前资产 */}
      {isBot && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">投入本金 ¥</label>
            <input type="number" step="0.01" value={cost} onChange={e => setCost(e.target.value)} className={`${inp} w-28`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              {boundToOkx ? '当前资产 ¥ (OKX 同步,编辑将覆盖)' : '当前资产 ¥'}
            </label>
            <input type="number" step="0.01" value={manualValue} onChange={e => setManualValue(e.target.value)} className={`${inp} w-32`} />
          </div>
        </>
      )}

      <button onClick={save} disabled={busy}
        className="px-4 py-1.5 rounded bg-accent text-bg font-semibold text-[12px] hover:opacity-90 cursor-pointer disabled:opacity-50">
        {busy ? '...' : '保存'}
      </button>
      {boundToOkx && (
        <button onClick={unbindOkx} disabled={busy}
          className="px-3 py-1.5 rounded border border-border text-text-dim text-[12px] hover:text-bear cursor-pointer">
          解绑 OKX
        </button>
      )}
      <button onClick={onCancel}
        className="px-3 py-1.5 rounded border border-border text-text-dim text-[12px] hover:text-text cursor-pointer">
        取消
      </button>

      {(isFund || isCrypto) && (
        <DcaPanel assetId={asset.id} />
      )}
    </div>
  )
}
