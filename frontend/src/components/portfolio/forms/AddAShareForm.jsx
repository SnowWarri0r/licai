import { useCallback, useState } from 'react'
import { api, fetchJSON } from '../../../hooks/useApi'
import { STOCK_MARKETS } from '../constants'
import { stockCodeForMarket } from '../helpers'

export function AddAShareForm({ initialMarket = 'A', onDone, onCancel, brokers = [] }) {
  const [form, setForm] = useState({
    code: '', name: '', shares: '', cost: '',
    tradeDate: new Date().toISOString().slice(0, 10),
    tradeTime: '',
  })
  const [market, setMarket] = useState(initialMarket)
  const [submitting, setSubmitting] = useState(false)
  const [nameLooking, setNameLooking] = useState(false)
  const [broker, setBroker] = useState('')

  const lookup = useCallback(async (nextCode, nextMarket = market) => {
    const fullCode = stockCodeForMarket(nextMarket, nextCode)
    if (!nextCode) return
    setNameLooking(true)
    try {
      const q = await fetchJSON(`/api/market/quote/${encodeURIComponent(fullCode)}`)
      if (q?.stock_name) setForm(f => ({ ...f, name: q.stock_name }))
    } catch {}
    setNameLooking(false)
  }, [market])

  const submit = async () => {
    const meta = STOCK_MARKETS[market]
    const stockCode = stockCodeForMarket(market, form.code)
    if (!form.code) return alert('请输入股票代码')
    if (market === 'A' && !/^\d{6}$/.test(stockCode)) return alert('请输入6位A股代码')
    if (market === 'HK' && !/^HK\.\d{5}$/.test(stockCode)) return alert('请输入港股5位代码')
    if (market === 'US' && !/^US\.[A-Z.]+$/.test(stockCode)) return alert('请输入美股Ticker')
    if (!form.shares || parseInt(form.shares) < meta.minShares) return alert(`持仓数量至少${meta.minShares}`)
    if (!form.cost || parseFloat(form.cost) <= 0) return alert('请输入成本价')
    setSubmitting(true)
    try {
      const res = await api.addHolding({
        stock_code: stockCode, stock_name: form.name,
        shares: parseInt(form.shares), cost_price: parseFloat(form.cost),
        trade_date: form.tradeDate || undefined,
        trade_time: (market === 'A' && form.tradeTime) ? form.tradeTime : undefined,
        broker: broker || null,
      })
      if (res.message) onDone?.()
      else alert(res.detail || '添加失败')
    } finally { setSubmitting(false) }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none focus:border-accent'

  return (
    <div className="px-6 py-3 bg-surface-2/50 border-b border-border flex flex-wrap gap-2 items-end">
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">市场</label>
        <select className={`${inp} w-24`} value={market}
          onChange={e => {
            const nextMarket = e.target.value
            setMarket(nextMarket)
            setForm({ code: '', name: '', shares: '', cost: '' })
          }}>
          {Object.entries(STOCK_MARKETS).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">代码</label>
        <input className={`${inp} w-28 font-mono`} placeholder={STOCK_MARKETS[market].placeholder}
          value={form.code}
          onChange={e => {
            const v = e.target.value.toUpperCase()
            setForm({ ...form, code: v })
            if ((market === 'A' && v.length === 6) || (market === 'HK' && v.length >= 4) || (market === 'US' && v.length >= 1)) {
              lookup(v, market)
            }
          }} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">名称</label>
        <input className={`${inp} w-28`} placeholder={nameLooking ? '查询中...' : '可留空'}
          value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
      </div>
      {/* 字段顺序与加仓/减仓表单一致: 先价格后数量 */}
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">成本价</label>
        <input type="number" className={`${inp} w-28 font-mono`}
          placeholder="12.7401" step={0.0001} value={form.cost}
          onChange={e => setForm({ ...form, cost: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">数量</label>
        <input type="number" className={`${inp} w-24 font-mono`}
          placeholder={market === 'US' ? '10' : '300'} min={STOCK_MARKETS[market].minShares} step={STOCK_MARKETS[market].step} value={form.shares}
          onChange={e => setForm({ ...form, shares: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">买入日期</label>
        <input type="date" className={`${inp} w-36 font-mono`}
          value={form.tradeDate}
          onChange={e => setForm({ ...form, tradeDate: e.target.value })} />
      </div>
      {market === 'A' && (
        <div className="flex flex-col gap-1">
          <label className="text-[11px] text-text-dim">时刻 <span className="text-text-muted text-[10px]">可空</span></label>
          <input type="time" className={`${inp} w-28 font-mono text-text-dim`}
            value={form.tradeTime}
            onChange={e => setForm({ ...form, tradeTime: e.target.value })}
            title="成交时刻(可选), 留空用录入时间, 供分时图打点" />
        </div>
      )}
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">券商</label>
        <select className={`${inp} w-28`} value={broker} onChange={e => setBroker(e.target.value)}>
          <option value="">默认</option>
          {brokers.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
        </select>
      </div>
      <button onClick={submit} disabled={submitting}
        className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 transition-opacity disabled:opacity-50 cursor-pointer">
        {submitting ? '...' : '确认'}
      </button>
      <button onClick={onCancel}
        className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text transition-colors cursor-pointer">
        取消
      </button>
      <div className="text-[10px] text-text-muted pb-1">
        {STOCK_MARKETS[market].hint} · 成本价按{market === 'US' ? '美元' : market === 'HK' ? '港币' : '人民币'}录入，总资产自动折人民币
      </div>
    </div>
  )
}
