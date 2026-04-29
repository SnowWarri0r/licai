import { useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtPrice, fmtCost, fmtMoney, fmtPct, priceColor } from '../helpers'
import NPVPanel from './NPVPanel'
import TrancheLadder from './TrancheLadder'

function HealthPill({ fundamental }) {
  const level = fundamental?.level || 'yellow'
  const labelMap = { green: '可加仓', yellow: '仅浅档', red: '暂停' }
  const colorMap = {
    green: 'text-bull border-bull/40 bg-bull/10',
    yellow: 'text-[var(--color-signal-moderate)] border-[var(--color-signal-moderate)]/40 bg-[var(--color-signal-moderate)]/10',
    red: 'text-bear border-bear/40 bg-bear/10',
  }
  const dotColor = { green: 'bg-bull', yellow: 'bg-[var(--color-signal-moderate)]', red: 'bg-bear' }
  return (
    <div className={`inline-flex items-center gap-1.5 pl-1.5 pr-2.5 py-[3px] rounded-full border text-[11px] font-medium ${colorMap[level]}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${dotColor[level]}`}
        style={{ boxShadow: `0 0 6px currentColor` }} />
      {labelMap[level]}
    </div>
  )
}

export default function UnwindCard({ plan, onChange }) {
  const [generating, setGenerating] = useState(false)
  const [recommendation, setRecommendation] = useState(null)

  const {
    stock_code, stock_name, cost_price, current_price, shares, holding_days,
    nominal_loss_pct, real_cost, real_loss_pct,
    opportunity_cost_accumulated, daily_opportunity_cost,
    price_progress, cost_progress,
    total_budget, used_budget, remaining_budget,
    pending_tranche_cost, overspent, under_funded, budget_status,
    tranches, fundamental,
    unwind_exit_price, can_unwind_now,
    npv_analysis, benchmark,
  } = plan

  const gapPct = current_price > 0
    ? ((unwind_exit_price - current_price) / current_price) * 100
    : 0

  const generatePlan = async () => {
    if (!total_budget || total_budget <= 0) {
      alert('请先在顶部"加仓子弹池"设置总预算并分配')
      return
    }
    setGenerating(true)
    try {
      const rec = await fetchJSON(
        `/api/unwind/recommend/${stock_code}?total_budget=${total_budget}`,
        { method: 'POST' }
      )
      setRecommendation(rec)
    } finally {
      setGenerating(false)
    }
  }

  const savePlan = async () => {
    if (!recommendation) return
    await fetchJSON(`/api/unwind/plans/${stock_code}`, {
      method: 'PUT',
      body: JSON.stringify({
        total_budget: recommendation.recommended_budget,
        tranches: recommendation.tranches.map(t => ({
          idx: t.idx,
          trigger_price: t.trigger_price,
          shares: t.shares,
          requires_health: t.requires_health,
        })),
      }),
    })
    setRecommendation(null)
    onChange?.()
  }

  const priceProg = Math.max(0, Math.min(1, price_progress || 0))
  const costProg = Math.max(0, Math.min(1, cost_progress || 0))
  const progressPct = Math.max(priceProg, costProg) * 100

  return (
    <div
      className="rounded-xl bg-surface overflow-hidden flex flex-col gap-3 border border-border"
      style={{
        animation: 'fade-up 0.35s ease-out backwards',
        boxShadow: '0 8px 24px -12px var(--color-bg-deeper), 0 1px 0 #4d4c5866 inset',
      }}
    >
      {/* Dark rail header */}
      <div className="px-4 py-3.5 bg-surface-3 border-b border-border flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-1.5">
            <span className="text-[16px] font-semibold text-text-bright truncate">{stock_name}</span>
            <span className="text-[11px] font-mono text-text-muted">{stock_code}</span>
          </div>
          <div className="flex items-baseline gap-2.5 mt-0.5">
            <span className="font-mono text-[22px] font-bold text-text-bright tracking-tight">
              ¥{fmtPrice(current_price)}
            </span>
            <span className={`text-[12px] font-mono font-semibold ${priceColor(nominal_loss_pct)}`}>
              {fmtPct(nominal_loss_pct)}
            </span>
          </div>
          <div className="text-[10px] text-text-muted mt-1">
            成本 <span className="font-mono text-text">{fmtCost(cost_price)}</span>
            <span className="mx-1.5">·</span>
            持有 {Math.round((holding_days || 0) / 30)}月
          </div>
        </div>
        <HealthPill fundamental={fundamental} />
      </div>

      <div className="px-4 pb-4 flex flex-col gap-3">
        {/* Cost reality check — 2-col split */}
        <div className="rounded-lg overflow-hidden grid grid-cols-2 gap-px bg-border">
          <div className="px-3 py-2.5 bg-surface-2">
            <div className="text-[10px] uppercase tracking-wider text-text-muted font-semibold">真实成本线</div>
            <div className="font-mono font-semibold text-[15px] text-text-bright mt-0.5">
              ¥{fmtCost(real_cost)}
            </div>
            <div className={`text-[10px] font-mono mt-0.5 ${priceColor(real_loss_pct)}`}>
              TVM {fmtPct(real_loss_pct)}
            </div>
          </div>
          <div className="px-3 py-2.5 bg-surface-2">
            <div className="text-[10px] uppercase tracking-wider text-text-muted font-semibold">每日机会损失</div>
            <div className="font-mono font-semibold text-[15px] text-bear mt-0.5">
              −¥{daily_opportunity_cost?.toFixed(2)}
            </div>
            <div className="text-[10px] font-mono text-text-muted mt-0.5">
              累计 ¥{fmtMoney(opportunity_cost_accumulated)}
            </div>
          </div>
        </div>

        {/* Progress bar — accent gradient */}
        <div>
          <div className="flex justify-between mb-1.5 text-[10px] uppercase tracking-wider text-text-muted">
            <span>解套进度</span>
            <span className="font-mono text-text font-semibold normal-case tracking-normal">
              {progressPct.toFixed(0)}%
            </span>
          </div>
          <div className="relative h-2.5 rounded bg-surface-3 overflow-hidden">
            <div
              className="absolute inset-y-0 left-0 rounded"
              style={{
                width: `${priceProg * 100}%`,
                background: 'linear-gradient(90deg, var(--color-accent-warm), var(--color-accent))',
              }}
            />
            <div
              className="absolute inset-y-0 left-0 rounded bg-accent/40"
              style={{ width: `${costProg * 100}%` }}
            />
          </div>
          <div className="flex justify-between mt-1 text-[9px] font-mono text-text-muted">
            <span>现价 {fmtPrice(current_price)}</span>
            <span>真实成本 {fmtPrice(real_cost)}</span>
          </div>
        </div>

        {/* Benchmark — 沪深300 */}
        {benchmark && benchmark.start_close > 0 && (
          <div className="rounded-lg border border-border-subtle bg-surface-2/50 px-3 py-2 text-[11px]">
            <div className="flex items-center justify-between mb-1">
              <span className="text-text-muted uppercase tracking-wider text-[10px] font-semibold">
                同期机会成本 <span className="normal-case tracking-normal">({benchmark.days}天)</span>
              </span>
              <span className="font-mono text-text-muted text-[10px]">
                建仓 {benchmark.start_date}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <div className="text-text-muted text-[9px] uppercase tracking-wider">沪深300</div>
                <div className={`font-mono font-medium text-[12px] ${benchmark.return_pct >= 0 ? 'text-bear' : 'text-bull'}`}>
                  {benchmark.return_pct >= 0 ? '+' : ''}{(benchmark.return_pct * 100).toFixed(2)}%
                </div>
              </div>
              <div>
                <div className="text-text-muted text-[9px] uppercase tracking-wider">你这只</div>
                <div className={`font-mono font-medium text-[12px] ${priceColor(benchmark.stock_return_pct * 100)}`}>
                  {benchmark.stock_return_pct >= 0 ? '+' : ''}{(benchmark.stock_return_pct * 100).toFixed(2)}%
                </div>
              </div>
              <div>
                <div className="text-text-muted text-[9px] uppercase tracking-wider">机会差额</div>
                <div className={`font-mono font-medium text-[12px] ${benchmark.bench_gap > 0 ? 'text-bear' : 'text-bull'}`}>
                  {benchmark.bench_gap >= 0 ? '+' : ''}{fmtMoney(benchmark.bench_gap)}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Exit price — gradient accent */}
        {unwind_exit_price > 0 && (
          <div
            className={`rounded-lg px-3.5 py-2.5 flex items-center justify-between border ${
              can_unwind_now ? 'border-bull/60 bg-bull/10' : 'border-accent/40'
            }`}
            style={can_unwind_now ? undefined : {
              background: 'linear-gradient(90deg, #c8a87618, transparent)',
            }}
          >
            <div>
              <div className={`text-[10px] uppercase tracking-wider font-semibold ${can_unwind_now ? 'text-bull' : 'text-accent'}`}>
                清仓解套线
              </div>
              <div className="text-[9px] text-text-muted mt-0.5">TVM 调整后回本价</div>
            </div>
            <div className="text-right">
              <div className="font-mono text-[20px] font-bold text-text-bright">
                ¥{fmtPrice(unwind_exit_price)}
              </div>
              <div className={`text-[10px] font-mono ${can_unwind_now ? 'text-bull font-semibold' : 'text-text-dim'}`}>
                {can_unwind_now ? '✓ 可清仓' : `距 +${gapPct.toFixed(1)}%`}
              </div>
            </div>
          </div>
        )}

        {/* NPV micro-bar */}
        <NPVPanel npv={npv_analysis} />

        {/* Tranche table */}
        <TrancheLadder
          tranches={tranches}
          currentPrice={current_price}
          currentHealth={fundamental?.level}
          onExecute={onChange}
        />

        {/* Budget status warning */}
        {budget_status === 'overspent' && (
          <div className="rounded-lg border border-bear/40 bg-bear/10 px-3 py-2 text-[11px]">
            <div className="flex items-center justify-between">
              <span className="text-bear font-semibold">⚠️ 子弹超支</span>
              <span className="font-mono text-bear">−¥{fmtMoney(overspent)}</span>
            </div>
            <div className="text-[10px] text-text-dim mt-1">
              已用 ¥{fmtMoney(used_budget)} &gt; 总预算 ¥{fmtMoney(total_budget)} ·
              建议提高总预算或停止后续档位
            </div>
          </div>
        )}
        {budget_status === 'underfunded' && (
          <div className="rounded-lg border border-[var(--color-signal-moderate)]/40 bg-[var(--color-signal-moderate)]/10 px-3 py-2 text-[11px]">
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-signal-moderate)] font-semibold">⚠️ 子弹不够买完计划</span>
              <span className="font-mono text-[var(--color-signal-moderate)]">差 ¥{fmtMoney(under_funded)}</span>
            </div>
            <div className="text-[10px] text-text-dim mt-1">
              剩余 ¥{fmtMoney(remaining_budget)} · 待执行档位需 ¥{fmtMoney(pending_tranche_cost)} ·
              A 股最小 100 股/档，点"重新生成"可按可行数量重排
            </div>
          </div>
        )}

        {/* Footer */}
        {!recommendation ? (
          <div className="flex items-center justify-between pt-1">
            <div className="text-[11px] text-text-dim">
              子弹 <span className="font-mono text-text font-semibold">¥{fmtMoney(total_budget)}</span>
              <span className="text-text-muted ml-1.5">
                已用 <span className={overspent > 0 ? 'text-bear' : ''}>{fmtMoney(used_budget)}</span>
                <span className="mx-1">·</span>
                余 <span className={remaining_budget < 0 ? 'text-bear' : ''}>{fmtMoney(remaining_budget)}</span>
              </span>
            </div>
            <button
              onClick={generatePlan}
              disabled={generating}
              className="px-3 py-1.5 rounded-md text-[12px] font-semibold border border-accent/60 text-accent hover:bg-accent/10 transition-colors cursor-pointer disabled:opacity-50"
            >
              {generating ? '分析中...' : tranches?.length > 0 ? '重新生成档位' : '生成解套计划'}
            </button>
          </div>
        ) : (
          <div className="rounded-lg border border-accent/40 bg-accent/5 p-3 space-y-2">
            <div className="text-[12px] text-accent font-semibold">
              系统推荐：预算 ¥{recommendation.recommended_budget} · {recommendation.tranches.length} 档
            </div>
            <div className="space-y-1">
              {recommendation.tranches.map(t => (
                <div key={t.idx} className="flex justify-between text-[11px] text-text-dim">
                  <span className="flex-1">档{t.idx} {t.source}</span>
                  <span className="font-mono">{fmtPrice(t.trigger_price)} × {t.shares}股</span>
                  <span className={`ml-2 ${t.feasibility?.feasible ? 'text-bull' : 'text-bear'}`}>
                    {t.feasibility?.feasible ? '可行' : '不可行'}
                  </span>
                </div>
              ))}
            </div>
            <div className="flex gap-2">
              <button onClick={savePlan}
                className="flex-1 text-[12px] px-3 py-1.5 rounded-md bg-accent text-bg font-semibold hover:opacity-90 transition-opacity cursor-pointer">
                确认保存
              </button>
              <button onClick={() => setRecommendation(null)}
                className="text-[12px] px-3 py-1.5 rounded-md border border-border text-text-dim hover:text-text transition-colors cursor-pointer">
                取消
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
