import React, { useEffect, useState } from 'react'
import { estimateFee, fmtMoney, isOnchainEtf } from '../../../helpers'
import { fetchJSON } from '../../../hooks/useApi'
import { isEtfCode } from '../helpers'

// AddLotRow — 加仓 modal. 与 ReduceLotRow 对称.
// OTC 基金 (场外): 只填本金 + 日期, 写 pending 流水, T+1 净值出来后回流水"确认"补份额
// 场内 ETF / CRYPTO: 三选二 (本金/份额/单价) + 手续费 + 日期, 立即 confirmed
// WEALTH/CASH: 本金 + 日期 (+ WEALTH 可填本笔起投日 / 年化)
// ============================================================
export function AddLotRow({ asset, onDone, onCancel, brokers = [] }) {
  const [principal, setPrincipal] = useState('')
  const [shares, setShares] = useState('')        // FUND/CRYPTO: 新增份额
  const [unitPrice, setUnitPrice] = useState('')  // FUND/CRYPTO: 单价 (净值 / 币价)
  const [fee, setFee] = useState('')              // FUND/CRYPTO: 手续费 ¥ (场内 ETF / 加密 taker fee)
  const [feeTouched, setFeeTouched] = useState(false)
  const [lotStartDate, setLotStartDate] = useState(new Date().toISOString().slice(0, 10))
  const [lotTime, setLotTime] = useState('')     // 成交时刻 HH:MM (可选, 供分时图打点)
  const [lotYield, setLotYield] = useState('')   // WEALTH: 加投年化 %
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onCancel?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  const t = asset.asset_type
  const isFund = t === 'FUND'
  const isCrypto = t === 'CRYPTO'
  const isWealth = t === 'WEALTH'
  const isCash = t === 'CASH'
  const isOtcFund = isFund && !isEtfCode(asset.code)
  const isShareBased = isFund || isCrypto

  // --- Live preview ---
  const oldCost = parseFloat(asset.cost_amount) || 0
  const oldShares = parseFloat(asset.shares) || 0
  const oldPending = parseFloat(asset.pending_amount) || 0
  const p = parseFloat(principal) || 0
  const sNum = parseFloat(shares) || 0
  const uNum = parseFloat(unitPrice) || 0
  const feeNum = parseFloat(fee) || 0

  // Auto-suggest 手续费 for 按股买 mode (场内 ETF: 万 3, 最低 ¥5)
  useEffect(() => {
    if (!(isFund || isCrypto) || feeTouched) return
    if (sNum > 0 && uNum > 0) {
      const amount = sNum * uNum
      const kind = isOnchainEtf(asset.code) ? 'etf' : 'stock'
      const est = estimateFee(amount, brokers, asset.broker, kind)
      setFee(est.toFixed(2))
    }
  }, [shares, unitPrice, feeTouched, isFund, isCrypto, brokers, asset.broker, asset.code])

  let preview = null
  if (isFund || isCrypto) {
    const hasShares = sNum > 0
    const hasUnit = uNum > 0
    const hasPrincipal = p > 0
    // 模式判定:
    //   按股买:    shares + unit_price → 本金 = 单价×份额 + 手续费
    //   本金+单价: principal + unit_price → 后端算 shares = (principal-fee)/price
    //   本金+份额: principal + shares
    //   待确认:    只有 principal
    if (hasShares && hasUnit) {
      const gross = sNum * uNum
      const totalCost = gross + feeNum
      const newCost = oldCost + totalCost
      const newShares = oldShares + sNum
      const newAvg = newShares > 0 ? newCost / newShares : 0
      preview = {
        模式: '✓ 按股买（本金自动算）',
        本笔成交: `${sNum.toFixed(4)} × ¥${uNum.toFixed(4)} = ¥${gross.toFixed(2)}` + (feeNum > 0 ? ` + 手续费 ¥${feeNum.toFixed(2)} = ¥${totalCost.toFixed(2)}` : ''),
        新累计本金: '¥' + fmtMoney(newCost),
        新累计份额: newShares.toFixed(4),
        新持有成本: '¥' + newAvg.toFixed(4) + '/份',
      }
    } else if (hasPrincipal && (hasShares || hasUnit)) {
      const totalP = p + feeNum
      const incShares = hasShares ? sNum : (p / uNum)
      const newCost = oldCost + totalP
      const newShares = oldShares + incShares
      const newAvg = newShares > 0 ? newCost / newShares : 0
      preview = {
        模式: '✓ 确认型',
        新累计本金: '¥' + fmtMoney(newCost) + (feeNum > 0 ? ` (含手续费 ¥${feeNum.toFixed(2)})` : ''),
        新累计份额: newShares.toFixed(4),
        新持有成本: '¥' + newAvg.toFixed(4) + '/份',
        加仓份额: incShares.toFixed(4),
      }
    } else if (hasPrincipal) {
      const newPending = oldPending + p
      preview = {
        模式: '⏳ 待确认型',
        说明: '只填了金额，进入待确认。基金 T+1/T+2 出份额后回来编辑：清空待确认 → 填入实际份额 + 净值',
        新待确认金额: '¥' + fmtMoney(newPending),
        原累计本金: '¥' + fmtMoney(oldCost) + '（不变）',
        原持有份额: oldShares.toFixed(4) + '（不变）',
      }
    }
  } else if (p > 0) {
    if (isWealth) {
      const today = new Date()
      let oldStart
      try { oldStart = new Date(asset.start_date || asset.created_at) } catch { oldStart = today }
      const daysOld = Math.max(0, Math.floor((today - oldStart) / 86400000))
      let lotStart
      try { lotStart = new Date(lotStartDate) } catch { lotStart = today }
      const daysLot = Math.max(0, Math.floor((today - lotStart) / 86400000))
      const newCost = oldCost + p
      const wDays = newCost > 0 ? (oldCost * daysOld + p * daysLot) / newCost : 0
      const newStart = new Date(today.getTime() - Math.round(wDays) * 86400000)
      preview = {
        新累计本金: '¥' + fmtMoney(newCost),
        新有效起投日: newStart.toISOString().slice(0, 10),
        '(原起投日)': asset.start_date || '--',
      }
      if (lotYield !== '' && asset.annual_yield_rate != null) {
        const blended = (oldCost * asset.annual_yield_rate + p * (parseFloat(lotYield) / 100)) / newCost
        preview['新混合年化'] = (blended * 100).toFixed(3) + '%'
      }
    }
  }

  const save = async () => {
    setErr('')
    let body
    if (isFund || isCrypto) {
      // 4 种模式 (实际入账本金 = 名义本金 + 手续费):
      //   1) 按股买: shares + unit_price → 名义 = shares × unit_price; 实际 = 名义 + fee
      //   2) 本金 + 份额 → 实际 = principal + fee; shares 直接累加
      //   3) 本金 + 单价 → 实际 = principal + fee; 后端算 shares = principal / unit_price
      //      (注意: 这种模式下 unit_price 应是裸净值, 后端算的份额未考虑 fee)
      //   4) 仅本金 → 待确认（pending_amount, 也含 fee）
      if (sNum > 0 && uNum > 0) {
        body = { principal: Number((sNum * uNum + feeNum).toFixed(4)), shares: sNum }
      } else if (p > 0 && sNum > 0) {
        body = { principal: Number((p + feeNum).toFixed(4)), shares: sNum }
      } else if (p > 0 && uNum > 0) {
        body = { principal: Number((p + feeNum).toFixed(4)), unit_price: uNum }
      } else if (p > 0) {
        body = { principal: Number((p + feeNum).toFixed(4)) }
      } else {
        setErr('至少填本金，或同时填单价 + 份额（按股买）'); return
      }
      // 手续费单独透传, 后端单存到流水的 fee 字段 (principal 已含 fee, 算份额用净额),
      // 这样流水编辑里能看到/改手续费, 不会"消失"。
      if (feeNum > 0) body.fee = feeNum
    } else if (isWealth) {
      if (!(p > 0)) { setErr('请输入本金'); return }
      body = { principal: p }
      if (lotStartDate) body.lot_start_date = lotStartDate
      if (lotYield !== '') body.lot_yield_rate = parseFloat(lotYield) / 100
    } else {
      setErr('该资产类型不支持加仓'); return
    }
    // 所有类型都把日期透传 (后端 lot_start_date 会写到 action.trade_date)
    if (lotStartDate && !body.lot_start_date) body.lot_start_date = lotStartDate
    if (lotTime) body.trade_time = lotTime
    setBusy(true)
    try {
      const r = await fetchJSON(`/api/assets/${asset.id}/add-lot`, {
        method: 'POST', body: JSON.stringify(body),
      })
      if (r.message === 'lot added') onDone?.()
      else setErr(JSON.stringify(r))
    } catch (e) { setErr(String(e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onCancel}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[480px] max-w-[95vw] space-y-3"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">⊕ 加仓 / 申购 <span className="text-text">{asset.name}</span></h3>
          <button onClick={onCancel} className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>
        <div className="text-[11px] text-text-dim">
          当前: cost ¥{fmtMoney(oldCost)}
          {isShareBased && asset.shares && <> · shares {parseFloat(asset.shares).toFixed(4)}</>}
          {isOtcFund && (
            <span className="ml-2 px-1.5 py-[1px] rounded bg-warn/15 text-warn text-[10px] border border-warn/40">OTC 场外基金</span>
          )}
        </div>

        {isOtcFund ? (
          <>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">申购金额 (CNY)</label>
              <input type="number" inputMode="decimal" placeholder="例: 1000" autoFocus
                value={principal} onChange={e => setPrincipal(e.target.value)}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bull-bright outline-none focus:border-accent" />
            </div>
            <div className="text-[10.5px] text-text-muted leading-relaxed bg-warn/5 border border-warn/30 rounded px-2 py-1.5">
              场外基金 T+1/T+2 才出净值。这一步只记申请，会标 <span className="text-warn font-mono">pending</span>，
              暂不进总盈亏。等净值确认后回 <span className="text-text">流水</span> 里点 <span className="text-text">确认</span>，补份额/净值再入账。
            </div>
          </>
        ) : isShareBased ? (
          <>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">本金 (CNY) <span className="text-text-muted">— 按股买可空</span></label>
              <input type="number" inputMode="decimal" placeholder="1000" autoFocus
                value={principal} onChange={e => setPrincipal(e.target.value)}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bull-bright outline-none focus:border-accent" />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[11.5px] text-text-dim block mb-1">{isFund ? '成交净值' : '单价'}</label>
                <input type="number" inputMode="decimal" placeholder="3.4399"
                  value={unitPrice} onChange={e => setUnitPrice(e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
              </div>
              <div>
                <label className="text-[11.5px] text-text-dim block mb-1">{isFund ? '成交份额' : '成交数量'}</label>
                <input type="number" inputMode="decimal" placeholder="290.7"
                  value={shares} onChange={e => setShares(e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
              </div>
            </div>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">
                手续费 ¥ <span className="text-text-muted text-[10px]">— 场内 ETF 默认万2.5/最低5; 场外公募填 0</span>
              </label>
              <input type="number" inputMode="decimal" placeholder="5.00"
                value={fee} onChange={e => { setFee(e.target.value); setFeeTouched(true) }}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
            </div>
          </>
        ) : (
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">本金 (CNY)</label>
            <input type="number" inputMode="decimal" placeholder="1000" autoFocus
              value={principal} onChange={e => setPrincipal(e.target.value)}
              className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bull-bright outline-none focus:border-accent" />
          </div>
        )}

        <div className={isWealth ? 'grid grid-cols-2 gap-2' : ''}>
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">{isWealth ? '本笔起投日' : '日期'}</label>
            <input type="date" value={lotStartDate} onChange={e => setLotStartDate(e.target.value)}
              className="bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent w-full" />
            {isShareBased && (
              <input type="time" value={lotTime} onChange={e => setLotTime(e.target.value)}
                title="成交时刻(可选), 留空用录入时间, 供分时图打点"
                className="bg-bg border border-border rounded-lg px-3 py-1.5 text-[12px] font-mono outline-none focus:border-accent w-full mt-1.5 text-text-dim" />
            )}
          </div>
          {isWealth && (
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">本笔年化 % <span className="text-text-muted text-[10px]">可空</span></label>
              <input type="number" inputMode="decimal" placeholder="2.15"
                value={lotYield} onChange={e => setLotYield(e.target.value)}
                className="bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent w-full" />
            </div>
          )}
        </div>

        {preview && (
          <div className="bg-surface-3 rounded-md px-3 py-2 space-y-0.5 text-[11px]">
            {Object.entries(preview).map(([k, v]) => (
              <div key={k} className="flex items-baseline justify-between gap-2">
                <span className="text-text-muted">{k}</span>
                <span className="font-mono text-bull-bright">{v}</span>
              </div>
            ))}
          </div>
        )}

        {err && <div className="text-[11px] text-bear-bright">{err}</div>}

        <div className="flex gap-2 pt-1">
          <button onClick={save}
            disabled={busy || !(isShareBased ? (p > 0 || (sNum > 0 && uNum > 0)) : p > 0)}
            className="flex-1 px-4 py-2 rounded-lg bg-bull text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {busy ? '...' : '确认加仓'}
          </button>
          <button onClick={onCancel}
            className="px-4 py-2 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med text-[13px] cursor-pointer">
            取消
          </button>
        </div>
      </div>
    </div>
  )
}

// ReduceLotRow — 减仓 / 赎回 modal.
// OTC 基金 (场外): 只填份额 + 日期, 写 pending 流水, 等 T+1 净值出来后回来"确认"
// ETF/CRYPTO: 三选二 (amount/shares/unit_price), 直接写 confirmed
// WEALTH/CASH: 仅 amount
export function ReduceLotRow({ asset, onDone, onCancel }) {
  const t = asset.asset_type
  const isShareBased = t === 'FUND' || t === 'CRYPTO'
  const isOtcFund = t === 'FUND' && !isEtfCode(asset.code)
  const isImmediate = !isOtcFund  // ETF/CRYPTO/WEALTH/CASH 立即结算

  const [amount, setAmount] = React.useState('')
  const [shares, setShares] = React.useState('')
  const [unitPrice, setUnitPrice] = React.useState('')
  const [tradeDate, setTradeDate] = React.useState(() => new Date().toISOString().slice(0, 10))
  const [tradeTime, setTradeTime] = React.useState('')         // 成交时刻 HH:MM (可选, 供分时图打点)
  const [interestPart, setInterestPart] = React.useState('')   // WEALTH/CASH: 其中利息部分
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState('')

  const f = (v) => v === '' ? null : parseFloat(v) || 0
  const a = f(amount), s = f(shares), u = f(unitPrice)

  // 实时推算第三个字段 (ETF/CRYPTO 即时模式)
  const inferred = React.useMemo(() => {
    if (!isShareBased || isOtcFund) return null
    if (a && s && (!u || u === 0)) return { unit_price: (a / s).toFixed(4) }
    if (a && u && (!s || s === 0)) return { shares: (a / u).toFixed(4) }
    if (s && u && (!a || a === 0)) return { amount: (s * u).toFixed(2) }
    return null
  }, [a, s, u, isShareBased, isOtcFund])

  // 估算实现盈亏 (按比例摊销当前 cost)
  const estRealized = React.useMemo(() => {
    const curCost = parseFloat(asset.cost_amount || 0)
    if (curCost <= 0) return null
    if (isOtcFund) {
      // OTC 阶段没有 amount, 只能算"按当前持仓平均成本算的占用成本"
      const curShares = parseFloat(asset.shares || 0)
      if (!s || s <= 0 || curShares <= 0) return null
      const matchedCost = (s / curShares) * curCost
      return { type: 'occupied_cost', val: matchedCost }
    }
    if (isShareBased) {
      if (!a || a <= 0) return null
      const curShares = parseFloat(asset.shares || 0)
      const consumeShares = s || (u && u > 0 ? a / u : 0)
      if (curShares <= 0 || !consumeShares) return null
      const matchedCost = (consumeShares / curShares) * curCost
      return { type: 'realized', val: a - matchedCost }
    }
    return null
  }, [a, s, u, isShareBased, isOtcFund, asset])

  const submit = async () => {
    setErr('')
    // ETF/CRYPTO 三选二: 缺哪个就用另两个反推
    let finalAmount = a, finalShares = s, finalUnit = u
    if (!isOtcFund && isShareBased) {
      if (!finalAmount && finalShares > 0 && finalUnit > 0) finalAmount = finalShares * finalUnit
      if (!finalShares && finalAmount > 0 && finalUnit > 0) finalShares = finalAmount / finalUnit
      if (!finalUnit && finalAmount > 0 && finalShares > 0) finalUnit = finalAmount / finalShares
    }

    if (isOtcFund) {
      if (!finalShares || finalShares <= 0) { setErr('请填卖出份额'); return }
    } else if (isShareBased) {
      if (!finalAmount || finalAmount <= 0) { setErr('金额 / 份额 / 单价 至少填两个'); return }
    } else {
      // WEALTH / CASH 仅金额
      if (!finalAmount || finalAmount <= 0) { setErr('赎回金额必须为正数'); return }
    }
    setBusy(true)
    try {
      const body = { trade_date: tradeDate }
      if (isShareBased && tradeTime) body.trade_time = tradeTime
      if (isOtcFund) {
        body.amount = 0
        body.shares = finalShares
      } else {
        body.amount = Number(finalAmount.toFixed(4))
        if (isShareBased) {
          if (finalShares) body.shares = Number(finalShares.toFixed(6))
          if (finalUnit) body.unit_price = Number(finalUnit.toFixed(6))
        }
      }
      // WEALTH/CASH 拆出利息部分 (可选), 写入 realized_pnl
      if ((t === 'WEALTH' || t === 'CASH') && interestPart !== '') {
        const ip = parseFloat(interestPart) || 0
        if (ip > 0) body.interest_part = Number(ip.toFixed(2))
      }
      await fetchJSON(`/api/assets/${asset.id}/reduce-lot`, {
        method: 'POST', body: JSON.stringify(body),
      })
      onDone?.()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onCancel}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[440px] max-w-[95vw] space-y-3"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">⊖ 减仓 / 赎回 <span className="text-text">{asset.name}</span></h3>
          <button onClick={onCancel} className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>
        <div className="text-[11px] text-text-dim">
          当前: cost ¥{fmtMoney(parseFloat(asset.cost_amount || 0))}
          {isShareBased && asset.shares && <> · shares {parseFloat(asset.shares).toFixed(4)}</>}
          {isOtcFund && (
            <span className="ml-2 px-1.5 py-[1px] rounded bg-warn/15 text-warn text-[10px] border border-warn/40">OTC 场外基金</span>
          )}
        </div>

        {isOtcFund ? (
          <>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">卖出份额</label>
              <input type="number" inputMode="decimal" placeholder="例: 500"
                value={shares} onChange={e => setShares(e.target.value)}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bear-bright outline-none focus:border-accent" />
            </div>
            <div className="text-[10.5px] text-text-muted leading-relaxed bg-warn/5 border border-warn/30 rounded px-2 py-1.5">
              场外基金 T+1/T+2 才出净值。这一步只记申请，会标 <span className="text-warn font-mono">pending</span>，
              暂不进总盈亏。等净值确认后回来 <span className="text-text">确认</span>，补金额/净值再入账。
            </div>
          </>
        ) : (
          <>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">赎回金额 (CNY)</label>
              <input type="number" inputMode="decimal" placeholder={inferred?.amount || '0'}
                value={amount} onChange={e => setAmount(e.target.value)}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bear-bright outline-none focus:border-accent" />
            </div>
            {(t === 'WEALTH' || t === 'CASH') && (
              <div>
                <label className="text-[11.5px] text-text-dim block mb-1">
                  其中利息 ¥ (可选)
                  <span className="ml-1 text-text-muted text-[10.5px]">— 留空则全当本金返还</span>
                </label>
                <input type="number" step="0.01" inputMode="decimal" placeholder="0.00"
                  value={interestPart} onChange={e => setInterestPart(e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono text-bull outline-none focus:border-accent" />
                {parseFloat(amount) > 0 && parseFloat(interestPart) > 0 && (
                  <div className="text-[10.5px] font-mono text-text-muted mt-1">
                    → 本金消耗 ¥{(parseFloat(amount) - parseFloat(interestPart)).toFixed(2)} · 利息计入已实现 +¥{parseFloat(interestPart).toFixed(2)}
                  </div>
                )}
              </div>
            )}
            {isShareBased && (
              /* 字段顺序与加仓表单一致: 先净值后份额 */
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[11.5px] text-text-dim block mb-1">单价 / 净值</label>
                  <input type="number" inputMode="decimal" placeholder={inferred?.unit_price || '可选'}
                    value={unitPrice} onChange={e => setUnitPrice(e.target.value)}
                    className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
                </div>
                <div>
                  <label className="text-[11.5px] text-text-dim block mb-1">赎回份额</label>
                  <input type="number" inputMode="decimal" placeholder={inferred?.shares || '可选'}
                    value={shares} onChange={e => setShares(e.target.value)}
                    className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
                </div>
              </div>
            )}
          </>
        )}

        <div>
          <label className="text-[11.5px] text-text-dim block mb-1">日期{isShareBased && <span className="text-text-muted text-[10px]"> · 时刻可空</span>}</label>
          <div className="flex gap-2">
            <input type="date" value={tradeDate} onChange={e => setTradeDate(e.target.value)}
              className="bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
            {isShareBased && (
              <input type="time" value={tradeTime} onChange={e => setTradeTime(e.target.value)}
                title="成交时刻(可选), 留空用录入时间, 供分时图打点"
                className="bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent text-text-dim" />
            )}
          </div>
        </div>

        {estRealized && (
          <div className="bg-surface-3 rounded-md px-3 py-2 flex items-center justify-between">
            <span className="text-[11.5px] text-text-dim">
              {estRealized.type === 'realized' ? '预估实现盈亏 (FIFO 比例)' : '占用成本 (赎回份额所占成本)'}
            </span>
            <span className={`font-mono font-semibold text-[13px] ${
              estRealized.type === 'realized'
                ? (estRealized.val >= 0 ? 'text-bull-bright' : 'text-bear-bright')
                : 'text-text'
            }`}>
              {estRealized.type === 'realized' && (estRealized.val >= 0 ? '+' : '')}
              ¥{fmtMoney(Math.abs(estRealized.val))}
            </span>
          </div>
        )}

        {err && <div className="text-[11px] text-bear-bright">{err}</div>}

        <div className="flex gap-2 pt-1">
          <button onClick={submit} disabled={busy}
            className="flex-1 px-4 py-2 rounded-lg bg-bear text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {busy ? '...' : '确认减仓'}
          </button>
          <button onClick={onCancel}
            className="px-4 py-2 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med text-[13px] cursor-pointer">
            取消
          </button>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// CashAdjustRow — 现金直接调余额。现金模型就是一个数字 (cost_amount = manual_value =
// balance), 挪入挪出/消费不是投资交易, 免加减仓流水: 输新余额, 或以 +/- 开头输增减额。
export function CashAdjustRow({ asset, onDone, onCancel }) {
  const current = Number(asset.manual_value ?? asset.cost_amount ?? 0)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const rootRef = React.useRef(null)
  useEffect(() => { rootRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }, [])

  const trimmed = input.trim()
  const isDelta = /^[+-]/.test(trimmed)
  const num = parseFloat(trimmed)
  const valid = trimmed !== '' && Number.isFinite(num)
  const next = valid ? Math.round((isDelta ? current + num : num) * 100) / 100 : null
  const nextBad = next != null && next < 0

  const save = async () => {
    if (!valid || nextBad || busy) return
    setBusy(true); setErr('')
    try {
      await fetchJSON(`/api/assets/${asset.id}`, {
        method: 'PUT',
        body: JSON.stringify({ cost_amount: next, manual_value: next }),
      })
      onDone?.()
    } catch (e) {
      setErr(String(e?.message || e))
    } finally { setBusy(false) }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1 text-[12px] text-text font-mono outline-none focus:border-accent'
  return (
    <div ref={rootRef}
      className="px-6 py-3 border-b-2 border-accent bg-accent/5 flex flex-wrap gap-3 items-end"
      style={{ animation: 'fade-up 0.2s ease-out' }}>
      <span className="text-[11px] text-accent font-semibold mr-2 basis-full">
        ± 调整余额 <span className="text-text-bright">{asset.name}</span>
        <span className="text-text-dim font-normal ml-2">当前 ¥{current.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</span>
      </span>
      <label className="flex flex-col gap-1 text-[10.5px] text-text-dim">
        新余额 / 增减额
        <input autoFocus value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') onCancel?.() }}
          placeholder="12000 或 +2000 / -500"
          className={`${inp} w-44`} />
      </label>
      <span className="text-[11.5px] font-mono pb-1.5 min-w-[120px]">
        {next != null && (
          nextBad
            ? <span className="text-bear-bright">余额会变成负数</span>
            : <span className="text-text-dim">→ <span className="text-text-bright">¥{next.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</span>
                {isDelta && <span className="ml-1">({num >= 0 ? '+' : ''}{num.toLocaleString('zh-CN')})</span>}</span>
        )}
      </span>
      <button onClick={save} disabled={!valid || nextBad || busy}
        className="text-[12px] px-3.5 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed">
        {busy ? '...' : '保存'}
      </button>
      <button onClick={onCancel} className="text-[12px] px-3 py-1.5 rounded-lg text-text-dim hover:text-text border border-border">
        取消
      </button>
      {err && <span className="text-[11px] text-bear-bright basis-full">{err}</span>}
    </div>
  )
}
