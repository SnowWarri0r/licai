export const ACQUIRE = new Set(['BUY', 'ADD', 'BONUS'])

export const MA_WARMUP = 60           // 日K 多取的均线预热根数(够 MA60 从首根可见蜡烛起连续)

// A 股口径: 红涨绿跌
export const UP = '#cf5c5c', DOWN = '#5fa86c'

export const BUY_COLOR = '#3fae6a', SELL_COLOR = '#d04a4a'

export const fmtVal = (v) => v == null ? '--' : v < 10 ? v.toFixed(3) : v < 100 ? v.toFixed(2) : v.toFixed(1)

export const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'

export const colorPct = (v) => v == null ? 'text-text-dim' : v >= 0 ? 'text-bear-bright' : 'text-bull-bright'

export const fmtHand = (h, unit = '手') => h == null ? '--'
  : h >= 1e8 ? (h / 1e8).toFixed(2) + '亿' + unit
  : h >= 1e4 ? (h / 1e4).toFixed(1) + '万' + unit
  : Math.round(h) + unit

export const colorPctHex = (v) => v >= 0 ? UP : DOWN

// ProKline / DayOverlay 用的价格格式化。⚠️ 与上面的 fmtVal 只差一点(取绝对值判档、
// 档位也不同), 但两边的显示精度都是调过的 —— 合并会静默改掉其中一边的位数, 所以分开放。
export const fmt = (v) => v == null ? '--' : Math.abs(v) >= 100 ? v.toFixed(1) : Math.abs(v) >= 10 ? v.toFixed(2) : v.toFixed(3)
