import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtMoney } from '../helpers'

export default function BudgetAllocator({ onAllocated }) {
  const [total, setTotal] = useState('')
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    // Load previous total from config
    fetch('/api/settings/feishu').catch(() => {})
    fetch('/api/unwind/total-budget')
      .then(r => r.json())
      .then(d => {
        if (d.total_budget) setTotal(String(d.total_budget))
      })
      .catch(() => {})
  }, [])

  const previewAllocation = async () => {
    const budget = parseFloat(total)
    if (!budget || budget <= 0) return alert('请输入有效的预算金额')
    setLoading(true)
    try {
      const res = await fetchJSON(`/api/unwind/allocate?total_budget=${budget}`, {
        method: 'POST',
      })
      setPreview(res)
    } catch (e) {
      alert('计算失败')
    }
    setLoading(false)
  }

  const applyAllocation = async () => {
    if (!preview) return
    setSaving(true)
    try {
      await fetchJSON('/api/unwind/apply-allocation', {
        method: 'POST',
        body: JSON.stringify({
          total_budget: parseFloat(total),
          allocations: preview.map(s => ({ stock_code: s.stock_code, budget: s.budget })),
        }),
      })
      onAllocated?.()
      setPreview(null)
    } catch (e) {
      alert('保存失败')
    }
    setSaving(false)
  }

  return (
    <div className="rounded-xl border border-accent/30 bg-accent-dim/15 p-4 space-y-3"
      style={{ animation: 'fade-up 0.3s ease-out' }}>
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[13px] font-medium text-accent">加仓子弹池</div>
          <div className="text-[11px] text-text-dim mt-0.5">
            设定总预算 → 系统按每只股的亏损深度/波动率/基本面自动分配
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1">
          <span className="text-[12px] text-text-dim">¥</span>
          <input type="number" step="1000" min="1000" value={total}
            onChange={e => setTotal(e.target.value)}
            placeholder="20000"
            className="w-32 bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text font-mono outline-none focus:border-accent"
          />
        </div>
        <button onClick={previewAllocation} disabled={loading}
          className="text-[12px] px-3 py-1.5 rounded bg-accent/10 text-accent hover:bg-accent/20 cursor-pointer disabled:opacity-50">
          {loading ? '计算中...' : '预览分配'}
        </button>
        {preview && (
          <button onClick={applyAllocation} disabled={saving}
            className="text-[12px] px-3 py-1.5 rounded bg-accent text-bg font-medium cursor-pointer disabled:opacity-50">
            {saving ? '保存中...' : '确认分配'}
          </button>
        )}
      </div>

      {preview && (
        <div className="rounded-lg bg-surface-2/60 border border-border p-3 space-y-1.5">
          <div className="text-[11px] text-text-dim mb-1">系统分配预览：</div>
          {preview.map(s => (
            <div key={s.stock_code} className="flex items-center justify-between text-[12px]">
              <span className="text-text">{s.stock_name}<span className="text-text-muted font-mono ml-1">({s.stock_code})</span></span>
              <div className="flex items-center gap-3">
                <span className="text-text-dim text-[11px]">权重 {s.priority?.toFixed(4)}</span>
                <span className="font-mono font-medium text-accent w-20 text-right">¥{fmtMoney(s.budget)}</span>
              </div>
            </div>
          ))}
          <div className="text-[11px] text-text-muted pt-1 border-t border-border-subtle mt-2">
            保存后各卡片自动填入预算，点"生成解套计划"即可生成档位
          </div>
        </div>
      )}
    </div>
  )
}
