import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import Tooltip from './Tooltip'

export default function AlertFeed({ alerts, onClear, holdings }) {
  const [showAlertForm, setShowAlertForm] = useState(false)
  const [customAlerts, setCustomAlerts] = useState([])
  const [form, setForm] = useState({ code: '', type: 'price_below', price: '', message: '' })
  const [notifyOn, setNotifyOn] = useState(() => localStorage.getItem('notifyEnabled') !== 'false')
  const [soundOn, setSoundOn] = useState(() => localStorage.getItem('notifySound') !== 'false')

  const loadCustomAlerts = async () => {
    try { setCustomAlerts(await fetchJSON('/api/settings/alerts')) } catch {}
  }

  useEffect(() => {
    loadCustomAlerts()
    // 启动时把后端飞书静音状态同步到前端按钮 (后端是 source of truth)
    fetchJSON('/api/settings/feishu').then(s => {
      if (typeof s?.muted === 'boolean') {
        const on = !s.muted
        setNotifyOn(on)
        localStorage.setItem('notifyEnabled', on ? 'true' : 'false')
      }
    }).catch(() => {})
  }, [])

  const toggleNotify = async () => {
    const next = !notifyOn
    setNotifyOn(next)
    localStorage.setItem('notifyEnabled', next ? 'true' : 'false')
    if (next && 'Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission()
    }
    // 同步切换后端飞书静音状态 (next=开 → muted=false; next=关 → muted=true)
    try {
      await fetchJSON('/api/settings/feishu/mute', {
        method: 'POST',
        body: JSON.stringify({ muted: !next }),
      })
    } catch (e) { console.error('feishu mute sync failed', e) }
  }

  const toggleSound = () => {
    const next = !soundOn
    setSoundOn(next)
    localStorage.setItem('notifySound', next ? 'true' : 'false')
  }

  const addAlert = async () => {
    if (!form.code || !form.price) return alert('请填写完整')
    await fetchJSON('/api/settings/alerts', {
      method: 'POST',
      body: JSON.stringify({ stock_code: form.code, alert_type: form.type, price: parseFloat(form.price), message: form.message }),
    })
    setForm({ ...form, price: '', message: '' })
    loadCustomAlerts()
  }

  const removeAlert = async (id) => {
    await fetchJSON(`/api/settings/alerts/${id}`, { method: 'DELETE' })
    loadCustomAlerts()
  }

  const typeLabels = { price_below: '跌破', price_above: '突破', stop_loss: '止损' }
  const stockOptions = holdings || []

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.6s ease-out' }}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h2 className="text-[13px] font-medium text-accent tracking-wide">实时提醒</h2>
        <div className="flex gap-2">
          <button onClick={() => setShowAlertForm(!showAlertForm)}
            className="text-[12px] px-3 py-1 rounded-md bg-accent/10 text-accent hover:bg-accent/20 transition-colors cursor-pointer">
            + 条件单
          </button>
          <Tooltip content={notifyOn ? '关闭后浏览器通知 + 飞书推送都静音' : '开启浏览器通知 + 飞书推送'}>
            <button onClick={toggleNotify}
              className={`text-[12px] px-3 py-1 rounded-md border transition-colors cursor-pointer ${
                notifyOn
                  ? 'border-accent/40 text-accent hover:bg-accent/10'
                  : 'border-border text-text-muted hover:text-text'
              }`}>
              {notifyOn ? '🔔 通知开' : '🔕 通知关'}
            </button>
          </Tooltip>
          <Tooltip content={soundOn ? '点击静音' : '点击开启提示音'}>
            <button onClick={toggleSound}
              className={`text-[12px] px-3 py-1 rounded-md border transition-colors cursor-pointer ${
                soundOn
                  ? 'border-accent/40 text-accent hover:bg-accent/10'
                  : 'border-border text-text-muted hover:text-text'
              }`}>
              {soundOn ? '🔊 声音开' : '🔇 静音'}
            </button>
          </Tooltip>
          <button onClick={onClear}
            className="text-[12px] px-3 py-1 rounded-md border border-border text-text-dim hover:text-text transition-colors cursor-pointer">
            清空
          </button>
        </div>
      </div>

      {/* Custom alert form */}
      {showAlertForm && (
        <div className="px-4 py-3 bg-surface-2/50 border-b border-border flex flex-wrap gap-2 items-end">
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">股票</label>
            <select className="bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none"
              value={form.code} onChange={e => setForm({ ...form, code: e.target.value })}>
              <option value="">选择</option>
              {stockOptions.map(h => <option key={h.stock_code} value={h.stock_code}>{h.stock_name || h.stock_code}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">类型</label>
            <select className="bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none"
              value={form.type} onChange={e => setForm({ ...form, type: e.target.value })}>
              <option value="price_below">跌破提醒</option>
              <option value="price_above">突破提醒</option>
              <option value="stop_loss">止损警告</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">价格</label>
            <input type="number" step="0.01"
              className="w-24 bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text font-mono outline-none focus:border-accent"
              value={form.price} onChange={e => setForm({ ...form, price: e.target.value })} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">备注</label>
            <input type="text"
              className="w-32 bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none focus:border-accent"
              placeholder="可选" value={form.message} onChange={e => setForm({ ...form, message: e.target.value })} />
          </div>
          <button onClick={addAlert}
            className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 cursor-pointer">
            添加
          </button>
        </div>
      )}

      {/* Active custom alerts */}
      {customAlerts.filter(a => !a.triggered).length > 0 && (
        <div className="px-4 py-2 border-b border-border-subtle flex flex-wrap gap-2">
          {customAlerts.filter(a => !a.triggered).map(a => (
            <span key={a.id} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-surface-3 text-[11px] text-text-dim border border-border">
              <span className="font-mono">{a.stock_code}</span>
              <span>{typeLabels[a.alert_type] || a.alert_type}</span>
              <span className="font-mono text-text">{a.price}</span>
              {a.message && <span className="text-text-muted">({a.message})</span>}
              <button onClick={() => removeAlert(a.id)} className="ml-0.5 text-text-muted hover:text-bear cursor-pointer">&times;</button>
            </span>
          ))}
        </div>
      )}

      {/* Alert feed */}
      <div className="max-h-[260px] overflow-y-auto">
        {alerts.length === 0 ? (
          <div className="py-8 text-center text-text-dim text-[13px]">暂无提醒</div>
        ) : alerts.map((a) => {
          const isCustom = a.alert_type?.startsWith('CUSTOM')
          const isUnwind = a.alert_type === 'TRANCHE_TRIGGERED'
            || a.alert_type === 'TRANCHE_LOCKED'
            || a.alert_type === 'HEALTH_DEGRADED'

          let channelTag, chipClass, rowClass
          if (isUnwind) {
            channelTag = '解套'
            chipClass = 'bg-accent/15 text-accent border-accent/40'
            rowClass = 'bg-accent/5 border-l-accent'
          } else if (isCustom) {
            channelTag = '条件单'
            chipClass = 'bg-[var(--color-signal-moderate)]/15 text-[var(--color-signal-moderate)] border-[var(--color-signal-moderate)]/40'
            rowClass = 'bg-[var(--color-signal-moderate)]/5 border-l-[var(--color-signal-moderate)]'
          } else {
            channelTag = '提醒'
            chipClass = 'bg-surface-3 text-text-dim border-border'
            rowClass = 'bg-surface-2/30 border-l-border'
          }

          return (
            <div key={a.id}
              className={`mx-3 my-1.5 px-3 py-2 rounded-lg text-[13px] flex justify-between items-center border-l-2 ${rowClass}`}
              style={{ animation: 'slide-in 0.3s ease-out' }}>
              <span className="flex items-center gap-2 min-w-0">
                <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold border shrink-0 ${chipClass}`}>
                  {channelTag}
                </span>
                <span className="font-medium shrink-0">{a.stock_name}</span>
                <span className="text-text-dim shrink-0">({a.stock_code})</span>
                <span className="truncate">{a.message}</span>
              </span>
              <span className="text-[11px] text-text-muted font-mono ml-3 shrink-0">
                {a.time?.toLocaleTimeString?.('zh-CN') || ''}
              </span>
            </div>
          )
        })}
      </div>
    </section>
  )
}
