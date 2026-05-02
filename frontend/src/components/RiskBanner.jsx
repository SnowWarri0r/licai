import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtMoney } from '../helpers'

export default function RiskBanner({ holdings }) {
  const [maxDailyLoss, setMaxDailyLoss] = useState(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    fetchJSON('/api/settings/feishu').catch(() => {}) // warm up
    // Load risk config
    fetch('/api/settings/risk').then(r => r.json()).then(d => {
      if (d.max_daily_loss) setMaxDailyLoss(parseFloat(d.max_daily_loss))
    }).catch(() => {})
  }, [])

  if (!holdings || holdings.length === 0 || dismissed) return null

  // Suppress risk banner outside A-share trading days (price_change_pct is stale).
  const dow = new Date().getDay()
  if (dow === 0 || dow === 6) return null

  const todayPnl = holdings.reduce((s, h) => {
    if (!h.current_price || !h.price_change_pct) return s
    const prevPrice = h.current_price / (1 + h.price_change_pct / 100)
    return s + (h.current_price - prevPrice) * h.shares
  }, 0)

  const threshold = maxDailyLoss || 500 // default 500 yuan
  const isWarning = todayPnl < -threshold

  if (!isWarning) return null

  return (
    <div className="mx-4 mt-3 px-4 py-2 rounded-lg bg-bear/15 border border-bear/30 flex items-center justify-between"
      style={{ animation: 'fade-up 0.3s ease-out' }}>
      <div className="flex items-center gap-2">
        <span className="text-bear text-[14px]">&#9888;</span>
        <span className="text-[13px] text-bear-bright font-medium">
          风控警告：今日浮亏 {fmtMoney(Math.abs(todayPnl))}，超过阈值 {fmtMoney(threshold)}，建议暂停加仓
        </span>
      </div>
      <button onClick={() => setDismissed(true)}
        className="text-[11px] px-2 py-0.5 rounded border border-bear/30 text-bear hover:bg-bear/20 cursor-pointer shrink-0">
        知道了
      </button>
    </div>
  )
}
