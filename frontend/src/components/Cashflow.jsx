import { useState, useEffect, useCallback, useMemo } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtMoney } from '../helpers'

// 当月 YYYY-MM
function nowMonth() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

const colorPos = (v) => v == null ? 'text-text-dim'
  : v > 0 ? 'text-bull-bright'  // 储蓄/收入正向 = 绿
  : v < 0 ? 'text-bear-bright'  // 净支出 = 红
  : 'text-text-dim'

// 12 months ago → now (含当前)
function recentMonths(n) {
  const out = []
  const d = new Date()
  for (let i = 0; i < n; i++) {
    out.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
    d.setMonth(d.getMonth() - 1)
  }
  return out.reverse()
}

function BudgetTracker({ summary, onSaveRate }) {
  const [editingRate, setEditingRate] = useState(false)
  const [rateInput, setRateInput] = useState('')
  const cur = summary?.current
  const cap = summary?.hard_cap
  const soft = summary?.soft_avg
  const rate = summary?.savings_rate_target ?? 0.3
  const actual = cur?.discretionary ?? 0

  if (!cap && soft == null && !cur) return null

  const startEdit = () => {
    setRateInput((rate * 100).toFixed(0))
    setEditingRate(true)
  }
  const saveRate = async () => {
    const v = parseFloat(rateInput)
    if (isNaN(v) || v < 0 || v > 95) {
      alert('储蓄率需在 0-95 之间')
      return
    }
    await onSaveRate(v / 100)
    setEditingRate(false)
  }

  // 进度条 % (相对硬上限). 超过 100% 时仍显示 100% (后面文案标超出量)
  const pct = cap > 0 ? Math.min(100, (actual / cap) * 100) : 0
  // soft_avg 在条上的位置 (相对 cap)
  const softPct = (cap > 0 && soft != null) ? Math.min(100, (soft / cap) * 100) : null

  // 状态颜色
  const overCap = cap != null && actual > cap
  const overSoft = soft != null && actual > soft
  const toneClass = overCap ? 'text-bear-bright' : overSoft ? 'text-warn' : 'text-bull-bright'
  const fillColor = overCap ? '#cf5c5c' : overSoft ? '#d4a05c' : '#5fa86c'

  let msg
  if (cap == null && soft == null) {
    msg = <span className="text-text-dim">填一下当月收入/固定开销，就能算出可支配预算上限</span>
  } else if (overCap) {
    msg = <span className="text-bear-bright">已超上限 <span className="font-mono">¥{fmtMoney(actual - cap)}</span> · 需要克制后续支出</span>
  } else if (overSoft) {
    msg = <>
      <span className="text-warn">已超 3 月均值 <span className="font-mono">¥{fmtMoney(actual - soft)}</span></span>
      <span className="text-text-dim mx-1.5">·</span>
      <span className="text-text-dim">距上限还剩 <span className="font-mono text-text">¥{fmtMoney(cap - actual)}</span></span>
    </>
  } else if (cap != null) {
    msg = <>
      <span className="text-bull-bright">剩余 <span className="font-mono">¥{fmtMoney(cap - actual)}</span> 安全</span>
      {soft != null && actual < soft && (
        <span className="text-text-dim ml-2">还在均值之下 <span className="font-mono">¥{fmtMoney(soft - actual)}</span></span>
      )}
    </>
  } else {
    msg = <span className="text-text-dim">本月还没填收入，无法算上限。3 月均值参考: <span className="font-mono text-text">¥{fmtMoney(soft || 0)}</span></span>
  }

  return (
    <div className="bg-surface-3 rounded-md px-3 py-2.5 mb-3">
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-1.5">
        <div className="text-[11px] text-text-dim flex items-baseline gap-1.5 flex-wrap">
          <span className="text-[11.5px] text-text-bright font-semibold">可支配预算</span>
          {cap != null && (
            <>
              <span>· 上限 <span className="font-mono text-text">¥{fmtMoney(cap)}</span></span>
              <span className="text-text-muted">(储蓄率 {(rate * 100).toFixed(0)}% 反推)</span>
            </>
          )}
          {soft != null && (
            <span>· 3 月均 <span className="font-mono text-text">¥{fmtMoney(soft)}</span></span>
          )}
        </div>
        {editingRate ? (
          <div className="flex items-center gap-1">
            <input type="number" min="0" max="95" value={rateInput}
              onChange={e => setRateInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') saveRate() }}
              autoFocus
              className="w-12 bg-bg border border-border rounded px-1.5 py-0.5 text-[11px] font-mono text-text outline-none focus:border-accent" />
            <span className="text-[10.5px] text-text-dim">%</span>
            <button onClick={saveRate}
              className="text-[10.5px] text-accent hover:underline cursor-pointer ml-1">保存</button>
            <button onClick={() => setEditingRate(false)}
              className="text-[10.5px] text-text-dim hover:text-text cursor-pointer ml-1">取消</button>
          </div>
        ) : (
          <button onClick={startEdit}
            className="text-[10.5px] text-text-dim hover:text-accent cursor-pointer underline decoration-dotted underline-offset-2">
            目标储蓄率 {(rate * 100).toFixed(0)}% 改
          </button>
        )}
      </div>

      {cap != null && (
        <>
          <div className="relative h-2.5 bg-bg rounded-full overflow-hidden mb-1.5">
            <div className="absolute top-0 left-0 h-full rounded-full transition-all"
              style={{ width: pct + '%', background: fillColor }} />
            {/* 软线: 3月均位置 */}
            {softPct != null && (
              <div className="absolute top-0 h-full w-px bg-text-muted"
                style={{ left: `calc(${softPct}% - 0.5px)` }} />
            )}
          </div>
          <div className="flex items-baseline justify-between text-[11px]">
            <span className="text-text-dim">
              本月已花 <span className={`font-mono font-semibold ${toneClass}`}>¥{fmtMoney(actual)}</span>
              <span className="text-text-muted"> / ¥{fmtMoney(cap)}</span>
              <span className="text-text-muted ml-1">({(actual / cap * 100).toFixed(0)}%)</span>
            </span>
          </div>
        </>
      )}

      <div className="text-[11px] mt-1.5 leading-relaxed">{msg}</div>
    </div>
  )
}

function NetTrend({ entries }) {
  // 12 月柱图: 净储蓄. 缺月柱子留白. 高度按绝对值最大归一.
  const months = recentMonths(12)
  const byMonth = Object.fromEntries((entries || []).map(e => [e.month, e]))
  const data = months.map(m => ({
    month: m,
    value: byMonth[m] ? (byMonth[m].net_savings || 0) : null,
  }))
  const maxAbs = Math.max(1, ...data.map(d => Math.abs(d.value || 0)))
  const W = 480, H = 60, P = 6
  const barW = (W - P * 2) / months.length - 2

  return (
    <div className="bg-surface-3 rounded-md px-2 py-2">
      <div className="text-[10.5px] text-text-dim mb-1">最近 12 月净储蓄趋势</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-[60px]">
        {/* 0 轴 */}
        <line x1={P} y1={H/2} x2={W-P} y2={H/2} stroke="var(--color-border-subtle)" strokeWidth="1" />
        {data.map((d, i) => {
          const x = P + i * (barW + 2)
          if (d.value == null) return (
            <rect key={i} x={x} y={H/2 - 1} width={barW} height={2}
              fill="var(--color-border-subtle)" opacity="0.6" />
          )
          const h = (Math.abs(d.value) / maxAbs) * (H/2 - 4)
          const y = d.value >= 0 ? H/2 - h : H/2
          const c = d.value >= 0 ? '#5fa86c' : '#cf5c5c'
          return <rect key={i} x={x} y={y} width={barW} height={Math.max(1, h)} fill={c} rx="1" />
        })}
        {/* 标签: 最早/最近 */}
        <text x={P} y={H - 1} fontSize="8" fill="var(--color-text-muted)">
          {months[0].slice(2)}
        </text>
        <text x={W - P} y={H - 1} fontSize="8" fill="var(--color-text-muted)" textAnchor="end">
          {months[months.length - 1].slice(2)}
        </text>
      </svg>
    </div>
  )
}

function CashflowEditor({ entry, month, onSave, onClose }) {
  const [income, setIncome] = useState(entry?.income ?? '')
  const [fixed, setFixed] = useState(entry?.fixed_cost ?? '')
  const [disc, setDisc] = useState(entry?.discretionary ?? '')
  const [notes, setNotes] = useState(entry?.notes ?? '')
  const [saving, setSaving] = useState(false)

  const f = (v) => v === '' ? 0 : parseFloat(v) || 0
  const net = f(income) - f(fixed) - f(disc)

  const handleSave = async () => {
    setSaving(true)
    try {
      await fetchJSON('/api/cashflow', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          month, income: f(income), fixed_cost: f(fixed), discretionary: f(disc), notes,
        }),
      })
      onSave()
      onClose()
    } catch (e) {
      alert('保存失败: ' + e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[420px] max-w-[95vw] space-y-3"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">月度现金流 · {month}</h3>
          <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>

        {[
          ['income', '月收入(税后)', income, setIncome, 'text-bull-bright'],
          ['fixed', '固定开销 (房租 / 餐饮 / 账单 / 还贷)', fixed, setFixed, 'text-text'],
          ['disc',  '可自由支配 (购物 / 娱乐 / 旅行)', disc, setDisc, 'text-warn'],
        ].map(([k, label, v, setter, cls]) => (
          <div key={k}>
            <label className="text-[11.5px] text-text-dim block mb-1">{label}</label>
            <input type="number" inputMode="decimal" placeholder="0"
              value={v} onChange={e => setter(e.target.value)}
              className={`w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent transition-colors ${cls}`} />
          </div>
        ))}

        <div>
          <label className="text-[11.5px] text-text-dim block mb-1">备注 (可选)</label>
          <input type="text" placeholder="" value={notes} onChange={e => setNotes(e.target.value)}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[12px] outline-none focus:border-accent transition-colors" />
        </div>

        <div className="bg-surface-3 rounded-md px-3 py-2 flex items-center justify-between">
          <span className="text-[11.5px] text-text-dim">净储蓄 (= 收入 − 固定 − 可支配)</span>
          <span className={`font-mono font-semibold text-[14px] ${colorPos(net)}`}>
            {net >= 0 ? '+' : ''}¥{fmtMoney(Math.abs(net))}
          </span>
        </div>

        <div className="flex gap-2 pt-1">
          <button onClick={handleSave} disabled={saving}
            className="flex-1 px-4 py-2 rounded-lg bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {saving ? '保存中...' : '保存'}
          </button>
          <button onClick={onClose}
            className="px-4 py-2 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med text-[13px] cursor-pointer">
            取消
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Cashflow() {
  const [summary, setSummary] = useState(null)
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)
  const [editingMonth, setEditingMonth] = useState(null)
  const [showHistory, setShowHistory] = useState(false)

  const reload = useCallback(async () => {
    try {
      const [s, l] = await Promise.all([
        fetchJSON('/api/cashflow/summary'),
        fetchJSON('/api/cashflow?months=12'),
      ])
      setSummary(s)
      setEntries(l.entries || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  const saveRate = useCallback(async (rate) => {
    await fetchJSON('/api/cashflow/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ savings_rate_target: rate }),
    })
    await reload()
  }, [reload])

  const cur = summary?.current
  const curMonth = summary?.current_month || nowMonth()
  const editingEntry = useMemo(
    () => entries.find(e => e.month === editingMonth) || null,
    [entries, editingMonth],
  )

  const savingsRate = (() => {
    if (!summary || !summary.avg_income || summary.avg_income <= 0) return null
    return (summary.avg_net / summary.avg_income) * 100
  })()

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-center justify-between flex-wrap gap-2"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">月度现金流</h3>
          <span className="text-[11px] text-text-dim">收入 · 开销 · 储蓄</span>
        </div>
        <div className="flex gap-1.5">
          <button onClick={() => setEditingMonth(curMonth)}
            className="px-2.5 py-[3px] rounded-md text-[11px] border border-accent bg-accent/15 text-accent hover:bg-accent/25 cursor-pointer">
            {cur ? '编辑当月' : '录入当月'}
          </button>
          <button onClick={() => setShowHistory(s => !s)}
            className="px-2.5 py-[3px] rounded-md text-[11px] border border-border-med text-text-dim hover:text-text hover:border-text-muted cursor-pointer">
            {showHistory ? '隐藏历史' : `历史 ${entries.length}`}
          </button>
        </div>
      </div>

      <div className="px-3 md:px-5 py-3">
        {loading ? (
          <div className="text-text-dim text-[12px] text-center py-2">加载中...</div>
        ) : (
          <>
            {/* 当月 + 平均 卡片组 */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
              <div className="bg-surface-3 rounded-md px-3 py-2">
                <div className="text-text-dim text-[10.5px] mb-0.5">当月收入</div>
                <div className="font-mono font-semibold text-[14px] text-bull-bright">
                  ¥{fmtMoney(cur?.income || 0)}
                </div>
              </div>
              <div className="bg-surface-3 rounded-md px-3 py-2">
                <div className="text-text-dim text-[10.5px] mb-0.5">固定开销</div>
                <div className="font-mono font-semibold text-[14px] text-text">
                  ¥{fmtMoney(cur?.fixed_cost || 0)}
                </div>
              </div>
              <div className="bg-surface-3 rounded-md px-3 py-2">
                <div className="text-text-dim text-[10.5px] mb-0.5">可自由支配</div>
                <div className="font-mono font-semibold text-[14px] text-warn">
                  ¥{fmtMoney(cur?.discretionary || 0)}
                </div>
              </div>
              <div className="bg-surface-3 rounded-md px-3 py-2">
                <div className="text-text-dim text-[10.5px] mb-0.5">当月净储蓄</div>
                <div className={`font-mono font-semibold text-[14px] ${colorPos(cur?.net_savings || 0)}`}>
                  {(cur?.net_savings || 0) >= 0 ? '+' : ''}¥{fmtMoney(Math.abs(cur?.net_savings || 0))}
                </div>
              </div>
            </div>

            {/* 平均行 */}
            {summary && summary.window > 0 && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-text-dim mb-3">
                <span>近 {summary.window} 月平均</span>
                <span>· 收入 <span className="text-text font-mono">¥{fmtMoney(summary.avg_income)}</span></span>
                <span>· 净储蓄 <span className={`font-mono ${colorPos(summary.avg_net)}`}>
                  {summary.avg_net >= 0 ? '+' : ''}¥{fmtMoney(Math.abs(summary.avg_net))}
                </span></span>
                {savingsRate != null && (
                  <span>· 储蓄率 <span className={`font-mono ${savingsRate > 30 ? 'text-bull-bright' : savingsRate > 15 ? 'text-text' : 'text-warn'}`}>
                    {savingsRate.toFixed(1)}%
                  </span></span>
                )}
              </div>
            )}

            <BudgetTracker summary={summary} onSaveRate={saveRate} />

            <NetTrend entries={entries} />

            {/* 历史表格 */}
            {showHistory && (
              <div className="mt-3 border border-border-subtle rounded-md overflow-hidden">
                <div className="grid grid-cols-[1fr_1fr_1fr_1fr_1fr_auto] gap-2 px-2 py-1.5 text-[10.5px] text-text-dim bg-surface-2 border-b border-border-subtle font-medium">
                  <div>月份</div>
                  <div className="text-right">收入</div>
                  <div className="text-right">固定</div>
                  <div className="text-right">可支配</div>
                  <div className="text-right">净储蓄</div>
                  <div className="w-[28px]"></div>
                </div>
                {entries.length === 0 ? (
                  <div className="px-2 py-3 text-center text-[11px] text-text-dim">暂无记录</div>
                ) : entries.map(e => (
                  <div key={e.month} className="grid grid-cols-[1fr_1fr_1fr_1fr_1fr_auto] gap-2 px-2 py-1.5 text-[11.5px] items-center border-b border-border-subtle last:border-b-0 hover:bg-surface-2/40">
                    <div className="font-mono text-text">{e.month}</div>
                    <div className="text-right font-mono text-bull">¥{fmtMoney(e.income || 0)}</div>
                    <div className="text-right font-mono text-text">¥{fmtMoney(e.fixed_cost || 0)}</div>
                    <div className="text-right font-mono text-warn">¥{fmtMoney(e.discretionary || 0)}</div>
                    <div className={`text-right font-mono font-semibold ${colorPos(e.net_savings)}`}>
                      {e.net_savings >= 0 ? '+' : ''}¥{fmtMoney(Math.abs(e.net_savings || 0))}
                    </div>
                    <button onClick={() => setEditingMonth(e.month)}
                      className="text-text-dim hover:text-accent text-[10.5px] px-1 cursor-pointer">编辑</button>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {editingMonth && (
        <CashflowEditor
          month={editingMonth}
          entry={editingEntry}
          onSave={reload}
          onClose={() => setEditingMonth(null)}
        />
      )}
    </section>
  )
}
