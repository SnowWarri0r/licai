import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { MiniMarkdown } from './askShared'

// 盯盘员当天的发现(services/watcher.py): 固定在内容区顶部, 逐条可收起。没有发现时不占位。
// 深挖(services/watch_dig.py): 查到补充的挂在那条发现下; 查过没补充的不显示; 可手动点「查原因」。
// 自检(services/watch_health.py): 有异常(检测器出错、该跑没跑、当日数据没生成)时置顶一条; 「运行状态」展开逐个检测器。
const OUTCOME = { ok: '跑成', skipped: '跳过', error: '出错' }
const hhmm = (iso) => (iso ? iso.slice(5, 16).replace('T', ' ') : '—')
const digging = (f) => f.dig_status === 'queued' || f.dig_status === 'running'
const canDig = (f) => f.diggable && ['', 'skipped', 'failed', 'interrupted'].includes(f.dig_status || '')

export default function WatchFindings() {
  const [items, setItems] = useState([])
  const [fold, setFold] = useState({})              // id → 深挖结论收起
  const [health, setHealth] = useState(null)
  const [showHealth, setShowHealth] = useState(false)

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

  const dismiss = (id) => {
    setItems(xs => xs.filter(x => x.id !== id))
    fetchJSON(`/api/watch/findings/${id}/dismiss`, { method: 'POST' }).catch(load)
  }

  const dig = (id) => {
    setItems(xs => xs.map(x => x.id === id ? { ...x, dig_status: 'queued' } : x))
    fetchJSON(`/api/watch/findings/${id}/dig`, { method: 'POST' }).then(load).catch(load)
  }

  const problems = health?.problems || []
  if (!items.length && !problems.length) return null
  return (
    <div className="mx-4 mt-3 flex flex-col gap-1.5" style={{ animation: 'fade-up 0.3s ease-out' }}>
      {problems.length > 0 && (
        <div className="px-3 py-2 rounded-lg border bg-bear/8 border-bear/30">
          <div className="text-[12.5px] font-medium text-text">
            <span className="text-[10.5px] text-text-muted mr-1.5">盯盘员自检</span>{problems.length} 处没正常运行, 这些检查今天的结果不可信
          </div>
          {problems.map((p, i) => (
            <div key={i} className="text-[11.5px] text-text-dim mt-0.5"><span className="text-text-muted">{p.who}: </span>{p.text}</div>
          ))}
        </div>
      )}
      {items.map(f => (
        <div key={f.id}
          className={`px-3 py-2 rounded-lg border flex items-start justify-between gap-3 ${
            f.level === 'warn' ? 'bg-accent/10 border-accent/30' : 'bg-surface border-border'}`}>
          <div className="min-w-0 flex-1">
            <div className="text-[12.5px] font-medium text-text">
              <span className="text-[10.5px] text-text-muted mr-1.5">盯盘员</span>{f.title}
            </div>
            {f.body && <div className="text-[11.5px] text-text-dim whitespace-pre-line mt-0.5">{f.body}</div>}
            {digging(f) && (
              <div className="text-[10.5px] text-text-muted mt-1 flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />正在查原因, 查到补充会显示在这里
              </div>
            )}
            {f.dig_status === 'done' && f.dig_result && (
              <div className="mt-1.5 pl-2 border-l-2 border-accent/40">
                <button onClick={() => setFold(s => ({ ...s, [f.id]: !s[f.id] }))}
                  className="text-[10.5px] text-text-muted hover:text-text cursor-pointer">
                  查到的原因 {fold[f.id] ? '展开' : '收起'}
                </button>
                {!fold[f.id] && <div className="text-[11.5px]"><MiniMarkdown text={f.dig_result} /></div>}
              </div>
            )}
          </div>
          <div className="flex gap-1.5 shrink-0">
            {canDig(f) && (
              <button onClick={() => dig(f.id)} title="派一个子 agent 查这条提醒背后的原因, 查不出就不显示"
                className="text-[11px] px-2 py-0.5 rounded border border-border text-text-muted hover:text-text cursor-pointer">
                查原因
              </button>
            )}
            <button onClick={() => dismiss(f.id)}
              className="text-[11px] px-2 py-0.5 rounded border border-border text-text-muted hover:text-text cursor-pointer">
              收起
            </button>
          </div>
        </div>
      ))}
      <div>
        <button onClick={() => setShowHealth(v => !v)} className="text-[10.5px] text-text-muted hover:text-text cursor-pointer">
          盯盘员运行状态 {showHealth ? '收起' : '展开'}
        </button>
        {showHealth && health && (
          <table className="mt-1 w-full text-[11px] text-text-dim">
            <thead><tr className="text-text-muted text-left">
              <th className="font-normal py-0.5 pr-3">检查</th><th className="font-normal pr-3">时段</th>
              <th className="font-normal pr-3">最近一次</th><th className="font-normal pr-3">结果</th><th className="font-normal">说明</th>
            </tr></thead>
            <tbody>
              {health.detectors.map(d => (
                <tr key={d.detector} className={d.problem ? 'text-bear' : ''}>
                  <td className="py-0.5 pr-3 whitespace-nowrap">{d.label}</td>
                  <td className="pr-3 whitespace-nowrap font-mono">{d.window}</td>
                  <td className="pr-3 whitespace-nowrap font-mono">{hhmm(d.last_at)}</td>
                  <td className="pr-3 whitespace-nowrap">{OUTCOME[d.outcome] || '还没跑过'}{d.outcome === 'ok' && d.n_new ? ` · 新 ${d.n_new} 条` : ''}</td>
                  <td>{d.problem || [d.note, d.alert].filter(Boolean).join('; ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
