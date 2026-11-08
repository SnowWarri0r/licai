import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { MiniMarkdown } from './askShared'

// 盯盘员(services/watcher.py): 顶栏一个按钮, 点开是盖在内容上的面板, 不占内容区。
// 按钮上的数字 = 没看过的发现; 有需留意(warn)的没看过就高亮; 自检有异常(services/watch_health.py)加红点。
// 每条发现只显示标题和一句结论(summary), 明细(body)与查到的原因(services/watch_dig.py)点开才展开。
const OUTCOME = { ok: '跑成', skipped: '跳过', error: '出错' }
const TAG = {
  fresh_buy: '新买入', risk_hits: '排雷', tail_pull: '尾盘', sealed_close: '封板', unlock_soon: '解禁',
  fund_est_miss: '基金估值', fund_drop: '基金跌幅', books_drift: '对账', scheduled: '预约', notice: '公告', vwap_break: '均价线',
}
const SEEN_KEY = 'watch_seen_ids'
const hhmm = (iso) => (iso ? iso.slice(5, 16).replace('T', ' ') : '—')
const digging = (f) => f.dig_status === 'queued' || f.dig_status === 'running'
const canDig = (f) => f.diggable && ['', 'skipped', 'failed', 'interrupted'].includes(f.dig_status || '')
const firstLine = (s) => (s || '').split('\n').find(l => l.trim()) || ''
const plain = (s) => s.replace(/\*\*|__|`|^#+\s*/g, '').replace(/^结论[:：]\s*/, '').replace(/\[(实测|联网)[^\]]*\]/g, '')

function readSeen() {
  try { return new Set(JSON.parse(localStorage.getItem(SEEN_KEY) || '[]')) } catch { return new Set() }
}

function Finding({ f, onDig, onDismiss }) {
  const [detail, setDetail] = useState(false)
  const [why, setWhy] = useState(false)
  const watch = f.title.startsWith('观察池 ')
  const title = watch ? f.title.slice(4) : f.title
  const summary = f.summary || firstLine(f.body)
  const rest = f.summary ? f.body : (f.body || '').split('\n').slice(1).join('\n')
  return (
    <div className="px-3 py-2.5 border-b border-border-subtle last:border-b-0">
      <div className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${f.level === 'warn' ? 'bg-accent' : 'bg-text-muted'}`} />
        <span className="text-[10px] px-1.5 rounded border border-border text-text-muted shrink-0">{TAG[f.detector] || f.detector}</span>
        {watch && <span className="text-[10px] px-1.5 rounded border border-border text-text-muted shrink-0">观察池</span>}
        <span className="text-[12.5px] font-medium text-text-bright truncate">{title}</span>
        <span className="flex-1" />
        {canDig(f) && (
          <button onClick={() => onDig(f.id)} title="派一个子 agent 查这条提醒背后的原因, 查不出就不显示"
            className="text-[10.5px] text-text-muted hover:text-text cursor-pointer shrink-0">查原因</button>
        )}
        <button onClick={() => onDismiss(f.id)} className="text-[10.5px] text-text-muted hover:text-text cursor-pointer shrink-0 ml-2">收起</button>
      </div>
      {summary && <div className="text-[12px] text-text mt-1 pl-3">{summary}</div>}
      <div className="pl-3 mt-1 flex items-center gap-3 text-[10.5px] text-text-muted">
        {rest && <button onClick={() => setDetail(v => !v)} className="hover:text-text cursor-pointer">{detail ? '收起明细' : '明细'}</button>}
        {digging(f) && <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />正在查原因</span>}
      </div>
      {detail && rest && <div className="pl-3 mt-1 text-[11.5px] text-text-dim whitespace-pre-line">{rest}</div>}
      {f.dig_status === 'done' && f.dig_result && (
        <div className="ml-3 mt-1.5 pl-2 border-l-2 border-accent/40">
          <button onClick={() => setWhy(v => !v)} className="text-left text-[11.5px] text-text-dim hover:text-text cursor-pointer">
            <span className="text-accent mr-1">查到的原因</span>
            {!why && <span className="line-clamp-2">{plain(firstLine(f.dig_result))}</span>}
          </button>
          {why && <div className="text-[11.5px] mt-0.5"><MiniMarkdown text={f.dig_result} /></div>}
        </div>
      )}
    </div>
  )
}

export default function WatchFindings() {
  const [items, setItems] = useState([])
  const [health, setHealth] = useState(null)
  const [open, setOpen] = useState(false)
  const [showHealth, setShowHealth] = useState(false)
  const [seen, setSeen] = useState(readSeen)

  const load = () => {
    fetchJSON('/api/watch/findings').then(d => setItems(d.items || [])).catch(() => {})
    fetchJSON('/api/watch/health').then(setHealth).catch(() => {})
  }
  const busy = items.some(digging)
  useEffect(() => {
    load()
    const t = setInterval(load, (busy ? 15 : 5 * 60) * 1000)   // 有在查的就勤一点拉
    return () => clearInterval(t)
  }, [busy])

  // 打开面板 = 当前这些都看过了
  useEffect(() => {
    if (!open || !items.length) return
    const s = new Set([...seen, ...items.map(x => x.id)])
    setSeen(s)
    try { localStorage.setItem(SEEN_KEY, JSON.stringify([...s].slice(-300))) } catch { /* 存不下就算了 */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, items])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const dismiss = (id) => {
    setItems(xs => xs.filter(x => x.id !== id))
    fetchJSON(`/api/watch/findings/${id}/dismiss`, { method: 'POST' }).catch(load)
  }
  const dig = (id) => {
    setItems(xs => xs.map(x => x.id === id ? { ...x, dig_status: 'queued' } : x))
    fetchJSON(`/api/watch/findings/${id}/dig`, { method: 'POST' }).then(load).catch(load)
  }

  const problems = health?.problems || []
  const unseen = items.filter(x => !seen.has(x.id))
  const hot = unseen.some(x => x.level === 'warn')

  return (
    <>
      <button onClick={() => setOpen(v => !v)} title="盯盘员: 持仓与观察池的提醒"
        className={`relative px-3 py-1 rounded-md border transition-colors cursor-pointer ${
          hot ? 'border-accent/50 text-accent bg-accent/10' : 'border-border text-text-dim hover:text-text hover:border-text-muted'}`}>
        盯盘员{items.length > 0 && <span className="ml-1 font-mono">{unseen.length || items.length}</span>}
        {problems.length > 0 && <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-bear" />}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-[140]" onClick={() => setOpen(false)} />
          <div className="fixed right-3 top-12 z-[150] w-[min(580px,94vw)] max-h-[75vh] overflow-y-auto rounded-xl border border-border bg-surface-2 shadow-2xl"
            style={{ animation: 'fade-up 0.2s ease-out' }}>
            <div className="sticky top-0 bg-surface-2 px-3 py-2 border-b border-border flex items-baseline gap-2">
              <span className="text-[13px] font-semibold text-text-bright">盯盘员</span>
              <span className="text-[10.5px] text-text-muted">今天 {items.length} 条 · 只陈述事实与历史同类, 买卖你定</span>
              <span className="flex-1" />
              <button onClick={() => setOpen(false)} className="text-text-dim hover:text-text text-[16px] leading-none">×</button>
            </div>
            {problems.length > 0 && (
              <div className="px-3 py-2 bg-bear/8 border-b border-bear/30">
                <div className="text-[11.5px] text-text">自检: {problems.length} 项检查今天没正常运行, 结果不可信</div>
                {problems.map((p, i) => (
                  <div key={i} className="text-[11px] text-text-dim mt-0.5"><span className="text-text-muted">{p.who}: </span>{p.text}</div>
                ))}
              </div>
            )}
            {items.length === 0
              ? <div className="px-3 py-4 text-[11.5px] text-text-muted">今天还没有提醒</div>
              : items.map(f => <Finding key={f.id} f={f} onDig={dig} onDismiss={dismiss} />)}
            <div className="px-3 py-2 border-t border-border">
              <button onClick={() => setShowHealth(v => !v)} className="text-[10.5px] text-text-muted hover:text-text cursor-pointer">
                运行状态 {showHealth ? '收起' : '展开'}
              </button>
              {showHealth && health && (
                <table className="mt-1 w-full text-[11px] text-text-dim">
                  <thead><tr className="text-text-muted text-left">
                    <th className="font-normal py-0.5 pr-2">检查</th><th className="font-normal pr-2">最近一次</th>
                    <th className="font-normal pr-2">结果</th><th className="font-normal">说明</th>
                  </tr></thead>
                  <tbody>
                    {health.detectors.map(d => (
                      <tr key={d.detector} className={d.problem ? 'text-bear' : ''}>
                        <td className="py-0.5 pr-2 whitespace-nowrap" title={d.window}>{d.label}</td>
                        <td className="pr-2 whitespace-nowrap font-mono">{hhmm(d.last_at)}</td>
                        <td className="pr-2 whitespace-nowrap">{OUTCOME[d.outcome] || '还没跑过'}{d.outcome === 'ok' && d.n_new ? ` · 新 ${d.n_new}` : ''}</td>
                        <td>{d.problem || [d.note, d.alert].filter(Boolean).join('; ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </>
      )}
    </>
  )
}
