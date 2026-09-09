import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { CandleChart } from './kline/CandleChart'
import { MinuteChart } from './kline/MinuteChart'
import { LhbPanel, OrderBook, Ticks } from './kline/panels'
import { BUY_COLOR, MA_WARMUP, SELL_COLOR, colorPct, fmtPct, fmtVal } from './kline/shared'
import { createPortal } from 'react-dom'


export default function StockKlineModal({ holding, onClose }) {
  const [tdxOn, setTdxOn] = useState(false)
  const [tab, setTab] = useState('日')            // 分时 | 日 | 周 | 月
  const [days, setDays] = useState(60)
  const [series, setSeries] = useState([])
  const [warmup, setWarmup] = useState([])        // MA 预热: 可见窗口前的 close 序列(不显示)
  const [actions, setActions] = useState([])
  const [minute, setMinute] = useState(null)
  const [book, setBook] = useState(null)
  const [ticks, setTicks] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const code = holding?.stock_code
  const assetId = holding?.asset_id               // 场外 ETF: 据此走 assets 流水端点
  const isA = code && /^\d{6}$/.test(String(code).replace(/^(sh|sz|SH|SZ)/, ''))

  // TDX 是否启用(决定显示哪些 tab)
  useEffect(() => {
    fetchJSON('/api/market/tdx/status').then(d => setTdxOn(!!d.enabled)).catch(() => setTdxOn(false))
  }, [])

  // 主图数据: 日→akshare(带成本/BS); 周月→TDX蜡烛; 分时→TDX
  useEffect(() => {
    if (!code) return
    setLoading(true); setErr('')
    const done = () => setLoading(false)
    if (tab === '分时' && tdxOn) {
      fetchJSON(`/api/market/tdx/minute/${encodeURIComponent(code)}`)
        .then(d => setMinute(d?.data || null)).catch(e => setErr(e?.message || '加载失败')).finally(done)
    } else if ((tab === '周' || tab === '月') && tdxOn) {
      setWarmup([])
      fetchJSON(`/api/market/tdx/kline/${encodeURIComponent(code)}?type=${tab === '周' ? 'week' : 'month'}&limit=200`)
        .then(d => {
          const bars = d?.data?.bars || []
          if (!bars.length) { setErr('暂无K线'); setSeries([]) }
          else setSeries(bars.map(b => ({ date: (b.date || '').slice(0, 10), open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume })))
        }).catch(e => setErr(e?.message || '加载失败')).finally(done)
    } else {
      // 日K (akshare, 带成本线 + 自己买卖标记). 多取 MA_WARMUP 根做均线预热, 让 MA 从首根可见
      // 蜡烛就连续, 不在左侧错位断头. 场内 ETF 走 /api/assets/{id}/actions 取 BS 流水.
      const actUrl = assetId
        ? `/api/assets/${assetId}/actions`
        : `/api/portfolio/${encodeURIComponent(code)}/actions`
      Promise.all([
        fetchJSON(`/api/market/history/${encodeURIComponent(code)}?days=${days + MA_WARMUP}`),
        fetchJSON(actUrl).catch(() => []),
      ]).then(([k, a]) => {
        if (!Array.isArray(k) || !k.length) { setErr('暂无 K 线数据'); setSeries([]); setWarmup([]) }
        else {
          const all = k.map(x => ({ date: x.time, open: x.open, high: x.high, low: x.low, close: x.close, volume: x.volume }))
          const cut = Math.max(0, all.length - days)        // 前 cut 根仅作 MA 预热, 不显示
          setWarmup(all.slice(0, cut).map(b => b.close))
          setSeries(all.slice(cut))
        }
        // 场外 asset 流水: {actions:[{unit_price,...}]} → 归一成 BS 标记要的 {price,...}
        // 份额拆分后 K 线是前复权标度: 标记优先用后端算的 adj_price/adj_shares(拆分调整),
        // 原始成交价留在流水列表里; SPLIT 记录本身不是买卖, 不打点
        const raw = Array.isArray(a) ? a : (a?.actions || [])
        setActions(raw.filter(x => x.action_type !== 'SPLIT').map(x => ({
          ...x,
          price: x.adj_price ?? x.price ?? x.unit_price,
          shares: x.adj_shares ?? x.shares,
        })))
      }).catch(e => setErr(e?.message || '加载失败')).finally(done)
    }
  }, [code, tab, days, tdxOn, assetId])

  // 五档 + 逐笔 (TDX, 仅 A 股; 5s 刷新)
  useEffect(() => {
    if (!tdxOn || !isA || !code) { setBook(null); setTicks([]); return }
    let alive = true
    const pull = () => {
      fetchJSON(`/api/market/tdx/orderbook/${encodeURIComponent(code)}`).then(d => alive && setBook(d?.data || null)).catch(() => {})
      fetchJSON(`/api/market/tdx/trade/${encodeURIComponent(code)}?limit=40`).then(d => alive && setTicks(d?.data?.ticks || [])).catch(() => {})
    }
    pull()
    const t = setInterval(pull, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [code, tdxOn, isA])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!holding) return null
  const cost = holding.cost_price > 0 ? holding.cost_price : null
  const prevClose = book?.prev_close || (series.length ? series[series.length - 1].close : holding.current_price) || holding.current_price
  const closes = series.map(d => d.close).filter(c => c > 0)
  const vsCostPct = cost && (book?.price || closes[closes.length - 1]) ? (((book?.price || closes[closes.length - 1]) / cost) - 1) * 100 : null
  const showTabs = tdxOn ? ['分时', '日', '周', '月'] : ['日']
  const hasSide = tdxOn && isA

  return createPortal(
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className={`bg-surface-2 border border-border rounded-xl p-4 md:p-5 ${hasSide ? 'w-[1040px]' : 'w-[820px]'} max-w-[96vw]`} onClick={e => e.stopPropagation()}>
        {/* header */}
        <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="text-[15px] font-semibold text-text-bright m-0">{holding.stock_name}</h3>
            <span className="text-[11px] font-mono text-text-dim">{holding.stock_code}</span>
            <span className="text-[14px] font-mono text-text-bright">{fmtVal(book?.price || holding.current_price)}</span>
            <span className={`text-[12px] font-mono ${colorPct(holding.price_change_pct)}`}>{fmtPct(holding.price_change_pct)}</span>
            {cost != null && <span className={`text-[11px] font-mono ${colorPct(vsCostPct)}`} title="相对持仓成本">vs 成本 {fmtPct(vsCostPct)}</span>}
            {book?.['盘口'] && <span className="text-[10.5px] text-accent">· {book['盘口']}</span>}
          </div>
          <div className="flex gap-1 items-center">
            {showTabs.map(t => (
              <button key={t} onClick={() => setTab(t)} className="px-2.5 py-[3px] rounded text-[11px] cursor-pointer transition-colors"
                style={{ border: '1px solid', borderColor: tab === t ? 'var(--color-accent)' : 'var(--color-border-med)', color: tab === t ? 'var(--color-accent)' : 'var(--color-text-dim)', background: tab === t ? 'rgba(200,168,118,.1)' : 'transparent' }}>{t}{t !== '分时' ? 'K' : ''}</button>
            ))}
            <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-2 ml-1 cursor-pointer">×</button>
          </div>
        </div>

        <div className={hasSide ? 'flex gap-3' : ''}>
          {/* 主图 */}
          <div className="flex-1 min-w-0">
            {/* 日K 才显示天数切换 */}
            {tab === '日' && (
              <div className="flex gap-1 mb-2">
                {[30, 60, 120, 250].map(d => (
                  <button key={d} onClick={() => setDays(d)} className="px-2 py-[2px] rounded text-[10px] cursor-pointer"
                    style={{ border: '1px solid', borderColor: days === d ? 'var(--color-accent)' : 'var(--color-border-med)', color: days === d ? 'var(--color-accent)' : 'var(--color-text-dim)' }}>{d}日</button>
                ))}
              </div>
            )}
            <div className="bg-surface-3 rounded-md p-2">
              {loading ? <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">加载中…</div>
                : err ? <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">{err}</div>
                : tab === '分时' ? <MinuteChart points={minute?.points || []} prevClose={prevClose} actions={actions} day={minute?.date} />
                : <CandleChart series={series} cost={tab === '日' ? cost : null} actions={tab === '日' ? actions : []} warmup={tab === '日' ? warmup : []} />}
            </div>
          </div>

          {/* 侧栏: 五档 + 逐笔 (TDX) */}
          {hasSide && (
            <div className="w-[200px] shrink-0 bg-surface-3 rounded-md p-2.5 space-y-3">
              <OrderBook data={book} prevClose={prevClose} decimals={/^[15]\d{5}$/.test(String(code)) ? 3 : 2} />
              <div className="border-t border-border-subtle" />
              <Ticks ticks={ticks} decimals={/^[15]\d{5}$/.test(String(code)) ? 3 : 2} />
            </div>
          )}
        </div>

        {isA && <LhbPanel code={code} />}

        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-dim">
          {cost != null && <span>成本 <span className="text-accent font-mono">{fmtVal(cost)}</span></span>}
          <span><span className="inline-block w-2 h-2 rounded-sm align-middle mr-1" style={{ background: BUY_COLOR }} />B 买入<span className="mx-1.5" /><span className="inline-block w-2 h-2 rounded-sm align-middle mr-1" style={{ background: SELL_COLOR }} />S 卖出</span>
          {tdxOn && <span className="text-accent/70">TDX 盘口/分时已接入</span>}
          <span className="text-text-muted ml-auto">仅展示数据，不构成投资建议</span>
        </div>
      </div>
    </div>,
    document.body
  )
}
