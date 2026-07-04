import { useState } from 'react'

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

export function ToolIcon({ tool }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
      {TOOL_ICONS[tool] || DEFAULT_ICON}
    </svg>
  )
}

// 工具调用流: "调用了N个工具" 标头 + 每个工具的图标胶囊(运行时跳动点, 完成后打勾)。
// 问问市场 / 排行榜弹窗共用, 保证两处样式一致。
export function ToolCallStrip({ steps, settled }) {
  if (!steps || steps.length === 0) return null
  return (
    <div className="mb-2">
      <div className="flex items-center gap-1.5 mb-1.5 text-[10px] text-text-muted">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
          <path d="M14.5 4.5a4 4 0 00-5 5L4 15v5h5l5.5-5.5a4 4 0 005-5l-3 3-2.5-2.5z" />
        </svg>
        <span>{settled ? '调用了' : '正在取数据'}</span>
        <span className="font-mono text-text-dim">{steps.length}</span>
        <span>个工具</span>
        {!settled && <span className="flex gap-0.5 ml-0.5">
          <span className="w-1 h-1 rounded-full bg-accent animate-bounce" style={{ animationDelay: '0ms' }} />
          <span className="w-1 h-1 rounded-full bg-accent animate-bounce" style={{ animationDelay: '150ms' }} />
          <span className="w-1 h-1 rounded-full bg-accent animate-bounce" style={{ animationDelay: '300ms' }} />
        </span>}
        <span className="flex-1 h-px bg-border-subtle ml-1" />
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {steps.map((s, j) => (
          <span key={j}
            className={`inline-flex items-center gap-1 text-[10.5px] pl-1.5 pr-2 py-[3px] rounded-full border transition-colors ${
              settled ? 'bg-accent/8 border-accent/25 text-text-dim' : 'bg-accent/12 border-accent/40 text-text'}`}>
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
}

// 只放行 http(s) 链接, 挡 javascript:/data: 等可执行 scheme
export function safeUrl(url) {
  try { const u = new URL(url); return (u.protocol === 'https:' || u.protocol === 'http:') ? u.href : null }
  catch { return null }
}

export function domainOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return '' }
}

// 正文内联引用角标 ⟦N⟧ → 可点上标
export function CiteMark({ n, src }) {
  const cls = 'align-super text-[8.5px] font-medium text-accent/90 hover:text-accent px-[1px]'
  const href = src && safeUrl(src.url)
  if (!href) return <sup className={cls}>[{n}]</sup>
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" title={src.title}
      className={`${cls} no-underline hover:underline cursor-pointer`}>[{n}]</a>
  )
}

// 带符号涨跌数字上色(A股 红涨绿跌); 排除时间段/日期/代码区间误判
export function colorizeSigned(text, kp) {
  return text.split(/((?<![\d:.])[+-]\d[\d,.]*%?(?!:))/g).map((seg, j) => {
    const m = seg.match(/^([+-])\d[\d,.]*%?$/)
    if (m && !seg.endsWith(':')) return <span key={`${kp}-s${j}`} className={m[1] === '+' ? 'text-bear' : 'text-bull'}>{seg}</span>
    return seg
  })
}

function renderInlineBase(text, kp, sources) {
  return text.split(/(\*\*[^*]+\*\*|⟦\d+⟧)/g).map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**'))
      return <strong key={`${kp}-${i}`} className="text-text-bright">{colorizeSigned(p.slice(2, -2), `${kp}-${i}`)}</strong>
    const m = p.match(/^⟦(\d+)⟧$/)
    if (m) { const n = parseInt(m[1], 10); return <CiteMark key={`${kp}-${i}`} n={n} src={sources && sources[n - 1]} /> }
    return <span key={`${kp}-${i}`}>{colorizeSigned(p, `${kp}-${i}`)}</span>
  })
}

const isTableRow = (t) => t.startsWith('|') && t.indexOf('|', 1) > 0
const isTableSep = (t) => /^\|?[\s:|-]+\|[\s:|-]*$/.test(t) && t.includes('-')
const splitCells = (t) => t.replace(/^\||\|$/g, '').split('|').map(c => c.trim())

// 极简 markdown(## 标题/**粗**/列表/表格/⟦N⟧引用/红涨绿跌), 不引依赖
export function MiniMarkdown({ text, sources }) {
  const renderInline = (t, kp) => renderInlineBase(t, kp, sources)
  const lines = (text || '').replace(/<\/?cite[^>]*>/g, '').split('\n')
  const out = []
  let i = 0
  while (i < lines.length) {
    const t = lines[i].trim()
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

// 联网来源列表(折叠)
export function SourcesBlock({ sources }) {
  const [open, setOpen] = useState(false)
  if (!sources || sources.length === 0) return null
  return (
    <div className="mt-2.5 pt-2 border-t border-border-subtle">
      <button onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 text-[10.5px] text-text-muted hover:text-text-dim">
        <span>联网来源</span><span className="font-mono text-text-dim">{sources.length}</span>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`}><path d="M9 6l6 6-6 6" /></svg>
      </button>
      {open && (
        <ol className="mt-1.5 space-y-1 max-h-52 overflow-y-auto pr-1">
          {sources.map((s, i) => {
            const href = safeUrl(s.url)
            const inner = (<>
              <span className="block truncate group-hover:underline">{s.title}</span>
              <span className="block truncate text-[9.5px] text-text-muted">{domainOf(s.url)}{s.age ? ` · ${s.age}` : ''}</span>
            </>)
            return (
              <li key={i} className="flex gap-1.5 text-[11px] leading-snug">
                <span className="text-text-muted font-mono shrink-0 w-4 text-right">{i + 1}</span>
                {href
                  ? <a href={href} target="_blank" rel="noopener noreferrer" className="group min-w-0 flex-1 hover:text-accent text-text-dim">{inner}</a>
                  : <span className="group min-w-0 flex-1 text-text-dim">{inner}</span>}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}

// 跑一次单轮分析(SSE): 给定 question(可带 history 支持追问), 回调 onStep/onChart/onSource/onAnswer。
export function streamAnalysis(question, { onStep, onChart, onSource, onAnswer, onDone, onError, signal, history } = {}) {
  fetch('/api/ask/stock/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, history: history || [] }), signal,
  }).then(async (resp) => {
    const reader = resp.body.getReader(); const dec = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      const parts = buf.split('\n\n'); buf = parts.pop()
      for (const p of parts) {
        const line = p.split('\n').find(l => l.startsWith('data: '))
        if (!line) continue
        let ev; try { ev = JSON.parse(line.slice(6)) } catch { continue }
        if (ev.type === 'step') onStep?.(ev)
        else if (ev.type === 'chart') onChart?.(ev)
        else if (ev.type === 'sources') onSource?.(ev.sources || [])
        else if (ev.type === 'answer') onAnswer?.(ev.text || '')
        else if (ev.type === 'error') onError?.(ev.error)
      }
    }
    onDone?.()
  }).catch(e => { if (e.name !== 'AbortError') onError?.(String(e)) })
}
