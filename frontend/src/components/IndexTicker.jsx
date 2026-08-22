import { useState, useEffect, useMemo } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { priceColor } from '../helpers'
import MacroKlineModal from './MacroKlineModal'

// 顶部指数条: 只显示一个指数(一排四五个数字挤在总资产旁边谁都看不清), 点开是 K线 + 今开/高低/成交额,
// 面板顶部有横向滚动的切换条换看哪一个。选中的存 localStorage, 下次进来还是它。
// 覆盖 A股宽基 → 港股 → 美股 → 日韩欧, 数据复用宏观仪表盘那份 30s 缓存的批量快照。
const GROUPS = ['a_index', 'hk_index', 'us_index', 'overseas_index']
const STORE_KEY = 'licai.ticker.index'
const DEFAULT_SYM = 'sh000001'

export default function IndexTicker() {
  const [groups, setGroups] = useState(null)
  const [sym, setSym] = useState(() => {
    try { return localStorage.getItem(STORE_KEY) || DEFAULT_SYM } catch { return DEFAULT_SYM }
  })
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const load = () => fetchJSON('/api/market/macro').then(setGroups).catch(() => {})
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [])

  const items = useMemo(
    () => GROUPS.flatMap(g => (groups?.[g] || []).map(it => ({ ...it, group: g }))),
    [groups]
  )
  // 存的 symbol 可能已经不在表里(改过 MACRO_SYMBOLS), 退回第一个而不是白屏
  const cur = items.find(i => i.symbol === sym) || items[0]

  const pick = (s) => {
    setSym(s)
    try { localStorage.setItem(STORE_KEY, s) } catch {}
  }

  if (!cur) return null
  const pct = Number(cur.change_pct ?? 0)

  return (
    <>
      <button onClick={() => setOpen(true)} title="点开看 K线 / 成交额, 可切换其他指数"
        className="flex items-baseline gap-1.5 shrink-0 rounded-md border border-transparent hover:border-border-med hover:bg-surface-3 px-1.5 py-0.5 cursor-pointer transition-colors">
        <span className="text-[11px] text-text-muted">{cur.name}</span>
        <span className="text-[12px] font-mono text-text">
          {cur.price != null ? cur.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--'}
        </span>
        <span className={`text-[11px] font-mono ${priceColor(pct)}`}>
          {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
        </span>
        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
          strokeLinecap="round" strokeLinejoin="round" className="text-text-muted self-center">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <MacroKlineModal item={cur} items={items} onPick={pick} onClose={() => setOpen(false)} />
      )}
    </>
  )
}
