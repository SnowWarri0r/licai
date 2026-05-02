export function fmtPrice(v) {
  if (v == null || v === 0) return '--'
  return parseFloat(v).toFixed(2)
}

export function fmtCost(v) {
  if (v == null) return '--'
  return parseFloat(v).toFixed(4)
}

export function fmtPct(v) {
  if (v == null) return '--'
  const sign = v > 0 ? '+' : ''
  return `${sign}${parseFloat(v).toFixed(2)}%`
}

export function fmtMoney(v) {
  if (v == null) return '--'
  const abs = Math.abs(v)
  if (abs >= 10000) return (v / 10000).toFixed(2) + '万'
  return v.toFixed(2)
}

export function priceColor(v) {
  if (v == null || v === 0) return 'text-text-dim'
  return v > 0 ? 'text-bear-bright' : 'text-bull-bright'
}

export function signalLabel(s) {
  return { STRONG: '强信号', MODERATE: '中等', WEAK: '弱信号' }[s] || s
}

export function signalColor(s) {
  return {
    STRONG: 'bg-signal-strong text-white',
    MODERATE: 'bg-signal-moderate text-black',
    WEAK: 'bg-signal-weak text-text-dim',
    CLOSED: 'bg-signal-closed text-text-dim',
  }[s] || 'bg-signal-closed text-text-dim'
}

export function fmtDays(n) {
  if (n == null) return '--'
  if (n < 30) return `${n}天`
  if (n < 365) return `${Math.round(n / 30)}月`
  return `${(n / 365).toFixed(1)}年`
}

export function fmtHealthColor(level) {
  if (level === 'green') return 'text-bull-bright'
  if (level === 'yellow') return 'text-signal-moderate'
  if (level === 'red') return 'text-bear-bright'
  return 'text-text-dim'
}

export function fmtHealthEmoji(level) {
  return { green: '🟢', yellow: '🟡', red: '🔴' }[level] || '⚪'
}

export function fmtHealthLabel(level) {
  return { green: '可加仓', yellow: '仅浅档', red: '暂停加仓' }[level] || '未知'
}
