import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { MiniMarkdown, SourcesBlock, ToolCallStrip, streamAnalysis } from './askShared'
import ImageZoom from './ImageZoom'

function pctColor(v) {
  if (v > 0) return 'text-bear'
  if (v < 0) return 'text-bull'
  return 'text-text-dim'
}

// 抽屉一关就卸载, 对话跟着没了 —— 关一下再打开是空的, 追问的上下文也断了。所以按代码
// 把对话留在模块级(组件外, 不随卸载消失), 关开不丢; 同一只票接着问还是同一个会话(落库
// 也接着落在那条会话里)。只留最近几只, 免得把长对话无限攒在内存里。
// 注: 刷新整页会清掉这份内存缓存 —— 原文已经落库, 在「问问市场」的历史里能翻到。
const DRAWER_CACHE = new Map()      // code -> { history, sessionId }
const DRAWER_CACHE_MAX = 8

function remember(code, history, sessionId) {
  if (!code) return
  DRAWER_CACHE.delete(code)                       // 删了再插: Map 按插入序, 这样它排到最后
  DRAWER_CACHE.set(code, { history, sessionId })
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

  // 先播放滑出动画再卸载
  const close = () => { setShown(false); clearTimeout(closeTimer.current); closeTimer.current = setTimeout(onClose, 280) }

  const patchLast = (fn) => setHistory(h => h.map((it, i) => i === h.length - 1 ? fn(it) : it))

  // 落库一轮(与「问问市场」同一个端点/同一张表)。
  // 为什么要落: 这个抽屉原来什么都不存, 于是在这儿发生的每一次纠正, 纠正挖掘都看不见 ——
  // 那套"答错了不再错第二遍"的机制对抽屉里的对话完全失效。事后想复盘也没有原文。
  const persistTurn = async (question, item) => {
    try {
      const r1 = await fetch('/api/ask/messages', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId.current, role: 'user', content: question,
                               title: `${stock.name}(${stock.code}) ${question}`.slice(0, 40) }),
      })
      const j1 = await r1.json(); sessionId.current = j1.session_id
      await fetch('/api/ask/messages', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId.current, role: 'assistant',
          content: item.answer || '',
          // llm_retry/self_check 是过程提示不是工具, 别存进历史
          meta: { tools_used: (item.steps || []).map(s => s.tool)
                    .filter(t => t !== 'llm_retry' && t !== 'self_check'),
                  sources: item.sources || [], charts: item.charts || [] } }),
      })
    } catch { /* 落库失败不影响使用 */ }
  }

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
    remember(stock?.code, history, sessionId.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [history])

  const ask = (question) => {
    const text = (question ?? q).trim()
    if (!text || loading || !stock) return
    // 已完成轮次作为上下文(最近4轮), 支持追问
    const hist = history.filter(it => it.answer && !it.err).slice(-4)
      .flatMap(it => [{ role: 'user', content: it.q }, { role: 'assistant', content: it.answer }])
    setQ(''); setLoading(true); follow.current = true
    setHistory(h => [...h, { q: text, steps: [], answer: null, typed: '', done: false, sources: [], charts: [] }])
    abortRef.current?.abort()
    const ctrl = new AbortController(); abortRef.current = ctrl
    streamAnalysis(`${stock.name}(${stock.code}): ${text}`, {
      signal: ctrl.signal,
      history: hist,
      onStep: (e) => patchLast(it => ({ ...it, steps: [...it.steps, { tool: e.tool, label: e.label, arg: e.arg }] })),
      onChart: (e) => patchLast(it => ({ ...it, charts: [...it.charts, e.url] })),
      onSource: (arr) => patchLast(it => ({ ...it, sources: [...it.sources, ...arr] })),
      onAnswer: (t) => { patchLast(it => ({ ...it, answer: t })); typewriter(t || '') },
      onError: (err) => { patchLast(it => ({ ...it, err: err || '连接中断', done: true })); setLoading(false) },
      onDone: () => {
        setLoading(false)
        // 用打字机之前的完整答案落库(typed 是逐字追加的中间态)
        setHistory(h => { const last = h[h.length - 1]; if (last?.answer) persistTurn(text, last); return h })
      },
    })
  }

  // 挂载后触发滑入; 打开即自动问第一句
  useEffect(() => {
    const t = setTimeout(() => setShown(true), 10)
    if (!started.current && initialQuestion && stock) { started.current = true; ask(initialQuestion) }
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey); abortRef.current?.abort()
      clearInterval(typer.current); clearTimeout(closeTimer.current)
      // 正在流式的那一轮会被 abort 掉。不定格的话重开进来是一行永远转不完的"正在取数据",
      // 而且打字机停在半句上 —— 有答案的按已收到的部分收尾, 没答案的标成已中断。
      // 这里不能用 setHistory: 组件正在卸载, React 18 会把更新丢掉, updater 根本不跑。
      // 所以读 ref 里那份镜像, 直接写缓存。
      const h = historyRef.current
      if (h?.length && !h[h.length - 1].done) {
        const last = h[h.length - 1]
        const fixed = last.answer
          ? { ...last, typed: last.answer, done: true }
          : { ...last, done: true, err: last.err || '已中断(关抽屉时这一问还没答完)' }
        remember(stock?.code, [...h.slice(0, -1), fixed], sessionId.current)
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
          </div>
          <div className="text-[10px] text-text-muted pt-2 mt-2 border-t border-border-subtle">纯客观解读，不构成任何买卖建议</div>
        </div>
      </div>
    </div>,
    document.body
  )
}
