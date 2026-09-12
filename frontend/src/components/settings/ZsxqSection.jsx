import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'

/* 知识星球接入(可选, 只读)。
   走官方 MCP 端点(https://mcp.zsxq.com/topic/mcp?api_key=...), 不用装 npm 包也不碰 Keychain。
   URL 里带 api_key: 存 DB(不进 config.py)、前端只回显脱敏后的 host+path、日志也只打脱敏值。
   只读白名单在 services/zsxq_client.py 的 _READ_TOOLS —— 远端那些 create_/set_ 写口不接。 */
export function ZsxqSection() {
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
