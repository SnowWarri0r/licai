import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'

/* 开盘啦登录态(可选)。填手机登录 App 时那条登录响应里的 UserID + Token(长期有效),
   解锁深度龙虎榜游资标签等登录态接口。Token 存 DB(不进代码库), 界面只回显 UID + 是否有效,
   不回显 Token 明文。失效了(改密码/被踢)在这重填即可。 */
export function KplSection() {
  const [st, setSt] = useState(null)     // {configured, valid, uid, note}
  const [uid, setUid] = useState('')
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState({ text: '', ok: null })

  const load = () => fetchJSON('/api/settings/kpl').then(setSt).catch(() => {})
  useEffect(() => { load() }, [])

  const save = async () => {
    if (!uid.trim() || !token.trim()) { setMsg({ text: 'UID 和 Token 都要填', ok: false }); return }
    setBusy('save'); setMsg({ text: '', ok: null })
    try {
      const r = await fetchJSON('/api/settings/kpl', {
        method: 'POST', body: JSON.stringify({ uid: uid.trim(), token: token.trim() }),
      })
      setMsg({ text: r.valid ? 'Token 有效, 已保存' : (r.note || '保存了但探活未通过'), ok: !!r.valid })
      setUid(''); setToken('')
      await load()
    } catch (e) { setMsg({ text: String(e?.message || e), ok: false }) } finally { setBusy('') }
  }
  const clear = async () => {
    setBusy('clear')
    try { await fetchJSON('/api/settings/kpl', { method: 'DELETE' }); setMsg({ text: '已清除', ok: null }); await load() }
    catch (e) { setMsg({ text: String(e?.message || e), ok: false }) } finally { setBusy('') }
  }

  return (
    <>
      <div className="flex items-center justify-between mb-2">
        <label className="text-[12px] text-text-dim font-semibold">开盘啦登录态（可选 · 深度龙虎榜）</label>
        <span className="text-[11px] font-mono text-text-muted">
          {!st ? '' : !st.configured ? '未配置' : st.valid ? `已登录 · UID ${st.uid}` : 'Token 失效'}
        </span>
      </div>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        用<span className="text-[var(--color-signal-moderate)]">你自己的开盘啦账号</span>解锁深度龙虎榜（席位带游资身份标签，比东财裸营业部名多一层）。
        在手机 App 登录时，那条登录成功的响应里有 <span className="font-mono">UserID</span> 和 <span className="font-mono">Token</span>，粘进来即可，长期有效。
        Token 存本机数据库、不进代码库、界面不回显明文。
      </p>
      <div className="flex items-center gap-2 mb-2">
        <input className="w-28 bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
          placeholder="UserID" value={uid} onChange={e => setUid(e.target.value)} autoComplete="off" spellCheck={false} />
        <input className="flex-1 bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
          placeholder="Token" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" spellCheck={false} />
        <button onClick={save} disabled={!!busy}
          className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer whitespace-nowrap">
          {busy === 'save' ? '验证中' : '保存并验证'}
        </button>
        {st?.configured && (
          <button onClick={clear} disabled={!!busy}
            className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[12px] hover:text-text disabled:opacity-40 cursor-pointer">清除</button>
        )}
      </div>
      {msg.text && (
        <span className={`text-[12px] font-medium break-all ${msg.ok === true ? 'text-bull' : msg.ok === false ? 'text-bear' : 'text-text-dim'}`}>
          {msg.text}
        </span>
      )}
    </>
  )
}
