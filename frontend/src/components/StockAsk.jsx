import { useState, useEffect, useRef } from 'react'
import { fetchJSON } from '../hooks/useApi'

// 每个数据工具一套线性 SVG 图标(描边跟随 currentColor, 贴合暖金深色主题)
const TOOL_ICONS = {
  resolve_stock: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="M19.5 19.5l-4.2-4.2" /></>,
  get_quote: <><rect x="5.5" y="8" width="4" height="7" rx="1" /><path d="M7.5 5v3M7.5 15v4" /><rect x="14.5" y="7" width="4" height="6" rx="1" /><path d="M16.5 4v3M16.5 13v4" /></>,
  get_trend: <><path d="M4 4v16h16" /><path d="M7 14l3-3 3 2 4-6" /><path d="M15.5 7H18v2.5" /></>,
  get_intraday: <><circle cx="12" cy="12" r="8" /><path d="M12 8v4l3 2" /></>,
  get_news: <><rect x="5" y="4" width="14" height="16" rx="2" /><path d="M8 9h8M8 12h8M8 15h5" /></>,
  get_announcements: <><path d="M4 10v4l10 5V5l-10 5z" /><path d="M14 9.5a3 3 0 010 5" /></>,
  get_fund_flow: <><circle cx="12" cy="12" r="8" /><path d="M9 8l3 3.5L15 8M12 11.5V16M9.5 12.5h5M9.5 14.5h5" /></>,
  get_lhb: <><path d="M8 4h8v4a4 4 0 01-8 0z" /><path d="M8 5H5v1a3 3 0 003 3M16 5h3v1a3 3 0 01-3 3M10 15h4M9 19.5h6M12 15v4.5" /></>,
  get_company_profile: <><rect x="4" y="8" width="9" height="12" rx="1" /><path d="M13 12h7v8h-7M7 11h3M7 14h3M7 17h3M16 15h1M16 17.5h1" /></>,
  get_red_flags: <><path d="M5 21V4M5 4h11l-2 4 2 4H5" /></>,
  get_stock_concepts: <><path d="M11 3H4v7l9 9 7-7z" /><circle cx="7.5" cy="7.5" r="1.3" /></>,
  get_fundamentals: <><rect x="5" y="4" width="14" height="16" rx="2" /><path d="M9 14v3M12 10.5v6.5M15 13v4" /></>,
  get_commodity: <><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" /><path d="M12 12v9M4 7.5l8 4.5 8-4.5" /></>,
  get_peers: <><path d="M12 5v15M7 20h10M5 8l7-2 7 2" /><path d="M5 8l-2 5a3 3 0 006 0zM19 8l2 5a3 3 0 01-6 0z" /></>,
  get_shareholders: <><circle cx="9" cy="8" r="3" /><path d="M3.5 19a5.5 5 0 0111 0" /><path d="M16 6a3 3 0 010 6M20.5 19a5.5 5 0 00-4-5" /></>,
  get_holdings: <><rect x="4" y="8" width="16" height="11" rx="2" /><path d="M9 8V6a2 2 0 012-2h2a2 2 0 012 2v2M4 13h16" /></>,
  get_thesis: <><path d="M7 4h8l4 4v12H7zM15 4v4h4M10 13h6M10 16.5h4" /></>,
  get_asset_allocation: <><circle cx="12" cy="12" r="8" /><path d="M12 12V4M12 12l7 3.5" /></>,
  get_trades: <><path d="M4 9h12M13 6l3 3-3 3" /><path d="M20 15H8M11 12l-3 3 3 3" /></>,
  get_market_sentiment: <><path d="M4 16a8 8 0 0116 0" /><path d="M12 16l4.5-4" /><circle cx="12" cy="16" r="1" /></>,
  get_sector_momentum: <><rect x="4" y="4" width="7" height="7" rx="1" /><rect x="13" y="4" width="7" height="7" rx="1" /><rect x="4" y="13" width="7" height="7" rx="1" /><rect x="13" y="13" width="7" height="7" rx="1" /></>,
  get_hot_rank: <><path d="M12 3c.5 3 3.5 4 3.5 8a3.5 3.5 0 01-7 0c0-1.5 1-2.5 1.5-3 .3 1.5 2 1 2-5z" /></>,
  get_hot_concepts: <><path d="M9.5 18h5M10.5 21h3" /><path d="M12 3a6 6 0 00-3.5 10.8c.6.5.9 1.2 1 2.2h5c.1-1 .4-1.7 1-2.2A6 6 0 0012 3z" /></>,
  get_board_stocks: <><path d="M4 8l3.5 9h9L20 8l-5 4-3-6-3 6z" /></>,
  get_market_news: <><path d="M4 20h16M6 20V8l6-4 6 4v12M10 20v-5h4v5" /></>,
  web_search: <><circle cx="12" cy="12" r="8" /><path d="M4 12h16M12 4c2.5 2.4 2.5 13.6 0 16M12 4c-2.5 2.4-2.5 13.6 0 16" /></>,
  get_chain_quote: <><circle cx="6" cy="6" r="2.2" /><circle cx="18" cy="6" r="2.2" /><circle cx="12" cy="18" r="2.2" /><path d="M8 7l8 0M7 8l4 8M17 8l-4 8" /></>,
  read_url: <><rect x="5" y="3.5" width="14" height="17" rx="2" /><path d="M8 8h8M8 11.5h8M8 15h5" /></>,
}
const DEFAULT_ICON = <><circle cx="12" cy="12" r="2.6" /><path d="M12 4v2.5M12 17.5V20M4 12h2.5M17.5 12H20M6.3 6.3l1.8 1.8M15.9 15.9l1.8 1.8M17.7 6.3l-1.8 1.8M8.1 15.9l-1.8 1.8" /></>

function ToolIcon({ tool }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
      {TOOL_ICONS[tool] || DEFAULT_ICON}
    </svg>
  )
}

// 只放行 http(s) 链接, 挡 javascript:/data: 等可执行 scheme(来源是联网搜索结果, 纵深防御)
function safeUrl(url) {
  try { const u = new URL(url); return (u.protocol === 'https:' || u.protocol === 'http:') ? u.href : null }
  catch { return null }
}

// 正文内联引用角标: ⟦N⟧ → 可点上标, 跳到第 N 条联网来源原文
function CiteMark({ n, src }) {
  const cls = "align-super text-[8.5px] font-medium text-accent/90 hover:text-accent px-[1px]"
  const href = src && safeUrl(src.url)
  if (!href) return <sup className={cls}>[{n}]</sup>
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" title={src.title}
      className={`${cls} no-underline hover:underline cursor-pointer`}>[{n}]</a>
  )
}

// 给带符号涨跌数字上色(A股 红涨绿跌): +X% / -X% → 红 / 绿。在表格和正文里都生效。
// 前界排除数字/冒号/点、后界排除冒号, 避免把时间段(10:30-11:00)/日期(2024-06)/代码区间误当负数。
function colorizeSigned(text, kp) {
  return text.split(/((?<![\d:.])[+-]\d[\d,.]*%?(?!:))/g).map((seg, j) => {
    const m = seg.match(/^([+-])\d[\d,.]*%?$/)
    if (m && !seg.endsWith(':')) return <span key={`${kp}-s${j}`} className={m[1] === '+' ? 'text-bear' : 'text-bull'}>{seg}</span>
    return seg
  })
}

// 极简 markdown 渲染 (## 标题 / **粗** / - 列表 / ⟦N⟧引用 / 涨跌红绿 / 段落), 不引依赖
function renderInlineBase(text, kp, sources) {
  return text.split(/(\*\*[^*]+\*\*|⟦\d+⟧)/g).map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**'))
      return <strong key={`${kp}-${i}`} className="text-text-bright">{colorizeSigned(p.slice(2, -2), `${kp}-${i}`)}</strong>
    const m = p.match(/^⟦(\d+)⟧$/)
    if (m) { const n = parseInt(m[1], 10); return <CiteMark key={`${kp}-${i}`} n={n} src={sources && sources[n - 1]} /> }
    return <span key={`${kp}-${i}`}>{colorizeSigned(p, `${kp}-${i}`)}</span>
  })
}
// 表格行: 以 | 开头/结尾且含分隔; 分隔行: |---|:--|... (全是 - 和 : 和 |)
const isTableRow = (t) => t.startsWith('|') && t.indexOf('|', 1) > 0
const isTableSep = (t) => /^\|?[\s:|-]+\|[\s:|-]*$/.test(t) && t.includes('-')
const splitCells = (t) => t.replace(/^\||\|$/g, '').split('|').map(c => c.trim())

function MiniMarkdown({ text, sources }) {
  const renderInline = (t, kp) => renderInlineBase(t, kp, sources)
  // 模型偶尔内联 <cite index="x">...</cite> 引用标签, 去掉只留文字(后端已剥, 这里兜底)
  const lines = (text || '').replace(/<\/?cite[^>]*>/g, '').split('\n')
  const out = []
  let i = 0
  while (i < lines.length) {
    const t = lines[i].trim()
    // 表格: 连续的 | 行 (第二行是分隔行)
    if (isTableRow(t) && i + 1 < lines.length && isTableSep(lines[i + 1].trim())) {
      const header = splitCells(t)
      const rows = []
      let j = i + 2
      while (j < lines.length && isTableRow(lines[j].trim())) { rows.push(splitCells(lines[j].trim())); j++ }
      out.push(
        <div key={i} className="my-2 overflow-x-auto">
          <table className="text-[11.5px] border-collapse w-full">
            <thead><tr>{header.map((h, k) => (
              <th key={k} className="text-left font-semibold text-text-bright px-2 py-1 border-b border-border bg-surface-3 whitespace-nowrap">{renderInline(h, `h${i}-${k}`)}</th>
            ))}</tr></thead>
            <tbody>{rows.map((r, ri) => (
              <tr key={ri} className="border-b border-border-subtle">{r.map((c, k) => (
                <td key={k} className="px-2 py-1 text-text-dim whitespace-nowrap">{renderInline(c, `c${i}-${ri}-${k}`)}</td>
              ))}</tr>
            ))}</tbody>
          </table>
        </div>
      )
      i = j
      continue
    }
    if (!t) { out.push(<div key={i} className="h-1.5" />) }
    else if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) out.push(<hr key={i} className="my-2.5 border-0 border-t border-border-subtle" />)
    else if (t.startsWith('## ')) out.push(<div key={i} className="text-[12.5px] font-semibold text-accent mt-2 mb-0.5">{renderInline(t.slice(3), i)}</div>)
    else if (t.startsWith('### ')) out.push(<div key={i} className="text-[12px] font-semibold text-text-bright mt-1.5">{renderInline(t.slice(4), i)}</div>)
    else if (t.startsWith('# ')) out.push(<div key={i} className="text-[13px] font-semibold text-accent mt-2 mb-0.5">{renderInline(t.slice(2), i)}</div>)
    else if (t.startsWith('> ')) out.push(<div key={i} className="text-[12px] text-text-muted border-l-2 border-accent/40 pl-2 my-1 italic">{renderInline(t.slice(2), i)}</div>)
    else if (t.startsWith('- ') || t.startsWith('• ') || t.startsWith('* ')) out.push(<div key={i} className="flex gap-1.5 text-[12px] leading-relaxed"><span className="text-accent shrink-0">·</span><span className="text-text-dim">{renderInline(t.slice(2), i)}</span></div>)
    else if (/^\d+\.\s/.test(t)) { const m = t.match(/^(\d+)\.\s+(.*)$/); out.push(<div key={i} className="flex gap-1.5 text-[12px] leading-relaxed"><span className="text-accent shrink-0 font-medium">{m[1]}.</span><span className="text-text-dim">{renderInline(m[2], i)}</span></div>) }
    else out.push(<div key={i} className="text-[12px] text-text-dim leading-relaxed">{renderInline(t, i)}</div>)
    i++
  }
  return <div>{out}</div>
}

// 从 url 取域名(去 www), 当来源出处展示
function domainOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return '' }
}

// 联网来源列表: 默认折叠, 展开后逐条可点(新标签打开原文)
function SourcesBlock({ sources }) {
  const [open, setOpen] = useState(false)
  if (!sources || sources.length === 0) return null
  return (
    <div className="mt-2.5 pt-2 border-t border-border-subtle">
      <button onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 text-[10.5px] text-text-muted hover:text-text-dim">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
          <circle cx="12" cy="12" r="8" /><path d="M4 12h16M12 4c2.5 2.4 2.5 13.6 0 16M12 4c-2.5 2.4-2.5 13.6 0 16" />
        </svg>
        <span>联网来源</span>
        <span className="font-mono text-text-dim">{sources.length}</span>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`}>
          <path d="M9 6l6 6-6 6" />
        </svg>
      </button>
      {open && (
        <ol className="mt-1.5 space-y-1 max-h-52 overflow-y-auto pr-1">
          {sources.map((s, i) => {
            const href = safeUrl(s.url)
            const inner = (<>
              <span className="block truncate group-hover:underline">{s.title}</span>
              <span className="block truncate text-[9.5px] text-text-muted">
                {domainOf(s.url)}{s.age ? ` · ${s.age}` : ''}
              </span>
            </>)
            return (
              <li key={i} className="flex gap-1.5 text-[11px] leading-snug">
                <span className="text-text-muted font-mono shrink-0 w-4 text-right">{i + 1}</span>
                {href
                  ? <a href={href} target="_blank" rel="noopener noreferrer"
                      className="group min-w-0 flex-1 hover:text-accent text-text-dim">{inner}</a>
                  : <span className="group min-w-0 flex-1 text-text-dim">{inner}</span>}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}

// 能力展示型推荐问题 (page 模式空态用), 覆盖 市场风格/资金主线/政策/基本面/同行/筹码
const MARKET_SUGGESTIONS = [
  '这周市场什么风格,资金主线在哪',
  '现在量化资金在冲哪个概念',
  '最近有什么政策面/国家调控影响市场',
  '资金人气榜上抱团方向是什么',
]

export default function StockAsk({ page = false }) {
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState([])   // [{q, steps, thought, answer, typed, done, err}]
  const [holdings, setHoldings] = useState([])
  const [sessions, setSessions] = useState([])      // 历史会话列表
  const [showHist, setShowHist] = useState(false)   // 历史抽屉开关
  const [copied, setCopied] = useState(false)
  const [pendImgs, setPendImgs] = useState([])      // 待发送图片(data URL), 随下条问题一起发
  const fileRef = useRef(null)
  const sessionId = useRef(null)                    // 当前会话 id(首次保存时由后端分配)
  const abortRef = useRef(null)
  const typer = useRef(null)
  const scrollBox = useRef(null)
  const follow = useRef(true)   // 是否跟随滚到底; 用户往上拖就关掉, 拖回底部再开

  useEffect(() => {
    fetchJSON('/api/portfolio').then(d => {
      const hs = Array.isArray(d) ? d : (d.holdings || d.positions || [])
      // 只留当前在持(shares>0); 已清仓的票不该出现在"我的持仓"快捷入口
      setHoldings(hs.filter(h => (h.stock_name || h.stock_code) && Number(h.shares) > 0).slice(0, 8))
    }).catch(() => {})
    return () => { abortRef.current?.abort(); clearInterval(typer.current) }
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

  const newChat = () => {
    abortRef.current?.abort(); clearInterval(typer.current)
    sessionId.current = null; setHistory([]); setShowHist(false); setLoading(false)
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
      sessionId.current = id; setHistory(turns); setShowHist(false)
    } catch { /* ignore */ }
  }

  const deleteSession = async (id, e) => {
    e?.stopPropagation()
    try { await fetch(`/api/ask/sessions/${id}`, { method: 'DELETE' }) } catch { /* ignore */ }
    if (sessionId.current === id) newChat()
    loadSessions()
  }

  // 持久化一轮(user + assistant)。user 带发送的图、assistant 带 tools_used/sources 进 meta。
  const persistTurn = async (question, item) => {
    try {
      const r1 = await fetch('/api/ask/messages', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId.current, role: 'user', content: question, title: question,
                               meta: (item.images || []).length ? { images: item.images } : null }),
      })
      const j1 = await r1.json(); sessionId.current = j1.session_id
      await fetch('/api/ask/messages', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId.current, role: 'assistant', content: item.answer || '',
          meta: { tools_used: (item.steps || []).map(s => s.tool), sources: item.sources || [], charts: item.charts || [] },
        }),
      })
    } catch { /* 持久化失败不影响使用 */ }
  }

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
  // 每次内容变化(打字机每跳一下也会触发)后, 若处于跟随态就贴到底
  useEffect(() => {
    const el = scrollBox.current
    if (el && follow.current) el.scrollTop = el.scrollHeight
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
    setQ(''); setPendImgs([]); setLoading(true)
    follow.current = true
    setHistory(h => [...h, { q: text || '(看图)', images: imgs, steps: [], thought: '', answer: null, typed: '', done: false, sources: [], charts: [] }])
    abortRef.current?.abort()
    const ctrl = new AbortController(); abortRef.current = ctrl
    try {
      const resp = await fetch('/api/ask/stock/stream', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text, history: hist, images: imgs }), signal: ctrl.signal,
      })
      const reader = resp.body.getReader(); const dec = new TextDecoder()
      let buf = ''
      let fAnswer = null; const fSources = []; const fSteps = []; const fCharts = []   // 本地累计, 供持久化
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const parts = buf.split('\n\n'); buf = parts.pop()   // 留下不完整的最后一段
        for (const p of parts) {
          const line = p.split('\n').find(l => l.startsWith('data: '))
          if (!line) continue
          let ev; try { ev = JSON.parse(line.slice(6)) } catch { continue }
          if (ev.type === 'answer') fAnswer = ev.text
          else if (ev.type === 'sources') fSources.push(...(ev.sources || []))
          else if (ev.type === 'step') fSteps.push({ tool: ev.tool })
          else if (ev.type === 'chart') fCharts.push(ev.url)
          handleEv(ev)
        }
      }
      if (fAnswer != null) persistTurn(text || '(看图)', { answer: fAnswer, steps: fSteps, sources: fSources, charts: fCharts, images: imgs })
    } catch (e) {
      if (e.name !== 'AbortError') patchLast(it => it.answer == null ? { ...it, err: '连接中断', done: true } : it)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-3">
        <h3 className={`${page ? 'text-[16px]' : 'text-[14px]'} font-semibold text-text-bright m-0`}>问问市场</h3>
        <span className="text-[10.5px] text-text-muted hidden sm:inline">
          {page ? '挂了28个数据工具的AI · 裸K量价/资金流/基本面/筹码 · 产业链全景 · 联网带来源' : '个股涨跌/消息 · 这周市场什么风格 · 资金主线'}
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

      <div ref={scrollBox} onScroll={onScroll} className={`space-y-3 mb-3 ${history.length ? `${page ? 'max-h-[82vh] min-h-[60vh]' : 'max-h-[58vh]'} overflow-y-auto pr-1` : ''}`}>
        {history.map((it, i) => (
          <div key={i}>
            <div className="text-[12px] text-text-bright bg-surface-3 rounded-lg px-3 py-1.5 inline-block">{it.q}</div>
            {it.images?.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                {it.images.map((src, k) => (
                  <img key={k} src={src} alt="" className="h-20 w-auto rounded-lg border border-border-subtle object-cover" />
                ))}
              </div>
            )}
            <div className="mt-2 px-3 py-2.5 rounded-lg bg-accent/8 border border-accent/25">
              {/* 步骤实时流: 工具调用胶囊 */}
              {it.steps.length > 0 && (() => {
                const settled = it.answer != null || it.done
                return (
                  <div className="mb-2">
                    <div className="flex items-center gap-1.5 mb-1.5 text-[10px] text-text-muted">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                        strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                        <path d="M14.5 4.5a4 4 0 00-5 5L4 15v5h5l5.5-5.5a4 4 0 005-5l-3 3-2.5-2.5z" />
                      </svg>
                      <span>{settled ? '调用了' : '正在取数据'}</span>
                      <span className="font-mono text-text-dim">{it.steps.length}</span>
                      <span>个工具</span>
                      {!settled && <span className="flex gap-0.5 ml-0.5">
                        <span className="w-1 h-1 rounded-full bg-accent animate-bounce" style={{ animationDelay: '0ms' }} />
                        <span className="w-1 h-1 rounded-full bg-accent animate-bounce" style={{ animationDelay: '150ms' }} />
                        <span className="w-1 h-1 rounded-full bg-accent animate-bounce" style={{ animationDelay: '300ms' }} />
                      </span>}
                      <span className="flex-1 h-px bg-border-subtle ml-1" />
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {it.steps.map((s, j) => (
                        <span key={j}
                          className={`inline-flex items-center gap-1 text-[10.5px] pl-1.5 pr-2 py-[3px] rounded-full border transition-colors ${
                            settled
                              ? 'bg-accent/8 border-accent/25 text-text-dim'
                              : 'bg-accent/12 border-accent/40 text-text'}`}>
                          <ToolIcon tool={s.tool} />
                          <span>{s.label}</span>
                          {s.arg ? <span className="font-mono text-text-muted">{s.arg}</span> : null}
                          {settled
                            ? <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className="text-bull shrink-0"><path d="M5 12.5l4.5 4.5L19 7" /></svg>
                            : <span className="text-accent/50 leading-none">·</span>}
                        </span>
                      ))}
                    </div>
                  </div>
                )
              })()}
              {it.thought && it.answer == null && <div className="text-[11px] text-text-muted italic mb-1.5">{it.thought}</div>}
              {/* AI 渲染的K线图(结构已标注): 我方数据画→精确, 数字以正文为准 */}
              {(it.charts || []).length > 0 && (
                <div className="flex flex-col gap-2 mb-2">
                  {it.charts.map((src, k) => (
                    <a key={k} href={src} target="_blank" rel="noreferrer" className="block">
                      <img src={src} alt="K线图" loading="lazy"
                        className="w-full max-w-[640px] rounded-lg border border-border-subtle" />
                    </a>
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
      <div className="flex gap-2">
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
      </div>
      <div className="text-[10px] text-text-muted pt-2.5 mt-2 border-t border-border-subtle">
        Agent 自取行情/走势/新闻/大盘情绪后客观解读 · 可发图(截图/K线/持仓)让它看 · 纯解读不构成任何买卖建议
      </div>
    </div>
  )
}
