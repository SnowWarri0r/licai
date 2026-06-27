import { useState, useEffect } from 'react'
import { api, fetchJSON } from '../hooks/useApi'

export default function Settings({ onClose }) {
  const [url, setUrl] = useState('')
  const [status, setStatus] = useState({ text: '', ok: null })
  const [saving, setSaving] = useState(false)

  // OKX credentials
  const [okxStatus, setOkxStatus] = useState(null)
  const [okxApiKey, setOkxApiKey] = useState('')
  const [okxSecret, setOkxSecret] = useState('')
  const [okxPassphrase, setOkxPassphrase] = useState('')
  const [okxStatusText, setOkxStatusText] = useState({ text: '', ok: null })
  const [okxSaving, setOkxSaving] = useState(false)

  const loadOkxStatus = async () => {
    try { setOkxStatus(await fetchJSON('/api/assets/okx/status')) } catch {}
  }

  useEffect(() => {
    api.getFeishuConfig().then(d => {
      setUrl(d.webhook_url || '')
      if (d.enabled) setStatus({ text: '已启用', ok: true })
    })
    loadOkxStatus()
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

        {/* 本地代理 (OKX/外发统一) */}
        <div className="mt-2 pt-4 border-t border-border">
          <ProxySection />
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

        {/* LLM 配置 */}
        <div className="mt-2 pt-4 border-t border-border">
          <LLMConfigSection />
        </div>
      </div>
    </section>
  )
}

function ProxySection() {
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

function LLMConfigSection() {
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [apiKeyHeader, setApiKeyHeader] = useState('x-api-key')
  const [apiKeyPrefix, setApiKeyPrefix] = useState('')
  const [proxy, setProxy] = useState('')
  const [modelMap, setModelMap] = useState('')
  const [status, setStatus] = useState({ text: '', ok: null })
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [dbHasKey, setDbHasKey] = useState(false)

  useEffect(() => {
    api.getLLMConfig().then(d => {
      setBaseUrl(d.db_base_url || '')
      setDbHasKey(d.has_api_key)
      setApiKeyHeader(d.db_api_key_header || 'x-api-key')
      setApiKeyPrefix(d.db_api_key_prefix || '')
      setProxy(d.db_proxy || '')
      setModelMap(d.db_model_map && Object.keys(d.db_model_map).length ? JSON.stringify(d.db_model_map, null, 2) : '')
    }).catch(() => {})
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      let modelMapObj = {}
      if (modelMap.trim()) {
        try { modelMapObj = JSON.parse(modelMap) } catch {
          setStatus({ text: '模型映射 JSON 格式错误', ok: false })
          setSaving(false)
          return
        }
      }
      await api.saveLLMConfig({
        base_url: baseUrl.trim(),
        api_key: apiKey.trim() || (dbHasKey ? '****' : ''),
        api_key_header: apiKeyHeader.trim() || 'x-api-key',
        api_key_prefix: apiKeyPrefix.trim(),
        proxy: proxy.trim(),
        model_map: modelMapObj,
        update_api_key: apiKey.trim().length > 0,
      })
      setStatus({ text: '已保存', ok: true })
      if (apiKey.trim()) setDbHasKey(true)
      setApiKey('')
    } catch (e) {
      setStatus({ text: '保存失败: ' + (e.message || e), ok: false })
    }
    setSaving(false)
  }

  const handleTest = async () => {
    setTesting(true)
    setStatus({ text: '测试中...', ok: null })
    try {
      const r = await api.testLLM()
      if (r.ok) {
        setStatus({ text: `连接成功 · ${r.model} · ${r.latency_ms}ms`, ok: true })
      } else {
        setStatus({ text: `失败: ${r.error}`, ok: false })
      }
    } catch (e) {
      setStatus({ text: '测试失败: ' + (e.message || e), ok: false })
    }
    setTesting(false)
  }

  return (
    <>
      <label className="text-[12px] text-text-dim font-semibold">LLM 配置</label>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        支持 Anthropic 协议兼容的 API 端点（DeepSeek / 硅基流动 / OpenRouter 等）。
        不配置则走原有 Anthropic 官方 + Keychain OAuth。
      </p>

      <div className="grid grid-cols-1 gap-2 mb-2">
        <div>
          <label className="text-[11px] text-text-muted">API Base URL</label>
          <input
            className="w-full bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
            placeholder="https://api.anthropic.com"
            value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
          />
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11px] text-text-muted">API Key Header</label>
            <input
              className="w-full bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
              placeholder="x-api-key"
              value={apiKeyHeader} onChange={e => setApiKeyHeader(e.target.value)}
            />
          </div>
          <div>
            <label className="text-[11px] text-text-muted">API Key Prefix（如 Bearer）</label>
            <input
              className="w-full bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
              placeholder="留空或填 Bearer"
              value={apiKeyPrefix} onChange={e => setApiKeyPrefix(e.target.value)}
            />
          </div>
        </div>

        <div>
          <label className="text-[11px] text-text-muted">API Key {dbHasKey && <span className="text-bull">（已保存，留空则不动）</span>}</label>
          <input type="password"
            className="w-full bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
            placeholder={dbHasKey ? '输入新 key 覆盖，留空保持原 key' : 'sk-...'}
            value={apiKey} onChange={e => setApiKey(e.target.value)}
          />
        </div>

        <div>
          <label className="text-[11px] text-text-muted">HTTP 代理（可选）</label>
          <input
            className="w-full bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
            placeholder="http://127.0.0.1:7890"
            value={proxy} onChange={e => setProxy(e.target.value)}
          />
        </div>

        <div>
          <label className="text-[11px] text-text-muted">模型别名映射（JSON，可选）</label>
          <textarea rows={3}
            className="w-full bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent resize-none"
            placeholder='{"smart":"deepseek-chat","balanced":"deepseek-chat","fast":"deepseek-chat"}'
            value={modelMap} onChange={e => setModelMap(e.target.value)}
          />
          <p className="text-[10px] text-text-muted mt-0.5">逻辑名: smart / balanced / fast → 实际模型名</p>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button onClick={handleSave} disabled={saving}
          className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
          {saving ? '保存中...' : '保存'}
        </button>
        <button onClick={handleTest} disabled={testing}
          className="px-4 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text transition-colors cursor-pointer">
          {testing ? '测试中...' : '测试连接'}
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
