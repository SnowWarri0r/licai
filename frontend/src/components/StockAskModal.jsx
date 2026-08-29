import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { MiniMarkdown, SourcesBlock, ToolCallStrip, startRun, followRun, liveRuns, cancelRun } from './askShared'
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
const DRAWER_CACHE = new Map()      // code -> { history, sessionId, run }
const DRAWER_CACHE_MAX = 8

function remember(code, history, sessionId, run) {
  if (!code) return
  DRAWER_CACHE.delete(code)                       // 删了再插: Map 按插入序, 这样它排到最后
  DRAWER_CACHE.set(code, { history, sessionId, run })
  while (DRAWER_CACHE.size > DRAWER_CACHE_MAX) DRAWER_CACHE.delete(DRAWER_CACHE.keys().next().value)
}

// 个股 AI 分析弹窗: 多轮对话, 工具调用胶囊/正文/来源全部复用 askShared, 与"问问市场"样式一致。
// stock: {code, name, pct, 行业}; initialQuestion: 打开即自动问的第一句(可空)。
export default function StockAskModal({ stock, onClose, initialQuestion = '' }) {
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  // [{q, steps, answer, typed, done, sources, charts, err}]
  const [history, setHistory] = useState(() => DRAWER_CACHE.get(stock?.code)?.history || [])
  const [shown, setShown] = useState(false)     // 滑入动画: 挂载后置 true → 从右侧划出
  const abortRef = useRef(null)
  const typer = useRef(null)
  const scrollBox = useRef(null)
  const follow = useRef(true)
  const started = useRef(false)
  const closeTimer = useRef(null)
  const sessionId = useRef(DRAWER_CACHE.get(stock?.code)?.sessionId ?? null)   // 落库用: 同一只票接着同一条会话
  const runRef = useRef(DRAWER_CACHE.get(stock?.code)?.run ?? null)            // {id, cursor}: 关了再开接着看
  const scope = `stock:${stock?.code || ''}`

  // 先播放滑出动画再卸载
  const close = () => { setShown(false); clearTimeout(closeTimer.current); closeTimer.current = setTimeout(onClose, 280) }

  const patchLast = (fn) => setHistory(h => h.map((it, i) => i === h.length - 1 ? fn(it) : it))

  // 落库(与「问问市场」同一张表)现在归服务端: 问题在开跑那刻就写进会话, 答案跑完写。
  // 为什么非得落: 这个抽屉原来什么都不存, 于是在这儿发生的每一次纠正, 纠正挖掘都看不见 ——
  // 那套"答错了不再错第二遍"的机制对抽屉里的对话完全失效。事后想复盘也没有原文。
  // 而只要落库还挂在前端, "关抽屉"就等于"这一轮从没发生过"。

  const typewriter = (full) => {
    clearInterval(typer.current)
    let n = 0
    typer.current = setInterval(() => {
      n = Math.min(full.length, n + 3)
      patchLast(it => ({ ...it, typed: full.slice(0, n) }))
      if (n >= full.length) { clearInterval(typer.current); patchLast(it => ({ ...it, done: true })) }
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
    remember(stock?.code, history, sessionId.current, runRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [history])

  const handleEv = (ev) => {
    if (ev.type === 'step') patchLast(it => ({ ...it, steps: [...it.steps, { tool: ev.tool, label: ev.label, arg: ev.arg }] }))
    else if (ev.type === 'chart') patchLast(it => ({ ...it, charts: [...it.charts, ev.url] }))
    else if (ev.type === 'sources') patchLast(it => ({ ...it, sources: [...it.sources, ...(ev.sources || [])] }))
    else if (ev.type === 'answer') { patchLast(it => ({ ...it, answer: ev.text })); typewriter(ev.text || '') }
    else if (ev.type === 'error') patchLast(it => ({ ...it, err: ev.error, done: true }))
  }

  // 最后一问的答案从库里补回来(服务端内存里已经没这条 run 了, 但落库是它跑完时就做的)。
  // 库里也没有 → 那一轮真没跑完(比如服务重启过), 标出来; 不能让它一直转"分析中"。
  const recoverTail = async () => {
    let last = null
    try {
      if (sessionId.current) {
        const s = await (await fetch(`/api/ask/sessions/${sessionId.current}`)).json()
        const msgs = s.messages || []
        if (msgs[msgs.length - 1]?.role === 'assistant') last = msgs[msgs.length - 1]
      }
    } catch { /* 取不到当没有 */ }
    patchLast(it => {
      if (it.answer) return { ...it, typed: it.answer, done: true }
      if (last) return { ...it, answer: last.content, typed: last.content, done: true,
                         sources: (last.meta && last.meta.sources) || it.sources,
                         charts: (last.meta && last.meta.charts) || it.charts }
      return { ...it, done: true, err: '这一轮没跑完(服务重启过)' }
    })
  }

  // 跟看一条 run。关抽屉只 abort 这根连接, run 在服务端照跑照落库。
  const attach = (runId, cursor = 0) => {
    abortRef.current?.abort()
    const ctrl = new AbortController(); abortRef.current = ctrl
    runRef.current = { id: runId, cursor }
    setLoading(true)
    followRun(runId, {
      cursor, signal: ctrl.signal,
      onEvent: (ev) => { if (ev.cursor != null && runRef.current) runRef.current.cursor = ev.cursor; handleEv(ev) },
      onEnd: ({ finished, gone }) => {
        if (finished || gone) { runRef.current = null; setLoading(false) }
        if (gone) recoverTail()      // 服务端不记得它了 → 答案在库里, 回去取
      },
      onError: (err) => { patchLast(it => ({ ...it, err, done: true })); runRef.current = null; setLoading(false) },
    })
  }

  const stopRun = () => {
    if (runRef.current) cancelRun(runRef.current.id)
    abortRef.current?.abort(); clearInterval(typer.current); runRef.current = null
    patchLast(it => (it.answer == null ? { ...it, err: '已停止', done: true } : { ...it, typed: it.answer, done: true }))
    setLoading(false)
  }

  const ask = async (question) => {
    const text = (question ?? q).trim()
    if (!text || loading || !stock) return
    // 已完成轮次作为上下文(最近4轮), 支持追问
    const hist = history.filter(it => it.answer && !it.err).slice(-4)
      .flatMap(it => [{ role: 'user', content: it.q }, { role: 'assistant', content: it.answer }])
    setQ(''); setLoading(true); follow.current = true
    setHistory(h => [...h, { q: text, steps: [], answer: null, typed: '', done: false, sources: [], charts: [] }])
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

  // 重开抽屉: 上次那一轮如果还在跑(关抽屉不会杀它), 接着跟看
  const resume = async () => {
    if (runRef.current) { attach(runRef.current.id, runRef.current.cursor); return true }
    // 内存缓存被整页刷新清了, 但 run 可能还在服务端: 按这只票的 scope 找回来, 从 0 补齐过程
    const r = (await liveRuns(scope)).filter(x => !x.done).pop()
    if (!r) return false
    sessionId.current = r.session_id
    setHistory(h => (h.length ? h : [{ q: r.question, steps: [], answer: null, typed: '', done: false, sources: [], charts: [] }]))
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
      const last = h?.[h.length - 1]
      if (last?.answer && !last.done) {
        remember(stock?.code, [...h.slice(0, -1), { ...last, typed: last.answer, done: true }],
                 sessionId.current, runRef.current)
      } else {
        remember(stock?.code, h || [], sessionId.current, runRef.current)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!stock) return null

  return createPortal(
    <div className={`fixed inset-0 z-[200] flex justify-end bg-black/60 backdrop-blur-sm transition-opacity duration-300 ${shown ? 'opacity-100' : 'opacity-0'}`} onClick={close}>
      <div className={`bg-surface-2 border-l border-border w-[520px] max-w-[94vw] h-full flex flex-col shadow-2xl transition-transform duration-300 ease-out ${shown ? 'translate-x-0' : 'translate-x-full'}`}
        onClick={e => e.stopPropagation()}>
        {/* header */}
        <div className="flex items-baseline gap-2 px-4 py-3 border-b border-border-subtle shrink-0">
          <span className="text-[15px] font-semibold text-text-bright">{stock.name}</span>
          <span className="text-[11px] font-mono text-text-muted">{stock.code}</span>
          {stock.pct != null && (
            <span className={`text-[13px] font-mono font-semibold ${pctColor(stock.pct)}`}>{stock.pct >= 0 ? '+' : ''}{stock.pct}%</span>
          )}
          {stock['行业'] && <span className="text-[10.5px] text-text-dim ml-1">{stock['行业']}</span>}
          <button onClick={close} className="ml-auto text-text-dim hover:text-text text-[20px] leading-none px-1">×</button>
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
          {history.map((it, i) => (
            <div key={i}>
              <div className="text-[12px] text-text-bright bg-surface-3 rounded-lg px-3 py-1.5 inline-block">{it.q}</div>
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
                    ? (it.steps.length === 0 && <div className="text-[11.5px] text-text-dim">分析中…</div>)
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
          <div className="flex gap-2">
            <input value={q} onChange={e => setQ(e.target.value)} autoFocus
              onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing && e.keyCode !== 229) ask() }}
              disabled={loading}
              placeholder={`问点 ${stock.name} 的事 例: 今天为什么这么走 / 量价怎么看`}
              className="flex-1 text-[12px] px-3 py-2 rounded-lg bg-surface-3 border border-border text-text placeholder:text-text-muted focus:border-accent/50 outline-none disabled:opacity-50" />
            <button onClick={() => ask()} disabled={loading || !q.trim()}
              className="text-[12px] px-3.5 py-2 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed">
              {loading ? '分析中' : '问'}
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
            {loading && ' · 关掉抽屉它也会在后台跑完并进历史'}
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}
