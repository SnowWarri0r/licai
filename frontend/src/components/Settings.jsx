import { useState, useEffect, useRef } from 'react'
import { api, fetchJSON } from '../hooks/useApi'
import { clearBrokersCache } from '../helpers'

export default function Settings({ onClose }) {
  const [url, setUrl] = useState('')
  const [status, setStatus] = useState({ text: '', ok: null })
  const [saving, setSaving] = useState(false)

  // LLM proxy
  const [proxyUrl, setProxyUrl] = useState('')
  const [proxySaving, setProxySaving] = useState(false)
  const [proxyStatus, setProxyStatus] = useState({ text: '', ok: null })
  const [proxyEnvOverride, setProxyEnvOverride] = useState(false)

  // OKX credentials
  const [okxStatus, setOkxStatus] = useState(null)
  const [okxApiKey, setOkxApiKey] = useState('')
  const [okxSecret, setOkxSecret] = useState('')
  const [okxPassphrase, setOkxPassphrase] = useState('')
  const [okxStatusText, setOkxStatusText] = useState({ text: '', ok: null })
  const [okxSaving, setOkxSaving] = useState(false)

  // 券商费率
  const [brokers, setBrokers] = useState([])
  const saveTimers = useRef({})
  const pendingPatch = useRef({})
  const reloadBrokers = () => fetch('/api/brokers').then(r => r.json()).then(setBrokers).catch(() => {})
  useEffect(() => { reloadBrokers() }, [])
  const editBroker = (id, field, val) => {
    setBrokers(bs => bs.map(b => b.id === id ? { ...b, [field]: val } : b))
    pendingPatch.current[id] = { ...(pendingPatch.current[id] || {}), [field]: val }
    clearTimeout(saveTimers.current[id])
    saveTimers.current[id] = setTimeout(() => {
      const patch = pendingPatch.current[id]; pendingPatch.current[id] = {}
      fetch(`/api/brokers/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) })
        .then(() => clearBrokersCache())
    }, 600)
  }
  const setDefaultBroker = (id) => fetch(`/api/brokers/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_default: true }) }).then(() => { clearBrokersCache(); reloadBrokers() })
  const delBroker = (id) => { if (confirm('删除该券商？')) fetch(`/api/brokers/${id}`, { method: 'DELETE' }).then(r => r.json()).then(d => { if (d.detail) alert(d.detail); clearBrokersCache(); reloadBrokers() }) }
  const addBroker = () => fetch('/api/brokers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: '新券商', stock_rate: 0.0001854, stock_min: 5, etf_rate: 0.0001854, etf_min: 5 }) }).then(() => { clearBrokersCache(); reloadBrokers() })

  const loadOkxStatus = async () => {
    try { setOkxStatus(await fetchJSON('/api/assets/okx/status')) } catch {}
  }

  useEffect(() => {
    api.getFeishuConfig().then(d => {
      setUrl(d.webhook_url || '')
      if (d.enabled) setStatus({ text: '已启用', ok: true })
    })
    loadOkxStatus()
    fetchJSON('/api/settings/llm').then(d => {
      setProxyUrl(d.proxy_url || '')
      setProxyEnvOverride(d.env_override || false)
    }).catch(() => {})
  }, [])

  const saveOkx = async () => {
    if (!okxApiKey || !okxSecret || !okxPassphrase) {
      return setOkxStatusText({ text: '三项都要填', ok: false })
    }
    setOkxSaving(true)
    setOkxStatusText({ text: '校验中...', ok: null })
    try {
      const r = await fetchJSON('/api/assets/okx/credentials', {
        method: 'POST',
        body: JSON.stringify({
          api_key: okxApiKey.trim(),
          secret_key: okxSecret.trim(),
          passphrase: okxPassphrase.trim(),
        }),
      })
      const detail = r.uid
        ? `UID ${r.uid} · ${r.bot_count} 个机器人`
        : `${r.bot_count} 个机器人` + (r.errors?.length ? `（注: ${r.errors.join('; ')}）` : '')
      setOkxStatusText({ text: `已保存 · ${detail}`, ok: true })
      setOkxApiKey(''); setOkxSecret(''); setOkxPassphrase('')
      await loadOkxStatus()
    } catch (e) {
      setOkxStatusText({ text: '保存失败：' + (e.message || e), ok: false })
    } finally {
      setOkxSaving(false)
    }
  }

  const clearOkx = async () => {
    if (!confirm('确定清除 OKX 凭证？已绑定的 BOT 资产将退回手动模式')) return
    try {
      await fetchJSON('/api/assets/okx/credentials', { method: 'DELETE' })
      setOkxStatusText({ text: '已清除', ok: true })
      await loadOkxStatus()
    } catch {}
  }

  const saveProxy = async () => {
    setProxySaving(true)
    setProxyStatus({ text: '', ok: null })
    try {
      await fetchJSON('/api/settings/llm', {
        method: 'POST',
        body: JSON.stringify({ proxy_url: proxyUrl.trim() }),
      })
      setProxyStatus({ text: proxyUrl.trim() ? '已保存并启用' : '已清除，走直连', ok: true })
    } catch {
      setProxyStatus({ text: '保存失败', ok: false })
    } finally {
      setProxySaving(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const res = await api.saveFeishuConfig(url)
      setStatus({ text: res.enabled ? '已保存并启用' : '已保存', ok: res.enabled })
    } catch {
      setStatus({ text: '保存失败', ok: false })
    }
    setSaving(false)
  }

  const handleTest = async () => {
    setStatus({ text: '发送中...', ok: null })
    try {
      const res = await api.testFeishu()
      setStatus({ text: res.message, ok: res.success })
    } catch {
      setStatus({ text: '发送失败', ok: false })
    }
  }

  return (
    <section className="rounded-xl border border-accent/20 bg-surface-2/80 overflow-hidden"
      style={{ animation: 'fade-up 0.3s ease-out' }}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h2 className="text-[13px] font-medium text-accent tracking-wide">推送设置</h2>
        <button onClick={onClose}
          className="text-[12px] px-3 py-1 rounded-md border border-border text-text-dim hover:text-text transition-colors cursor-pointer">
          关闭
        </button>
      </div>
      <div className="p-4 space-y-3">
        <div>
          <label className="text-[12px] text-text-dim block mb-1">飞书 Webhook URL</label>
          <p className="text-[11px] text-text-muted mb-2">
            飞书群 → 设置 → 群机器人 → 添加机器人 → 自定义机器人 → 复制 Webhook 地址
          </p>
          <input
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] text-text font-mono outline-none focus:border-accent transition-colors"
            placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxxx"
            value={url} onChange={e => setUrl(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-3">
          <button onClick={handleSave} disabled={saving}
            className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {saving ? '保存中...' : '保存'}
          </button>
          <button onClick={handleTest}
            className="px-4 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text transition-colors cursor-pointer">
            发送测试
          </button>
          {status.text && (
            <span className={`text-[12px] font-medium
              ${status.ok === true ? 'text-bull' : status.ok === false ? 'text-bear' : 'text-text-dim'}`}>
              {status.text}
            </span>
          )}
        </div>

        {/* LLM 代理配置 */}
        <div className="mt-2 pt-4 border-t border-border">
          <label className="text-[12px] text-text-dim font-semibold block mb-1">LLM 代理地址（早盘简报）</label>
          <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
            访问 Anthropic API 的本地代理，留空则直连。
          </p>
          {proxyEnvOverride && (
            <p className="text-[11px] text-warn mb-2">当前由环境变量 LLM_PROXY 覆盖，UI 配置不生效</p>
          )}
          <input
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] text-text font-mono outline-none focus:border-accent transition-colors disabled:opacity-50"
            placeholder="留空直连，或填入代理地址如 http://127.0.0.1:1082"
            value={proxyUrl}
            onChange={e => setProxyUrl(e.target.value)}
            disabled={proxyEnvOverride}
          />
          <div className="flex items-center gap-3 mt-2">
            <button onClick={saveProxy} disabled={proxySaving || proxyEnvOverride}
              className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
              {proxySaving ? '保存中...' : '保存'}
            </button>
            {proxyStatus.text && (
              <span className={`text-[12px] font-medium ${proxyStatus.ok ? 'text-bull' : 'text-bear'}`}>
                {proxyStatus.text}
              </span>
            )}
          </div>
        </div>

        {/* OKX API 凭证 */}
        <div className="mt-2 pt-4 border-t border-border">
          <div className="flex items-center justify-between mb-2">
            <label className="text-[12px] text-text-dim font-semibold">OKX API 凭证</label>
            {okxStatus?.configured && (
              <span className="text-[11px] text-bull flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-bull"
                  style={{ boxShadow: '0 0 6px currentColor' }} />
                已连接
                {okxStatus.uid && <span className="text-text-muted ml-1">· UID {okxStatus.uid}</span>}
                {!okxStatus.uid && okxStatus.ok && <span className="text-text-muted ml-1">· 机器人接口可用</span>}
              </span>
            )}
          </div>
          <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
            用于自动同步网格/马丁格尔机器人的本金和盈亏。<span className="text-[var(--color-signal-moderate)]">
            只需勾选 <code className="bg-surface-3 px-1 rounded">Read</code> 权限</span>，
            禁用交易/提币 scope。凭证存入 macOS Keychain，不写数据库。
            <br />
            获取路径：OKX App → 账户 → API → 创建 API Key（IP 白名单填你的出口 IP，或留空）
          </p>

          {!okxStatus?.configured ? (
            <>
              <div className="grid grid-cols-1 gap-2 mb-2">
                <input type="password"
                  className="bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
                  placeholder="API Key" value={okxApiKey}
                  onChange={e => setOkxApiKey(e.target.value)} />
                <input type="password"
                  className="bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
                  placeholder="Secret Key" value={okxSecret}
                  onChange={e => setOkxSecret(e.target.value)} />
                <input type="password"
                  className="bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
                  placeholder="Passphrase (创建 Key 时你设的)" value={okxPassphrase}
                  onChange={e => setOkxPassphrase(e.target.value)} />
              </div>
              <div className="flex items-center gap-3">
                <button onClick={saveOkx} disabled={okxSaving}
                  className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50">
                  {okxSaving ? '校验中...' : '保存并校验'}
                </button>
                {okxStatusText.text && (
                  <span className={`text-[12px] ${
                    okxStatusText.ok === true ? 'text-bull'
                    : okxStatusText.ok === false ? 'text-bear' : 'text-text-dim'
                  }`}>
                    {okxStatusText.text}
                  </span>
                )}
              </div>
            </>
          ) : (
            <button onClick={clearOkx}
              className="px-3 py-1 rounded border border-bear/40 text-bear hover:bg-bear/10 text-[12px]">
              清除凭证
            </button>
          )}
        </div>

        <DataExportImport />

        {/* 券商费率管理 */}
        <section className="rounded-xl border border-accent/20 bg-surface-2/80 overflow-hidden mt-4">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <h2 className="text-[13px] font-medium text-accent tracking-wide">券商费率</h2>
            <button onClick={addBroker} className="text-[11px] px-2 py-1 rounded border border-accent/40 text-accent hover:bg-accent/10 cursor-pointer">+ 新增券商</button>
          </div>
          <div className="p-3 space-y-2">
            <div className="grid grid-cols-[1fr_auto_auto_auto_auto_auto] gap-2 text-[10px] text-text-dim px-1">
              <div>券商</div><div className="w-20 text-center">股票(万)</div><div className="w-16 text-center">股票起¥</div><div className="w-20 text-center">ETF(万)</div><div className="w-16 text-center">ETF起¥</div><div className="w-[72px]"></div>
            </div>
            {brokers.map(b => (
              <div key={b.id} className="grid grid-cols-[1fr_auto_auto_auto_auto_auto] gap-2 items-center text-[12px]">
                <input value={b.name} onChange={e => editBroker(b.id, 'name', e.target.value)} className="bg-bg border border-border rounded px-2 py-1 text-text" placeholder="券商名" />
                <input type="number" step="0.0001" value={+(b.stock_rate * 10000).toFixed(4)} onChange={e => editBroker(b.id, 'stock_rate', parseFloat(e.target.value) / 10000)} className="w-20 bg-bg border border-border rounded px-2 py-1 text-text" />
                <input type="number" step="0.1" value={b.stock_min} onChange={e => editBroker(b.id, 'stock_min', parseFloat(e.target.value))} className="w-16 bg-bg border border-border rounded px-2 py-1 text-text" />
                <input type="number" step="0.0001" value={+(b.etf_rate * 10000).toFixed(4)} onChange={e => editBroker(b.id, 'etf_rate', parseFloat(e.target.value) / 10000)} className="w-20 bg-bg border border-border rounded px-2 py-1 text-text" />
                <input type="number" step="0.1" value={b.etf_min} onChange={e => editBroker(b.id, 'etf_min', parseFloat(e.target.value))} className="w-16 bg-bg border border-border rounded px-2 py-1 text-text" />
                <div className="flex gap-1">
                  <button onClick={() => setDefaultBroker(b.id)} className={`text-[10px] px-1.5 py-1 rounded border cursor-pointer ${b.is_default ? 'border-accent text-accent bg-accent/10' : 'border-border text-text-dim'}`}>{b.is_default ? '默认' : '设默认'}</button>
                  {!b.is_default && <button onClick={() => delBroker(b.id)} className="text-[10px] px-1.5 py-1 rounded border border-bear/40 text-bear cursor-pointer">删</button>}
                </div>
              </div>
            ))}
            <div className="text-[10px] text-text-muted">费率单位「万」(如 1.854 = 万1.854)；起¥ 为每笔最低收费。改完自动保存。</div>
          </div>
        </section>
      </div>
    </section>
  )
}

function DataExportImport() {
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState(null)
  const [importMode, setImportMode] = useState('replace')

  const handleExport = () => {
    // 直链下载, 让浏览器原生触发 Save As
    window.location.href = '/api/data/export'
  }

  const handleImport = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (!confirm(`确定从「${file.name}」${importMode === 'replace' ? '覆盖导入' : '合并导入'}？\n当前 DB 会先自动备份到 backups/。`)) {
      e.target.value = ''
      return
    }
    setImporting(true)
    setImportResult(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const res = await fetch(`/api/data/import?mode=${importMode}`, {
        method: 'POST',
        body: fd,
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '导入失败')
      setImportResult({ ok: true, ...data })
      setTimeout(() => window.location.reload(), 1500)
    } catch (err) {
      setImportResult({ ok: false, message: err.message })
    } finally {
      setImporting(false)
      e.target.value = ''
    }
  }

  return (
    <div className="mt-2 pt-4 border-t border-border">
      <label className="text-[12px] text-text-dim font-semibold block mb-1">数据导出 / 导入</label>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        导出为单个 JSON 文件，包含持仓、资产、交易记录、解套计划、提醒、月度现金流和应用设置。换设备时上传此文件还原。
      </p>
      <div className="flex flex-wrap gap-2 items-center mb-2">
        <button onClick={handleExport}
          className="px-3 py-1.5 rounded-md text-[12px] border border-accent text-accent hover:bg-accent/10 cursor-pointer">
          导出全部数据
        </button>
        <span className="w-px h-4 bg-border mx-1" />
        <span className="text-[11px] text-text-muted">导入模式</span>
        {[
          ['replace', '覆盖（清空再导入）'],
          ['merge', '合并（仅新增/更新）'],
        ].map(([k, l]) => (
          <label key={k} className="text-[11px] text-text-dim flex items-center gap-1 cursor-pointer">
            <input type="radio" name="importMode" value={k}
              checked={importMode === k} onChange={() => setImportMode(k)} />
            {l}
          </label>
        ))}
      </div>
      <label className="inline-block">
        <input type="file" accept="application/json,.json" onChange={handleImport}
          disabled={importing} className="hidden" />
        <span className={`px-3 py-1.5 rounded-md text-[12px] border border-border-med text-text-dim hover:text-text hover:border-text-muted cursor-pointer inline-block ${importing ? 'opacity-50 pointer-events-none' : ''}`}>
          {importing ? '导入中...' : '选择 JSON 导入'}
        </span>
      </label>
      {importResult && (
        <div className={`mt-2 text-[11.5px] p-2 rounded ${importResult.ok ? 'bg-bull/10 text-bull' : 'bg-bear/10 text-bear'}`}>
          {importResult.ok ? (
            <>
              <div className="font-semibold mb-1">✓ {importResult.message}</div>
              <div className="text-text-dim font-mono text-[10.5px]">
                pre-import 备份: {importResult.pre_import_backup}
              </div>
              <div className="text-text-dim text-[10.5px] mt-1">
                {Object.entries(importResult.imported).map(([t, n]) => `${t}: ${n}`).join(' · ')}
              </div>
              <div className="text-[11px] mt-1.5 text-text">页面将自动刷新...</div>
            </>
          ) : (
            <div className="font-semibold">✗ {importResult.message}</div>
          )}
        </div>
      )}
    </div>
  )
}
