import { useEffect, useState } from 'react'
import { estimateFee, isOnchainEtf } from '../../../helpers'
import { fetchJSON } from '../../../hooks/useApi'
import Tooltip from '../../Tooltip'
import { BROKER_COMMISSION_MIN, BROKER_COMMISSION_RATE, KEY_TO_ASSET_TYPE } from '../constants'

// ============================================================
// Add asset form — covers F/C/R with type-specific fields
// (trimmed version of the original ExternalAssets AddAssetForm)
// ============================================================
export function AddAssetForm({ typeKey, onDone, onCancel, brokers = [] }) {
  const assetType = KEY_TO_ASSET_TYPE[typeKey]
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [platform, setPlatform] = useState('')
  const [shares, setShares] = useState('')
  const [cost, setCost] = useState('')
  const [costTouched, setCostTouched] = useState(false)
  const [selectedBroker, setSelectedBroker] = useState('')
  const [manualValue, setManualValue] = useState('')
  const [unitPrice, setUnitPrice] = useState('')
  const [fee, setFee] = useState('')
  const [feeTouched, setFeeTouched] = useState(false)
  const [note, setNote] = useState('')
  const [tradeTime, setTradeTime] = useState('')     // 成交时刻 HH:MM (场内 ETF/加密, 可选)
  const [feeRatePct, setFeeRatePct] = useState('')   // FUND 申购费率 %, C 类填 0
  const [lookingUp, setLookingUp] = useState(false)
  const [hint, setHint] = useState('')
  // OKX integration (only for BOT)
  const [okxBots, setOkxBots] = useState(null)
  const [okxAlgoId, setOkxAlgoId] = useState('')
  const [okxBotType, setOkxBotType] = useState('')
  // WEALTH (理财)
  const [annualYield, setAnnualYield] = useState('')
  const [startDate, setStartDate] = useState(new Date().toISOString().slice(0, 10))
  // DCA 定投 (FUND/CRYPTO only, 可选)
  const [dcaEnabled, setDcaEnabled] = useState(false)
  const [dcaValue, setDcaValue] = useState('')
  const [dcaFrequency, setDcaFrequency] = useState('monthly')
  const [dcaDayOfMonth, setDcaDayOfMonth] = useState(15)
  const [dcaDayOfWeek, setDcaDayOfWeek] = useState(1)

  useEffect(() => {
    if (assetType !== 'BOT') return
    fetchJSON('/api/assets/okx/status').then(s => {
      if (!s.configured) { setOkxBots([]); return }
      fetchJSON('/api/assets/okx/bots').then(r => setOkxBots(r.bots || [])).catch(() => setOkxBots([]))
    }).catch(() => setOkxBots([]))
  }, [assetType])

  // 按股买: 自动估算手续费 (场内 ETF 默认; 场外公募手动改 0)
  useEffect(() => {
    if (!(assetType === 'FUND' || assetType === 'CRYPTO') || feeTouched) return
    const s = parseFloat(shares); const u = parseFloat(unitPrice)
    if (s > 0 && u > 0) {
      const amount = s * u
      const kind = isOnchainEtf(code) ? 'etf' : 'stock'
      const est = estimateFee(amount, brokers, selectedBroker, kind)
      setFee(est.toFixed(2))
    }
  }, [shares, unitPrice, feeTouched, assetType, code, brokers, selectedBroker])

  // 按股买: 累计投入 = 单价 × 份额 + 手续费 (用户没手填本金时自动)
  useEffect(() => {
    if (!(assetType === 'FUND' || assetType === 'CRYPTO') || costTouched) return
    const s = parseFloat(shares); const u = parseFloat(unitPrice); const f = parseFloat(fee) || 0
    if (s > 0 && u > 0) {
      setCost((s * u + f).toFixed(2))
    }
  }, [shares, unitPrice, fee, costTouched, assetType])

  const pickOkxBot = (bot) => {
    if (!bot) { setOkxAlgoId(''); setOkxBotType(''); return }
    setOkxAlgoId(bot.algo_id)
    setOkxBotType(bot.bot_type)
    setCode(`OKX-${bot.algo_id.slice(-6)}`)
    setName(`OKX ${bot.inst_id} ${bot.kind_label}`)
    setPlatform('OKX')
    const rate = 7.2
    setCost((bot.investment_usdt * rate).toFixed(2))
    setManualValue((bot.current_value_usdt * rate).toFixed(2))
    setHint(`✓ 已绑定 · 投入 ${bot.investment_usdt}U · 当前 ${bot.current_value_usdt}U · ${bot.pnl_pct >= 0 ? '+' : ''}${bot.pnl_pct}%`)
  }

  const lookupCode = async () => {
    if (!code || assetType === 'BOT' || assetType === 'WEALTH' || assetType === 'CASH') return
    setLookingUp(true)
    setHint('')
    try {
      if (assetType === 'FUND') {
        const q = await fetchJSON(`/api/assets/quote/fund/${code}`)
        if (q?.name) { setName(q.name); setHint(`✓ ${q.realtime ? '估值' : '昨净值'} ${q.est_nav || q.nav}`) }
      } else if (assetType === 'CRYPTO') {
        const q = await fetchJSON(`/api/assets/quote/crypto/${code}`)
        if (q?.price) { if (!name) setName(code); setHint(`✓ 现价 $${q.price}`) }
      }
    } catch { setHint('✗ 查询失败,可手填') }
    finally { setLookingUp(false) }
  }

  const submit = async () => {
    // CASH: balance maps to cost_amount + manual_value; optional yield used for monthly est
    const isYieldType = assetType === 'WEALTH' || assetType === 'CASH'
    const costLabel = assetType === 'CASH' ? '当前余额必填'
      : (assetType === 'BOT' || assetType === 'WEALTH') ? '投入本金必填' : '累计投入必填'
    if (!cost) return alert(costLabel)
    if (!code || !name) return alert('代码/名称必填')
    if (assetType === 'BOT' && !manualValue && !okxAlgoId) return alert('当前资产必填（或绑定 OKX 自动同步）')

    // 内联 DCA 校验 (仅 FUND/CRYPTO)
    let dcaPayload = null
    if (dcaEnabled && (assetType === 'FUND' || assetType === 'CRYPTO')) {
      const v = parseFloat(dcaValue)
      if (!(v > 0)) return alert('定投金额必填且 > 0')
      dcaPayload = {
        mode: 'amount',
        value: v,
        frequency: dcaFrequency,
        day_of_month: dcaFrequency === 'monthly' ? parseInt(dcaDayOfMonth, 10) : null,
        day_of_week: dcaFrequency === 'weekly' ? parseInt(dcaDayOfWeek, 10) : null,
        note: '',
      }
    }

    await fetchJSON('/api/assets', {
      method: 'POST',
      body: JSON.stringify({
        asset_type: assetType, code: code.trim(), name: name.trim(), platform: platform.trim(),
        cost_amount: parseFloat(cost),
        shares: shares ? parseFloat(shares) : null,
        manual_value: manualValue !== '' ? parseFloat(manualValue) : null,
        note: note.trim(),
        okx_algo_id: okxAlgoId || null,
        okx_bot_type: okxBotType || null,
        annual_yield_rate: isYieldType && annualYield !== ''
          ? parseFloat(annualYield) / 100  // user inputs %, store as decimal
          : null,
        start_date: isYieldType ? (startDate || null) : null,
        trade_time: ((assetType === 'FUND' && isOnchainEtf(code)) || assetType === 'CRYPTO') && tradeTime ? tradeTime : null,
        purchase_fee_rate: assetType === 'FUND' && !isOnchainEtf(code) && feeRatePct !== '' ? parseFloat(feeRatePct) / 100 : null,
        broker: assetType === 'FUND' && isOnchainEtf(code) && selectedBroker ? selectedBroker : null,
        // 手续费单独透传 (cost 已含它), 后端单存到初始流水的 fee 字段, 避免在流水里"消失"
        fee: (assetType === 'FUND' || assetType === 'CRYPTO') && parseFloat(fee) > 0 ? parseFloat(fee) : null,
        dca: dcaPayload,
      }),
    })
    onDone?.()
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1 text-[12px] text-text outline-none focus:border-accent'

  return (
    <div className="px-6 py-3 bg-surface-2/50 border-b border-border space-y-2.5 text-[12px]">
      {/* OKX bot picker — only for BOT */}
      {assetType === 'BOT' && okxBots !== null && (
        <div className="rounded border border-border-subtle bg-surface-3/40 px-2.5 py-2">
          {okxBots.length === 0 ? (
            <div className="text-[10.5px] text-text-muted">
              OKX 无可用机器人。手动录入即可，或去 设置 → OKX 配置凭证。
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <label className="text-[10.5px] text-text-muted shrink-0">从 OKX 绑定</label>
              <select value={okxAlgoId}
                onChange={e => pickOkxBot(okxBots.find(b => b.algo_id === e.target.value))}
                className={`${inp} flex-1`}>
                <option value="">-- 不绑定，手动录入 --</option>
                {okxBots.map(b => (
                  <option key={b.algo_id} value={b.algo_id}>
                    {b.active ? '●' : '○'} {b.kind_label} · {b.inst_id} · 投入 {b.investment_usdt}U · {b.pnl_pct >= 0 ? '+' : ''}{b.pnl_pct}%
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[11px] text-text-dim">
            {assetType === 'FUND' ? '基金代码' : assetType === 'CRYPTO' ? '币对' :
             assetType === 'WEALTH' ? '产品代码' :
             assetType === 'CASH' ? '账户标识' : '标识'}
          </label>
          <div className="flex gap-1.5">
            <input value={code} onChange={e => setCode(e.target.value)}
              onBlur={lookupCode}
              className={`${inp} w-36 font-mono`}
              placeholder={assetType === 'FUND' ? '161226' : assetType === 'CRYPTO' ? 'BTC-USDT' :
                assetType === 'WEALTH' ? 'YC040204 / 周周宝' :
                assetType === 'CASH' ? 'yuebao / zlt / cczb' : '自定义ID'} />
            {assetType === 'FUND' || assetType === 'CRYPTO' ? (
              <button onClick={lookupCode} disabled={lookingUp}
                className="px-2 py-1 rounded border border-accent/40 text-accent hover:bg-accent/10 text-[11px] cursor-pointer">
                {lookingUp ? '...' : '查询'}
              </button>
            ) : null}
          </div>
        </div>
        <div className="flex flex-col gap-1 flex-1 min-w-[160px]">
          <label className="text-[11px] text-text-dim">名称</label>
          <input value={name} onChange={e => setName(e.target.value)} className={`${inp} w-full`}
            placeholder={
              assetType === 'BOT' ? 'OKX BTC 现货马丁' :
              assetType === 'WEALTH' ? '招商月添利 / 周周宝' :
              assetType === 'CASH' ? '货币基金 / 银行活期' :
              '查询后自动填充'
            } />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[11px] text-text-dim">平台</label>
          <input value={platform} onChange={e => setPlatform(e.target.value)} className={`${inp} w-28`}
            placeholder={assetType === 'BOT' ? 'OKX' : assetType === 'WEALTH' ? '银行' :
              assetType === 'CASH' ? '支付平台 / 银行' : '基金平台 / 交易所'} />
        </div>
      </div>

      {hint && <div className={`text-[10px] font-mono ${hint.startsWith('✓') ? 'text-bull' : 'text-bear'}`}>{hint}</div>}

      {/* Pending hint: 场外基金/CRYPTO 只填了金额没填份额 → T+1 待确认 (场内 ETF 按市价即时成交, 不适用) */}
      {((assetType === 'FUND' && !isOnchainEtf(code)) || assetType === 'CRYPTO') && parseFloat(cost) > 0 && !shares && (
        <div className="text-[10.5px] font-mono text-accent">
          💡 没填{assetType === 'FUND' ? '份额' : '数量'} → 当作 T+1 待确认，¥{parseFloat(cost).toFixed(2)} 先记到 pending。
          {assetType === 'FUND' ? '基金' : '币'} 到账后回来编辑补份额自动结算。
        </div>
      )}

      {/* WEALTH 双向估算预览 (CASH 不需要这种估算 — 只录余额) */}
      {assetType === 'WEALTH' && cost && startDate && (() => {
        const principal = parseFloat(cost) || 0
        const days = Math.max(0, Math.floor((new Date() - new Date(startDate)) / 86400000))
        if (days === 0 || principal === 0) return null
        if (annualYield) {
          const r = parseFloat(annualYield) / 100
          const accrued = principal * (1 + r * days / 365)
          return <div className="text-[10px] font-mono text-bull">
            ✓ 持有 {days}天 · 年化 {annualYield}% → 当前总额 ≈ ¥{accrued.toFixed(2)} (利息 +¥{(accrued - principal).toFixed(2)})
          </div>
        }
        if (manualValue) {
          const mv = parseFloat(manualValue) || 0
          const r = (mv / principal - 1) * 365 / days
          return <div className="text-[10px] font-mono text-accent">
            ✓ 持有 {days}天 · 当前 ¥{mv} → 反推年化 ≈ {(r * 100).toFixed(3)}% (利息 +¥{(mv - principal).toFixed(2)})
          </div>
        }
        return null
      })()}

      <div className="flex flex-wrap gap-2 items-end">
        {/* CASH: 当前余额 + 可选 7日年化 */}
        {assetType === 'CASH' && (
          <>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">当前余额 ¥</label>
              <input type="number" step="0.01" value={cost}
                onChange={e => { setCost(e.target.value); setManualValue(e.target.value) }}
                className={`${inp} w-36 font-mono`} placeholder="3000" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">7日年化 % (可选)</label>
              <input type="number" step="0.001" value={annualYield}
                onChange={e => setAnnualYield(e.target.value)}
                className={`${inp} w-24 font-mono`} placeholder="1.17" />
            </div>
          </>
        )}

        {assetType === 'FUND' || assetType === 'CRYPTO' ? (
          <>
            {/* 字段顺序与加仓/减仓表单一致: 先价格后数量 */}
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">
                {assetType === 'FUND' ? '净值/单价' : '单价 $'}
              </label>
              <input type="number" step="0.0001" value={unitPrice} onChange={e => setUnitPrice(e.target.value)}
                className={`${inp} w-28 font-mono`} placeholder={assetType === 'FUND' ? '3.4915' : '40000'} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">
                {assetType === 'FUND' ? '份额' : '数量'}
              </label>
              <input type="number" step="0.0001" value={shares} onChange={e => setShares(e.target.value)}
                className={`${inp} w-28 font-mono`} placeholder={assetType === 'FUND' ? '1500.23' : '0.012'} />
            </div>
          </>
        ) : null}
        {assetType !== 'CASH' && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              {assetType === 'BOT' || assetType === 'WEALTH' ? '投入本金 ¥' : '累计投入 ¥'}
              {(assetType === 'FUND' || assetType === 'CRYPTO') && !costTouched && parseFloat(shares) > 0 && parseFloat(unitPrice) > 0 && (
                <span className="text-[9.5px] text-accent ml-1">自动算</span>
              )}
            </label>
            <input type="number" step="0.01" value={cost}
              onChange={e => { setCost(e.target.value); setCostTouched(true) }}
              className={`${inp} w-32 font-mono`} placeholder="5000" />
          </div>
        )}
        {(assetType === 'FUND' || assetType === 'CRYPTO') && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              手续费 ¥
              <Tooltip content={
                <div className="leading-relaxed">
                  <div className="text-text-bright font-semibold mb-0.5">手续费 (单笔)</div>
                  <div className="text-text-dim text-[10.5px]">
                    场内 ETF: 默认按 config.commission_rate (万 {(BROKER_COMMISSION_RATE * 10000).toFixed(2)}) + 最低 ¥{BROKER_COMMISSION_MIN}<br/>
                    场外公募 (天天基金 / 支付宝): 通常 0 (C 类) 或申购费<br/>
                    加密货币: 按交易所费率
                  </div>
                </div>
              }>
                <span className="ml-0.5 cursor-help text-text-muted">ⓘ</span>
              </Tooltip>
            </label>
            <input type="number" step="0.01" value={fee}
              onChange={e => { setFee(e.target.value); setFeeTouched(true) }}
              className={`${inp} w-24 font-mono`} placeholder="5.00" />
          </div>
        )}
        {((assetType === 'FUND' && isOnchainEtf(code)) || assetType === 'CRYPTO') && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">成交时刻 <span className="text-text-muted text-[10px]">可空</span></label>
            <input type="time" value={tradeTime} onChange={e => setTradeTime(e.target.value)}
              className={`${inp} w-28 font-mono text-text-dim`} title="今日建仓的成交时刻(可选), 留空用录入时间, 供分时图打点" />
          </div>
        )}
        {assetType === 'FUND' && !isOnchainEtf(code) && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">申购费率 %</label>
            <input type="number" step="0.01" value={feeRatePct} onChange={e => setFeeRatePct(e.target.value)}
              className={`${inp} w-24 font-mono`} placeholder="C类填0"
              title="定投批量确认按此费率内扣算份额。A类填折后实际费率(如0.15), C类/无申购费填0或留空" />
          </div>
        )}
        {assetType === 'FUND' && isOnchainEtf(code) && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">券商</label>
            <select className={`${inp} w-28 font-mono`} value={selectedBroker} onChange={e => setSelectedBroker(e.target.value)}>
              <option value="">默认</option>
              {brokers.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
            </select>
          </div>
        )}
        {assetType !== 'WEALTH' && assetType !== 'CASH' && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              {assetType === 'BOT'
                ? (okxAlgoId ? '当前资产 ¥ (OKX 自动同步)' : '当前资产 ¥')
                : '手动市值 ¥ (可空)'}
            </label>
            <input type="number" step="0.01" value={manualValue} onChange={e => setManualValue(e.target.value)}
              className={`${inp} w-32 font-mono`}
              placeholder={
                assetType === 'BOT' ? (okxAlgoId ? '可留空' : '必填') :
                '留空=实时算'
              } />
          </div>
        )}
        {assetType === 'WEALTH' && (
          <>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">起投日</label>
              <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
                className={`${inp} w-36 font-mono`} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">年化 % (二选一)</label>
              <input type="number" step="0.001" value={annualYield} onChange={e => setAnnualYield(e.target.value)}
                className={`${inp} w-24 font-mono`} placeholder="2.15" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-text-dim">当前总额 ¥ (二选一)</label>
              <input type="number" step="0.01" value={manualValue} onChange={e => setManualValue(e.target.value)}
                className={`${inp} w-32 font-mono`} placeholder="本金+利息" />
            </div>
          </>
        )}
        <div className="flex flex-col gap-1 flex-1 min-w-[160px]">
          <label className="text-[11px] text-text-dim">备注</label>
          <input value={note} onChange={e => setNote(e.target.value)} className={`${inp} w-full`} />
        </div>
        <button onClick={submit}
          className="px-4 py-1.5 rounded bg-accent text-bg font-semibold text-[12px] hover:opacity-90 cursor-pointer">
          保存{dcaEnabled && (assetType === 'FUND' || assetType === 'CRYPTO') ? ' + 建定投' : ''}
        </button>
        <button onClick={onCancel}
          className="px-3 py-1.5 rounded border border-border text-text-dim text-[12px] hover:text-text cursor-pointer">
          取消
        </button>
      </div>

      {/* 内联定投 — FUND/CRYPTO 可勾选, 资产创建后顺手建一条 DCA */}
      {(assetType === 'FUND' || assetType === 'CRYPTO') && (
        <div className="rounded border border-border-subtle bg-surface-3/30 px-2.5 py-2 space-y-2">
          <label className="flex items-center gap-2 text-[11.5px] text-text cursor-pointer">
            <input type="checkbox" checked={dcaEnabled}
              onChange={e => setDcaEnabled(e.target.checked)} />
            <span>同时建定投计划 ({assetType === 'FUND' ? '基金' : '币'} DCA)</span>
          </label>
          {dcaEnabled && (
            <div className="flex flex-wrap gap-2 items-end pl-5">
              <div className="flex flex-col gap-1">
                <label className="text-[10.5px] text-text-dim">每期金额 ¥</label>
                <input type="number" step="0.01" value={dcaValue}
                  onChange={e => setDcaValue(e.target.value)}
                  className={`${inp} w-28 font-mono`} placeholder="500" />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10.5px] text-text-dim">频率</label>
                <select value={dcaFrequency} onChange={e => setDcaFrequency(e.target.value)}
                  className={`${inp} w-32`}>
                  <option value="monthly">每月</option>
                  <option value="weekly">每周</option>
                  <option value="daily_trading">每个交易日</option>
                </select>
              </div>
              {dcaFrequency === 'monthly' && (
                <div className="flex flex-col gap-1">
                  <label className="text-[10.5px] text-text-dim">每月几号</label>
                  <input type="number" min="1" max="31" value={dcaDayOfMonth}
                    onChange={e => setDcaDayOfMonth(e.target.value)}
                    className={`${inp} w-20 font-mono`} />
                </div>
              )}
              {dcaFrequency === 'weekly' && (
                <div className="flex flex-col gap-1">
                  <label className="text-[10.5px] text-text-dim">每周几</label>
                  <select value={dcaDayOfWeek} onChange={e => setDcaDayOfWeek(e.target.value)}
                    className={`${inp} w-24`}>
                    <option value={1}>周一</option>
                    <option value={2}>周二</option>
                    <option value={3}>周三</option>
                    <option value={4}>周四</option>
                    <option value={5}>周五</option>
                    <option value={6}>周六</option>
                    <option value={7}>周日</option>
                  </select>
                </div>
              )}
              <div className="text-[10.5px] text-text-muted">触发后写一条 pending ADD，等你回来补份额结算。</div>
            </div>
          )}
        </div>
      )}

      {/* 按股买预览 (FUND/CRYPTO 同时填了份额 + 单价) */}
      {(assetType === 'FUND' || assetType === 'CRYPTO') && parseFloat(shares) > 0 && parseFloat(unitPrice) > 0 && (() => {
        const s = parseFloat(shares); const u = parseFloat(unitPrice); const f = parseFloat(fee) || 0
        const gross = s * u
        const total = gross + f
        const avg = total / s
        return (
          <div className="text-[10.5px] font-mono text-bull">
            ✓ 按股买: {s.toFixed(4)} × ¥{u.toFixed(4)} = ¥{gross.toFixed(2)}
            {f > 0 && ` + 手续费 ¥${f.toFixed(2)} = ¥${total.toFixed(2)}`}
            <span className="ml-2 text-text-dim">持有成本 ¥{avg.toFixed(4)}/{assetType === 'FUND' ? '份' : '币'}</span>
          </div>
        )
      })()}
    </div>
  )
}
