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
    tranches, fundamental,
    unwind_exit_price, can_unwind_now,
    npv_analysis, benchmark,
    realized_carry, full_cycle_breakeven,
  } = plan

  // 全周期回本: 仅当历史净亏(carry<0)才有额外的山要爬; 触发价不变, 这只是参考
  const showFullCycle = full_cycle_breakeven > 0 && (realized_carry || 0) < -0.5
    && full_cycle_breakeven > current_price * 1.005
  const fcGapPct = current_price > 0 ? (full_cycle_breakeven / current_price - 1) * 100 : 0

  const hasTranches = (tranches?.length || 0) > 0
  const [showTranches, setShowTranches] = useState(hasTranches)
  const executedCount = (tranches || []).filter(t => t.status === 'executed').length
  const remainingTrancheShares = (tranches || [])
    .filter(t => t.status !== 'executed')
    .reduce((sum, t) => sum + (t.shares || 0), 0)
  const trancheSummary = hasTranches
    ? `${tranches.length} 档 · 已卖 ${executedCount} · 待卖 ${remainingTrancheShares}股`
    : '未配置'

  const gapPct = current_price > 0
    ? ((unwind_exit_price - current_price) / current_price) * 100
    : 0

  const generatePlan = async () => {
    setGenerating(true)
    try {
      const rec = await fetchJSON(
        `/api/unwind/recommend/${stock_code}`,
        { method: 'POST' }
      )
      setRecommendation(rec)
    } catch (e) {
      alert('生成失败: ' + (e?.message || ''))
    } finally {
      setGenerating(false)
    }
  }

  const savePlan = async () => {
    if (!recommendation) return
    await fetchJSON(`/api/unwind/plans/${stock_code}`, {
      method: 'PUT',
      body: JSON.stringify({
        total_budget: 0,  // 减仓模式无预算概念, 占位
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

        {/* 全周期回本价 — 含历史已实现, 仅参考不当触发价 */}
        {showFullCycle && (
          <div className="rounded-lg px-3.5 py-2 flex items-center justify-between border border-border-subtle bg-surface-3/40">
            <div>
              <div className="text-[10px] uppercase tracking-wider font-semibold text-text-dim">全周期回本价</div>
              <div className="text-[9px] text-text-muted mt-0.5">
                含历史已实现 {fmtMoney(realized_carry)} · 把这只票从头到尾拉平 · 仅参考非触发价
              </div>
            </div>
            <div className="text-right">
              <div className="font-mono text-[16px] font-bold text-text">¥{fmtPrice(full_cycle_breakeven)}</div>
              <div className="text-[10px] font-mono text-bear-bright">
                距 +{fcGapPct.toFixed(1)}% · 还差{fmtMoney(-realized_carry)}
              </div>
            </div>
          </div>
        )}

        {/* NPV micro-bar */}
        <NPVPanel npv={npv_analysis} />

        {/* Collapsible tranche section — 减仓阶梯 */}
        <div className="rounded-lg border border-border bg-surface-2/40 overflow-hidden">
          <button
            type="button"
            onClick={() => setShowTranches(v => !v)}
            className="w-full px-3 py-2 flex items-center justify-between hover:bg-surface-2 transition-colors cursor-pointer"
          >
            <span className="text-[11px] uppercase tracking-wider font-semibold text-text-muted">
              减仓阶梯
            </span>
            <span className="text-[10px] font-mono text-text-dim flex items-center gap-2">
              {trancheSummary}
              <span className="text-text-muted">{showTranches ? '▾' : '▸'}</span>
            </span>
          </button>
          {showTranches && (
            <div className="px-3 pb-3 pt-1 space-y-3">
              <TrancheLadder
                tranches={tranches}
                currentPrice={current_price}
                onExecute={onChange}
              />

              {/* Footer — 生成 / 重排 */}
              {!recommendation ? (
                <div className="flex items-center justify-between pt-1">
                  <div className="text-[10px] text-text-muted">
                    反弹触发后分批卖出, 直至清仓
                  </div>
                  <button
                    onClick={generatePlan}
                    disabled={generating}
                    className="px-3 py-1.5 rounded-md text-[12px] font-semibold border border-accent/60 text-accent hover:bg-accent/10 transition-colors cursor-pointer disabled:opacity-50"
                  >
                    {generating ? '分析中...' : hasTranches ? '重新生成阶梯' : '生成减仓阶梯'}
                  </button>
                </div>
              ) : (
                <div className="rounded-lg border border-accent/40 bg-accent/5 p-3 space-y-2">
                  <div className="text-[12px] text-accent font-semibold">
                    系统推荐: {recommendation.tranches.length} 档 · 共 {
                      recommendation.tranches.reduce((s, t) => s + (t.shares || 0), 0)
                    }股
                  </div>
                  <div className="space-y-1">
                    {recommendation.tranches.map(t => (
                      <div key={t.idx} className="flex justify-between text-[11px] text-text-dim">
                        <span className="flex-1">档{t.idx} {t.source}</span>
                        <span className="font-mono">{fmtPrice(t.trigger_price)} × {t.shares}股</span>
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
          )}
        </div>
      </div>
    </div>
  )
}
