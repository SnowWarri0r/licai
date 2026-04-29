import { useState, useCallback } from 'react'
import { api, fetchJSON } from '../hooks/useApi'
import { fmtPrice, fmtCost, fmtPct, fmtMoney, priceColor } from '../helpers'

export default function Portfolio({ holdings, onEdit, onAdd, onHistory }) {
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ code: '', name: '', shares: '', cost: '' })
  const [submitting, setSubmitting] = useState(false)
  const [nameLooking, setNameLooking] = useState(false)

  const lookupName = useCallback(async (code) => {
    if (code.length !== 6) return
    setNameLooking(true)
    try {
      const q = await fetchJSON(`/api/market/quote/${code}`)
      if (q && q.stock_name) setForm(f => ({ ...f, name: q.stock_name }))
    } catch {}
    setNameLooking(false)
  }, [])

  const totals = holdings.reduce((acc, h) => ({
    pnl: acc.pnl + (h.unrealized_pnl || 0),
    value: acc.value + (h.market_value || 0),
    cost: acc.cost + h.cost_price * h.shares,
  }), { pnl: 0, value: 0, cost: 0 })

  const handleAdd = async () => {
    if (!form.code || form.code.length !== 6) return alert('请输入6位股票代码')
    if (!form.shares || parseInt(form.shares) < 100) return alert('持仓数量至少100股')
    if (!form.cost || parseFloat(form.cost) <= 0) return alert('请输入成本价')
    setSubmitting(true)
    try {
      const res = await api.addHolding({
        stock_code: form.code, stock_name: form.name,
        shares: parseInt(form.shares), cost_price: parseFloat(form.cost),
      })
      if (res.message) {
        setForm({ code: '', name: '', shares: '', cost: '' })
        setShowForm(false)
        onAdd()
      } else {
        alert(res.detail || '添加失败')
      }
    } finally { setSubmitting(false) }
  }

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h2 className="text-[13px] font-medium text-accent tracking-wide">A 股持仓</h2>
        <button onClick={() => setShowForm(!showForm)}
          className="text-[12px] px-3 py-1 rounded-md bg-accent/10 text-accent hover:bg-accent/20 transition-colors cursor-pointer">
          + 添加
        </button>
      </div>

      {showForm && (
        <div className="px-4 py-3 bg-surface-2/50 border-b border-border flex flex-wrap gap-2 items-end">
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">代码</label>
            <input className="w-24 bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none focus:border-accent font-mono"
              placeholder="601212" maxLength={6} value={form.code}
              onChange={e => {
                const v = e.target.value
                setForm({ ...form, code: v })
                if (v.length === 6) lookupName(v)
              }} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">名称</label>
            <input className="w-24 bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none focus:border-accent"
              placeholder={nameLooking ? '查询中...' : '可留空'} value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">数量</label>
            <input type="number" className="w-24 bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none focus:border-accent font-mono"
              placeholder="300" min={100} step={100} value={form.shares}
              onChange={e => setForm({ ...form, shares: e.target.value })} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">成本价</label>
            <input type="number" className="w-28 bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none focus:border-accent font-mono"
              placeholder="12.7401" step={0.0001} value={form.cost}
              onChange={e => setForm({ ...form, cost: e.target.value })} />
          </div>
          <button onClick={handleAdd} disabled={submitting}
            className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 transition-opacity disabled:opacity-50 cursor-pointer">
            {submitting ? '...' : '确认'}
          </button>
          <button onClick={() => setShowForm(false)}
            className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text transition-colors cursor-pointer">
            取消
          </button>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-text-dim text-[11px] tracking-wider">
              {['代码', '名称', '持仓', '成本', '现价', '涨跌%', '盈亏', '盈亏%', '市值', ''].map((h, i) => (
                <th key={i} className={`py-2.5 px-3 font-normal whitespace-nowrap bg-surface-2/40
                  ${i < 2 ? 'text-left' : i === 9 ? 'text-center w-16' : 'text-right'}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {holdings.length === 0 ? (
              <tr><td colSpan={10} className="text-center py-8 text-text-dim">暂无持仓，点击"添加"开始</td></tr>
            ) : holdings.map(h => (
              <tr key={h.stock_code} className="border-t border-border-subtle hover:bg-surface-2/30 transition-colors">
                <td className="py-2 px-3 text-left font-mono text-text-bright">{h.stock_code}</td>
                <td className="py-2 px-3 text-left">{h.stock_name}</td>
                <td className="py-2 px-3 text-right font-mono">{h.shares}</td>
                <td className="py-2 px-3 text-right font-mono">{fmtCost(h.cost_price)}</td>
                <td className={`py-2 px-3 text-right font-mono font-medium ${priceColor(h.price_change_pct)}`}>
                  {h.current_price ? fmtPrice(h.current_price) : '--'}
                </td>
                <td className={`py-2 px-3 text-right font-mono ${priceColor(h.price_change_pct)}`}>
                  {h.price_change_pct != null ? fmtPct(h.price_change_pct) : '--'}
                </td>
                <td className={`py-2 px-3 text-right font-mono ${priceColor(h.pnl_pct)}`}>
                  {h.unrealized_pnl != null ? fmtMoney(h.unrealized_pnl) : '--'}
                </td>
                <td className={`py-2 px-3 text-right font-mono ${priceColor(h.pnl_pct)}`}>
                  {h.pnl_pct != null ? fmtPct(h.pnl_pct) : '--'}
                </td>
                <td className="py-2 px-3 text-right font-mono">
                  {h.market_value ? fmtMoney(h.market_value) : '--'}
                </td>
                <td className="py-2 px-3 text-center whitespace-nowrap">
                  <button onClick={() => onHistory?.(h)}
                    className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-accent hover:border-accent/30 transition-colors cursor-pointer mr-1">
                    历史
                  </button>
                  <button onClick={() => onEdit(h)}
                    className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-accent hover:border-accent/30 transition-colors cursor-pointer">
                    编辑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
          {holdings.length > 0 && (
            <tfoot>
              <tr className="border-t-2 border-border text-[12px] font-medium">
                <td colSpan={6} className="py-2 px-3 text-right text-text-dim">合计</td>
                <td className={`py-2 px-3 text-right font-mono ${priceColor(totals.pnl)}`}>
                  {fmtMoney(totals.pnl)}
                </td>
                <td className={`py-2 px-3 text-right font-mono ${priceColor(totals.pnl)}`}>
                  {totals.cost > 0 ? fmtPct(totals.pnl / totals.cost * 100) : '--'}
                </td>
                <td className="py-2 px-3 text-right font-mono">{fmtMoney(totals.value)}</td>
                <td />
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </section>
  )
}
