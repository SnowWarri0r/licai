import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { fmtMoney, fmtPct, fundPassthroughType, loadBrokers, priceColor } from '../helpers'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'
import Tooltip from './Tooltip'
import { AllocationDonut, FxHint, MarketChip, ProxyPulse, RowActions, TodayPulse, TypeChip, TypeMiniInfo, WeightBar } from './portfolio/atoms'
import { FAMILY_LABEL, TYPE_COLOR, TYPE_META, TYPE_ORDER } from './portfolio/constants'
import { buildFamilyIndex, formatCurrencyMoney, riskFamiliesOf } from './portfolio/helpers'
import { aggregate, normalizeAsset, normalizeHolding } from './portfolio/model'
import { HoldingRow } from './portfolio/HoldingRow'
import { PortfolioModals } from './portfolio/PortfolioModals'
import { ClosedPositionsBlock, SummaryStrip } from './portfolio/summary'
import { usePortfolioModals } from './portfolio/useModals'

// ============================================================
// UnifiedPortfolio — replaces Portfolio + ExternalAssets
// Groups: A股 / 基金 / 加密 / 机器人. Sorted by market value desc
// within groups. Hover-only row actions. Donut allocation header.
// ============================================================

// ============================================================
// Main
// ============================================================
export default function UnifiedPortfolio({ holdings, onEdit, onHistory, onAdd, dataVersion = 0 }) {
  const [assets, setAssets] = useState([])
  const [brokers, setBrokers] = useState([])
  useEffect(() => { loadBrokers().then(setBrokers) }, [])
  const [assetsLoaded, setAssetsLoaded] = useState(false)
  const [tradingDay, setTradingDay] = useState(null)
  const [collapsed, setCollapsed] = useState({})
  const [hoverId, setHoverId] = useState(null)
  const [filter, setFilter] = useState('ALL')
  const [sortKey, setSortKey] = useState('mv')
  // 九个弹窗/内联表单的开关(原来是九个同构 useState) —— 见 usePortfolioModals
  const { open, show, hide } = usePortfolioModals()
  const [thesisCodes, setThesisCodes] = useState(() => new Set())
  const loadThesisCodes = useCallback(async () => {
    try {
      const list = await fetchJSON('/api/portfolio/thesis')
      setThesisCodes(new Set((list || []).map(t => t.code)))
    } catch {}
  }, [])
  useEffect(() => { loadThesisCodes() }, [loadThesisCodes])
  const [realized, setRealized] = useState({ stock: 0, asset: 0 })
  const [todayPnl, setTodayPnl] = useState(null)     // 券商口径当日盈亏(后端算)
  const [settling, setSettling] = useState(false)
  const [settleMsg, setSettleMsg] = useState('')

  const loadAssets = useCallback(async () => {
    try {
      const d = await fetchJSON('/api/assets')
      setAssets(d.assets || [])
    } catch {} finally { setAssetsLoaded(true) }
  }, [])

  // 一键确认所有到期定投: 后端按每条 trade_date 拉当日净值自动结算份额。
  const settlePending = useCallback(async () => {
    setSettling(true); setSettleMsg('')
    try {
      const d = await fetchJSON('/api/assets/settle-pending', { method: 'POST' })
      const parts = [`确认 ${d.settled || 0} 笔`]
      if (d.skipped > 0) parts.push(`${d.skipped} 笔净值未出, 留着下次`)
      setSettleMsg(parts.join(' · '))
      await loadAssets()
    } catch (e) {
      setSettleMsg('失败: ' + e.message)
    } finally { setSettling(false) }
  }, [loadAssets])

  // 费率改了之后重算已确认的定投 (只动设了申购费率的基金, 按当日净值内扣重算份额)
  const recomputeDca = useCallback(async () => {
    setSettling(true); setSettleMsg('')
    try {
      const d = await fetchJSON('/api/assets/recompute-dca', { method: 'POST' })
      setSettleMsg(d.recomputed > 0 ? `重算 ${d.recomputed} 笔` : '没有需要重算的（份额已是最新）')
      await loadAssets()
    } catch (e) {
      setSettleMsg('失败: ' + e.message)
    } finally { setSettling(false) }
  }, [loadAssets])

  const loadRealized = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([
        fetchJSON('/api/portfolio/realized'),
        fetchJSON('/api/assets/realized'),
      ])
      // 总盈亏 = 浮动(行内 pnl) + 已实现. 但有两处会重复算, 必须从已实现里剔掉:
      //  1) 股票: 当前持仓段的 realized 已被「综合成本法」摊进剩余成本 → 行内浮动已含。
      //     只补 realized_carry (已平仓段 + 分红, 后端算好), 含清仓后又复活的旧那轮。
      //  2) CASH 的 realized(利息) 已作为现金行的 pnl 计入浮动 → 资产已实现里排除 CASH。
      // FUND/CRYPTO/WEALTH 的成本只算剩余 lot, 浮动不含 realized, 所以全额计入。
      const stockCarry = s.total_realized_carry != null
        ? s.total_realized_carry
        : (s.items || []).filter(it => !it.still_holding).reduce((sum, it) => sum + (it.realized_pnl || 0), 0)
      const assetExclCash = (a.items || [])
        .filter(it => it.asset_type !== 'CASH')
        .reduce((sum, it) => sum + (it.closed_realized ?? it.realized_pnl ?? 0), 0)
      setRealized({
        stock: Math.round(stockCarry * 100) / 100,
        asset: Math.round(assetExclCash * 100) / 100,
        stockItems: s.items || [],
        assetItems: a.items || [],
      })
    } catch (e) { console.error('realized load failed', e) }
  }, [])

  const loadTodayPnl = useCallback(async () => {
    try {
      const d = await fetchJSON('/api/portfolio/today-pnl')
      setTodayPnl(d && typeof d.total === 'number' ? d : null)   // 出错时置 null → 退回前端口径
    } catch { setTodayPnl(null) }
  }, [])

  useEffect(() => {
    loadAssets()
    loadRealized()
    loadTodayPnl()
    // Crypto & OKX bots are 24/7 markets; use shorter interval. Server-side
    // caches handle upstream rate limits (crypto 30s, fund 120s).
    const t = setInterval(() => { loadAssets(); loadRealized(); loadTodayPnl() }, 20000)
    return () => clearInterval(t)
  }, [loadAssets, loadRealized, loadTodayPnl])

  useEffect(() => {
    fetchJSON('/api/market/trading-day').then(setTradingDay).catch(() => {})
    const t = setInterval(() => fetchJSON('/api/market/trading-day').then(setTradingDay).catch(() => {}), 3600000)
    return () => clearInterval(t)
  }, [])

  // 交易流水编辑后 (dataVersion 变化): 立即重载已实现/已清仓/资产, 不用等 20s 轮询
  useEffect(() => {
    if (dataVersion > 0) { loadRealized(); loadAssets(); loadTodayPnl() }
  }, [dataVersion, loadRealized, loadAssets, loadTodayPnl])

  const rows = useMemo(() => {
    // 0 持仓的股票/基金 不在主列表显示, 走 "已清仓" 区块
    const a = (holdings || []).filter(h => (h.shares || 0) > 0).map(normalizeHolding)
    // BOT/CASH 常驻; 理财(WEALTH)/基金/加密 已清仓(无成本/无市值/无在途)的不进主列表, 走"已清仓"区块
    const e = (assets || []).filter(x => x.asset_type === 'BOT' || x.asset_type === 'CASH'
      || (x.shares || 0) > 0 || (x.cost_amount || 0) > 0
      || (x.current_value || 0) > 0 || (x.pending_amount || 0) > 0).map(normalizeAsset)
    return [...a, ...e]
  }, [holdings, assets])

  const filtered = useMemo(
    () => filter === 'ALL' ? rows : rows.filter(r => r.type === filter),
    [rows, filter]
  )

  const aShareTradingDay = tradingDay
    ? !!tradingDay.is_trading_day
    : ![0, 6].includes(new Date().getDay())
  const agg = useMemo(() => aggregate(filtered, aShareTradingDay), [filtered, aShareTradingDay])
  // 个股「历史已实现」(清仓段+分红, 不在当前浮动里). 用来在行内补「真实总盈亏」,
  // 否则清仓后复活的票只看浮动会误判 (中钨高新浮动+245 实则全周期 -373)。
  const carryByCode = useMemo(() => {
    const m = {}
    for (const it of (realized?.stockItems || [])) {
      if (it.realized_carry) m[it.stock_code] = it.realized_carry
    }
    return m
  }, [realized])
  // tabTypes: always show all types regardless of holdings
  const tabTypes = TYPE_ORDER
  // visibleTypes: from filtered rows — drives content section rendering
  const visibleTypes = TYPE_ORDER.filter(t => agg.groups[t])

  // 穿透敞口(同源风险)走后端: 基金拆到季报前十大, 同一标的/同一行业合起来算。
  const [expo, setExpo] = useState(null)
  useEffect(() => {
    let alive = true
    fetchJSON('/api/portfolio/exposure').then(d => { if (alive && !d?.error) setExpo(d) }).catch(() => {})
    return () => { alive = false }
  }, [])
  const famIdx = useMemo(() => buildFamilyIndex(expo), [expo])

  // Risk insights: concentration warnings + cross-type overlap families.
  // Always computed from ALL rows (not filtered) so toggling filter doesn't change advice.
  const insights = useMemo(() => {
    const allAgg = aggregate(rows, true)
    const total = allAgg.totalMv || 0
    const warnings = []
    // 1. Major class concentration — 穿透后口径
    //    FUND 按 fundPassthroughType 拆到 A/F/W/M/C 再算各桶占比.
    //    避免"我买了海外/黄金 ETF 但被堆在基金桶, 然后说股票集中"的误判.
    const passthroughBuckets = { A: 0, F: 0, W: 0, M: 0, C: 0, R: 0 }
    for (const r of rows) {
      const v = r.mv || 0
      if (r.type === 'F') {
        const target = fundPassthroughType(r.name || '')
        passthroughBuckets[target] = (passthroughBuckets[target] || 0) + v
      } else {
        passthroughBuckets[r.type] = (passthroughBuckets[r.type] || 0) + v
      }
    }
    for (const t of TYPE_ORDER) {
      const mv = passthroughBuckets[t] || 0
      if (mv === 0 || total === 0) continue
      const pct = (mv / total) * 100
      if (pct >= 50) {
        warnings.push({
          level: pct >= 70 ? 'high' : 'med',
          text: `${TYPE_META[t].label} 占比 ${pct.toFixed(1)}%，集中度过高`,
        })
      }
    }
    // 2. Sub-category single-track within FUND / A股
    for (const t of ['A', 'F']) {
      const g = allAgg.groups[t]
      if (!g || g.items.length < 2) continue
      const subs = {}
      for (const it of g.items) {
        const cid = it.category?.id || 'other'
        subs[cid] = (subs[cid] || 0) + (it.mv || 0)
      }
      const entries = Object.entries(subs)
      if (entries.length === 1) {
        const cat = g.items[0].category?.label || '同一类'
        warnings.push({
          level: 'high',
          text: `${TYPE_META[t].label}全押「${cat}」，板块未分散`,
        })
      } else {
        const top = entries.sort((a, b) => b[1] - a[1])[0]
        const topPct = top[1] / g.mv * 100
        if (topPct >= 70) {
          const cat = g.items.find(it => (it.category?.id || 'other') === top[0])?.category?.label || '某类'
          warnings.push({
            level: 'med',
            text: `${TYPE_META[t].label}内「${cat}」占 ${topPct.toFixed(0)}%，板块过度集中`,
          })
        }
      }
    }
    // 3. 同源: 哪几行其实是同一块风险。以后端穿透的行业为准, 后端没回来才退回关键词。
    const familyRows = {}
    const add = (fam, r) => {
      if (!familyRows[fam]) familyRows[fam] = { types: new Set(), rows: [], mv: 0 }
      familyRows[fam].types.add(r.type)
      familyRows[fam].rows.push(r)
      familyRows[fam].mv += r.mv || 0
    }
    for (const r of rows) {
      const fams = famIdx
        ? (famIdx.byRow.get(`${r.type}:${r.code}`) || [])
        : riskFamiliesOf(r).map(f => FAMILY_LABEL[f] || f)
      for (const f of fams) add(f, r)
    }
    // 后端已经报过的行业别再报一遍(它那条带穿透金额, 更准)
    const saidByBackend = new Set((expo?.warnings || [])
      .filter(w => w.kind === 'industry' && w.industry).map(w => w.industry))
    const overlapRowIds = new Set()
    const overlapByRow = {}
    for (const [fam, info] of Object.entries(familyRows)) {
      if (info.rows.length < 2) continue
      // 家族占比要用穿透后的钱。拿整行市值加起来会得出"通信设备 57%"这种数(实测) ——
      // 那 6 只基金只有 0.25万 在通信设备里, 不是整只 7 万都是。
      const famMv = famIdx?.fams.get(fam)?.mv ?? info.mv
      const famPct = total > 0 ? (famMv / total * 100) : 0
      for (const r of info.rows) {
        overlapRowIds.add(r.id)
        if (!overlapByRow[r.id]) overlapByRow[r.id] = []
        // 带上"还有谁跟你同一块": 光说家族名等于让人自己去表里找
        overlapByRow[r.id].push({ fam, pct: famPct, others: info.rows.filter(x => x.id !== r.id).map(x => x.name) })
      }
      // 横跨 A股 和 基金 才提示: 同类型内部的集中度上面第 2 条已经在管了
      if (info.types.size >= 2 && !saidByBackend.has(fam)) {
        warnings.push({
          level: 'med',
          text: `「${fam}」横跨 A股 + 基金，穿透后合计 ${famPct.toFixed(1)}% — ${info.rows.map(r => r.name).slice(0, 4).join(' + ')} 其实是同一块风险`,
        })
      }
    }
    return { warnings, overlapRowIds, overlapByRow }
  }, [rows, famIdx, expo])

  const removeAsset = async (row) => {
    if (!confirm(`删除 ${row.name}？`)) return
    await fetchJSON(`/api/assets/${row._raw.id}`, { method: 'DELETE' })
    loadAssets()
  }

  const handleEdit = (row) => {
    if (row.type === 'A') onEdit?.(row._raw)
    else show('edit', row._raw)
  }
  const handleAddLot = (row) => {
    if (row.type === 'A') return  // A股 走 TransactionHistory
    show('addLot', row._raw)
  }
  const handleReduceLot = (row) => {
    if (row.type === 'A') return
    show('reduce', row._raw)
  }
  const handleShowActions = (row) => {
    if (row.type === 'A') return
    show('actions', row._raw)
  }

  // 马丁策略的"总预算"。OKX 的 raw 里没有这个字段, 算法反推出来的常常不准,
  // 所以允许按 OKX 客户端里看到的实际值覆盖(留空 = 恢复估算)。
  const editBotBudget = async (row) => {
    const cur = row.extra?.okxTotalBudgetUsdt
    const next = prompt(
      `「${row.name}」马丁实际总预算 (USDT)\n填 OKX 客户端策略详情看到的"总投资额", 留空恢复算法估算`,
      cur ? String(cur) : ''
    )
    if (next === null) return
    const v = next.trim() === '' ? null : parseFloat(next)
    if (next.trim() !== '' && (!v || v <= 0)) { alert('总预算必须为正数'); return }
    try {
      await fetchJSON(`/api/assets/${row.extra.assetIdRaw}`, {
        method: 'PUT',
        body: JSON.stringify({ bot_budget_override_usdt: v }),
      })
      await loadAssets()
    } catch (err) { alert('保存失败: ' + (err?.message || '')) }
  }

  // 行内操作的一整套回调。RowActions 的入参本来就是这几个, 所以整体传,
  // 不在 HoldingRow 上摊成九个 prop。
  const rowActions = {
    onEditBotBudget: editBotBudget,
    onEdit: handleEdit, onHistory, onRemove: removeAsset,
    onAddLot: handleAddLot, onReduceLot: handleReduceLot,
    onShowActions: handleShowActions,
    onCashAdjust: (r) => show('cashAdjust', r._raw),
    onKline: (h) => show('kline', h),
    onThesis: (r) => show('thesis', r),
    onQuality: (r) => show('quality', r),
  }

  const isEmpty = rows.length === 0
  if (isEmpty && !assetsLoaded) {
    return <SkeletonCard rows={8} label="持仓加载中" />
  }

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      {/* Header: summary + donut */}
      <div className="px-3 md:px-6 py-3 md:py-5 border-b border-border flex flex-col md:flex-row md:flex-wrap md:justify-between md:items-center gap-4 md:gap-8"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex flex-col gap-3 w-full md:flex-1 md:w-auto min-w-0 md:min-w-[340px]">
          <div className="flex items-baseline gap-3">
            <h2 className="text-[14px] font-semibold text-text-bright tracking-wide m-0">持仓总览</h2>
            <span className="text-[11px] text-text-dim">股票 · 基金 · 理财 · 现金 · 加密 · 机器人</span>
          </div>
          {isEmpty
            ? <div className="text-text-dim text-[12px] py-2">还没有持仓,点击下方「+ 添加」开始</div>
            : <SummaryStrip agg={agg} aShareClosed={!aShareTradingDay} realized={realized} todayPnl={todayPnl} />
          }
        </div>
        {!isEmpty && <AllocationDonut groups={agg.groups} totalMv={agg.totalMv} />}
      </div>

      {/* 定投待确认 banner: 一键按当日净值自动结算所有 pending 申购 */}
      {(() => {
        const pendN = (assets || []).reduce((s, a) => s + (a.pending_actions_count || 0), 0)
        const hasRateFund = (assets || []).some(a => a.asset_type === 'FUND' && a.purchase_fee_rate != null)
        if (pendN === 0 && !hasRateFund && !settleMsg) return null
        return (
          <div className="px-3 md:px-6 py-2.5 border-b border-border bg-accent/5 flex items-center gap-3 flex-wrap">
            <span className="inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] shrink-0 bg-accent/15 text-accent border border-accent/50">⏳</span>
            <span className="text-[11.5px] text-text">
              {pendN > 0 ? `${pendN} 笔定投待确认份额` : '定投'}
              {settleMsg && <span className="text-text-dim ml-2">· {settleMsg}</span>}
            </span>
            <div className="ml-auto flex items-center gap-2">
              {hasRateFund && (
                <button onClick={recomputeDca} disabled={settling}
                  title="改了申购费率后, 按当日净值重算已确认的定投份额"
                  className="px-3 py-1 rounded text-[11.5px] border border-border-med text-text-dim hover:text-text hover:border-accent transition-colors cursor-pointer disabled:opacity-50">
                  {settling ? '处理中…' : '按费率重算'}
                </button>
              )}
              {pendN > 0 && (
                <button onClick={settlePending} disabled={settling}
                  className="px-3 py-1 rounded text-[11.5px] border border-accent/50 bg-accent/10 text-accent hover:bg-accent/20 transition-colors cursor-pointer disabled:opacity-50">
                  {settling ? '确认中…' : '一键按净值确认'}
                </button>
              )}
            </div>
          </div>
        )
      })()}

      {/* Risk insights strip */}
      {!isEmpty && (insights.warnings.length > 0 || (expo?.warnings || []).length > 0) && (
        <div className="px-3 md:px-6 py-2.5 border-b border-border bg-surface-2/60 flex flex-col gap-1.5">
          {/* low 级的不进这条提示带: 穿透会挖出一堆"某只票占 0.8%、来自 5 只基金"的细项,
              全铺出来会把真正该看的两三条(行业过半、两只基金同一注)压下去。细项在
              「问问市场」问一句就有(get_exposure 工具), 或直接看 /api/portfolio/exposure */}
          {[...insights.warnings, ...(expo?.warnings || []).filter(w => w.level !== 'low')].map((w, i) => {
            const color = w.level === 'high' ? '#e58a8a' : '#d4a05c'
            return (
              <div key={i} className="flex items-center gap-2 text-[11.5px]">
                <span className="inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] font-bold shrink-0"
                  style={{ background: `${color}1f`, color, border: `1px solid ${color}60` }}>!</span>
                <span className="text-text">{w.text}</span>
              </div>
            )
          })}
          {expo?.coverage && (
            <div className="text-[10px] text-text-muted leading-snug pl-6">
              穿透口径: 基金只拆到季报前十大(合计约占净值 25%~50%), 所以上面的敞口是<b>下限</b>；
              已穿透 {expo.coverage.penetrated_funds}/{expo.n_funds} 只基金
              {(expo.coverage.uncovered || []).length > 0 &&
                `，拉不到明细的 ${expo.coverage.uncovered.length} 只(${expo.coverage.uncovered.map(u => u.name).join('、')})未计入，那不等于敞口为 0`}
            </div>
          )}
        </div>
      )}

      {/* Toolbar */}
      <div className="px-3 md:px-6 py-2 border-b border-border flex justify-between items-center gap-3 flex-wrap"
        style={{ background: 'var(--color-surface-2)' }}>
        <div className="flex gap-1.5 flex-wrap">
          {[['ALL', '全部'], ...tabTypes.map(t => [t, TYPE_META[t].label])].map(([k, l]) => {
            const active = filter === k
            const c = k === 'ALL' ? '#c8a876' : TYPE_COLOR[k]
            return (
              <button key={k} onClick={() => setFilter(k)}
                className="px-2.5 py-[3px] rounded-md text-[11px] border transition-colors cursor-pointer"
                style={{
                  borderColor: active ? c : 'var(--color-border-med)',
                  background: active ? `${c}1a` : 'transparent',
                  color: active ? c : 'var(--color-text-dim)',
                }}>
                {l}
              </button>
            )
          })}
        </div>
        <div className="flex gap-1.5">
          <button onClick={() => setSortKey(sortKey === 'mv' ? 'pnl' : 'mv')}
            className="px-2.5 py-[3px] rounded-md text-[11px] border border-border-med bg-transparent text-text-dim hover:text-text transition-colors cursor-pointer">
            排序 · {sortKey === 'mv' ? '市值' : '盈亏'}
          </button>
          <button onClick={() => show('add', 'menu')}
            className="px-2.5 py-[3px] rounded-md text-[11px] border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 transition-colors cursor-pointer">
            + 添加
          </button>
        </div>
      </div>

      {/* 弹窗与内联表单: 全部收在 PortfolioModals 里 */}
      <PortfolioModals
        open={open} show={show} hide={hide} brokers={brokers}
        onAdd={onAdd}
        onAssetsChanged={loadAssets}
        onRealizedChanged={() => { loadAssets(); loadRealized() }}
        onThesisSaved={loadThesisCodes} />

      {/* Column headers */}
      {!isEmpty && (
        <div className="licai-row px-3 md:px-6 py-2 text-[10.5px] text-text-dim tracking-wider font-medium border-b border-border bg-surface">
          <div className="text-left">名称 · 代码</div>
          <div className="text-right">市值</div>
          <div className="text-right licai-md-only">成本</div>
          <div className="text-right">浮动盈亏</div>
          <div className="text-right">今日</div>
          <div className="text-right licai-md-only">占比</div>
          <div className="text-left pl-2">操作</div>
        </div>
      )}

      {/* Groups + rows */}
      {visibleTypes.map(type => {
        const g = agg.groups[type]
        const isCol = collapsed[type]
        const groupFxExposure = type === 'A' ? (agg.fxExposure || []) : []
        const items = [...g.items].sort((a, b) =>
          sortKey === 'pnl'
            ? (b.pnl || 0) - (a.pnl || 0)
            : (b.mv || 0) - (a.mv || 0)
        )
        return (
          <div key={type}>
            {/* Group strip */}
            <div onClick={() => setCollapsed(c => ({ ...c, [type]: !c[type] }))}
              className="licai-row px-3 md:px-6 py-2 border-b border-border cursor-pointer select-none items-center text-[11px] font-semibold text-text"
              style={{ background: 'var(--color-surface-2)' }}>
              <div className="flex items-center gap-2">
                <span className="inline-block w-2 text-text-dim transition-transform"
                  style={{ transform: isCol ? 'rotate(-90deg)' : 'rotate(0)' }}>▾</span>
                <div className="w-[3px] h-[13px] rounded-sm" style={{ background: TYPE_COLOR[type] }} />
                <span>{TYPE_META[type].label}</span>
                <span className="text-text-dim font-normal text-[10.5px]">{items.length} 项</span>
              </div>
              <div className="text-right flex flex-col items-end">
                <span className="font-mono text-text">¥{fmtMoney(g.mv)}</span>
                {groupFxExposure.length > 0 && (
                  <span className="font-mono text-[9.5px] text-text-muted hidden md:inline">
                    {groupFxExposure.map(e => formatCurrencyMoney(e.currency, e.originalMarketValue)).join(' / ')}
                  </span>
                )}
              </div>
              <div className="text-right font-mono text-text-dim text-[10.5px] licai-md-only">¥{fmtMoney(g.cost)}</div>
              <div className={`text-right font-mono ${priceColor(g.pnl)}`}>
                {g.pnl >= 0 ? '+' : ''}{fmtMoney(g.pnl)}
                <span className="text-[10px] opacity-80 ml-1.5">({fmtPct(g.pnlPct)})</span>
              </div>
              <div />
              <div className="text-right font-mono text-text licai-md-only">{(g.weight * 100).toFixed(1)}%</div>
              <div />
            </div>

            {/* Rows (with optional fund subcategory headers) */}
            {!isCol && (() => {
              // key 必须挂在这里而不是 HoldingRow 内部的根 div: 这个函数是在 map 里
              // 调的, React 认的是 map 直接产出的那个元素上的 key。
              const renderRow = (row, isLast) => (
                <HoldingRow key={row.id} row={row} isLast={isLast}
                  hovered={hoverId === row.id} onHover={setHoverId}
                  totalMv={agg.totalMv} insights={insights}
                  carry={carryByCode[row.code]} hasThesis={thesisCodes.has(row.code)}
                  actions={rowActions} />
              )

              // For FUND / A股 with >1 items: render category subgroups.
              if ((type === 'F' || type === 'A') && items.length > 1) {
                const clusters = {}
                for (const it of items) {
                  const cid = it.category?.id || 'other'
                  if (!clusters[cid]) clusters[cid] = { id: cid, label: it.category?.label || '其他', items: [], mv: 0, cost: 0, pnl: 0 }
                  clusters[cid].items.push(it)
                  clusters[cid].mv += it.mv || 0
                  clusters[cid].cost += it.cost || 0
                  clusters[cid].pnl += it.pnl || 0
                }
                const ordered = Object.values(clusters).sort((a, b) => b.mv - a.mv)
                if (ordered.length > 1) {
                  return ordered.map((cl, ci) => {
                    const isLastCluster = ci === ordered.length - 1
                    return (
                      <React.Fragment key={cl.id}>
                        <div className="licai-row px-3 md:px-6 py-1.5 items-center text-[10.5px] text-text-dim border-b border-border-subtle"
                          style={{ background: 'var(--color-surface)' }}>
                          <div className="flex items-center gap-1.5 pl-4">
                            <span className="inline-block w-1 h-1 rounded-full" style={{ background: TYPE_COLOR[type] }} />
                            <span className="font-medium text-text">{cl.label}</span>
                            <span className="opacity-70">{cl.items.length} 项</span>
                          </div>
                          <div className="text-right font-mono">¥{fmtMoney(cl.mv)}</div>
                          <div className="licai-md-only" />
                          <div className={`text-right font-mono ${priceColor(cl.pnl)}`}>
                            {cl.pnl >= 0 ? '+' : ''}{fmtMoney(cl.pnl)}
                          </div>
                          <div />
                          <div className="licai-md-only" />
                          <div />
                        </div>
                        {cl.items.map((row, ri) =>
                          renderRow(row, isLastCluster && ri === cl.items.length - 1))}
                      </React.Fragment>
                    )
                  })
                }
              }
              return items.map((row, ri) => renderRow(row, ri === items.length - 1))
            })()}
          </div>
        )
      })}

      {filtered.length === 0 && !isEmpty && (
        <div className="py-8 text-center text-text-dim text-[12px]">
          当前筛选下没有持仓
        </div>
      )}

      <ClosedPositionsBlock
        items={[
          ...(realized?.stockItems || []).filter(i => !i.still_holding),
          // 已清仓的理财/基金/加密 (排除 CASH/BOT): 同一只基金分多次买卖会记成多条(不同 asset_id),
          // 按 code 合并、已实现盈亏求和, 一只基金只显示一行(避免同名重复)。
          ...Object.values(
            (realized?.assetItems || [])
              .filter(i => !i.still_holding && i.asset_type !== 'CASH' && i.asset_type !== 'BOT'
                && (i.realized_pnl || 0) !== 0)
              .reduce((acc, i) => {
                const key = i.code || `#${i.asset_id}`
                if (!acc[key]) acc[key] = { stock_code: key, stock_name: i.name, realized_pnl: 0,
                  _asset: true, asset_id: i.asset_id, code: i.code }
                acc[key].realized_pnl += (i.realized_pnl || 0)
                return acc
              }, {})
          ),
        ]}
        onHistory={onHistory} onKline={(h) => show('kline', h)} />
    </section>
  )
}
