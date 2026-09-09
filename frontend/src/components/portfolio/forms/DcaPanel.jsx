import React from 'react'
import { fmtMoney } from '../../../helpers'
import { fetchJSON } from '../../../hooks/useApi'

// DcaPanel — 定投计划管理 (FUND/CRYPTO 嵌在 EditAssetRow 底部)
export function DcaPanel({ assetId }) {
  const [schedules, setSchedules] = React.useState([])
  const [loading, setLoading] = React.useState(false)
  const [adding, setAdding] = React.useState(false)
  const [editingId, setEditingId] = React.useState(null)
  const EMPTY_FORM = {
    mode: 'amount', value: '', frequency: 'daily_trading',
    day_of_month: '15', day_of_week: '1', note: '',
  }
  const [form, setForm] = React.useState(EMPTY_FORM)

  const reload = React.useCallback(async () => {
    setLoading(true)
    try {
      const d = await fetchJSON(`/api/dca?asset_id=${assetId}`)
      setSchedules(d.schedules || [])
    } catch {}
    finally { setLoading(false) }
  }, [assetId])

  React.useEffect(() => { reload() }, [reload])

  const closeForm = () => {
    setAdding(false)
    setEditingId(null)
    setForm(EMPTY_FORM)
  }

  const startEdit = (s) => {
    setEditingId(s.id)
    setAdding(false)
    setForm({
      mode: s.mode || 'amount',
      value: String(s.value ?? ''),
      frequency: s.frequency || 'daily_trading',
      day_of_month: String(s.day_of_month ?? '15'),
      day_of_week: String(s.day_of_week ?? '1'),
      note: s.note || '',
    })
  }

  const save = async () => {
    const v = parseFloat(form.value)
    if (!v || v <= 0) return alert('请输入金额或份数')
    const body = {
      mode: form.mode, value: v,
      frequency: form.frequency, note: form.note || '',
    }
    if (form.frequency === 'monthly') {
      const d = parseInt(form.day_of_month)
      if (!d || d < 1 || d > 31) return alert('每月几号: 1-31')
      body.day_of_month = d
    } else if (form.frequency === 'weekly') {
      const d = parseInt(form.day_of_week)
      if (!d || d < 1 || d > 7) return alert('星期几: 1-7 (Mon=1)')
      body.day_of_week = d
    }
    if (editingId) {
      await fetchJSON(`/api/dca/${editingId}`, {
        method: 'PUT', body: JSON.stringify(body),
      })
    } else {
      await fetchJSON('/api/dca', {
        method: 'POST',
        body: JSON.stringify({ asset_id: assetId, ...body }),
      })
    }
    closeForm()
    reload()
  }

  const FREQ_LABEL = {
    daily_trading: '每个交易日',
    weekly: '每周',
    monthly: '每月',
  }
  const WEEK_LABEL = ['', '一', '二', '三', '四', '五', '六', '日']
  const describeSchedule = (s) => {
    const f = s.frequency || 'monthly'
    if (f === 'daily_trading') return '每个交易日'
    if (f === 'weekly') return `每周${WEEK_LABEL[s.day_of_week] || '?'}`
    return `每月 ${s.day_of_month} 号`
  }

  const togglePause = async (s) => {
    await fetchJSON(`/api/dca/${s.id}`, {
      method: 'PUT',
      body: JSON.stringify({ status: s.status === 'active' ? 'paused' : 'active' }),
    })
    reload()
  }

  const remove = async (s) => {
    if (!confirm('确定删除此定投计划？已生成的流水不会受影响。')) return
    await fetchJSON(`/api/dca/${s.id}`, { method: 'DELETE' })
    reload()
  }

  return (
    <div className="basis-full mt-2 pt-2 border-t border-border-subtle">
      <div className="flex items-baseline justify-between mb-1.5">
        <div className="flex items-baseline gap-2">
          <span className="text-[11.5px] text-text-bright font-semibold">定投计划</span>
          <span className="text-[10.5px] text-text-muted">每个交易日 / 每周 / 每月 自动写一条 pending ADD，T+1 净值出来后在流水里确认</span>
        </div>
        {!adding && !editingId && (
          <button onClick={() => { setAdding(true); setEditingId(null) }}
            className="px-2 py-[3px] rounded text-[11px] border border-accent text-accent hover:bg-accent/10 cursor-pointer">
            + 新建
          </button>
        )}
      </div>

      {(adding || editingId) && (
        <div className="bg-surface-3 rounded-md p-2 mb-2 flex flex-wrap gap-2 items-end">
          <div className="flex flex-col gap-0.5">
            <label className="text-[10.5px] text-text-dim">频率</label>
            <select value={form.frequency} onChange={e => setForm(f => ({ ...f, frequency: e.target.value }))}
              className="bg-bg border border-border rounded px-2 py-1 text-[12px] text-text outline-none focus:border-accent">
              <option value="daily_trading">每个交易日 (跳节假日)</option>
              <option value="weekly">每周</option>
              <option value="monthly">每月</option>
            </select>
          </div>
          <div className="flex flex-col gap-0.5">
            <label className="text-[10.5px] text-text-dim">模式</label>
            <select value={form.mode} onChange={e => setForm(f => ({ ...f, mode: e.target.value }))}
              className="bg-bg border border-border rounded px-2 py-1 text-[12px] text-text outline-none focus:border-accent">
              <option value="amount">固定金额 ¥</option>
              <option value="shares">固定份额</option>
            </select>
          </div>
          <div className="flex flex-col gap-0.5">
            <label className="text-[10.5px] text-text-dim">{form.mode === 'amount' ? '金额 ¥' : '份数'}</label>
            <input type="number" inputMode="decimal" value={form.value}
              onChange={e => setForm(f => ({ ...f, value: e.target.value }))}
              className="bg-bg border border-border rounded px-2 py-1 text-[12px] font-mono w-24 outline-none focus:border-accent"
              placeholder={form.mode === 'amount' ? '50' : '13.6'} />
          </div>
          {form.frequency === 'monthly' && (
            <div className="flex flex-col gap-0.5">
              <label className="text-[10.5px] text-text-dim">每月几号</label>
              <input type="number" min={1} max={31} value={form.day_of_month}
                onChange={e => setForm(f => ({ ...f, day_of_month: e.target.value }))}
                className="bg-bg border border-border rounded px-2 py-1 text-[12px] font-mono w-16 outline-none focus:border-accent" />
            </div>
          )}
          {form.frequency === 'weekly' && (
            <div className="flex flex-col gap-0.5">
              <label className="text-[10.5px] text-text-dim">星期</label>
              <select value={form.day_of_week} onChange={e => setForm(f => ({ ...f, day_of_week: e.target.value }))}
                className="bg-bg border border-border rounded px-2 py-1 text-[12px] outline-none focus:border-accent">
                <option value="1">周一</option>
                <option value="2">周二</option>
                <option value="3">周三</option>
                <option value="4">周四</option>
                <option value="5">周五</option>
                <option value="6">周六</option>
                <option value="7">周日</option>
              </select>
            </div>
          )}
          <div className="flex flex-col gap-0.5 flex-1 min-w-[120px]">
            <label className="text-[10.5px] text-text-dim">备注 (可空)</label>
            <input type="text" value={form.note}
              onChange={e => setForm(f => ({ ...f, note: e.target.value }))}
              className="bg-bg border border-border rounded px-2 py-1 text-[12px] outline-none focus:border-accent"
              placeholder="支付宝月定投" />
          </div>
          <button onClick={save}
            className="px-3 py-1 rounded bg-accent text-bg text-[11.5px] font-medium hover:opacity-90 cursor-pointer">
            {editingId ? '保存' : '添加'}
          </button>
          <button onClick={closeForm}
            className="px-3 py-1 rounded border border-border text-text-dim text-[11.5px] hover:text-text cursor-pointer">
            取消
          </button>
        </div>
      )}

      {loading ? (
        <div className="text-[11px] text-text-dim">加载中...</div>
      ) : schedules.length === 0 ? (
        <div className="text-[11px] text-text-dim">还没有定投计划</div>
      ) : (
        <div className="space-y-1">
          {schedules.map(s => {
            const paused = s.status === 'paused'
            return (
              <div key={s.id} className={`flex items-baseline gap-3 px-2 py-1.5 rounded text-[11.5px] ${paused ? 'bg-surface-3 opacity-60' : 'bg-surface-3'}`}>
                <span className="font-mono">
                  {describeSchedule(s)} ·
                  <span className="text-bull-bright ml-1">
                    {s.mode === 'amount' ? `¥${fmtMoney(s.value)}` : `${s.value} 份`}
                  </span>
                </span>
                {s.next_due && <span className="text-text-dim">下次 <span className="font-mono text-text">{s.next_due}</span></span>}
                {s.last_fired_at && <span className="text-text-muted text-[10.5px]">上次 {s.last_fired_at}</span>}
                {s.note && <span className="text-text-dim text-[10.5px]">· {s.note}</span>}
                {paused && <span className="text-warn text-[10px] px-1 border border-warn/40 rounded">已暂停</span>}
                <button onClick={() => startEdit(s)}
                  className="ml-auto text-[10.5px] text-text-dim hover:text-accent cursor-pointer">
                  改
                </button>
                <button onClick={() => togglePause(s)}
                  className="text-[10.5px] text-text-dim hover:text-accent cursor-pointer">
                  {paused ? '激活' : '暂停'}
                </button>
                <button onClick={() => remove(s)}
                  className="text-[10.5px] text-text-dim hover:text-bear cursor-pointer">
                  删
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
