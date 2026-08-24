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

        {/* 知识星球(可选, 只读) */}
        <div className="mt-2 pt-4 border-t border-border">
          <ZsxqSection />
        </div>

        {/* OKX 策略之外的现货 */}
        {okxStatus?.configured && (
          <div className="mt-2 pt-4 border-t border-border">
            <OkxSpotSection />
          </div>
        )}

        {/* 从纠正里沉淀出来的规则(待审队列) */}
        <div className="mt-2 pt-4 border-t border-border">
          <PromptRulesSection />
        </div>

        {/* 开源许可 */}
        <div className="mt-2 pt-4 border-t border-border">
          <AttributionSection />
        </div>
      </div>
    </section>
  )
}


/* 知识星球接入(可选, 只读)。
   走官方 MCP 端点(https://mcp.zsxq.com/topic/mcp?api_key=...), 不用装 npm 包也不碰 Keychain。
   URL 里带 api_key: 存 DB(不进 config.py)、前端只回显脱敏后的 host+path、日志也只打脱敏值。
   只读白名单在 services/zsxq_client.py 的 _READ_TOOLS —— 远端那些 create_/set_ 写口不接。 */
function ZsxqSection() {
  const [st, setSt] = useState(null)          // {configured, ok, groups, endpoint, account, error}
  const [url, setUrl] = useState('')          // 只在用户新填时有值; 已保存的不回显(带 key)
  const [avail, setAvail] = useState(null)
  const [picked, setPicked] = useState([])
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState({ text: '', ok: null })

  const load = async () => {
    try {
      const d = await fetchJSON('/api/settings/zsxq')
      setSt(d); setPicked(Array.isArray(d.groups) ? d.groups : [])   // 后端给了非数组也别整页崩
    } catch { /* 后端老版本没这个端点时静默 */ }
  }
  useEffect(() => { load() }, [])

  const saveUrl = async () => {
    setBusy('url'); setMsg({ text: '保存并连接...', ok: null })
    try {
      const r = await fetchJSON('/api/settings/zsxq', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim() }),
      })
      setUrl('')
      const d = await fetchJSON('/api/settings/zsxq')
      setSt(d)
      setMsg(d.ok ? { text: `已连接${d.account ? ` · ${d.account}` : ''}`, ok: true }
                  : { text: d.error || (r.endpoint ? '已保存, 但连不上' : '已清空'), ok: !!r.endpoint === false })
    } catch (e) { setMsg({ text: '保存失败: ' + (e.message || e), ok: false }) }
    setBusy('')
  }

  const pull = async () => {
    setBusy('pull'); setMsg({ text: '读取星球列表...', ok: null })
    try {
      const r = await fetchJSON('/api/settings/zsxq/available')
      if (r.ok) { setAvail(r.groups || []); setMsg({ text: `找到 ${(r.groups || []).length} 个星球`, ok: true }) }
      else setMsg({ text: (r.error?.message || '读取失败') + (r.error?.hint ? ` · ${r.error.hint}` : ''), ok: false })
    } catch (e) { setMsg({ text: '读取失败: ' + (e.message || e), ok: false }) }
    setBusy('')
  }

  const toggle = (g) => setPicked(p => p.some(x => x.group_id === g.group_id)
    ? p.filter(x => x.group_id !== g.group_id)
    : [...p, { group_id: g.group_id, name: g.name, owner_only: false }])

  const setOwnerOnly = (gid, v) => setPicked(p => p.map(x =>
    x.group_id === gid ? { ...x, owner_only: v } : x))

  const saveGroups = async () => {
    setBusy('save')
    try {
      const r = await fetchJSON('/api/settings/zsxq', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ groups: picked }),
      })
      setMsg({ text: r.enabled ? `已接入 ${r.groups.length} 个星球` : '已关闭(未选星球)', ok: true })
      load()
    } catch (e) { setMsg({ text: '保存失败: ' + (e.message || e), ok: false }) }
    setBusy('')
  }

  return (
    <>
      <div className="flex items-center justify-between mb-2">
        <label className="text-[12px] text-text-dim font-semibold">知识星球（可选 · 只读观点面）</label>
        <span className="text-[11px] font-mono text-text-muted">
          {!st ? '' : !st.configured ? '未配置'
            : st.ok ? (st.groups?.length ? `已接入 ${st.groups.length} 个星球` : '已连接 · 未选星球')
            : '连不上'}
        </span>
      </div>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        给 AI 补一层「人在怎么说」的观点面（情绪面博主怎么定性今天的盘、社群在讲什么逻辑）——
        指标看不到的文本面。<span className="text-[var(--color-signal-moderate)]">只读</span>，
        原文不落库，返回一律按 <span className="font-mono">[星球观点]</span> 单独一档标注，
        不作为数字依据、不转成买卖建议。不勾星球=完全不启用。
      </p>
      <div className="flex items-center gap-2 mb-2">
        <input
          className="flex-1 bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
          placeholder={st?.endpoint ? `已配置: ${st.endpoint}（重填可覆盖）` : 'https://mcp.zsxq.com/topic/mcp?api_key=...'}
          value={url} onChange={e => setUrl(e.target.value)} autoComplete="off" spellCheck={false}
        />
        <button onClick={saveUrl} disabled={!!busy}
          className="px-3 py-1.5 rounded-md border border-accent/50 text-accent text-[12px] hover:bg-accent/10 disabled:opacity-50 cursor-pointer whitespace-nowrap">
          {busy === 'url' ? '连接中' : '保存端点'}
        </button>
      </div>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        URL 里带 api_key —— 存在本机数据库、不进代码库、界面只回显 <span className="font-mono">host/path</span>。
        端点从知识星球官方 MCP 服务拿。
      </p>
      <div className="flex items-center gap-3 mb-2">
        <button onClick={pull} disabled={!!busy || !st?.configured}
          className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[12px] hover:text-text disabled:opacity-40 cursor-pointer">
          {busy === 'pull' ? '读取中' : '读取我的星球'}
        </button>
        <button onClick={saveGroups} disabled={!!busy}
          className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
          {busy === 'save' ? '保存中...' : '保存选择'}
        </button>
        {msg.text && (
          <span className={`text-[12px] font-medium break-all
            ${msg.ok === true ? 'text-bull' : msg.ok === false ? 'text-bear' : 'text-text-dim'}`}>
            {msg.text}
          </span>
        )}
      </div>
      {(avail || picked.length > 0) && (
        <div className="max-h-44 overflow-y-auto rounded border border-border-subtle divide-y divide-border-subtle">
          {(avail || picked).map(g => {
            const on = picked.find(x => x.group_id === g.group_id)
            return (
              <div key={g.group_id} className="flex items-center gap-2 px-2.5 py-1.5 text-[12px] text-text-dim hover:bg-surface-3/60">
                <label className="flex items-center gap-2 flex-1 min-w-0 cursor-pointer">
                  <input type="checkbox" checked={!!on} onChange={() => toggle(g)}
                    className="accent-[var(--color-accent)]" />
                  <span className="flex-1 truncate">{g.name}</span>
                </label>
                {on && (
                  <label title="只取星主及合伙人的帖。实测有的星球发帖人不算星主, 勾上会筛成空 —— 先不勾, 内容太杂再开"
                    className="flex items-center gap-1 text-[10.5px] text-text-muted cursor-pointer whitespace-nowrap">
                    <input type="checkbox" checked={on.owner_only === true}
                      onChange={e => setOwnerOnly(g.group_id, e.target.checked)}
                      className="accent-[var(--color-accent)]" />
                    只看星主
                  </label>
                )}
                <span className="text-[10px] font-mono text-text-muted">{g.group_id}</span>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}

/* 开源许可署名。
   K 线用 TradingView Lightweight Charts (Apache-2.0)。它的许可要求「在用户可见的
   页面上给出署名 + tradingview.com 链接」—— 图上那枚角标只是满足要求的一种方式,
   我们把角标关了(layout.attributionLogo = false), 所以署名必须落在这里。
   要动这段先看 ProKline.jsx / PriceChart.jsx 里的说明。 */
function AttributionSection() {
  return (
    <div>
      <div className="text-[12px] text-text-dim mb-2 tracking-wide">开源许可</div>
      <p className="text-[11.5px] text-text-muted leading-relaxed">
        K 线图表由{' '}
        <a href="https://www.tradingview.com/lightweight-charts/" target="_blank" rel="noreferrer"
          className="text-accent hover:underline">TradingView Lightweight Charts</a>
        {' '}提供（Apache-2.0）。图表技术与金融数据可视化方案来自{' '}
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer"
          className="text-accent hover:underline">TradingView</a>。
      </p>
      <p className="text-[11.5px] text-text-muted leading-relaxed mt-1">
        本项目以 AGPL-3.0 开源：{' '}
        <a href="https://github.com/SnowWarri0r/licai" target="_blank" rel="noreferrer"
          className="text-accent hover:underline">github.com/SnowWarri0r/licai</a>
      </p>
    </div>
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


/* 从"用户纠正"里沉淀出来的候选规则。
   批准的会写进 agent 的 system prompt(下一次问答就生效), 待审的一条都不生效。
   为什么要有这个面板: 之前只有 CLI, 待审队列在界面上根本不存在 —— 挖出来的候选就一直
   躺着没人批, 这套机制等于白做。否决的不删, 留着才知道"提过、被否了"。 */
function PromptRulesSection() {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(0)          // 正在处理的规则 id
  const [mining, setMining] = useState(false)
  const [msg, setMsg] = useState('')

  const load = () => fetchJSON('/api/settings/rules')
    .then(setData).catch(() => setData({ rules: [], n_pending: 0, n_active: 0 }))
  useEffect(() => { load() }, [])

  const decide = async (id, decision) => {
    setBusy(id)
    try {
      await fetchJSON(`/api/settings/rules/${id}/${decision}`, { method: 'POST' })
      await load()
      setMsg(decision === 'approve' ? '已进 prompt, 下一次问答生效' : '已否决, 不进 prompt')
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setBusy(0) }
  }

  const mine = async () => {
    setMining(true); setMsg('挖掘中(要调模型, 十几秒)…')
    try {
      const r = await fetchJSON('/api/settings/rules/mine', { method: 'POST' })
      setMsg(`新纠正 ${r.corrections} 条 → 新增候选 ${r.added}, 判为非规则 ${r.skipped}, 重复 ${r.dup}, 失败 ${r.failed}`)
      await load()
    } catch (e) {
      setMsg(String(e.message || e))
    } finally { setMining(false) }
  }

  const rules = data?.rules || []
  const pending = rules.filter(r => r.status === 'pending')
  const active = rules.filter(r => r.status === 'active')

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="text-[12px] text-text-dim font-semibold">
          AI 读盘规则
          {data && (
            <span className="ml-2 text-[11px] font-normal">
              <span className={pending.length ? 'text-accent' : 'text-text-muted'}>待审 {pending.length}</span>
              <span className="text-text-muted"> · 已生效 {active.length}</span>
            </span>
          )}
        </label>
        <button onClick={mine} disabled={mining}
          className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text disabled:opacity-50 cursor-pointer">
          {mining ? '挖掘中…' : '立刻挖一次'}
        </button>
      </div>
      <p className="text-[11px] text-text-muted mb-2">
        从你历次「不对/怎么还是」这类纠正里自动起草。<b>批准了才进 prompt</b>，待审的一条都不生效 ——
        prompt 对单个词都敏感，一条坏规则会静默影响所有回答。每周一自动挖一次。
      </p>
      {pending.length === 0 && (
        <p className="text-[11px] text-text-muted">待审队列是空的。</p>
      )}
      {pending.map(r => (
        <div key={r.id} className="mb-2 p-2 rounded-lg border border-accent/25 bg-bg/40">
          <div className="text-[12px] text-text-bright">【{r.title}】</div>
          <div className="text-[11px] text-text-dim mt-0.5 leading-relaxed">{r.body}</div>
          {r.evidence && (
            <div className="text-[10px] text-text-muted mt-1">← 触发它的纠正: {r.evidence}</div>
          )}
          <div className="flex items-center gap-2 mt-1.5">
            <button onClick={() => decide(r.id, 'approve')} disabled={busy === r.id}
              className="text-[11px] px-2 py-0.5 rounded bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-50 cursor-pointer">
              批准进 prompt
            </button>
            <button onClick={() => decide(r.id, 'reject')} disabled={busy === r.id}
              className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text disabled:opacity-50 cursor-pointer">
              否决
            </button>
          </div>
        </div>
      ))}
      {active.length > 0 && (
        <details className="mt-1">
          <summary className="text-[11px] text-text-muted cursor-pointer">已生效的 {active.length} 条</summary>
          {active.map(r => (
            <div key={r.id} className="mt-1.5 text-[11px]">
              <span className="text-text-dim">【{r.title}】</span>
              <span className="text-text-muted"> {r.body}</span>
              <button onClick={() => decide(r.id, 'reject')} disabled={busy === r.id}
                title="从 prompt 里撤掉"
                className="ml-1 text-[10px] px-1 rounded border border-border text-text-muted hover:text-bear disabled:opacity-50 cursor-pointer">
                撤掉
              </button>
            </div>
          ))}
        </details>
      )}
      {msg && <p className="text-[11px] text-text-dim mt-1.5">{msg}</p>}
    </div>
  )
}


/* OKX 策略之外的现货余额。
   为什么不自动建仓: 余额只给数量, 给不出成本 —— 凭空建一笔成本会让盈亏从第一天就错。
   所以这里只列差异, 建/更新点一下确认(建仓成本按当前市值入账, 之后可以自己改)。
   为什么只取"可用+资金账户": 交易账户余额里的 frozenBal 是被网格/马丁占用的钱, 已经
   作为 BOT 资产在跟踪了, 再算一遍就是同一笔钱数两遍(实测 BTC 99.97% 都是 frozen)。 */
function OkxSpotSection() {
  const [d, setD] = useState(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')

  const load = () => fetchJSON('/api/assets/okx/spot')
    .then(setD).catch(e => setMsg(String(e.message || e)))
  useEffect(() => { load() }, [])

  const sync = async (row) => {
    setBusy(row.ccy); setMsg('')
    try {
      if (row.action === 'create') {
        await fetchJSON('/api/assets', {
          method: 'POST',
          body: JSON.stringify({
            asset_type: 'CRYPTO', code: `${row.ccy}-USDT`, name: `${row.ccy} 现货`,
            platform: 'OKX', shares: row.qty,
            // 成本未知 → 以当前市值入账, 盈亏从同步这一刻起算
            cost_amount: row.cny,
            note: 'OKX 现货同步(成本按同步时市值入账, 可自行修正)',
          }),
        })
      } else {
        await fetchJSON(`/api/assets/${row.tracked_id}`, {
          method: 'PUT', body: JSON.stringify({ shares: row.qty }),
        })
      }
      setMsg(`${row.ccy} 已${row.action === 'create' ? '建仓' : '更新数量'}`)
      await load()
    } catch (e) { setMsg(String(e.message || e)) } finally { setBusy('') }
  }

  const rows = d?.spot || []
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="text-[12px] text-text-dim font-semibold">
          OKX 策略之外的现货
          {d && <span className="ml-2 text-[11px] font-normal text-text-muted">
            可同步 {rows.length} 种{d.dust?.n ? ` · 尘埃 ${d.dust.n} 种已忽略` : ''}
          </span>}
        </label>
        <button onClick={load}
          className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text cursor-pointer">
          重新读取
        </button>
      </div>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        只读 <code className="bg-surface-3 px-1 rounded">可用余额 + 资金账户</code>，
        <b>不含被策略冻结的部分</b>——那笔钱已经作为机器人资产在跟踪，重复计入会让总资产虚高。
        余额只有数量没有成本，所以建仓/更新需要你点一下确认。
      </p>
      {rows.length === 0 && d && (
        <p className="text-[11px] text-text-muted">策略之外没有可同步的现货（低于 1 美元的尘埃已忽略）。</p>
      )}
      {rows.map(r => (
        <div key={r.ccy} className="flex items-center gap-2 mb-1.5 text-[11.5px]">
          <span className="font-mono text-text-bright w-14">{r.ccy}</span>
          <span className="font-mono text-text-dim w-32">{r.qty}</span>
          <span className="font-mono text-text-muted w-20">¥{r.cny}</span>
          <span className="text-text-muted">
            {r.action === 'create' ? '看板未跟踪'
              : r.action === 'ok' ? '数量一致'
              : `看板 ${r.tracked_shares} → 差 ${r.qty_diff}`}
          </span>
          {r.action !== 'ok' && (
            <button onClick={() => sync(r)} disabled={busy === r.ccy}
              className="ml-auto text-[11px] px-2 py-0.5 rounded bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-50 cursor-pointer">
              {busy === r.ccy ? '同步中…' : (r.action === 'create' ? '按现价建仓' : '更新数量')}
            </button>
          )}
        </div>
      ))}
      {(d?.frozen || []).length > 0 && (
        <details className="mt-2">
          <summary className="text-[11px] text-text-muted cursor-pointer">
            被策略冻结 ¥{((d.frozen_usd || 0) * (d.usdcny || 7.2)).toFixed(0)}（已在机器人资产里，不重复计）
          </summary>
          {d.frozen.map(f => (
            <div key={f.ccy} className="text-[11px] text-text-dim mt-1 font-mono">
              {f.ccy} {f.qty} · ¥{f.cny}
            </div>
          ))}
          {d.strategy_idle && (
            <div className="text-[11px] text-warn mt-1.5 leading-snug">
              其中约 ¥{d.strategy_idle.cny} 是策略<b>预留但还没投进去</b>的现金
              （冻结 {d.frozen_usd} USD − 两个策略市值 {d.bot_value_usd} USD）——
              这部分是你的钱，但看板目前没算进总资产。
            </div>
          )}
        </details>
      )}
      {msg && <p className="text-[11px] text-text-dim mt-1.5">{msg}</p>}
    </div>
  )
}
