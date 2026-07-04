import { useState } from 'react'

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

// 跑一次单轮分析(SSE): 给定 question, 回调 onStep/onChart/onSource/onAnswer。返回 abort 函数。
export function streamAnalysis(question, { onStep, onChart, onSource, onAnswer, onDone, onError, signal } = {}) {
  fetch('/api/ask/stock/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }), signal,
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
