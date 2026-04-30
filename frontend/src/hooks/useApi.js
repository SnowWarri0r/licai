const BASE = ''
const enc = (value) => encodeURIComponent(value)

export async function fetchJSON(path, options) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  return res.json()
}

export const api = {
  // Portfolio
  getPortfolio: () => fetchJSON('/api/portfolio'),
  addHolding: (data) => fetchJSON('/api/portfolio', { method: 'POST', body: JSON.stringify(data) }),
  updateHolding: (code, data) => fetchJSON(`/api/portfolio/${enc(code)}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteHolding: (code) => fetchJSON(`/api/portfolio/${enc(code)}`, { method: 'DELETE' }),

  // Settings
  getFeishuConfig: () => fetchJSON('/api/settings/feishu'),
  saveFeishuConfig: (url) => fetchJSON('/api/settings/feishu', { method: 'POST', body: JSON.stringify({ webhook_url: url }) }),
  testFeishu: () => fetchJSON('/api/settings/feishu/test', { method: 'POST' }),
}
