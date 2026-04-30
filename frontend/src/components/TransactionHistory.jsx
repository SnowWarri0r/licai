import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtPrice } from '../helpers'

const enc = (value) => encodeURIComponent(value)

const ACTION_TYPES = [
  { value: 'BUY', label: '买入' },
  { value: 'SELL', label: '卖出' },
  { value: 'ADD', label: '补仓(加仓)' },
  { value: 'REDUCE', label: '减仓' },
  { value: 'T_BUY', label: 'T买' },
  { value: 'T_SELL', label: 'T卖' },
]

const ACQUIRE_TYPES = new Set(['BUY', 'ADD', 'T_BUY'])

function ActionRow({ action, editing, onSave, onCancel, onEdit, onDelete }) {
  const [draft, setDraft] = useState(action)
  useEffect(() => { setDraft(action) }, [action])

  if (!editing) {
    const isAcquire = ACQUIRE_TYPES.has(action.action_type)
    const typeLabel = ACTION_TYPES.find(t => t.value === action.action_type)?.label || action.action_type
    return (
      <tr className="border-t border-border-subtle hover:bg-surface-2/30">
        <td className="py-1.5 px-2 text-text-muted">{action.trade_date || '--'}</td>
        <td className={`py-1.5 px-2 text-[11px] ${isAcquire ? 'text-bull' : 'text-bear'}`}>{typeLabel}</td>
        <td className="py-1.5 px-2 text-right font-mono">{fmtPrice(action.price)}</td>
        <td className="py-1.5 px-2 text-right font-mono">{action.shares}</td>
        <td className="py-1.5 px-2 text-[11px] text-text-muted">{action.note || '--'}</td>
        <td className="py-1.5 px-2 text-center">
          <button onClick={() => onEdit()} className="text-[11px] text-accent hover:underline cursor-pointer mr-2">编辑</button>
          <button onClick={() => onDelete()} className="text-[11px] text-bear hover:underline cursor-pointer">删除</button>
        </td>
      </tr>
    )
  }

  return (
    <tr className="border-t border-border-subtle bg-surface-3/40">
      <td className="py-1.5 px-2"><input type="date" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-32" value={draft.trade_date || ''} onChange={e => setDraft({ ...draft, trade_date: e.target.value })} /></td>
      <td className="py-1.5 px-2">
        <select className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px]" value={draft.action_type} onChange={e => setDraft({ ...draft, action_type: e.target.value })}>
          {ACTION_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </td>
      <td className="py-1.5 px-2"><input type="number" step="0.0001" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-20 text-right font-mono" value={draft.price} onChange={e => setDraft({ ...draft, price: parseFloat(e.target.value) || 0 })} /></td>
      <td className="py-1.5 px-2"><input type="number" step="100" min="100" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-20 text-right font-mono" value={draft.shares} onChange={e => setDraft({ ...draft, shares: parseInt(e.target.value) || 0 })} /></td>
      <td className="py-1.5 px-2"><input type="text" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-full" placeholder="备注" value={draft.note || ''} onChange={e => setDraft({ ...draft, note: e.target.value })} /></td>
      <td className="py-1.5 px-2 text-center">
        <button onClick={() => onSave(draft)} className="text-[11px] text-bull hover:underline cursor-pointer mr-2">保存</button>
        <button onClick={() => onCancel()} className="text-[11px] text-text-dim hover:underline cursor-pointer">取消</button>
      </td>
    </tr>
  )
}

export default function TransactionHistory({ stockCode, stockName, onClose, onChange }) {
  const [actions, setActions] = useState([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState(null)
  const [adding, setAdding] = useState(false)
  const [newAction, setNewAction] = useState({
    action_type: 'BUY',
    price: '',
    shares: '',
    trade_date: new Date().toISOString().slice(0, 10),
    note: '',
  })

  const load = async () => {
    try {
      const list = await fetchJSON(`/api/portfolio/${enc(stockCode)}/actions`)
      // Sort by trade_date ascending (oldest first)
      list.sort((a, b) => (a.trade_date || '').localeCompare(b.trade_date || ''))
      setActions(list)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { load() }, [stockCode])

  const handleAdd = async () => {
    if (!newAction.price || !newAction.shares || !newAction.trade_date) return alert('请填完整')
    await fetchJSON(`/api/portfolio/${enc(stockCode)}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        ...newAction,
        price: parseFloat(newAction.price),
        shares: parseInt(newAction.shares),
      }),
    })
    setAdding(false)
    setNewAction({ action_type: 'BUY', price: '', shares: '', trade_date: new Date().toISOString().slice(0, 10), note: '' })
    await load()
    onChange?.()
  }

  const handleSave = async (draft) => {
    await fetchJSON(`/api/portfolio/actions/${draft.id}`, {
      method: 'PUT',
      body: JSON.stringify({
        action_type: draft.action_type,
        price: parseFloat(draft.price),
        shares: parseInt(draft.shares),
        trade_date: draft.trade_date,
        note: draft.note || '',
      }),
    })
    setEditingId(null)
    await load()
    onChange?.()
  }

  const handleDelete = async (id) => {
    if (!confirm('确定删除这条记录？会重新计算持仓成本。')) return
    await fetchJSON(`/api/portfolio/actions/${id}`, { method: 'DELETE' })
    await load()
    onChange?.()
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}>
      <div className="bg-surface border border-border rounded-xl p-5 w-[720px] max-w-[95vw] max-h-[85vh] overflow-hidden flex flex-col"
        onClick={e => e.stopPropagation()}>

        <div className="flex items-center justify-between mb-3">
          <h3 className="text-[15px] font-semibold text-text-bright">
            交易历史 — {stockName} <span className="text-[12px] font-mono text-text-dim">({stockCode})</span>
          </h3>
          <button onClick={onClose} className="text-text-dim hover:text-text cursor-pointer">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="text-center text-text-dim py-6">加载中...</div>
          ) : (
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-text-dim text-[11px] sticky top-0 bg-surface">
                  <th className="py-2 px-2 text-left font-normal">日期</th>
                  <th className="py-2 px-2 text-left font-normal">类型</th>
                  <th className="py-2 px-2 text-right font-normal">价格</th>
                  <th className="py-2 px-2 text-right font-normal">数量</th>
                  <th className="py-2 px-2 text-left font-normal">备注</th>
                  <th className="py-2 px-2 text-center font-normal w-24">操作</th>
                </tr>
              </thead>
              <tbody>
                {actions.map(a => (
                  <ActionRow
                    key={a.id}
                    action={a}
                    editing={editingId === a.id}
                    onSave={handleSave}
                    onCancel={() => setEditingId(null)}
                    onEdit={() => setEditingId(a.id)}
                    onDelete={() => handleDelete(a.id)}
                  />
                ))}

                {adding && (
                  <tr className="border-t border-border-subtle bg-bull-bg">
                    <td className="py-1.5 px-2"><input type="date" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-32" value={newAction.trade_date} onChange={e => setNewAction({ ...newAction, trade_date: e.target.value })} /></td>
                    <td className="py-1.5 px-2">
                      <select className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px]" value={newAction.action_type} onChange={e => setNewAction({ ...newAction, action_type: e.target.value })}>
                        {ACTION_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                      </select>
                    </td>
                    <td className="py-1.5 px-2"><input type="number" step="0.0001" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-20 text-right font-mono" placeholder="价格" value={newAction.price} onChange={e => setNewAction({ ...newAction, price: e.target.value })} /></td>
                    <td className="py-1.5 px-2"><input type="number" step="100" min="100" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-20 text-right font-mono" placeholder="数量" value={newAction.shares} onChange={e => setNewAction({ ...newAction, shares: e.target.value })} /></td>
                    <td className="py-1.5 px-2"><input type="text" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-full" placeholder="备注" value={newAction.note} onChange={e => setNewAction({ ...newAction, note: e.target.value })} /></td>
                    <td className="py-1.5 px-2 text-center">
                      <button onClick={handleAdd} className="text-[11px] text-bull hover:underline cursor-pointer mr-2">确认</button>
                      <button onClick={() => setAdding(false)} className="text-[11px] text-text-dim hover:underline cursor-pointer">取消</button>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        <div className="flex items-center justify-between pt-3 border-t border-border mt-2">
          <div className="text-[11px] text-text-muted">
            {actions.length} 条记录 · 每次修改会自动按 FIFO 重算持仓均价
          </div>
          {!adding && (
            <button onClick={() => setAdding(true)}
              className="text-[12px] px-3 py-1 rounded bg-accent/10 text-accent hover:bg-accent/20 cursor-pointer">
              + 添加记录
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
