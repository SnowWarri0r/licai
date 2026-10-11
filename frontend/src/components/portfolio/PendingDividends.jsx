import React, { useCallback, useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'

// 港美股分红待确认: 除净日已过、还没记 DIVIDEND 的现金分红。金额/税以券商结单为准,
// 所以只给建议 —— 可改每股金额和预扣税后「记入」, 或「忽略」(没收到 / 选了以股代息)。
const CCY = { HKD: 'HK$', USD: 'US$' }
const STATUS = { upcoming: '待派', needs_input: '待填金额' }

function Row({ it, onDone }) {
  const [ps, setPs] = useState(it.per_share ?? '')
  const [tax, setTax] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const cur = CCY[it.currency] || ''
  const psNum = Number(ps) || 0
  const gross = psNum * it.shares
  const taxNum = tax === '' ? gross * (it.tax_rate || 0) : Number(tax) || 0
  const canRecord = it.status !== 'upcoming' && psNum > 0

  const post = async (kind) => {
    setBusy(true); setErr('')
    try {
      const body = { ex_date: it.ex_date }
      if (kind === 'confirm') {
        if (psNum !== it.per_share) body.per_share = psNum
        if (tax !== '') body.fee = Number(tax)
      }
      await fetchJSON(`/api/portfolio/${encodeURIComponent(it.stock_code)}/dividends/${kind}`, {
        method: 'POST', body: JSON.stringify(body),
      })
      onDone()
    } catch (e) { setErr(e.message || '失败') } finally { setBusy(false) }
  }

  return (
    <div className="flex items-center gap-x-3 gap-y-1 flex-wrap text-[11.5px] py-1">
      <span className="font-mono text-text-bright">{it.stock_code.replace(/^(HK|US)\./, '')}</span>
      <span className="text-text-muted">{it.stock_name}</span>
      <span className="text-text-dim">除净 {it.ex_date.slice(5)}{it.pay_date ? ` · 派 ${it.pay_date.slice(5)}` : ''}</span>
      {STATUS[it.status] && <span className="text-[10px] px-1 rounded border border-border text-text-muted">{STATUS[it.status]}</span>}
      <span className="font-mono text-text">{it.shares} 股 ×</span>
      <input className="w-20 bg-surface-2 border border-border rounded px-1 font-mono text-[11px]" value={ps}
        title={`每股(${it.currency}) · 公告: ${it.declared || ''}`} onChange={e => setPs(e.target.value)} />
      <span className="text-text-dim">税</span>
      <input className="w-16 bg-surface-2 border border-border rounded px-1 font-mono text-[11px]" value={tax}
        placeholder={(gross * (it.tax_rate || 0)).toFixed(2)} title="预扣税(原币), 留空按默认税率估, 以结单为准"
        onChange={e => setTax(e.target.value)} />
      <span className="font-mono text-info">实收 {cur}{(gross - taxNum).toFixed(2)}</span>
      {it.warnings?.length > 0 && <span className="text-[10.5px] text-warn" title={it.warnings.join('\n')}>注意: {it.warnings[0]}{it.warnings.length > 1 ? ` 等 ${it.warnings.length} 条` : ''}</span>}
      <span className="ml-auto flex gap-2">
        <button disabled={busy || !canRecord} onClick={() => post('confirm')}
          title={it.status === 'upcoming' ? '派息日后再记(按到账日汇率)' : '记一笔现金分红流水'}
          className="text-[11px] text-accent hover:underline cursor-pointer disabled:opacity-40 disabled:cursor-default">记入</button>
        <button disabled={busy} onClick={() => post('dismiss')}
          className="text-[11px] text-text-dim hover:underline cursor-pointer">忽略</button>
      </span>
      {err && <span className="w-full text-[10.5px] text-bear">{err}</span>}
    </div>
  )
}

export function PendingDividends({ dataVersion = 0 }) {
  const [items, setItems] = useState([])
  const load = useCallback(() => {
    fetchJSON('/api/portfolio/dividends/pending').then(d => setItems(d?.items || [])).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load, dataVersion])
  if (!items.length) return null
  return (
    <div className="px-3 md:px-6 py-2.5 border-b border-border bg-info/5">
      <div className="text-[11.5px] text-text mb-1">
        港美股分红待确认 {items.length} 笔
        <span className="text-text-dim ml-2">按除净前一日收盘持股 × 每股派息识别; 金额和预扣税以券商结单为准</span>
      </div>
      {items.map(it => <Row key={`${it.stock_code}|${it.ex_date}`} it={it} onDone={load} />)}
    </div>
  )
}
