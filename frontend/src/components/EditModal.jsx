import { useState } from 'react'
import { api } from '../hooks/useApi'

export default function EditModal({ holding, onClose, onChange }) {
  const [code, setCode] = useState(holding.stock_code)
  const [shares, setShares] = useState(holding.shares)
  const [cost, setCost] = useState(holding.cost_price)
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!code || code.length !== 6) return alert('请输入6位股票代码')
    setSaving(true)
    try {
      if (code !== holding.stock_code) {
        await api.deleteHolding(holding.stock_code)
        await api.addHolding({ stock_code: code, stock_name: '', shares: parseInt(shares), cost_price: parseFloat(cost) })
      } else {
        await api.updateHolding(code, { shares: parseInt(shares), cost_price: parseFloat(cost) })
      }
      onChange()
      onClose()
    } catch {
      alert('操作失败')
    }
    setSaving(false)
  }

  const handleDelete = async () => {
    if (!confirm(`确定删除 ${holding.stock_name || holding.stock_code} 的持仓？`)) return
    await api.deleteHolding(holding.stock_code)
    onChange()
    onClose()
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[360px] max-w-[90vw] space-y-4"
        onClick={e => e.stopPropagation()}
        style={{ animation: 'fade-up 0.2s ease-out' }}>
        <h3 className="text-[15px] font-medium text-accent">编辑持仓</h3>

        <div className="space-y-3">
          <div>
            <label className="text-[12px] text-text-dim block mb-1">股票代码</label>
            <input className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] text-text font-mono outline-none focus:border-accent"
              maxLength={6} value={code} onChange={e => setCode(e.target.value)} />
          </div>
          <div>
            <label className="text-[12px] text-text-dim block mb-1">持仓数量</label>
            <input type="number" className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] text-text font-mono outline-none focus:border-accent"
              min={100} step={100} value={shares} onChange={e => setShares(e.target.value)} />
          </div>
          <div>
            <label className="text-[12px] text-text-dim block mb-1">成本价</label>
            <input type="number" className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] text-text font-mono outline-none focus:border-accent"
              step={0.0001} value={cost} onChange={e => setCost(e.target.value)} />
          </div>
        </div>

        <div className="flex gap-2 pt-1">
          <button onClick={handleSave} disabled={saving}
            className="flex-1 py-2 rounded-lg bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {saving ? '保存中...' : '保存'}
          </button>
          <button onClick={onClose}
            className="flex-1 py-2 rounded-lg border border-border text-text-dim text-[13px] hover:text-text transition-colors cursor-pointer">
            取消
          </button>
          <button onClick={handleDelete}
            className="py-2 px-4 rounded-lg bg-bear/15 text-bear text-[13px] hover:bg-bear/25 transition-colors cursor-pointer">
            删除
          </button>
        </div>
      </div>
    </div>
  )
}
