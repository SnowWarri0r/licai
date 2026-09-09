import { useEffect, useState } from 'react'
import { api } from '../../hooks/useApi'

export function ProxySection() {
  const [proxy, setProxy] = useState('')
  const [effective, setEffective] = useState('')
  const [status, setStatus] = useState({ text: '', ok: null })
  const [busy, setBusy] = useState('')   // '' | save | test | detect

  useEffect(() => {
    api.getProxy().then(d => {
      setProxy(d.db_proxy || '')
      setEffective(d.proxy || '')
    }).catch(() => {})
  }, [])

  const save = async () => {
    setBusy('save'); setStatus({ text: '保存中...', ok: null })
    try {
      const r = await api.saveProxy(proxy.trim())
      setEffective(r.proxy || '')
      setStatus({ text: r.proxy ? (r.ok ? '已保存 · 连接正常' : '已保存 · 但连不上') : '已保存 · 直连', ok: r.ok || !r.proxy })
    } catch (e) { setStatus({ text: '保存失败: ' + (e.message || e), ok: false }) }
    setBusy('')
  }

  const detect = async () => {
    setBusy('detect'); setStatus({ text: '探测中...', ok: null })
    try {
      const r = await api.detectProxy()
      if (r.ok) { setProxy(r.proxy); setEffective(r.proxy); setStatus({ text: '探测到: ' + r.proxy, ok: true }) }
      else setStatus({ text: r.error || '未探测到可用代理', ok: false })
    } catch (e) { setStatus({ text: '探测失败: ' + (e.message || e), ok: false }) }
    setBusy('')
  }

  const test = async () => {
    setBusy('test'); setStatus({ text: '测试中...', ok: null })
    try {
      const r = await api.testProxy(proxy.trim())
      setStatus({ text: r.ok ? '连接正常 ✓' : (r.error || '连不上'), ok: r.ok })
    } catch (e) { setStatus({ text: '测试失败: ' + (e.message || e), ok: false }) }
    setBusy('')
  }

  return (
    <>
      <div className="flex items-center justify-between mb-2">
        <label className="text-[12px] text-text-dim font-semibold">本地代理</label>
        {effective && <span className="text-[11px] text-text-muted font-mono">生效: {effective}</span>}
      </div>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        海外接口同步 / 外发请求统一走这个本地代理。代理重启后端口可能变化,
        点<span className="text-accent">自动探测</span>让它自己找,不用手改。
        留空=直连。<span className="text-[var(--color-signal-moderate)]">境内行情源始终直连,不受影响。</span>
      </p>
      <div className="flex items-center gap-2 mb-2">
        <input
          className="flex-1 bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
          placeholder="http://127.0.0.1:7890(留空=直连)"
          value={proxy} onChange={e => setProxy(e.target.value)}
        />
        <button onClick={detect} disabled={!!busy}
          className="px-3 py-1.5 rounded-md border border-accent/50 text-accent text-[12px] hover:bg-accent/10 disabled:opacity-50 cursor-pointer whitespace-nowrap">
          {busy === 'detect' ? '探测中' : '自动探测'}
        </button>
      </div>
      <div className="flex items-center gap-3">
        <button onClick={save} disabled={!!busy}
          className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
          {busy === 'save' ? '保存中...' : '保存'}
        </button>
        <button onClick={test} disabled={!!busy}
          className="px-4 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text transition-colors cursor-pointer">
          {busy === 'test' ? '测试中...' : '测试连接'}
        </button>
        {status.text && (
          <span className={`text-[12px] font-medium break-all
            ${status.ok === true ? 'text-bull' : status.ok === false ? 'text-bear' : 'text-text-dim'}`}>
            {status.text}
          </span>
        )}
      </div>
    </>
  )
}
