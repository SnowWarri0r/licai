import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { MiniMarkdown, DraftAnswer, draftText, sharedPrefix, markFailed, SourcesBlock, ToolCallStrip, startRun, startDeepDive, followRun, liveRuns, cancelRun,
         cancelTask } from './askShared'
import { TaskStrip, TaskCard, InterjectList } from './AgentTasks'
import { lastTurn, newTurn, turnsFromMessages, histForAgent, interject, withdrawFrom, handleInterjectEv, useSessionTasks } from './askTurns'
import ImageZoom from './ImageZoom'

function pctColor(v) {
  if (v > 0) return 'text-bear'
  if (v < 0) return 'text-bull'
  return 'text-text-dim'
}

// 抽屉一关就卸载, 对话跟着没了 —— 关一下再打开是空的, 追问的上下文也断了。所以按代码
// 把对话留在模块级(组件外, 不随卸载消失), 关开不丢; 同一只票接着问还是同一个会话(落库
// 也接着落在那条会话里)。只留最近几只, 免得把长对话无限攒在内存里。
// run 也记在这儿: 关抽屉时那一轮还在服务端跑(执行权不在浏览器), 重开照游标接着看。
// 注: 刷新整页会清掉这份内存缓存 —— 但 run 还活着, 重新打开抽屉会去服务端把它捞回来。
// 抽屉宽度: 可拖拽 + 记住。原来写死 520px —— 深挖那份输出里有多列表格(可验证的事实、同业
// PE/PB 排名), 520 下被横向截断, 要左右拖着看。默认调到 880, 但**不再由我拍一个数**:
// 左边缘可拖, 拖完记进 localStorage, 下次开还是这个宽度。
const DRAWER_W_KEY = 'ask_drawer_w'
const DRAWER_W_MIN = 420
const DRAWER_W_DEFAULT = 880

function readDrawerW() {
  const n = Number(localStorage.getItem(DRAWER_W_KEY))
  if (!n || Number.isNaN(n)) return DRAWER_W_DEFAULT
  return Math.min(Math.max(n, DRAWER_W_MIN), window.innerWidth - 40)
}

const DRAWER_CACHE = new Map()      // code -> { history, sessionId, run, nextRun }
const DRAWER_CACHE_MAX = 8

function remember(code, history, sessionId, run, nextRun = null) {
  if (!code) return
  DRAWER_CACHE.delete(code)                       // 删了再插: Map 按插入序, 这样它排到最后
  DRAWER_CACHE.set(code, { history, sessionId, run, nextRun })
  while (DRAWER_CACHE.size > DRAWER_CACHE_MAX) DRAWER_CACHE.delete(DRAWER_CACHE.keys().next().value)
}

// 个股 AI 分析弹窗: 多轮对话, 工具调用胶囊/正文/来源全部复用 askShared, 与"问问市场"样式一致。
// 与「问问市场」同一套交互(askTurns.js): 回答期间照样能补充, 子任务条与后台任务结果卡片。
// stock: {code, name, pct, 行业}; initialQuestion: 打开即自动问的第一句(可空)。
export default function StockAskModal({ stock, onClose, initialQuestion = '' }) {
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  // 结构见 askTurns.js
  const [history, setHistory] = useState(() => DRAWER_CACHE.get(stock?.code)?.history || [])
  const [shown, setShown] = useState(false)     // 滑入动画: 挂载后置 true → 从右侧划出
  const abortRef = useRef(null)
  const typer = useRef(null)
  const draftRef = useRef('')   // 本轮已收到的初稿全文(定稿时据此算公共前缀)
  const scrollBox = useRef(null)
  const follow = useRef(true)
  const started = useRef(false)
  const closeTimer = useRef(null)
  const sessionId = useRef(DRAWER_CACHE.get(stock?.code)?.sessionId ?? null)   // 落库用: 同一只票接着同一条会话
  const runRef = useRef(DRAWER_CACHE.get(stock?.code)?.run ?? null)            // {id, cursor}: 关了再开接着看
  const nextRunRef = useRef(DRAWER_CACHE.get(stock?.code)?.nextRun ?? null)    // 答完才到的补充续成的下一轮
  const endedRef = useRef(Promise.resolve())        // 当前跟看的那根连接结束时 resolve
  const scope = `stock:${stock?.code || ''}`

  // 先播放滑出动画再卸载
  const close = () => { setShown(false); clearTimeout(closeTimer.current); closeTimer.current = setTimeout(onClose, 280) }

  const patchAt = (idx, fn) => setHistory(h => h.map((it, i) => i === idx ? fn(it) : it))
  const patchLast = (fn) => setHistory(h => { const k = lastTurn(h); return h.map((it, i) => i === k ? fn(it) : it) })

  // 落库(与「问问市场」同一张表)现在归服务端: 问题在开跑那刻就写进会话, 答案跑完写。
  // 为什么非得落: 这个抽屉原来什么都不存, 于是在这儿发生的每一次纠正, 纠正挖掘都看不见 ——
  // 那套"答错了不再错第二遍"的机制对抽屉里的对话完全失效。事后想复盘也没有原文。
  // 而只要落库还挂在前端, "关抽屉"就等于"这一轮从没发生过"。

  // 打字机钉在开始时的那一轮上: 打字期间可能已经接着开了下一轮(补充续跑 / 新问)
  // 上一轮还没打完就来了下一轮的答案(补充续跑): 先把上一轮直接定格成完整答案, 不然停在半句
  const typingRef = useRef(null)
  const typewriter = (full, from = 0) => {
    clearInterval(typer.current)
    const prev = typingRef.current
    if (prev) patchAt(prev.idx, it => ({ ...it, typed: prev.full, done: true }))
    const idx = lastTurn(historyRef.current)
    typingRef.current = { idx, full }
    let n = from
    typer.current = setInterval(() => {
      n = Math.min(full.length, n + 3)
      patchAt(idx, it => ({ ...it, typed: full.slice(0, n) }))
      if (n >= full.length) { clearInterval(typer.current); typingRef.current = null; patchAt(idx, it => ({ ...it, done: true })) }
    }, 16)
  }

  const onScroll = () => {
    const el = scrollBox.current
    if (!el) return
    follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }
  const historyRef = useRef(history)
  useEffect(() => {
    const el = scrollBox.current
    if (el && follow.current) el.scrollTop = el.scrollHeight
    historyRef.current = history
    remember(stock?.code, history, sessionId.current, runRef.current, nextRunRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [history])

  const [tasks, setTasks] = useSessionTasks(sessionId, historyRef, setHistory, () => !!runRef.current)

  const handleEv = (ev) => {
    if (ev.type === 'step') patchLast(it => ({ ...it, steps: [...it.steps, { tool: ev.tool, label: ev.label, arg: ev.arg }] }))
    // 取数失败要回填到对应的 chip 上 —— step 是工具跑之前发的, 不回填的话失败的工具
    // 也会跟成功的一样打绿勾, 界面上看不出这一块其实没数
    if (ev.type === 'step_result' && ev.ok === false) patchLast(it => ({ ...it, steps: markFailed(it.steps, ev) }))
    else if (ev.type === 'chart') patchLast(it => ({ ...it, charts: [...it.charts, ev.url] }))
    else if (ev.type === 'sources') patchLast(it => ({ ...it, sources: [...it.sources, ...(ev.sources || [])] }))
    else if (ev.type === 'draft') { draftRef.current += ev.text || ''; patchLast(it => ({ ...it, draft: draftRef.current, stale: '' })) }
    // 自查要改时初稿不直接消失: 变淡留着并说明在重写, 新初稿或定稿到了再换掉
    else if (ev.type === 'draft_reset') {
      draftRef.current = ''
      const redo = ev.why === 'self_check'
      patchLast(it => ({ ...it, draft: '', stale: redo ? (it.draft || it.stale || '') : '',
                         staleNote: redo ? `自查发现 ${ev.n} 处要改, 正在重写` : '' }))
    }
    else if (ev.type === 'answer') {
      // 定稿接在初稿后面: 已经显示过的公共前缀直接落定, 打字机只打剩下的
      const full = ev.text || ''
      const from = sharedPrefix(draftText(draftRef.current), full)
      draftRef.current = ''
      patchLast(it => ({ ...it, answer: full, draft: '', stale: '', typed: full.slice(0, from) }))
      typewriter(full, from)
    }
    else if (ev.type === 'error') { draftRef.current = ''; patchLast(it => ({ ...it, err: ev.error, draft: '', stale: '', done: true })) }
    else handleInterjectEv(ev, patchLast, (nxt) => { nextRunRef.current = nxt })
  }

  // 最后一问的答案从库里补回来(服务端内存里已经没这条 run 了, 但落库是它跑完时就做的)。
  // 库里也没有 → 那一轮真没跑完(比如服务重启过), 标出来; 不能让它一直转"分析中"。
  const recoverTail = async () => {
    let last = null
    try {
      if (sessionId.current) {
        const s = await (await fetch(`/api/ask/sessions/${sessionId.current}`)).json()
        const turns = turnsFromMessages(s.messages)
        last = turns[lastTurn(turns)]
      }
    } catch { /* 取不到当没有 */ }
    patchLast(it => {
      if (it.answer) return { ...it, typed: it.answer, done: true }
      if (last?.answer) return { ...it, answer: last.answer, typed: last.answer, done: true,
                                 sources: last.sources.length ? last.sources : it.sources,
                                 charts: last.charts.length ? last.charts : it.charts }
      return { ...it, done: true, err: '这一轮没跑完(服务重启过)' }
    })
  }

  // 跟看一条 run。关抽屉只 abort 这根连接, run 在服务端照跑照落库。
  const attach = (runId, cursor = 0) => {
    abortRef.current?.abort()
    const ctrl = new AbortController(); abortRef.current = ctrl
    runRef.current = { id: runId, cursor }
    // 从头重放就从空初稿拼起; 断点续拉则接着页面上已有的初稿往后拼
    const h0 = historyRef.current
    draftRef.current = cursor ? (h0[lastTurn(h0)]?.draft || '') : ''
    setLoading(true)
    let ended
    endedRef.current = new Promise(r => { ended = r })
    followRun(runId, {
      cursor, signal: ctrl.signal,
      onEvent: (ev) => { if (ev.cursor != null && runRef.current) runRef.current.cursor = ev.cursor; handleEv(ev) },
      onEnd: ({ finished, gone }) => {
        ended()
        if (finished || gone) { runRef.current = null; setLoading(false) }
        // 答完之后才到的补充, 服务端已合成一问开了下一轮: 接着跟看那一轮
        const nxt = nextRunRef.current
        if (finished && nxt) {
          nextRunRef.current = null
          setHistory(h => [...h, newTurn(nxt.question)])
          attach(nxt.run_id, 0)
          return
        }
        if (gone) recoverTail()      // 服务端不记得它了 → 答案在库里, 回去取
      },
      onError: (err) => { ended(); patchLast(it => ({ ...it, err, done: true })); runRef.current = null; setLoading(false) },
    })
  }

  const stopRun = () => {
    if (runRef.current) cancelRun(runRef.current.id)
    abortRef.current?.abort(); clearInterval(typer.current); runRef.current = null; nextRunRef.current = null
    patchLast(it => ({ ...(it.answer == null ? { ...it, err: '已停止', done: true } : { ...it, typed: it.answer, done: true }),
                       interjects: (it.interjects || []).map(x => x.state === 'queued' ? { ...x, state: 'lost' } : x) }))
    setLoading(false)
  }

  // 这一轮在跑时发的话进它的收件箱, agent 做完当前这一步就会读到; 投不进(这一轮已收尾)就开新一轮。
  // 深挖那条链中途不读收件箱, 补充会在它跑完后合成下一问接着跑。
  const send = async () => {
    const text = q.trim()
    if (!runRef.current) return ask()
    if (!text) return
    setQ(''); follow.current = true
    const r = await interject(() => runRef.current?.id, () => endedRef.current, text)
    if (r) patchLast(it => ({ ...it, interjects: [...(it.interjects || []), { ...r, text, state: 'queued' }] }))
    else return ask(text, true)
  }

  const withdraw = (x) => withdrawFrom(setHistory, x, runRef.current?.id)

  const ask = async (question, force = false) => {
    const text = (question ?? q).trim()
    if (!text || (loading && !force) || !stock) return
    // 已完成轮次作为上下文, 支持追问
    const hist = histForAgent(historyRef.current)
    setQ(''); setLoading(true); follow.current = true
    setHistory(h => [...h, newTurn(text)])
    try {
      const r = await startRun({
        question: text, agent_question: `${stock.name}(${stock.code}): ${text}`, history: hist,
        session_id: sessionId.current, title: `${stock.name}(${stock.code}) ${text}`.slice(0, 40), scope,
      })
      sessionId.current = r.session_id
      attach(r.run_id, 0)
    } catch (e) {
      patchLast(it => ({ ...it, err: `没跑起来: ${e.message}`, done: true })); setLoading(false)
    }
  }

  // 四层交叉深挖。显式入口 —— 一轮 5 次 LLM、约 3 分半, 不能让模型自己决定要不要跑。
  // 走的是同一套 run + 跟随机制, 所以中途关抽屉/切页它照样跑完并落进历史。
  const deepDive = async () => {
    if (loading || !stock) return
    const label = `深挖 ${stock.name}: 四层交叉(生意/数/局/雷)`
    setLoading(true); follow.current = true
    setHistory(h => [...h, newTurn(label)])
    try {
      const r = await startDeepDive({ code: stock.code, name: stock.name, session_id: sessionId.current })
      if (r.error) throw new Error(r.error)
      sessionId.current = r.session_id
      attach(r.run_id, 0)
    } catch (e) {
      patchLast(it => ({ ...it, err: `深挖没跑起来: ${e.message}`, done: true })); setLoading(false)
    }
  }

  // 重开抽屉: 上次那一轮如果还在跑(关抽屉不会杀它), 接着跟看
  const resume = async () => {
    if (runRef.current) { attach(runRef.current.id, runRef.current.cursor); return true }
    // 内存缓存被整页刷新清了, 但 run 可能还在服务端: 按这只票的 scope 找回来, 从 0 补齐过程
    const r = (await liveRuns(scope)).filter(x => !x.done).pop()
    if (!r) return false
    sessionId.current = r.session_id
    setHistory(h => (h.length ? h : [{ ...newTurn(r.question), interjects: (r.inbox || []).map(m => ({ ...m, runId: r.run_id, state: 'queued' })) }]))
    attach(r.run_id, 0)
    return true
  }

  // 挂载后触发滑入; 打开即自动问第一句
  useEffect(() => {
    const t = setTimeout(() => setShown(true), 10)
    resume().then(resumed => {
      // 有缓存对话就别重问一遍(重开抽屉时 Rankings 还是会把 initialQuestion 传进来)
      if (!resumed && !started.current && initialQuestion && stock && !historyRef.current.length) {
        started.current = true; ask(initialQuestion)
      }
    })
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      abortRef.current?.abort()          // 只断开跟看; run 在服务端接着跑, 重开抽屉再续上
      clearInterval(typer.current); clearTimeout(closeTimer.current)
      // 打字机可能停在半句上: 有完整答案的定格成完整答案(不然重开看到的是半句)。
      // 没答完的**不再**标"已中断" —— 它真的还在跑, 标了就是撒谎; 重开会接着往下填。
      // 这里不能用 setHistory: 组件正在卸载, React 18 会把更新丢掉, updater 根本不跑。
      // 所以读 ref 里那份镜像, 直接写缓存。
      const h = historyRef.current
      const k = lastTurn(h || [])
      const last = h?.[k]
      if (last?.answer && !last.done) {
        remember(stock?.code, h.map((it, i) => i === k ? { ...last, typed: last.answer, done: true } : it),
                 sessionId.current, runRef.current, nextRunRef.current)
      } else {
        remember(stock?.code, h || [], sessionId.current, runRef.current, nextRunRef.current)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── 左边缘拖拽改宽 ──
  const [drawerW, setDrawerW] = useState(readDrawerW)
  const drawerWRef = useRef(drawerW)
  drawerWRef.current = drawerW
  const dragFrom = useRef(null)
  useEffect(() => {
    const onMove = (e) => {
      if (!dragFrom.current) return
      const { x0, w0 } = dragFrom.current
      // 抽屉贴右侧, 所以往左拖 = 变宽 → 用 x0 - clientX
      const w = Math.min(Math.max(w0 + (x0 - e.clientX), DRAWER_W_MIN), window.innerWidth - 40)
      setDrawerW(w)
    }
    const onUp = () => {
      if (!dragFrom.current) return
      dragFrom.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      try { localStorage.setItem(DRAWER_W_KEY, String(drawerWRef.current)) } catch {}
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
  }, [])
  const startDrag = (e) => {
    e.preventDefault(); e.stopPropagation()
    dragFrom.current = { x0: e.clientX, w0: drawerWRef.current }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'      // 不然拖的时候会把答案文字全选中
  }

  if (!stock) return null

  return createPortal(
    <div className={`fixed inset-0 z-[200] flex justify-end bg-black/60 backdrop-blur-sm transition-opacity duration-300 ${shown ? 'opacity-100' : 'opacity-0'}`} onClick={close}>
      <div style={{ width: drawerW }}
        className={`relative bg-surface-2 border-l border-border max-w-[96vw] h-full flex flex-col shadow-2xl transition-transform duration-300 ease-out ${shown ? 'translate-x-0' : 'translate-x-full'}`}
        onClick={e => e.stopPropagation()}>
        {/* 左边缘拖拽把手: 只有 6px 宽, hover 才显色, 不抢内容的注意力 */}
        <div onMouseDown={startDrag} title="拖动改变宽度(会记住)"
          className="absolute left-0 top-0 h-full w-[6px] -ml-[3px] cursor-col-resize z-10 hover:bg-accent/40 transition-colors" />
        {/* header */}
        <div className="flex items-baseline gap-2 px-4 py-3 border-b border-border-subtle shrink-0">
          <span className="text-[15px] font-semibold text-text-bright">{stock.name}</span>
          <span className="text-[11px] font-mono text-text-muted">{stock.code}</span>
          {stock.pct != null && (
            <span className={`text-[13px] font-mono font-semibold ${pctColor(stock.pct)}`}>{stock.pct >= 0 ? '+' : ''}{stock.pct}%</span>
          )}
          {stock['行业'] && <span className="text-[10.5px] text-text-dim ml-1">{stock['行业']}</span>}
          <button onClick={deepDive} disabled={loading}
            title="四层视角在同一份事实上各自成文, 再挑出彼此的冲突点与证伪条件。约 3 分半, 期间可以关掉抽屉"
            className="ml-auto text-[11px] px-2 py-[3px] rounded-full border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-40 disabled:cursor-not-allowed">
            深挖
          </button>
          <button onClick={close} className="text-text-dim hover:text-text text-[20px] leading-none px-1">×</button>
        </div>

        {/* 对话流 */}
        <div ref={scrollBox} onScroll={onScroll} className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
          {history.length === 0 && (
            <div className="h-full flex flex-col items-center justify-center text-center gap-2">
              <div className="text-[12px] text-text-dim">问点 {stock.name} 的事</div>
              <div className="flex flex-wrap gap-1.5 justify-center max-w-[420px]">
                {['今天为什么这么走', '量价配合怎么看', '最近有什么消息/题材', '基本面和同行对比'].map((s, i) => (
                  <button key={i} onClick={() => ask(s)}
                    className="text-[11px] px-2.5 py-1 rounded-full border border-accent/30 bg-accent/8 text-accent/90 hover:bg-accent/15 hover:border-accent/50">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {history.map((it, i) => it.kind === 'task' ? <TaskCard key={i} task={it.task} /> : (
            <div key={i}>
              <div className="text-[12px] text-text-bright bg-surface-3 rounded-lg px-3 py-1.5 inline-block">{it.q}</div>
              <InterjectList items={it.interjects} onWithdraw={withdraw} />
              <div className="mt-2 px-3 py-2.5 rounded-lg bg-accent/8 border border-accent/25">
                <ToolCallStrip steps={it.steps} settled={it.answer != null || it.done} />
                {(it.charts || []).length > 0 && (
                  <div className="flex flex-col gap-2 mb-2">
                    {it.charts.map((src, k) => (
                      <ImageZoom key={k} src={src} alt="K线图"
                        className="w-full max-w-[640px] rounded-lg border border-border-subtle block" />
                    ))}
                  </div>
                )}
                {it.err
                  ? <div className="text-[11.5px] text-bull-bright">出错: {it.err}</div>
                  : it.answer == null
                    ? (it.draft
                      ? <DraftAnswer text={it.draft} sources={it.sources} />
                      : it.stale
                        ? <DraftAnswer text={it.stale} sources={it.sources} note={it.staleNote} faded />
                      : it.steps.length === 0 && <div className="text-[11.5px] text-text-dim">分析中…</div>)
                    : <div className="relative">
                        <MiniMarkdown text={it.typed} sources={it.sources} />
                        {!it.done && <span className="inline-block w-1.5 h-3.5 bg-accent/70 align-middle animate-pulse ml-0.5" />}
                        {it.done && <SourcesBlock sources={it.sources} />}
                      </div>}
              </div>
            </div>
          ))}
        </div>

        {/* 输入 */}
        <div className="shrink-0 border-t border-border px-4 py-3">
          <TaskStrip tasks={tasks} onCancel={(id) => { cancelTask(id); setTasks(ts => ts.map(t => t.task_id === id ? { ...t, status: 'cancelled' } : t)) }} />
          <div className="flex gap-2">
            <input value={q} onChange={e => setQ(e.target.value)} autoFocus
              onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing && e.keyCode !== 229) send() }}
              placeholder={loading ? '补充或追问: 它做完当前这一步就会看到' : `问点 ${stock.name} 的事 例: 今天为什么这么走 / 量价怎么看`}
              className="flex-1 text-[12px] px-3 py-2 rounded-lg bg-surface-3 border border-border text-text placeholder:text-text-muted focus:border-accent/50 outline-none disabled:opacity-50" />
            <button onClick={() => send()} disabled={!q.trim()}
              className="text-[12px] px-3.5 py-2 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed">
              {loading ? '补充' : '问'}
            </button>
            {loading && (
              <button onClick={stopRun} title="真停掉这一轮(关抽屉不用停, 它会自己跑完)"
                className="text-[12px] px-3 py-2 rounded-lg bg-surface-3 border border-border text-text-dim hover:text-bear-bright hover:border-bear/40 shrink-0">
                停
              </button>
            )}
          </div>
          <div className="text-[10px] text-text-muted pt-2 mt-2 border-t border-border-subtle">
            纯客观解读，不构成任何买卖建议
            {loading && ' · 关掉抽屉它也会在后台跑完并进历史; 跑的时候可以接着补充'}
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}
