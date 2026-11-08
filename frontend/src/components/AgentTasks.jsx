import { useState } from 'react'
import { MiniMarkdown, SourcesBlock } from './askShared'

// 主 agent 派出的子任务(services/agent_tasks.py)。
// TaskStrip: 输入框上方的任务条 —— 在跑的逐个列出(步数/当前步骤/取消), 跑完的收进「已完成」可展开看结论。
// TaskCard: 后台任务跑完后进对话的结果卡片(与历史里 role=task 的消息同一样式)。

const STATUS = {
  scheduled: '已预约', queued: '排队中', running: '运行中', done: '已完成', failed: '失败', cancelled: '已取消', interrupted: '服务重启中断',
}
export const isActive = (t) => t.status === 'queued' || t.status === 'running'
const isScheduled = (t) => t.status === 'scheduled'

export function TaskStrip({ tasks, onCancel }) {
  const [open, setOpen] = useState(null)        // 展开看结论的那一个 task_id
  const [showDone, setShowDone] = useState(false)
  if (!tasks?.length) return null
  const active = tasks.filter(isActive)
  const booked = tasks.filter(isScheduled).sort((a, b) => (a.run_at < b.run_at ? -1 : 1))
  const done = tasks.filter(t => !isActive(t) && !isScheduled(t))
  return (
    <div className="mb-2 rounded-lg border border-border bg-surface-3/60 px-3 py-2 text-[11px]">
      <div className="flex items-center gap-2 text-text-muted">
        <span className="text-text-dim font-medium">子任务</span>
        {active.length > 0 && <span>{active.length} 个在跑</span>}
        {booked.length > 0 && <span>{booked.length} 个已预约</span>}
        {done.length > 0 && (
          <button onClick={() => setShowDone(v => !v)} className="hover:text-text">
            已结束 {done.length} 个 {showDone ? '收起' : '展开'}
          </button>
        )}
      </div>
      {[...active, ...booked, ...(showDone ? done.slice().reverse() : [])].map(t => (
        <div key={t.task_id} className="mt-1.5">
          <div className="flex items-center gap-2">
            {isScheduled(t)
              ? <span className="w-1.5 h-1.5 rounded-full border border-accent shrink-0" />
              : isActive(t)
              ? <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse shrink-0" />
              : <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${t.status === 'done' ? 'bg-bull' : 'bg-text-muted'}`} />}
            <span className="text-text truncate">{t.title}</span>
            <span className="text-text-muted shrink-0">
              {t.run_at ? `预约 ${t.run_at.slice(5)}` : t.background ? '后台' : '并行'} · {STATUS[t.status] || t.status}
            </span>
            {isActive(t) && t.steps > 0 && (
              <span className="text-text-muted truncate">已调用 {t.steps} 个工具{t.last_step ? ` · ${t.last_step}` : ''}</span>
            )}
            <span className="flex-1" />
            {isActive(t) || isScheduled(t)
              ? <button onClick={() => onCancel(t.task_id)} className="text-text-muted hover:text-bear-bright shrink-0">取消</button>
              : t.result && (
                <button onClick={() => setOpen(o => o === t.task_id ? null : t.task_id)} className="text-text-muted hover:text-text shrink-0">
                  {open === t.task_id ? '收起' : '看结论'}
                </button>)}
          </div>
          {open === t.task_id && t.result && (
            <div className="mt-1.5 ml-3.5 pl-2 border-l border-border-subtle max-h-[40vh] overflow-y-auto">
              <MiniMarkdown text={t.result} sources={t.sources} />
            </div>
          )}
          {t.error && !isActive(t) && <div className="ml-3.5 text-bull-bright">{t.error}</div>}
        </div>
      ))}
    </div>
  )
}

export function TaskCard({ task }) {
  const [fold, setFold] = useState(false)
  return (
    <div className="px-3 py-2.5 rounded-lg bg-surface-3/70 border border-border">
      <div className="flex items-center gap-2 mb-1.5 text-[11px]">
        <span className="text-text-muted">后台任务完成</span>
        <span className="text-text-bright font-medium">{task.title}</span>
        <span className="flex-1" />
        <button onClick={() => setFold(v => !v)} className="text-text-muted hover:text-text">{fold ? '展开' : '收起'}</button>
      </div>
      {!fold && <>
        <MiniMarkdown text={task.result || ''} sources={task.sources} />
        <SourcesBlock sources={task.sources} />
      </>}
    </div>
  )
}

// 一问下面挂着的执行中补充: 已送达 / 排队中(可撤回) / 未送达
export function InterjectList({ items, onWithdraw }) {
  if (!items?.length) return null
  return items.map(x => (
    <div key={x.id} className="mt-1 flex items-center gap-2">
      <div className="text-[11.5px] text-text-dim bg-surface-3/60 border border-border-subtle rounded-lg px-2.5 py-1 inline-block">
        <span className="text-text-muted mr-1.5">补充</span>{x.text}
      </div>
      <span className="text-[10px] text-text-muted">
        {x.state === 'merged' ? '已送达' : x.state === 'queued' ? '排队中, 下一步送达' : '未送达'}
      </span>
      {x.state === 'queued' && (
        <button onClick={() => onWithdraw(x)} className="text-[10px] text-text-muted hover:text-bear-bright">撤回</button>
      )}
    </div>
  ))
}
