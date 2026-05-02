import { useState } from 'react'
import { fmtMoney } from '../helpers'

const VERDICT = {
  cut:     { icon: '✂', label: '建议割肉换指数', color: 'bear' },
  hold:    { icon: '✊', label: '建议继续持有', color: 'bull' },
  neutral: { icon: '⚖', label: '两难', color: 'warn' },
}

export default function NPVPanel({ npv }) {
  const [showDetail, setShowDetail] = useState(false)
  if (!npv) return null
  const {
    hold_fv, cut_loss_fv, cut_better_by, recommendation,
    recovery_probability, recovery_model,
    holding_years_assumed, index_annual_return,
  } = npv
  const v = VERDICT[recommendation] || VERDICT.neutral

  const probPct = Math.round((recovery_probability || 0) * 100)
  const years = holding_years_assumed || 2
  const idxPct = Math.round((index_annual_return || 0.08) * 100)
  const diff = Math.abs(cut_better_by || 0)
  const isCut = recommendation === 'cut'

  // Plain-language one-liner
  let oneLine
  if (isCut) {
    oneLine = `等 ${years} 年回本概率 ${probPct}%，现在割肉换沪深300（年化 ${idxPct}%），${years} 年后多赚约 ¥${fmtMoney(diff)}`
  } else if (recommendation === 'hold') {
    oneLine = `等 ${years} 年回本概率 ${probPct}%，比割肉换沪深300多赚约 ¥${fmtMoney(diff)}，可以扛`
  } else {
    oneLine = `回本概率 ${probPct}%，持有/割肉差距很小（¥${fmtMoney(diff)}），凭你判断`
  }

  const textColor = v.color === 'bear' ? 'text-bear-bright'
    : v.color === 'bull' ? 'text-bull-bright'
    : 'text-[var(--color-signal-moderate)]'
  const borderColor = v.color === 'bear' ? 'border-bear/30'
    : v.color === 'bull' ? 'border-bull/30'
    : 'border-[var(--color-signal-moderate)]/30'
  const bgColor = v.color === 'bear' ? 'bg-bear/5'
    : v.color === 'bull' ? 'bg-bull/5'
    : 'bg-[var(--color-signal-moderate)]/5'

  const holdPct = (hold_fv || 0) + (cut_loss_fv || 0) > 0
    ? (hold_fv / (hold_fv + cut_loss_fv)) * 100
    : 50

  return (
    <div className={`rounded-lg px-3 py-2.5 border ${borderColor} ${bgColor}`}>
      <div className="flex items-center justify-between mb-1.5">
        <span className={`text-[12px] font-bold ${textColor}`}>
          {v.icon} {v.label}
        </span>
        <button onClick={() => setShowDetail(s => !s)}
          className="text-[10px] text-text-muted hover:text-text cursor-pointer">
          {showDetail ? '收起' : '怎么算的 ?'}
        </button>
      </div>

      <p className="text-[11.5px] text-text leading-relaxed m-0 mb-2">
        {oneLine}
      </p>

      {/* Comparison bar — always visible */}
      <div className="flex justify-between mb-1 text-[10px] text-text-dim">
        <span>持有 ¥{fmtMoney(hold_fv)}</span>
        <span>割肉换指数 ¥{fmtMoney(cut_loss_fv)}</span>
      </div>
      <div className="flex h-1.5 rounded-full overflow-hidden bg-surface-3">
        <div className="bg-bull" style={{ width: `${holdPct}%` }} />
        <div className="bg-bear" style={{ width: `${100 - holdPct}%` }} />
      </div>

      {showDetail && (
        <div className="mt-2.5 pt-2 border-t border-border-subtle space-y-1 text-[10.5px] text-text-dim leading-relaxed">
          <div>
            <span className="text-text">假设：</span>持有 {years} 年；沪深300 年化按 {idxPct}% 算
          </div>
          <div>
            <span className="text-text">回本概率：</span>用 GBM 首达模型算 — 现价、波动率、基本面 drift 喂进去，跑出"{years} 年内能至少摸到成本价一次"的概率
            {recovery_model && (
              <span className="font-mono ml-1">
                (需涨 {(recovery_model.required_log_return * 100).toFixed(1)}%
                · 年化波动 {(recovery_model.annualized_vol * 100).toFixed(0)}%
                · drift {(recovery_model.drift * 100).toFixed(0)}%)
              </span>
            )}
          </div>
          <div>
            <span className="text-text">"持有"未来值：</span>P(回本)×成本价 + P(不回本)×{years}年后预测价
          </div>
          <div>
            <span className="text-text">"割肉"未来值：</span>现市值 × (1+{idxPct}%)^{years}
          </div>
          <div className="text-text-muted italic">
            * 模型只是参考。基本面恶化（health→red）drift 会变负，回本概率会大幅下滑
          </div>
        </div>
      )}
    </div>
  )
}
