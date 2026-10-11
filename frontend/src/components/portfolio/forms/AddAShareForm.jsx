import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fetchJSON } from '../../../hooks/useApi'
import { etToBjTime, usTodayDate } from '../../../helpers'
import { STOCK_MARKETS } from '../constants'
import { stockCodeForMarket } from '../helpers'

// 美股日期按美东交易日(券商成交记录上的那天), 其余市场按本地日期(A 股原逻辑不变)
const defaultDate = (market) => market === 'US' ? usTodayDate() : new Date().toISOString().slice(0, 10)
const US_DATE_HINT = '盈立成交记录上的日期; 北京时间凌晨成交算前一天'

export function AddAShareForm({ initialMarket = 'A', onDone, onCancel, brokers = [] }) {
  const [form, setForm] = useState({
    code: '', name: '', shares: '', cost: '',
    tradeDate: defaultDate(initialMarket),
    tradeTime: '',
  })
  const [market, setMarket] = useState(initialMarket)
  const [submitting, setSubmitting] = useState(false)
  const [nameLooking, setNameLooking] = useState(false)
  const [broker, setBroker] = useState('')

  // 名称查询: 防抖 + 只认最后一次请求的结果(美股每敲一个字母都会触发, 旧请求晚回来会覆盖新名字)
  const lookupSeq = useRef(0)
  const lookupTimer = useRef(null)
  useEffect(() => () => clearTimeout(lookupTimer.current), [])
  const lookup = useCallback((nextCode, nextMarket = market) => {
    clearTimeout(lookupTimer.current)
    const seq = ++lookupSeq.current
    if (!nextCode) { setNameLooking(false); return }
    const fullCode = stockCodeForMarket(nextMarket, nextCode)
    lookupTimer.current = setTimeout(async () => {
      setNameLooking(true)
      try {
        const q = await fetchJSON(`/api/market/quote/${encodeURIComponent(fullCode)}`)
        if (seq === lookupSeq.current && q?.stock_name) setForm(f => ({ ...f, name: q.stock_name }))
      } catch { /* 查不到就留空, 用户可手填 */ }
      if (seq === lookupSeq.current) setNameLooking(false)
    }, 350)
  }, [market])

  const submit = async () => {
    const meta = STOCK_MARKETS[market]
    const stockCode = stockCodeForMarket(market, form.code)
    if (!form.code) return alert('请输入股票代码')
    if (market === 'A' && !/^\d{6}$/.test(stockCode)) return alert('请输入6位A股代码')
    if (market === 'HK' && !/^HK\.\d{5}$/.test(stockCode)) return alert('请输入港股5位代码')
    if (market === 'US' && !/^US\.[A-Z.]+$/.test(stockCode)) return alert('请输入美股Ticker')
    if (!form.shares || parseFloat(form.shares) < meta.minShares) return alert(`持仓数量至少${meta.minShares}`)
    if (!form.cost || parseFloat(form.cost) <= 0) return alert('请输入成本价')
    setSubmitting(true)
    try {
      const res = await api.addHolding({
        stock_code: stockCode, stock_name: form.name,
        shares: parseFloat(form.shares), cost_price: parseFloat(form.cost),
        trade_date: form.tradeDate || undefined,
        // 成交时刻后端统一存北京时间: 美股按美东录入, 这里换算(港股与北京同时区)
        trade_time: form.tradeTime
          ? (market === 'US' ? etToBjTime(form.tradeDate, form.tradeTime) : form.tradeTime)
          : undefined,
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
            lookup('')   // 作废进行中的名称查询
            setForm({ code: '', name: '', shares: '', cost: '', tradeDate: defaultDate(nextMarket), tradeTime: '' })
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
            } else {
              lookup('')
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
        <label className="text-[11px] text-text-dim" title={market === 'US' ? US_DATE_HINT : undefined}>
          {market === 'US' ? '美东交易日' : '买入日期'}
        </label>
        <input type="date" className={`${inp} w-36 font-mono`}
          value={form.tradeDate || ''}
          title={market === 'US' ? US_DATE_HINT : undefined}
          onChange={e => setForm({ ...form, tradeDate: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">{market === 'US' ? '美东时刻' : '时刻'} <span className="text-text-muted text-[10px]">可空</span></label>
        <input type="time" className={`${inp} w-28 font-mono text-text-dim`}
          value={form.tradeTime || ''}
          onChange={e => setForm({ ...form, tradeTime: e.target.value })}
          title={market === 'US'
            ? '成交时刻(可选, 美东时间, 如 09:35), 留空用录入时间, 供分时图打点'
            : '成交时刻(可选), 留空用录入时间, 供分时图打点'} />
      </div>
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
        {market === 'US' && <> · 日期/时刻按美东填({US_DATE_HINT})</>}
      </div>
    </div>
  )
}
