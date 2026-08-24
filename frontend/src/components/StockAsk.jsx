import { useState, useEffect, useRef } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { MiniMarkdown, SourcesBlock, ToolCallStrip, startRun, followRun, liveRuns, cancelRun } from './askShared'
import ImageZoom from './ImageZoom'

// 能力展示型推荐问题 (page 模式空态用), 覆盖 市场风格/资金主线/政策/基本面/同行/筹码
const MARKET_SUGGESTIONS = [
  '这周市场什么风格,资金主线在哪',
  '现在量化资金在冲哪个概念',
  '最近有什么政策面/国家调控影响市场',
  '资金人气榜上抱团方向是什么',
]

// 对话留在组件外: 这一页是条件渲染的(view === 'ask'), 切到别的页就卸载 —— 回来不该是空白。
// 那一轮本身也不再挂在这根 fetch 上: 执行权在服务端(services/ask_runs.py), 走开只是停止跟看。
// run 记 {id, cursor}, 回来照游标续看; 整页刷新把这份内存清了, 也还能问服务端要回来。
const PAGE = { history: [], sessionId: null, run: null }

export default function StockAsk({ page = false }) {
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState(PAGE.history)   // [{q, steps, thought, answer, typed, done, err}]
  const [holdings, setHoldings] = useState([])
  const [sessions, setSessions] = useState([])      // 历史会话列表
  const [showHist, setShowHist] = useState(false)   // 历史抽屉开关
  const [copied, setCopied] = useState(false)
  const [pendImgs, setPendImgs] = useState([])      // 待发送图片(data URL), 随下条问题一起发
  const fileRef = useRef(null)
  const sessionId = useRef(PAGE.sessionId)          // 当前会话 id(开跑时由后端分配)
  const abortRef = useRef(null)                     // 只是"跟看"那根连接, abort 它不影响 run
  const typer = useRef(null)
  const scrollBox = useRef(null)
  const historyRef = useRef(history)                // 卸载时 setState 会被丢, 得靠这份镜像
  const follow = useRef(true)   // 是否跟随滚到底; 用户往上拖就关掉, 拖回底部再开

  useEffect(() => {
    fetchJSON('/api/portfolio').then(d => {
      const hs = Array.isArray(d) ? d : (d.holdings || d.positions || [])
      // 只留当前在持(shares>0); 已清仓的票不该出现在"我的持仓"快捷入口
      setHoldings(hs.filter(h => (h.stock_name || h.stock_code) && Number(h.shares) > 0).slice(0, 8))
    }).catch(() => {})
    if (PAGE.run) attach(PAGE.run.id, PAGE.run.cursor)          // 切回来: 接着跟看
    else if (!PAGE.history.length) reattachOrphan()             // 刷新过: 去服务端把还在跑的捞回来
    return () => {
      abortRef.current?.abort(); clearInterval(typer.current)   // 只停止跟看, 不取消 run
      // 打字机可能停在半句上, 定格成完整答案(此刻不能 setState, 直接写组件外那份)
      const h = historyRef.current
      const last = h?.[h.length - 1]
      if (last?.answer && !last.done) PAGE.history = [...h.slice(0, -1), { ...last, typed: last.answer, done: true }]
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const patchLast = (fn) => setHistory(h => h.map((it, i) => i === h.length - 1 ? fn(it) : it))

  // 图片缩放到最长边 ≤1280 + JPEG 质量 0.82, 控制 base64 体积; 返回 data URL
  const downscaleImage = (file) => new Promise((resolve) => {
    const fr = new FileReader()
    fr.onload = () => {
      const img = new Image()
      img.onload = () => {
        const max = 1280
        let { width: w, height: h } = img
        if (Math.max(w, h) > max) { const r = max / Math.max(w, h); w = Math.round(w * r); h = Math.round(h * r) }
        const cv = document.createElement('canvas'); cv.width = w; cv.height = h
        cv.getContext('2d').drawImage(img, 0, 0, w, h)
        resolve(cv.toDataURL('image/jpeg', 0.82))
      }
      img.onerror = () => resolve(null)
      img.src = fr.result
    }
    fr.onerror = () => resolve(null)
    fr.readAsDataURL(file)
  })

  const addImages = async (files) => {
    const imgs = [...files].filter(f => f.type.startsWith('image/')).slice(0, 4)
    for (const f of imgs) {
      const url = await downscaleImage(f)
      if (url) setPendImgs(p => [...p, url].slice(0, 4))   // 最多 4 张
    }
  }

  const onPaste = (e) => {
    const imgs = [...(e.clipboardData?.items || [])].filter(it => it.type.startsWith('image/')).map(it => it.getAsFile()).filter(Boolean)
    if (imgs.length) { e.preventDefault(); addImages(imgs) }
  }

  const loadSessions = () => fetchJSON('/api/ask/sessions').then(d => setSessions(d.sessions || [])).catch(() => {})

  const openHist = () => { loadSessions(); setShowHist(true) }

  // 开新对话只是"我不看这条了": 在跑的那一轮不杀, 它会自己跑完落库, 之后在历史里能翻到。
  // 真要停用输入框旁边那个「停」。
  const newChat = () => {
    abortRef.current?.abort(); clearInterval(typer.current)
    PAGE.run = null; PAGE.history = []; PAGE.sessionId = null
    sessionId.current = null; setHistory([]); setShowHist(false); setLoading(false)
  }

  const stopRun = () => {
    if (PAGE.run) cancelRun(PAGE.run.id)
    abortRef.current?.abort(); clearInterval(typer.current); PAGE.run = null
    patchLast(it => (it.answer == null ? { ...it, err: '已停止', done: true } : { ...it, typed: it.answer, done: true }))
    setLoading(false)
  }

  // 跟看一条已经在跑(或刚跑完)的 run。断开只是不看了 —— 所以这里不做任何"中断"标记。
  const attach = (runId, cursor = 0) => {
    abortRef.current?.abort()
    const ctrl = new AbortController(); abortRef.current = ctrl
    PAGE.run = { id: runId, cursor }
    setLoading(true)
    followRun(runId, {
      cursor, signal: ctrl.signal,
      onEvent: (ev) => { if (ev.cursor != null && PAGE.run) PAGE.run.cursor = ev.cursor; handleEv(ev) },
      onEnd: ({ finished, gone }) => {
        if (finished || gone) { PAGE.run = null; setLoading(false) }
        // 服务端已经不记得它了(跑完太久被清 / 重启过): 原文在库里, 回去取, 别报错
        if (gone && sessionId.current) loadSession(sessionId.current)
      },
      onError: (err) => { patchLast(it => ({ ...it, err, done: true })); PAGE.run = null; setLoading(false) },
    })
  }

  // 整页刷新/关过页面: 组件外那份内存也没了, 但 run 还在服务端跑着(或刚跑完还在内存里)。
  // 从游标 0 拉一遍, 缺席期间的工具步骤一条不少地补回来。
  const reattachOrphan = async () => {
    const r = (await liveRuns('market')).filter(x => !x.done).pop()
    if (!r) return
    sessionId.current = r.session_id
    setHistory([{ q: r.question, images: [], steps: [], thought: '', answer: null, typed: '', done: false, sources: [], charts: [] }])
    attach(r.run_id, 0)
  }

  // 载入历史会话 → 还原成对话(user/assistant 配对成一轮)
  const loadSession = async (id) => {
    try {
      const s = await fetchJSON(`/api/ask/sessions/${id}`)
      const turns = []
      for (const m of (s.messages || [])) {
        if (m.role === 'user') turns.push({ q: m.content, images: (m.meta && m.meta.images) || [], steps: [], thought: '', answer: null, typed: '', done: true, sources: [], charts: [] })
        else if (turns.length) {
          const t = turns[turns.length - 1]
          t.answer = m.content; t.typed = m.content; t.sources = (m.meta && m.meta.sources) || []; t.charts = (m.meta && m.meta.charts) || []
        }
      }
      // 末尾这一问没答案: 问题是开跑时就落库的, 所以要么它还在跑(接着跟看), 要么当时没跑完
      const tail = turns[turns.length - 1]
      const running = tail && tail.answer == null
        ? (await liveRuns()).find(r => r.session_id === id && !r.done) : null
      if (tail && tail.answer == null && !running) { tail.err = '这一问没答完(当时中断了)' }
      if (tail && running) { tail.done = false }
      sessionId.current = id; setHistory(turns); setShowHist(false)
      if (running) attach(running.run_id, 0)
    } catch { /* ignore */ }
  }

  const deleteSession = async (id, e) => {
    e?.stopPropagation()
    try { await fetch(`/api/ask/sessions/${id}`, { method: 'DELETE' }) } catch { /* ignore */ }
    if (sessionId.current === id) newChat()
    loadSessions()
  }

  // 落库不在这边做了: 问题在开跑那一刻由服务端写进会话, 答案由服务端在跑完时写 ——
  // 前端在不在都一样。原来放在这里, 于是"没看到结尾"等于"这一轮从没发生过"。

  // 复制整段对话为纯文本(贴给开发者优化用)
  const copyConversation = () => {
    const txt = history.filter(it => it.answer != null).map(it =>
      `【我问】${it.q}\n【AI答】${it.answer}`).join('\n\n――――――\n\n')
    navigator.clipboard?.writeText(txt).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => {})
  }

  // 用户手动滚动: 贴近底部(<48px)就重新开启跟随, 往上拖就停跟随
  const onScroll = () => {
    const el = scrollBox.current
    if (!el) return
    follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }
  // 每次内容变化(打字机每跳一下也会触发)后, 若处于跟随态就贴到底; 顺手把对话镜像到组件外
  useEffect(() => {
    const el = scrollBox.current
    if (el && follow.current) el.scrollTop = el.scrollHeight
    historyRef.current = history
    PAGE.history = history; PAGE.sessionId = sessionId.current
  }, [history])

  const typewriter = (full) => {
    clearInterval(typer.current)
    let n = 0
    typer.current = setInterval(() => {
      n = Math.min(full.length, n + 3)   // 每 tick 3 字
      patchLast(it => ({ ...it, typed: full.slice(0, n) }))   // history 变 → 上面 effect 跟随滚动
      if (n >= full.length) { clearInterval(typer.current); patchLast(it => ({ ...it, done: true })) }
    }, 16)
  }

  const handleEv = (ev) => {
    if (ev.type === 'step') patchLast(it => ({ ...it, steps: [...it.steps, { tool: ev.tool, label: ev.label, arg: ev.arg }] }))
    else if (ev.type === 'thought') patchLast(it => ({ ...it, thought: ev.text }))
    else if (ev.type === 'answer') { patchLast(it => ({ ...it, answer: ev.text })); typewriter(ev.text || '') }
    else if (ev.type === 'sources') patchLast(it => ({ ...it, sources: [...(it.sources || []), ...(ev.sources || [])] }))
    else if (ev.type === 'chart') patchLast(it => ({ ...it, charts: [...(it.charts || []), ev.url] }))
    else if (ev.type === 'error') patchLast(it => ({ ...it, err: ev.error, done: true }))
  }

  const ask = async (question) => {
    const text = (question ?? q).trim()
    const imgs = pendImgs
    if ((!text && !imgs.length) || loading) return
    // 把已完成的历史轮次(最近4轮)作为上下文带给后端, 支持追问("它/明天呢")
    const hist = history.filter(it => it.answer && !it.err).slice(-4)
      .flatMap(it => [{ role: 'user', content: it.q }, { role: 'assistant', content: it.answer }])
    const shown = text || '(看图)'
    setQ(''); setPendImgs([]); setLoading(true)
    follow.current = true
    setHistory(h => [...h, { q: shown, images: imgs, steps: [], thought: '', answer: null, typed: '', done: false, sources: [], charts: [] }])
    try {
      const r = await startRun({ question: shown, history: hist, images: imgs,
                                 session_id: sessionId.current, title: shown, scope: 'market' })
      sessionId.current = r.session_id
      // 图换成落盘后的 URL: 跟历史里看到的同一份, 也不用在内存里扛着 base64
      if (r.images?.length) patchLast(it => ({ ...it, images: r.images }))
      attach(r.run_id, 0)
    } catch (e) {
      patchLast(it => ({ ...it, err: `没跑起来: ${e.message}`, done: true }))
      setLoading(false)
    }
  }

  return (
    <div className={`bg-surface-2 border border-border rounded-xl p-4 md:p-5 ${page ? 'flex flex-col h-full' : ''}`}>
      <div className="flex items-baseline gap-2 mb-3">
        <h3 className={`${page ? 'text-[16px]' : 'text-[14px]'} font-semibold text-text-bright m-0`}>问问市场</h3>
        <span className="text-[10.5px] text-text-muted hidden sm:inline">
          {/* 不写死具体个数: 工具一直在加, 写 28 的时候是对的, 现在实际 35(接星球 38) */}
          {page ? '挂了30多个数据工具的AI · 裸K量价/资金流/基本面/筹码 · 产业链全景 · 联网带来源' : '个股涨跌/消息 · 这周市场什么风格 · 资金主线'}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {history.some(it => it.answer != null) && (
            <button onClick={copyConversation} title="复制整段对话(贴给开发者优化)"
              className="text-[10.5px] px-2 py-1 rounded-md border border-border text-text-dim hover:text-text hover:border-accent/40">
              {copied ? '已复制' : '复制'}
            </button>
          )}
          <button onClick={newChat} title="开始新对话"
            className="text-[10.5px] px-2 py-1 rounded-md border border-border text-text-dim hover:text-text hover:border-accent/40">
            新对话
          </button>
          <button onClick={openHist} title="历史会话"
            className="text-[10.5px] px-2 py-1 rounded-md border border-border text-text-dim hover:text-text hover:border-accent/40">
            历史
          </button>
        </div>
      </div>

      {showHist && (
        <div className="mb-3 border border-border rounded-lg bg-surface-3/60 max-h-[42vh] overflow-y-auto">
          <div className="flex items-center justify-between px-3 py-2 border-b border-border-subtle sticky top-0 bg-surface-3">
            <span className="text-[11.5px] text-text-bright font-semibold">历史会话</span>
            <button onClick={() => setShowHist(false)} className="text-[11px] text-text-muted hover:text-text">关闭</button>
          </div>
          {sessions.length === 0
            ? <div className="px-3 py-4 text-[11px] text-text-muted">还没有历史会话</div>
            : sessions.map(s => (
              <div key={s.id} onClick={() => loadSession(s.id)}
                className="flex items-center gap-2 px-3 py-2 border-b border-border-subtle hover:bg-accent/8 cursor-pointer">
                <div className="min-w-0 flex-1">
                  <div className="text-[12px] text-text-dim truncate">{s.title || '(无标题)'}</div>
                  <div className="text-[9.5px] text-text-muted font-mono">{(s.updated_at || '').slice(0, 16).replace('T', ' ')} · {s.msg_count} 条</div>
                </div>
                <button onClick={(e) => deleteSession(s.id, e)}
                  className="text-[10px] text-text-muted hover:text-bear-bright shrink-0 px-1">删除</button>
              </div>
            ))}
        </div>
      )}

      {history.length === 0 && (
        <div className="flex flex-col gap-2 mb-3">
          {page && (
            <div className="flex flex-wrap gap-1.5">
              {MARKET_SUGGESTIONS.map((s, i) => (
                <button key={i} onClick={() => ask(s)}
                  className="text-[11px] px-2.5 py-1 rounded-full border border-accent/30 bg-accent/8 text-accent/90 hover:bg-accent/15 hover:border-accent/50">
                  {s}
                </button>
              ))}
            </div>
          )}
          {holdings.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {holdings.map((h, i) => (
                <button key={i} onClick={() => ask(`${h.stock_name || h.stock_code}最近为什么涨跌`)}
                  className="text-[11px] px-2 py-0.5 rounded-full border border-border bg-surface-3 text-text-dim hover:text-text hover:border-accent/40">
                  {h.stock_name || h.stock_code} ↗
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div ref={scrollBox} onScroll={onScroll} className={`space-y-3 mb-3 ${page ? 'flex-1 min-h-0 overflow-y-auto pr-1' : (history.length ? 'max-h-[58vh] overflow-y-auto pr-1' : '')}`}>
        {history.map((it, i) => (
          <div key={i}>
            <div className="text-[12px] text-text-bright bg-surface-3 rounded-lg px-3 py-1.5 inline-block">{it.q}</div>
            {it.images?.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                {it.images.map((src, k) => (
                  <ImageZoom key={k} src={src} className="h-20 w-auto rounded-lg border border-border-subtle object-cover" />
                ))}
              </div>
            )}
            <div className="mt-2 px-3 py-2.5 rounded-lg bg-accent/8 border border-accent/25">
              {/* 步骤实时流: 工具调用胶囊(与排行榜弹窗共用 ToolCallStrip) */}
              <ToolCallStrip steps={it.steps} settled={it.answer != null || it.done} />
              {it.thought && it.answer == null && <div className="text-[11px] text-text-muted italic mb-1.5">{it.thought}</div>}
              {/* AI 渲染的K线图(结构已标注): 我方数据画→精确, 数字以正文为准 */}
              {(it.charts || []).length > 0 && (
                <div className="flex flex-col gap-2 mb-2">
                  {it.charts.map((src, k) => (
                    <ImageZoom key={k} src={src} alt="K线图"
                      className="w-full max-w-[640px] rounded-lg border border-border-subtle block" />
                  ))}
                </div>
              )}
              {/* 答案 / loading / 错误 */}
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

      {pendImgs.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {pendImgs.map((src, k) => (
            <div key={k} className="relative">
              <img src={src} alt="" className="h-14 w-auto rounded-lg border border-border" />
              <button onClick={() => setPendImgs(p => p.filter((_, j) => j !== k))}
                className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-surface-raise border border-border text-text-dim text-[10px] leading-none hover:text-bear-bright">×</button>
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-2 shrink-0">
        <input ref={fileRef} type="file" accept="image/*" multiple className="hidden"
          onChange={e => { addImages(e.target.files); e.target.value = '' }} />
        <button onClick={() => fileRef.current?.click()} disabled={loading} title="发图给 AI 看(截图/K线/持仓)"
          className="text-[12px] px-2.5 py-2 rounded-lg bg-surface-3 border border-border text-text-dim hover:text-text hover:border-accent/40 disabled:opacity-50 shrink-0">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="8.5" cy="9" r="1.5" /><path d="M21 16l-5-5L5 20" />
          </svg>
        </button>
        <input value={q} onChange={e => setQ(e.target.value)} onPaste={onPaste}
          onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing && e.keyCode !== 229) ask() }} disabled={loading}
          placeholder="例: 这周市场什么风格 / 洛阳钼业为什么涨 / 也可贴图问"
          className="flex-1 text-[12px] px-3 py-2 rounded-lg bg-surface-3 border border-border text-text placeholder:text-text-muted focus:border-accent/50 outline-none disabled:opacity-50" />
        <button onClick={() => ask()} disabled={loading || (!q.trim() && !pendImgs.length)}
          className="text-[12px] px-3.5 py-2 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed">
          {loading ? '分析中' : '问'}
        </button>
        {loading && (
          <button onClick={stopRun} title="真停掉这一轮(切到别的页不用停, 它会自己跑完)"
            className="text-[12px] px-3 py-2 rounded-lg bg-surface-3 border border-border text-text-dim hover:text-bear-bright hover:border-bear/40 shrink-0">
            停
          </button>
        )}
      </div>
      <div className="text-[10px] text-text-muted pt-2.5 mt-2 border-t border-border-subtle">
        Agent 自取行情/走势/新闻/大盘情绪后客观解读 · 可发图(截图/K线/持仓)让它看 · 纯解读不构成任何买卖建议
        {loading && ' · 这一轮跑在后台, 切页/关页都不打断, 答完自动进历史'}
      </div>
    </div>
  )
}
