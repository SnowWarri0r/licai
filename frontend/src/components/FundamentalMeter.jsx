import { fmtHealthColor, fmtHealthEmoji, fmtHealthLabel } from '../helpers'

export default function FundamentalMeter({ fundamental }) {
  if (!fundamental) return null
  const { level, score, details } = fundamental
  const color = fmtHealthColor(level)
  const emoji = fmtHealthEmoji(level)
  const label = fmtHealthLabel(level)

  return (
    <div className="flex items-center gap-2 cursor-help relative group">
      <span className="text-[14px]">{emoji}</span>
      <span className={`text-[12px] font-medium ${color}`}>{label}</span>
      <span className="text-[11px] text-text-dim">
        ({score >= 0 ? '+' : ''}{score?.toFixed(2)})
      </span>
      <div className="absolute top-full left-0 mt-1.5 px-3 py-2 rounded-lg bg-surface-2 border border-border
                      text-[11px] text-text leading-relaxed w-64 opacity-0 pointer-events-none
                      group-hover:opacity-100 transition-opacity z-50 shadow-lg whitespace-normal">
        <div className="font-medium mb-1">基本面明细</div>
        <div>板块5日: {((details?.sector_5d_perf || 0) * 100).toFixed(2)}%</div>
        <div>期货5日: {((details?.futures_5d_perf || 0) * 100).toFixed(2)}%</div>
        <div>新闻情绪: {(details?.llm_sentiment || 0).toFixed(2)}</div>
        <div>公告评分: {(details?.announcement_score || 0).toFixed(2)}</div>
      </div>
    </div>
  )
}
