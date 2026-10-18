import { useCallback, useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { openStock } from './ask/stockOpener'

// 交易计划: 收盘后定次日计划 → 盘中触发推飞书+页面 → 收盘后对照实际成交。
// 计划由用户写, 这里只负责记录、展示状态、对账。也可以在问答里口述, agent 会调 save_trade_plan 记下。
export const PLAN_EVENT = 'licai:plan-alert'

const STATUS = {
  active: ['等待触发', 'text-text-dim border-border'],
  triggered: ['已触发', 'text-accent border-accent/50'],
  invalidated: ['已失效', 'text-bull border-bull/40'],
  cancelled: ['已取消', 'text-text-muted border-border-subtle'],
  expired: ['已过期', 'text-text-muted border-border-subtle'],
}
const VERDICT_CLS = {
  '按计划执行': 'text-accent', '没到触发价就成交': 'text-bear', '失效后仍成交': 'text-bear',
  '触发未执行': 'text-text-bright', '失效未执行': 'text-text-dim', '未触发': 'text-text-muted',
}
const SIDE = { BUY: '买入', SELL: '卖出' }

function cond(p) {
  return `${p.trigger_op === '<=' ? '≤' : '≥'} ${p.trigger_price}`
}

function PlanRow({ p, onCancel }) {
  const [label, cls] = STATUS[p.status] || [p.status, 'text-text-dim border-border']
  return (
    <tr className="border-b border-border-subtle text-[12px]">
      <td className="px-2 py-1.5 whitespace-nowrap text-text-muted">{p.plan_date.slice(5)}</td>
      <td className="px-2 py-1.5 whitespace-nowrap">
        <button onClick={() => openStock(p.code, p.name)} className="text-text-bright hover:text-accent hover:underline cursor-pointer">
          {p.name}({p.code})</button>
      </td>
      <td className={`px-2 py-1.5 whitespace-nowrap ${p.side === 'BUY' ? 'text-bear' : 'text-bull'}`}>{SIDE[p.side]}</td>
      <td className="px-2 py-1.5 whitespace-nowrap text-text">{cond(p)}{p.qty ? ` · ${p.qty}股` : ''}</td>
      <td className="px-2 py-1.5 whitespace-nowrap text-text-dim">{p.invalid_price ? `${p.invalid_op === '<=' ? '≤' : '≥'} ${p.invalid_price}` : '—'}</td>
      <td className="px-2 py-1.5 text-text-dim max-w-[260px] truncate" title={p.reason}>{p.reason || '—'}</td>
      <td className="px-2 py-1.5 whitespace-nowrap">
        <span className={`inline-block border rounded px-1.5 py-[1px] text-[11px] ${cls}`}>{label}</span>
        {p.triggered_at && <span className="ml-1.5 text-[11px] text-text-muted">{p.triggered_at.slice(0, 5)} @{p.triggered_price}</span>}
      </td>
      <td className="px-2 py-1.5 text-right">
        {(p.status === 'active' || p.status === 'triggered') && (
          <button onClick={() => onCancel(p.id)} className="text-[11px] text-text-muted hover:text-bear cursor-pointer">取消</button>
        )}
      </td>
    </tr>
  )
}

function AddForm({ nextDay, onAdded }) {
  const [f, setF] = useState({ code: '', side: 'BUY', trigger_op: '<=', trigger_price: '', qty: '', invalid_op: '<=', invalid_price: '', reason: '', plan_date: '' })
  const [err, setErr] = useState('')
  const set = (k) => (e) => setF(x => ({ ...x, [k]: e.target.value }))
  const submit = async () => {
    setErr('')
    const body = {
      code: f.code.trim(), side: f.side, trigger_op: f.trigger_op, trigger_price: parseFloat(f.trigger_price),
      plan_date: f.plan_date || nextDay, reason: f.reason,
      ...(f.qty ? { qty: parseInt(f.qty, 10) } : {}),
      ...(f.invalid_price ? { invalid_op: f.invalid_op, invalid_price: parseFloat(f.invalid_price) } : {}),
    }
    try {
      await fetchJSON('/api/plans', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      setF(x => ({ ...x, code: '', trigger_price: '', qty: '', invalid_price: '', reason: '' }))
      onAdded()
    } catch (e) { setErr(e?.message || '保存失败') }
  }
  const inp = 'bg-surface-2 border border-border rounded px-2 py-1 text-[12px] text-text outline-none focus:border-accent/60'
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-3 py-2 border-t border-border-subtle">
      <input className={`${inp} w-24`} placeholder="代码" value={f.code} onChange={set('code')} />
      <select className={inp} value={f.side} onChange={set('side')}><option value="BUY">买入</option><option value="SELL">卖出</option></select>
      <select className={inp} value={f.trigger_op} onChange={set('trigger_op')}><option value="<=">跌到 ≤</option><option value=">=">涨到 ≥</option></select>
      <input className={`${inp} w-20`} placeholder="触发价" value={f.trigger_price} onChange={set('trigger_price')} />
      <input className={`${inp} w-20`} placeholder="数量" value={f.qty} onChange={set('qty')} />
      <span className="text-[11px] text-text-muted">失效</span>
      <select className={inp} value={f.invalid_op} onChange={set('invalid_op')}><option value="<=">≤</option><option value=">=">≥</option></select>
      <input className={`${inp} w-20`} placeholder="失效价" value={f.invalid_price} onChange={set('invalid_price')} />
      <input className={`${inp} flex-1 min-w-[140px]`} placeholder="理由(为什么这么做)" value={f.reason} onChange={set('reason')} />
      <input className={`${inp} w-[118px]`} type="date" value={f.plan_date || nextDay || ''} onChange={set('plan_date')} />
      <button onClick={submit} disabled={!f.code || !f.trigger_price}
        className="px-3 py-1 rounded bg-accent/20 text-accent text-[12px] hover:bg-accent/30 disabled:opacity-40 cursor-pointer">记下</button>
      {err && <span className="text-[11px] text-bear w-full">{err}</span>}
    </div>
  )
}

function Review({ date }) {
  const [r, setR] = useState(null)
  useEffect(() => {
    fetchJSON(`/api/plans/review?date=${date}`).then(setR).catch(() => setR(null))
  }, [date])
  if (!r) return null
  const counts = Object.entries(r['汇总'] || {})
  return (
    <div className="px-3 py-2 border-t border-border-subtle">
      <div className="flex flex-wrap items-baseline gap-2 text-[12px] mb-1.5">
        <span className="font-medium text-text-bright">{r.date} 计划对照执行</span>
        {counts.map(([k, n]) => <span key={k} className={VERDICT_CLS[k] || 'text-text-dim'}>{k} {n}</span>)}
        {r['计划外成交']?.length > 0 && <span className="text-bear">计划外成交 {r['计划外成交'].length}</span>}
        {r['提示'] && <span className="text-text-muted">· {r['提示']}</span>}
      </div>
      {(r.plans?.length > 0 || r['计划外成交']?.length > 0) && (
        <table className="w-full text-[11.5px] border-collapse">
          <tbody>
            {r.plans.map((p, i) => (
              <tr key={i} className="border-b border-border-subtle">
                <td className="px-2 py-1 whitespace-nowrap">
                  <button onClick={() => openStock(p.code, p.name)} className="text-text hover:text-accent hover:underline cursor-pointer">{p.name}({p.code})</button>
                </td>
                <td className="px-2 py-1 text-text-dim">{p['计划']}</td>
                <td className={`px-2 py-1 whitespace-nowrap font-medium ${VERDICT_CLS[p['对照']] || ''}`}>{p['对照']}</td>
                <td className="px-2 py-1 whitespace-nowrap text-text-dim">
                  {p['成交均价'] ? `成交 ${p['成交数量']} @ ${p['成交均价']}` : ''}
                  {p['均价较触发价%'] != null ? ` (较触发价 ${p['均价较触发价%'] >= 0 ? '+' : ''}${p['均价较触发价%']}%)` : ''}
                  {p['数量偏差'] ? ` 数量偏差 ${p['数量偏差'] > 0 ? '+' : ''}${p['数量偏差']}` : ''}
                </td>
              </tr>
            ))}
            {(r['计划外成交'] || []).map((f, i) => (
              <tr key={`o${i}`} className="border-b border-border-subtle">
                <td className="px-2 py-1 whitespace-nowrap">
                  <button onClick={() => openStock(f.code, f.name)} className="text-text hover:text-accent hover:underline cursor-pointer">{f.name && f.name !== f.code ? `${f.name}(${f.code})` : f.code}</button>
                </td>
                <td className="px-2 py-1 text-text-muted">没有对应计划</td>
                <td className="px-2 py-1 whitespace-nowrap font-medium text-bear">计划外成交</td>
                <td className="px-2 py-1 whitespace-nowrap text-text-dim">{SIDE[f.side]} {f.shares} @ {f.price}{f.time ? ` · ${f.time}` : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export default function TradePlans() {
  const [d, setD] = useState(null)
  const [reviewDate, setReviewDate] = useState('')
  const load = useCallback(() => {
    fetchJSON('/api/plans').then(x => { setD(x); setReviewDate(r => r || x.today) }).catch(() => {})
  }, [])
  useEffect(() => {
    load()
    const on = () => load()
    window.addEventListener(PLAN_EVENT, on)
    return () => window.removeEventListener(PLAN_EVENT, on)
  }, [load])

  const cancel = async (id) => {
    await fetchJSON(`/api/plans/${id}`, { method: 'DELETE' }).catch(() => {})
    load()
  }
  const plans = d?.plans || []
  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden">
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-baseline gap-2 flex-wrap"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <h3 className="text-[13px] font-semibold text-text-bright m-0">交易计划</h3>
        <span className="text-[11px] text-text-dim">你定计划 · 盘中到价推飞书 · 收盘后对照执行 · 也可在问答里口述让 AI 记下</span>
      </div>
      {plans.length === 0
        ? <div className="px-3 py-3 text-[12px] text-text-muted">今天({d?.today})和下一个交易日({d?.next})还没有计划。</div>
        : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead><tr className="text-[11px] text-text-muted text-left">
                {['日期', '标的', '方向', '触发', '失效', '理由', '状态', ''].map(h => <th key={h} className="px-2 py-1 font-normal">{h}</th>)}
              </tr></thead>
              <tbody>{plans.map(p => <PlanRow key={p.id} p={p} onCancel={cancel} />)}</tbody>
            </table>
          </div>
        )}
      <AddForm nextDay={d?.next} onAdded={load} />
      <div className="px-3 pt-2 flex items-center gap-2 text-[11px] text-text-muted border-t border-border-subtle">
        复盘日期
        <input type="date" value={reviewDate} onChange={e => setReviewDate(e.target.value)}
          className="bg-surface-2 border border-border rounded px-2 py-0.5 text-[11px] text-text" />
      </div>
      {reviewDate && <Review date={reviewDate} />}
    </section>
  )
}

// 盘中计划触发/失效的页面提示(飞书同时也推)。挂在 App 根上。
export function PlanToast() {
  const [items, setItems] = useState([])
  useEffect(() => {
    const on = (e) => {
      const ev = e.detail
      if (!ev?.text) return
      const id = `${ev.plan_id}-${ev.status}`
      setItems(xs => [...xs.filter(x => x.id !== id), { id, ...ev }])
    }
    window.addEventListener(PLAN_EVENT, on)
    return () => window.removeEventListener(PLAN_EVENT, on)
  }, [])
  if (!items.length) return null
  return (
    <div className="fixed right-4 bottom-4 z-[300] flex flex-col gap-2 max-w-[360px]">
      {items.map(x => (
        <div key={x.id} className="rounded-lg border border-accent/50 bg-surface-2 shadow-lg px-3 py-2 text-[12px]">
          <div className="flex items-start justify-between gap-2">
            <span className={`font-semibold ${x.status === 'triggered' ? 'text-accent' : 'text-bull'}`}>
              {x.status === 'triggered' ? '计划触发' : '计划失效'} · {x.name}({x.code}) {x.price}
            </span>
            <button onClick={() => setItems(xs => xs.filter(y => y.id !== x.id))} className="text-text-muted hover:text-text cursor-pointer">×</button>
          </div>
          <div className="mt-1 whitespace-pre-line text-text-dim">{x.text.split('\n').slice(1).join('\n')}</div>
          <button onClick={() => openStock(x.code, x.name)} className="mt-1 text-[11px] text-accent hover:underline cursor-pointer">打开详情</button>
        </div>
      ))}
    </div>
  )
}
