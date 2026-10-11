import { useState, useEffect, useMemo } from 'react'
import { fetchJSON } from '../hooks/useApi'
import StockKlineModal from './StockKlineModal'

// 分析插件的每日清单(services/analyzers.py 的 daily_lists 约定)。
// 清单由插件离线生成, 这里只负责展示: 列定义来自插件, 名称/行业/市值由路由按代码补。
// 没装插件或插件没提供清单时整块不渲染。点表头排序, 点一行打开这只票的 K 线。

const TONE = {
  pos: 'var(--color-bear-bright)',     // A 股口径: 正 = 红
  neg: 'var(--color-bull-bright)',     // 负 = 绿
  muted: 'var(--color-text-muted)',
}
const PAGE = 60

function fmt(v, f) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  switch (f) {
    case 'int': return String(Math.round(v))
    case 'num2': return Number(v).toFixed(2)
    case 'pct1': return `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}%`
    case 'pct2': return `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`
    case 'share': return `${Math.round(v * 100)}%`
    case 'decile': return `第 ${v} 档`
    default: return String(v)
  }
}

function cellColor(v, f, row) {
  if (f === 'decile') return TONE[row._tone] || 'var(--color-text)'
  if ((f === 'pct1' || f === 'pct2') && typeof v === 'number' && v !== 0) return v > 0 ? TONE.pos : TONE.neg
  return 'var(--color-text-dim)'
}

function ListTable({ lst, onPick }) {
  const [sort, setSort] = useState(null)        // {key, desc}
  const [shown, setShown] = useState(PAGE)
  const rows = useMemo(() => {
    const r = [...(lst.rows || [])]
    if (sort) {
      r.sort((a, b) => {
        const x = a[sort.key], y = b[sort.key]
        if (x === y) return 0
        if (x === null || x === undefined) return 1
        if (y === null || y === undefined) return -1
        return (x > y ? 1 : -1) * (sort.desc ? -1 : 1)
      })
    }
    return r
  }, [lst, sort])
  const click = (key) => setSort(s => (s && s.key === key ? { key, desc: !s.desc } : { key, desc: true }))
  const cols = lst.columns || []
  if (!rows.length) return <div className="text-[11px] text-text-dim py-2">当天没有符合的票</div>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] border-collapse">
        <thead>
          <tr className="text-[10px] text-text-muted">
            <th className="text-left font-normal py-1 pr-3">名称 · 代码</th>
            <th className="text-left font-normal py-1 pr-3">行业</th>
            {cols.map(c => (
              <th key={c.key} onClick={() => click(c.key)}
                className="text-right font-normal py-1 px-2 cursor-pointer hover:text-text whitespace-nowrap">
                {c.label}{sort?.key === c.key ? (sort.desc ? ' ↓' : ' ↑') : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, shown).map(r => (
            <tr key={r.code} onClick={() => onPick(r)}
              className="border-t border-border/60 hover:bg-surface-3 cursor-pointer">
              <td className="py-[3px] pr-3 whitespace-nowrap">
                <span className="text-text-bright">{r.name || r.code}</span>
                <span className="text-text-muted font-mono text-[10px] ml-1.5">{r.code}</span>
              </td>
              <td className="py-[3px] pr-3 text-text-muted whitespace-nowrap">{r.industry || ''}</td>
              {cols.map(c => (
                <td key={c.key} className="py-[3px] px-2 text-right font-mono whitespace-nowrap"
                  style={{ color: cellColor(r[c.key], c.fmt, r) }}>{fmt(r[c.key], c.fmt)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > shown && (
        <button onClick={() => setShown(n => n + PAGE)}
          className="mt-1.5 text-[10.5px] text-text-muted hover:text-text cursor-pointer">
          再显示 {Math.min(PAGE, rows.length - shown)} 只(共 {rows.length})
        </button>
      )}
    </div>
  )
}

export default function AnalyzerLists() {
  const [lists, setLists] = useState(null)
  const [tab, setTab] = useState(0)
  const [openNote, setOpenNote] = useState(false)
  const [kline, setKline] = useState(null)
  useEffect(() => {
    let alive = true
    fetchJSON('/api/market/analyzer-lists').then(d => alive && setLists(d?.lists || [])).catch(() => alive && setLists([]))
    return () => { alive = false }
  }, [])
  if (!lists || !lists.length) return null
  const cur = lists[Math.min(tab, lists.length - 1)]
  const pick = (r) => setKline({ stock_code: r.code, stock_name: r.name || r.code, cost_price: 0, shares: 0, current_price: r.close })
  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-2 flex-wrap">
        <h3 className="text-[14px] font-semibold text-text-bright m-0">插件清单</h3>
        <span className="text-[10.5px] text-text-muted">{cur.display_name} · 数据日 {cur.date}</span>
        {cur.note && (
          <button onClick={() => setOpenNote(v => !v)} className="ml-auto text-[10.5px] text-text-muted hover:text-text cursor-pointer">
            {openNote ? '收起说明' : '说明'}
          </button>
        )}
      </div>
      {openNote && <div className="text-[10.5px] text-text-dim leading-relaxed mb-2">{cur.note}</div>}
      <div className="flex gap-1.5 mb-2 flex-wrap">
        {lists.map((l, i) => (
          <button key={`${l.analyzer}-${l.id}`} onClick={() => setTab(i)}
            className={`text-[11px] px-2 py-0.5 rounded cursor-pointer border ${i === tab ? 'border-accent text-text-bright' : 'border-border text-text-muted hover:text-text'}`}>
            {l.title}({(l.rows || []).length})
          </button>
        ))}
      </div>
      <ListTable key={`${cur.analyzer}-${cur.id}`} lst={cur} onPick={pick} />
      {kline && <StockKlineModal holding={kline} onClose={() => setKline(null)} />}
    </div>
  )
}
