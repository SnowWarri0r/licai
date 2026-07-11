const BASE = ''

// 数据变更广播: 持仓/资产的写操作成功后通知所有关心的组件(顶部 Dashboard 等)
// 立即重拉, 免得各自的轮询周期造成"改完了顶条还是旧数"的观感。
export const MUTATED_EVENT = 'licai:data-mutated'

export async function fetchJSON(path, options) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await res.json().catch(() => ({}))
  if (res.ok) {
    const method = (options?.method || 'GET').toUpperCase()
    if (method !== 'GET' && /^\/api\/(assets|portfolio)/.test(path)) {
      try { window.dispatchEvent(new CustomEvent(MUTATED_EVENT, { detail: path })) } catch {}
    }
  }
  if (!res.ok) {
    // FastAPI/Pydantic 422: {detail: [{loc, msg, type}, ...]}
    // FastAPI HTTPException: {detail: "..."}
    let msg = `HTTP ${res.status}`
    if (data.detail) {
      if (typeof data.detail === 'string') msg = data.detail
      else if (Array.isArray(data.detail)) {
        msg = data.detail.map(d => `${(d.loc || []).slice(-1)[0] || ''}: ${d.msg}`).join('; ')
      } else msg = JSON.stringify(data.detail)
    }
    throw new Error(msg)
  }
  return data
}

export const api = {
  // Portfolio
  getPortfolio: () => fetchJSON('/api/portfolio'),
  addHolding: (data) => fetchJSON('/api/portfolio', { method: 'POST', body: JSON.stringify(data) }),
  updateHolding: (code, data) => fetchJSON(`/api/portfolio/${code}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteHolding: (code) => fetchJSON(`/api/portfolio/${code}`, { method: 'DELETE' }),

  // Settings
  getFeishuConfig: () => fetchJSON('/api/settings/feishu'),
  saveFeishuConfig: (url) => fetchJSON('/api/settings/feishu', { method: 'POST', body: JSON.stringify({ webhook_url: url }) }),
  testFeishu: () => fetchJSON('/api/settings/feishu/test', { method: 'POST' }),

  // LLM Config
  getLLMConfig: () => fetchJSON('/api/settings/llm'),
  saveLLMConfig: (data) => fetchJSON('/api/settings/llm', { method: 'POST', body: JSON.stringify(data) }),
  testLLM: () => fetchJSON('/api/settings/llm/test', { method: 'POST' }),

  getProxy: () => fetchJSON('/api/settings/proxy'),
  saveProxy: (proxy) => fetchJSON('/api/settings/proxy', { method: 'POST', body: JSON.stringify({ proxy }) }),
  detectProxy: () => fetchJSON('/api/settings/proxy/detect', { method: 'POST' }),
  testProxy: (proxy) => fetchJSON('/api/settings/proxy/test', { method: 'POST', body: JSON.stringify({ proxy }) }),
}
