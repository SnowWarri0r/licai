import { useState, useEffect } from 'react'
import { postInbox, withdrawInbox, listTasks } from './askShared'
import { isActive } from './AgentTasks'

// 「问问市场」页与个股抽屉共用的对话结构。
// history = 问答轮次 + 后台任务的结果卡片 {kind:'task', task}; 流式事件只往最后一个问答轮次上写。
// 轮次的 interjects = 这一轮在跑时用户补充的话 [{id, text, state: queued|merged|lost, runId}]。

export const lastTurn = (h) => { for (let i = h.length - 1; i >= 0; i--) if (h[i].kind !== 'task') return i; return -1 }
export const newTurn = (q, images = []) => ({ q, images, steps: [], thought: '', answer: null, typed: '', done: false, sources: [], charts: [], interjects: [] })

// 库里的会话 → 对话。执行中补充的消息挂到它那一问下面; role=task 是后台任务的结果卡片。
export function turnsFromMessages(msgs) {
  const out = []
  for (const m of msgs || []) {
    const meta = m.meta || {}
    if (m.role === 'task') { out.push({ kind: 'task', task: { task_id: meta.task_id, title: meta.title, result: m.content, sources: meta.sources || [] } }); continue }
    if (m.role === 'user' && meta.interject) {
      const i = lastTurn(out)
      if (i >= 0) out[i].interjects.push({ id: m.id, text: m.content, state: meta.state === 'merged' ? 'merged' : 'lost' })
      continue
    }
    if (m.role === 'user') { out.push({ ...newTurn(m.content, meta.images || []), done: true }); continue }
    const i = lastTurn(out)
    if (i >= 0) { const t = out[i]; t.answer = m.content; t.typed = m.content; t.sources = meta.sources || []; t.charts = meta.charts || [] }
  }
  return out
}

// 带给后端的上下文: 最近 4 轮已完成的问答, 执行中补充的话并进那一问
export function histForAgent(history) {
  return history.filter(it => it.kind !== 'task' && it.answer && !it.err).slice(-4)
    .flatMap(it => [{ role: 'user', content: it.q + (it.interjects || []).filter(x => x.state === 'merged').map(x => `\n(执行中补充: ${x.text})`).join('') },
                    { role: 'assistant', content: it.answer }])
}

// 把一句话投进正在跑的那一轮的收件箱。这一轮恰好在收尾(closed)就等它那根连接结束再看:
// 服务端可能已把更早的补充续成了下一轮(调用方 onEnd 里接着跟看, getRunId 会变), 那就投给下一轮。
// 返回 {id, runId} = 已排队; null = 没有在跑的轮次可投, 调用方开新一轮。
export async function interject(getRunId, ended, text) {
  for (let i = 0; i < 3 && getRunId(); i++) {
    const runId = getRunId()
    let r
    try { r = await postInbox(runId, text) } catch { r = { status: 'closed' } }
    if (r.status === 'queued') return { id: r.id, runId }
    await Promise.race([ended(), new Promise(res => setTimeout(res, 5000))])
    await new Promise(res => setTimeout(res, 50))      // onEnd 里接着跟看下一轮是同步的, 让它先落定
  }
  return null
}

export async function withdrawFrom(setHistory, x, fallbackRunId) {
  if (await withdrawInbox(x.runId || fallbackRunId, x.id))
    setHistory(h => h.map(it => it.interjects ? { ...it, interjects: it.interjects.filter(y => y.id !== x.id) } : it))
}

// 流式事件里与补充相关的两种: interject = 交给 agent 了; next_run = 答完才到的补充被合成下一轮。
// 处理了返回 true。next_run 的 {run_id, question} 交给 onNext, 调用方在这一轮连接结束时接着跟看。
export function handleInterjectEv(ev, patchLast, onNext) {
  if (ev.type === 'interject') {
    const ids = new Set(ev.ids || [])
    patchLast(it => ({ ...it, interjects: (it.interjects || []).map(x => ids.has(x.id) ? { ...x, state: 'merged' } : x) }))
    return true
  }
  if (ev.type === 'next_run') {
    const ids = new Set(ev.ids || [])
    onNext({ run_id: ev.run_id, question: ev.question })
    patchLast(it => ({ ...it, interjects: (it.interjects || []).filter(x => !ids.has(x.id)) }))
    return true
  }
  return false
}

// 本会话的子任务轮询: 有在跑的(或这一轮还在跑, 可能刚派出)每 4 秒, 否则 20 秒。
// 后台任务跑完 → 结果卡片接进对话。sessionId / historyRef 是 ref, busy() 返回当前是否有轮次在跑。
export function useSessionTasks(sessionId, historyRef, setHistory, busy) {
  const [tasks, setTasks] = useState([])
  useEffect(() => {
    let alive = true, timer = null
    const tick = async () => {
      const sid = sessionId.current
      const ts = sid ? await listTasks(sid) : []
      if (!alive) return
      if (ts) {
        setTasks(ts)
        const have = new Set(historyRef.current.filter(x => x.kind === 'task').map(x => x.task.task_id))
        const fresh = ts.filter(t => t.background && t.status === 'done' && !have.has(t.task_id))
        if (fresh.length && sid === sessionId.current) setHistory(h => [...h, ...fresh.map(t => ({ kind: 'task', task: t }))])
      }
      timer = setTimeout(tick, busy() || (ts || []).some(isActive) ? 4000 : 20000)
    }
    tick()
    return () => { alive = false; clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return [tasks, setTasks]
}
