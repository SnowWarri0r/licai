import { useEffect, useState } from 'react'
import { api } from '../../hooks/useApi'

const FORMATS = [
  { value: 'anthropic', label: 'Anthropic Messages', url: 'https://api.anthropic.com', path: '/v1/messages',
    map: '{"smart":"claude-opus-5-5","fast":"claude-sonnet-5-5"}' },
  { value: 'openai-responses', label: 'OpenAI Responses', url: 'https://api.openai.com/v1', path: '/v1/responses',
    map: '{"smart":"gpt-5","balanced":"gpt-5","fast":"gpt-5-mini"}' },
  { value: 'openai-chat', label: 'OpenAI Chat Completions', url: 'https://中转站域名/v1', path: '/v1/chat/completions',
    map: '{"smart":"deepseek-chat","balanced":"deepseek-chat","fast":"deepseek-chat"}' },
  { value: 'claude-code', label: '本机 Claude Code（订阅）', url: '', path: '',
    map: '{"smart":"claude-opus-5-5","fast":"claude-sonnet-5-5"}' },
  { value: 'codex', label: '本机 Codex（ChatGPT 订阅）', url: '', path: '',
    map: '{"smart":"gpt-6-astra","fast":"gpt-6-luna"}' },
]

export function LLMConfigSection() {
  const [apiFormat, setApiFormat] = useState('anthropic')
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
      const f = d.db_api_format || 'anthropic'
      setApiFormat(f)
      setBaseUrl(d.db_base_url || '')
      setDbHasKey(d.has_api_key)
      // OpenAI 协议下 x-api-key 是"没改过"的默认值, 后端按 Authorization: Bearer 发, 这里显示为空
      const h = d.db_api_key_header || 'x-api-key'
      setApiKeyHeader(f !== 'anthropic' && h === 'x-api-key' ? '' : h)
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
        api_format: apiFormat,
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

  const fmt = FORMATS.find(f => f.value === apiFormat) || FORMATS[0]
  const isOpenAI = apiFormat !== 'anthropic'
  const isCC = apiFormat === 'claude-code' || apiFormat === 'codex'

  return (
    <>
      <label className="text-[12px] text-text-dim font-semibold">LLM 配置</label>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        支持 Anthropic Messages、OpenAI Responses、OpenAI Chat Completions 三种协议，官方端点与中转站均可。
        选 Anthropic 且不填 key 时走 Anthropic 官方 + Keychain OAuth。选「本机 Claude Code」时由本机已登录的
        Claude Code 交互模式代为请求，使用它自己的订阅额度与联网搜索；选「本机 Codex」时用本机 Codex 的 ChatGPT 登录。联网搜索只在 Anthropic 官方端点与本机 Claude Code 下可用。
      </p>

      <div className="grid grid-cols-1 gap-2 mb-2">
        <div>
          <label className="text-[11px] text-text-muted">接口格式</label>
          <select value={apiFormat} aria-label="接口格式"
            onChange={e => {
              const v = e.target.value
              setApiFormat(v)
              if (v !== 'anthropic' && apiKeyHeader === 'x-api-key') setApiKeyHeader('')
              if (v === 'anthropic' && !apiKeyHeader) setApiKeyHeader('x-api-key')
            }}
            className="w-full bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text outline-none focus:border-accent">
            {FORMATS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
          <p className="text-[10px] text-text-muted mt-0.5">
            {apiFormat === 'claude-code'
              ? '需要本机已安装并登录 Claude Code（终端里运行过一次 claude）。每段对话占用一个后台 Claude Code 进程，空闲 10 分钟后回收'
              : apiFormat === 'codex'
              ? '需要本机 Codex 已用 ChatGPT 账号登录（codex login）。按 Codex CLI 的请求格式发到 ChatGPT 后端；不在映射里的 Claude 模型名改用 Codex 配置的默认模型；不支持联网搜索'
              : <>请求地址 = Base URL + {fmt.path}；Base URL 已以 /v1 结尾时不再重复拼接</>}
          </p>
        </div>

        {!isCC && <>

        <div>
          <label className="text-[11px] text-text-muted">API Base URL</label>
          <input
            className="w-full bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
            placeholder={fmt.url}
            value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
          />
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11px] text-text-muted">API Key Header</label>
            <input
              className="w-full bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
              placeholder={isOpenAI ? 'Authorization' : 'x-api-key'}
              value={apiKeyHeader} onChange={e => setApiKeyHeader(e.target.value)}
            />
          </div>
          <div>
            <label className="text-[11px] text-text-muted">API Key Prefix（如 Bearer）</label>
            <input
              className="w-full bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
              placeholder={isOpenAI ? '默认 Bearer' : '留空或填 Bearer'}
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
        </>}

        <div>
          <label className="text-[11px] text-text-muted">模型别名映射（JSON，可选）</label>
          <textarea rows={3}
            className="w-full bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent resize-none"
            placeholder={fmt.map}
            value={modelMap} onChange={e => setModelMap(e.target.value)}
          />
          <p className="text-[10px] text-text-muted mt-0.5">
            逻辑名 smart / balanced / fast → 实际模型名；也可直接映射完整模型名（如 "claude-opus-5-5": "gpt-5"），代码里写死的模型名会被改发到映射后的模型
          </p>
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
