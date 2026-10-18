import { useState, useEffect, useMemo } from 'react'
import { fetchJSON } from '../hooks/useApi'
import StockKlineModal from './StockKlineModal'

// 分析插件的每日清单(services/analyzers.py 的 daily_lists 约定)。
// 清单由插件离线生成, 这里只负责展示: 列定义来自插件, 名称/行业/市值由路由按代码补。
// 没装插件或插件没提供清单时整块不渲染。点表头排序, 点一行打开这只票的 K 线。
// 可选字段: group_title(整块标题); kind:'stats'(行不是股票, 只做统计表展示);
//   plan:{price_key, side, op, reason}(每行给「记入计划」, 价格取 row[price_key], reason 里的 {key} 用该行的值替换)。

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

function fillTpl(tpl, row) {
  return String(tpl || '').replace(/\{(\w+)\}/g, (_, k) => (row[k] ?? ''))
}

// 从清单行记一条交易计划: 价格/方向由清单给, 数量、失效价、日期由用户定
function PlanInline({ row, spec, nextDay, onClose }) {
  const [f, setF] = useState({ price: row[spec.price_key] ?? '', qty: '', invalid_price: '', plan_date: nextDay || '',
    reason: fillTpl(spec.reason, row) })
  const [msg, setMsg] = useState('')
  const set = (k) => (e) => setF(x => ({ ...x, [k]: e.target.value }))
  const submit = async () => {
    setMsg('')
    const body = {
      code: row.code, side: spec.side || 'BUY', trigger_op: spec.op || '<=', trigger_price: parseFloat(f.price),
      plan_date: f.plan_date || nextDay, reason: f.reason,
      ...(f.qty ? { qty: parseInt(f.qty, 10) } : {}),
      ...(f.invalid_price ? { invalid_op: (spec.side || 'BUY') === 'BUY' ? '<=' : '>=', invalid_price: parseFloat(f.invalid_price) } : {}),
    }
    try {
      await fetchJSON('/api/plans', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      setMsg(`已记入 ${body.plan_date} 的计划, 在复盘页「交易计划」里看和改`)
    } catch (e) { setMsg(e?.message || '保存失败') }
  }
  const inp = 'bg-surface border border-border rounded px-2 py-1 text-[11.5px] text-text outline-none focus:border-accent/60'
  const sideTxt = (spec.side || 'BUY') === 'BUY' ? '买入' : '卖出'
  const opTxt = (spec.op || '<=') === '<=' ? '跌到 ≤' : '涨到 ≥'
  return (
    <div className="mt-2 rounded-lg border border-accent/40 bg-surface-3/60 px-3 py-2">
      <div className="flex flex-wrap items-center gap-1.5 text-[11.5px]">
        <span className="text-text-bright">{row.name || row.code}<span className="text-text-muted font-mono ml-1">{row.code}</span></span>
        <span className="text-text-muted">{sideTxt} · {opTxt}</span>
        <input className={`${inp} w-20`} value={f.price} onChange={set('price')} title="触发价" />
        <input className={`${inp} w-20`} placeholder="数量" value={f.qty} onChange={set('qty')} />
        <span className="text-text-muted">失效 ≤</span>
        <input className={`${inp} w-20`} placeholder="可不填" value={f.invalid_price} onChange={set('invalid_price')} />
        <input className={`${inp} w-[118px]`} type="date" value={f.plan_date} onChange={set('plan_date')} />
        <input className={`${inp} flex-1 min-w-[160px]`} value={f.reason} onChange={set('reason')} title="理由" />
        <button onClick={submit} disabled={!f.price}
          className="px-3 py-1 rounded bg-accent/20 text-accent text-[11.5px] hover:bg-accent/30 disabled:opacity-40 cursor-pointer">记下</button>
        <button onClick={onClose} className="text-[11px] text-text-muted hover:text-text cursor-pointer">关闭</button>
      </div>
      {msg && <div className="text-[11px] text-text-dim mt-1">{msg}</div>}
    </div>
  )
}

function ListTable({ lst, onPick, onPlan }) {
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
  const stats = lst.kind === 'stats'
  if (!rows.length) return <div className="text-[11px] text-text-dim py-2">{stats ? '还没有数据' : '当天没有符合的票'}</div>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] border-collapse">
        <thead>
          <tr className="text-[10px] text-text-muted">
            {!stats && <th className="text-left font-normal py-1 pr-3">名称 · 代码</th>}
            {!stats && <th className="text-left font-normal py-1 pr-3">行业</th>}
            {cols.map(c => (
              <th key={c.key} onClick={() => click(c.key)}
                className="text-right font-normal py-1 px-2 cursor-pointer hover:text-text whitespace-nowrap">
                {c.label}{sort?.key === c.key ? (sort.desc ? ' ↓' : ' ↑') : ''}
              </th>
            ))}
            {lst.plan && <th />}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, shown).map((r, i) => (
            <tr key={stats ? i : r.code} onClick={stats ? undefined : () => onPick(r)}
              className={`border-t border-border/60 ${stats ? '' : 'hover:bg-surface-3 cursor-pointer'}`}>
              {!stats && (
                <td className="py-[3px] pr-3 whitespace-nowrap">
                  <span className="text-text-bright">{r.name || r.code}</span>
                  <span className="text-text-muted font-mono text-[10px] ml-1.5">{r.code}</span>
                </td>
              )}
              {!stats && <td className="py-[3px] pr-3 text-text-muted whitespace-nowrap">{r.industry || ''}</td>}
              {cols.map(c => (
                <td key={c.key} className="py-[3px] px-2 text-right font-mono whitespace-nowrap"
                  style={{ color: cellColor(r[c.key], c.fmt, r) }}>{fmt(r[c.key], c.fmt)}</td>
              ))}
              {lst.plan && (
                <td className="py-[3px] pl-2 text-right whitespace-nowrap">
                  <button onClick={(e) => { e.stopPropagation(); onPlan(r) }}
                    className="text-[10.5px] text-accent hover:underline cursor-pointer">记入计划</button>
                </td>
              )}
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
  const [planRow, setPlanRow] = useState(null)
  const [days, setDays] = useState({ today: '', next: '' })
  useEffect(() => {
    let alive = true
    fetchJSON('/api/market/analyzer-lists').then(d => alive && setLists(d?.lists || [])).catch(() => alive && setLists([]))
    fetchJSON('/api/plans').then(d => alive && setDays({ today: d?.today || '', next: d?.next || '' })).catch(() => {})
    return () => { alive = false }
  }, [])
  if (!lists || !lists.length) return null
  const cur = lists[Math.min(tab, lists.length - 1)]
  // 计划默认日期 = 清单日之后的第一个交易日: 盘中看的是昨天收盘的清单 → 今天; 收盘后新清单出来 → 下一个交易日
  const planDay = cur.date && days.today && cur.date < days.today ? days.today : days.next
  const pick = (r) => setKline({ stock_code: r.code, stock_name: r.name || r.code, cost_price: 0, shares: 0, current_price: r.close })
  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-2 flex-wrap">
        <h3 className="text-[14px] font-semibold text-text-bright m-0">{cur.group_title || lists[0].group_title || '每日清单'}</h3>
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
          <button key={`${l.analyzer}-${l.id}`} onClick={() => { setTab(i); setPlanRow(null) }}
            className={`text-[11px] px-2 py-0.5 rounded cursor-pointer border ${i === tab ? 'border-accent text-text-bright' : 'border-border text-text-muted hover:text-text'}`}>
            {l.title}({(l.rows || []).length})
          </button>
        ))}
      </div>
      {planRow && cur.plan && (
        <PlanInline key={planRow.code} row={planRow} spec={cur.plan} nextDay={planDay} onClose={() => setPlanRow(null)} />
      )}
      <ListTable key={`${cur.analyzer}-${cur.id}`} lst={cur} onPick={pick} onPlan={setPlanRow} />
      {kline && <StockKlineModal holding={kline} onClose={() => setKline(null)} />}
    </div>
  )
}
