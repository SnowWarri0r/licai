import { useState, useEffect } from 'react'
import { api, fetchJSON } from '../hooks/useApi'

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
            访问 Anthropic API 的本地代理，留空则直连。常见：Shadowrocket <code className="bg-surface-3 px-1 rounded">http://127.0.0.1:1082</code>，Clash <code className="bg-surface-3 px-1 rounded">http://127.0.0.1:7890</code>
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
      </div>
    </section>
  )
}
