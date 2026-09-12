import { useEffect, useState } from 'react'
import { api } from '../../hooks/useApi'

export function LLMConfigSection() {
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
