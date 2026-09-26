import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import ProKline from './ProKline'
import { AnalyzerChips, AnalyzerPanel } from './AnalyzerPanel'
import { MinuteChart } from './kline/MinuteChart'
import { LhbPanel, OrderBook, Ticks } from './kline/panels'
import PriceVolumeTable from './kline/PriceVolumeTable'
import { BUY_COLOR, SELL_COLOR, colorPct, fmtPct, fmtVal } from './kline/shared'
import { createPortal } from 'react-dom'


export default function StockKlineModal({ holding, onClose }) {
  const [tdxOn, setTdxOn] = useState(false)
  const [tab, setTab] = useState('日')            // 分时 | 日 | 周 | 月
  const [actions, setActions] = useState([])
  const [minute, setMinute] = useState(null)
  const [tickMode, setTickMode] = useState(false)   // 分时: 逐笔精绘(还原分钟内秒级尖峰)
  const [panic, setPanic] = useState(null)          // 恐慌逃离指数(客观卖压强度)
  const [inst, setInst] = useState(null)            // 机构进货标记(龙虎榜机构净买, 滞后硬数据)
  const [analyzers, setAnalyzers] = useState([])    // 已装分析插件(licai.analyzers)的解读, 没装为空
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
    if (tab === '分价') { setLoading(false); return }   // 分价表组件自管取数
    setLoading(true); setErr('')
    const done = () => setLoading(false)
    if (tab === '分时' && tdxOn) {
      const murl = tickMode
        ? `/api/market/tdx/minute-all/${encodeURIComponent(code)}`   // 逐笔精绘: 全天逐笔, 有秒级尖峰
        : `/api/market/tdx/minute/${encodeURIComponent(code)}`       // 1分钟采样: 快, 240点
      fetchJSON(murl)
        .then(d => setMinute(d?.data || null)).catch(e => setErr(e?.message || '加载失败')).finally(done)
    } else {
      // 日/周/月 K 线交给 ProKline 自取(可缩放, 与榜单同源)。这里只取买卖点流水传给它。
      // 场内 ETF 走 /api/assets/{id}/actions; A股走 portfolio actions。
      const actUrl = assetId
        ? `/api/assets/${assetId}/actions`
        : `/api/portfolio/${encodeURIComponent(code)}/actions`
      fetchJSON(actUrl).catch(() => []).then(a => {
        // 份额拆分后 K 线是前复权标度: 标记优先用后端算的 adj_price/adj_shares; SPLIT 不打点
        const raw = Array.isArray(a) ? a : (a?.actions || [])
        setActions(raw.filter(x => x.action_type !== 'SPLIT').map(x => ({
          ...x,
          price: x.adj_price ?? x.price ?? x.unit_price,
          shares: x.adj_shares ?? x.shares,
        })))
      }).finally(done)
    }
  }, [code, tab, tdxOn, assetId, tickMode])

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

  // 恐慌逃离指数 + 机构进货标记(仅 A 股, 盘后/滞后硬数据, 客观非信号)
  useEffect(() => {
    if (!isA || !code) { setPanic(null); setInst(null); setAnalyzers([]); return }
    let alive = true
    setPanic(null); setInst(null); setAnalyzers([])
    fetchJSON(`/api/market/analyzers/${encodeURIComponent(code)}`).then(d => alive && setAnalyzers(d?.results || [])).catch(() => {})
    fetchJSON(`/api/market/panic/${encodeURIComponent(code)}`).then(d => alive && setPanic(d?.error ? null : d)).catch(() => {})
    fetchJSON(`/api/market/inst-accum/${encodeURIComponent(code)}`).then(d => alive && setInst(d?.error ? null : d)).catch(() => {})
    return () => { alive = false }
  }, [code, isA])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!holding) return null
  const cost = holding.cost_price > 0 ? holding.cost_price : null
  const prevClose = book?.prev_close || holding.current_price   // 分时基准: TDX盘口昨收, 退回现价
  const px = book?.price || holding.current_price
  const vsCostPct = cost && px ? ((px / cost) - 1) * 100 : null
  const showTabs = tdxOn ? ['分时', '日', '周', '月', ...(isA ? ['分价'] : [])] : ['日']
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
            {panic && (
              <span className="text-[10.5px] font-mono px-1.5 py-0.5 rounded"
                style={{ border: '1px solid var(--color-border-med)' }}
                title={`恐慌逃离指数 ${panic.score}/100(${panic.level})· 今天卖压比该股过去${panic.sample_days}日里 ${panic.percentile}% 的日子更急。跌${panic.drop_pct}% 收盘位置${panic.close_pos} 量比${panic.vol_ratio}${panic.limit_down ? ' 跌停' : ''}${panic.new_low ? ' 破新低' : ''}。客观卖压强度,非买卖信号。`}>
                恐慌 <span style={{ color: panic.score >= 50 ? 'var(--color-bull-bright)' : 'var(--color-text)' }}>{panic.score}</span>
                <span className="text-text-muted"> {panic.level}{panic.percentile != null ? ` · ${panic.percentile}%位` : ''}</span>
              </span>
            )}
            {inst && (
              <span className="text-[10.5px] font-mono px-1.5 py-0.5 rounded"
                style={{ border: '1px solid var(--color-border-med)' }}
                title={inst.appearances ? `近30天龙虎榜机构专用席位: ${inst.direction} 净额 ${inst.net_buy_yi}亿, ${inst.appearances}次上榜, 最近 ${inst.last_date}(现价较上榜${inst.since_last_pct}%)。上榜日才披露=抽样滞后,非全量、非买卖信号。` : '近30天该股无龙虎榜机构席位披露(上榜才披露,不代表机构没动作)'}>
                机构 {inst.appearances
                  ? <span style={{ color: inst.net_buy_yi > 0 ? 'var(--color-bear-bright)' : 'var(--color-bull-bright)' }}>{inst.direction}{inst.net_buy_yi}亿</span>
                  : <span className="text-text-muted">无上榜</span>}
              </span>
            )}
            <AnalyzerChips results={analyzers} />
          </div>
          <div className="flex gap-1 items-center">
            {showTabs.map(t => (
              <button key={t} onClick={() => setTab(t)} className="px-2.5 py-[3px] rounded text-[11px] cursor-pointer transition-colors"
                style={{ border: '1px solid', borderColor: tab === t ? 'var(--color-accent)' : 'var(--color-border-med)', color: tab === t ? 'var(--color-accent)' : 'var(--color-text-dim)', background: tab === t ? 'rgba(200,168,118,.1)' : 'transparent' }}>{t}{(t !== '分时' && t !== '分价') ? 'K' : ''}</button>
            ))}
            <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-2 ml-1 cursor-pointer">×</button>
          </div>
        </div>

        <div className={hasSide ? 'flex gap-3' : ''}>
          {/* 主图 */}
          <div className="flex-1 min-w-0">
            {/* 分时: 逐笔精绘开关. 1分钟采样丢失分钟内秒级尖峰(盘口被打空的"闪电"), 逐笔精绘用全天逐笔还原 */}
            {tab === '分时' && (
              <div className="flex gap-1 mb-2 items-center">
                <button onClick={() => setTickMode(false)} className="px-2 py-[2px] rounded text-[10px] cursor-pointer"
                  style={{ border: '1px solid', borderColor: !tickMode ? 'var(--color-accent)' : 'var(--color-border-med)', color: !tickMode ? 'var(--color-accent)' : 'var(--color-text-dim)' }}>1分钟</button>
                <button onClick={() => setTickMode(true)} className="px-2 py-[2px] rounded text-[10px] cursor-pointer"
                  style={{ border: '1px solid', borderColor: tickMode ? 'var(--color-accent)' : 'var(--color-border-med)', color: tickMode ? 'var(--color-accent)' : 'var(--color-text-dim)' }}
                  title="用全天逐笔重画, 还原分钟内的秒级尖峰(盘口被打空的闪电), 数据量更大">逐笔精绘</button>
              </div>
            )}
            <div className="bg-surface-3 rounded-md p-2">
              {tab === '分价' ? <PriceVolumeTable code={code} prevClose={prevClose} decimals={/^[15]\d{5}$/.test(String(code)) ? 3 : 2} />
                : tab === '分时' ? (loading ? <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">加载中…</div>
                    : err ? <div className="h-[360px] flex items-center justify-center text-text-dim text-[12px]">{err}</div>
                    : <MinuteChart points={minute?.points || []} prevClose={prevClose} actions={actions} day={minute?.date} tickMode={tickMode} />)
                : <ProKline code={code} period={tab === '周' ? 'week' : tab === '月' ? 'month' : 'day'}
                    days={250} cost={cost} actions={actions} height={460} />}
            </div>
            <AnalyzerPanel results={analyzers} />
          </div>

          {/* 侧栏: 五档 + 逐笔 (TDX) */}
          {hasSide && (
            <div className="w-[200px] shrink-0 bg-surface-3 rounded-md p-2.5 flex flex-col gap-3">
              <OrderBook data={book} prevClose={prevClose} decimals={/^[15]\d{5}$/.test(String(code)) ? 3 : 2} />
              <div className="border-t border-border-subtle" />
              {/* 逐笔占满侧栏剩余高度: 左侧主图下方加了说明条后侧栏被拉高, 固定 150px 会留一大块空 */}
              <Ticks ticks={ticks} fill decimals={/^[15]\d{5}$/.test(String(code)) ? 3 : 2} />
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
