import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { fmtMoney, fmtPct, fundPassthroughType, loadBrokers, priceColor } from '../helpers'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'
import StockKlineModal from './StockKlineModal'
import Tooltip from './Tooltip'
import { QualityModal } from './portfolio/QualityModal'
import { ThesisModal } from './portfolio/ThesisModal'
import { AssetActionsModal } from './portfolio/actions'
import { AllocationDonut, FxHint, MarketChip, ProxyPulse, RowActions, TodayPulse, TypeChip, TypeMiniInfo, WeightBar } from './portfolio/atoms'
import { FAMILY_LABEL, TYPE_COLOR, TYPE_META, TYPE_ORDER } from './portfolio/constants'
import { AddAShareForm } from './portfolio/forms/AddAShareForm'
import { AddAssetForm } from './portfolio/forms/AddAssetForm'
import { EditAssetRow } from './portfolio/forms/EditAssetRow'
import { AddLotRow, CashAdjustRow, ReduceLotRow } from './portfolio/forms/lots'
import { buildFamilyIndex, formatCurrencyMoney, riskFamiliesOf } from './portfolio/helpers'
import { aggregate, normalizeAsset, normalizeHolding } from './portfolio/model'
import { ClosedPositionsBlock, SummaryStrip } from './portfolio/summary'

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
  const [addTarget, setAddTarget] = useState(null) // null | 'A' | 'F' | 'C' | 'R'
  const [editAsset, setEditAsset] = useState(null)
  const [addLotAsset, setAddLotAsset] = useState(null)
  const [reduceAsset, setReduceAsset] = useState(null)
  const [cashAdjustAsset, setCashAdjustAsset] = useState(null)
  const [actionsAsset, setActionsAsset] = useState(null)
  const [klineHolding, setKlineHolding] = useState(null)
  const [thesisTarget, setThesisTarget] = useState(null)
  const [qualityTarget, setQualityTarget] = useState(null)
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
    else setEditAsset(row._raw)
  }
  const handleAddLot = (row) => {
    if (row.type === 'A') return  // A股 走 TransactionHistory
    setAddLotAsset(row._raw)
  }
  const handleReduceLot = (row) => {
    if (row.type === 'A') return
    setReduceAsset(row._raw)
  }
  const handleShowActions = (row) => {
    if (row.type === 'A') return
    setActionsAsset(row._raw)
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
          <button onClick={() => setAddTarget('menu')}
            className="px-2.5 py-[3px] rounded-md text-[11px] border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 transition-colors cursor-pointer">
            + 添加
          </button>
        </div>
      </div>

      {/* Add menu / form */}
      {addTarget === 'menu' && (
        <div className="px-3 md:px-6 py-3 border-b border-border bg-surface-2/60 flex flex-wrap gap-2 items-center">
          <span className="text-[11px] text-text-dim mr-2">添加到哪一类?</span>
          {[
            ['A:A', 'A股'],
            ['A:HK', '港股'],
            ['A:US', '美股'],
            ...TYPE_ORDER.filter(t => t !== 'A').map(t => [t, TYPE_META[t].label]),
          ].map(([target, label]) => (
            <button key={target} onClick={() => setAddTarget(target)}
              className="px-3 py-1 rounded border border-border-med text-[12px] text-text hover:border-accent hover:text-accent transition-colors cursor-pointer">
              + {label}
            </button>
          ))}
          <button onClick={() => setAddTarget(null)}
            className="ml-auto text-[11px] text-text-dim hover:text-text cursor-pointer">取消</button>
        </div>
      )}

      {String(addTarget || '').startsWith('A:') && (
        <AddAShareForm initialMarket={addTarget.split(':')[1]}
          brokers={brokers}
          onDone={() => { setAddTarget(null); onAdd?.() }} onCancel={() => setAddTarget(null)} />
      )}
      {(addTarget === 'F' || addTarget === 'C' || addTarget === 'R' || addTarget === 'W' || addTarget === 'M') && (
        <AddAssetForm typeKey={addTarget}
          brokers={brokers}
          onDone={() => { setAddTarget(null); loadAssets() }}
          onCancel={() => setAddTarget(null)} />
      )}

      {editAsset && (
        <EditAssetRow asset={editAsset}
          brokers={brokers}
          onDone={() => { setEditAsset(null); loadAssets() }}
          onCancel={() => setEditAsset(null)} />
      )}

      {addLotAsset && (
        <AddLotRow asset={addLotAsset}
          brokers={brokers}
          onDone={() => { setAddLotAsset(null); loadAssets(); loadRealized() }}
          onCancel={() => setAddLotAsset(null)} />
      )}

      {reduceAsset && (
        <ReduceLotRow asset={reduceAsset}
          onDone={() => { setReduceAsset(null); loadAssets(); loadRealized() }}
          onCancel={() => setReduceAsset(null)} />
      )}

      {cashAdjustAsset && (
        <CashAdjustRow asset={cashAdjustAsset}
          onDone={() => { setCashAdjustAsset(null); loadAssets() }}
          onCancel={() => setCashAdjustAsset(null)} />
      )}

      {actionsAsset && (
        <AssetActionsModal asset={actionsAsset}
          onClose={() => setActionsAsset(null)}
          onChanged={() => { loadAssets(); loadRealized() }} />
      )}

      {klineHolding && (
        <StockKlineModal holding={klineHolding} onClose={() => setKlineHolding(null)} />
      )}
      {thesisTarget && (
        <ThesisModal row={thesisTarget} onClose={() => setThesisTarget(null)}
          onSaved={() => { loadThesisCodes(); setThesisTarget(null) }} />
      )}
      {qualityTarget && (
        <QualityModal row={qualityTarget} onClose={() => setQualityTarget(null)} />
      )}

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
              const renderRow = (row, isLast) => (
                <div key={row.id}
                  onMouseEnter={() => setHoverId(row.id)}
                  onMouseLeave={() => setHoverId(null)}
                  className="licai-row px-3 md:px-6 py-[11px] items-center transition-colors"
                  style={{
                    borderBottom: isLast ? '1px solid var(--color-border)' : '1px solid var(--color-border-subtle)',
                    background: hoverId === row.id ? 'var(--color-surface-2)' : 'transparent',
                  }}>
                  <div className="flex flex-col gap-0.5 min-w-0">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="text-[13px] font-semibold text-text-bright truncate">{row.name}</span>
                      <TypeChip type={row.type} compact />
                      {row.type === 'A' && <MarketChip market={row.extra?.market} />}
                      {row.broker && (
                        <span className="text-[10px] px-1.5 py-[1px] rounded bg-info/15 text-info border border-info/40 shrink-0 whitespace-nowrap font-medium">
                          {row.broker}
                        </span>
                      )}
                      {row.extra?.okxSynced && !row.extra?.okxStopped && (
                        <Tooltip content="OKX 自动同步中">
                          <span className="text-bull cursor-help text-[12px] leading-none">🔗</span>
                        </Tooltip>
                      )}
                      {row.extra?.okxStopped && (
                        <Tooltip content={
                          <div>
                            <div className="text-text-bright font-semibold mb-1">
                              策略已停止{row.extra?.okxState ? ` (${row.extra.okxState})` : ''} · 待归档
                            </div>
                            <div>OKX 拉到的是结束时的最终数字, 不再变动; 钱已回到现货账户。</div>
                            <div className="mt-1 text-text-dim text-[10.5px]">在这一行点「平仓归档」把盈亏定格转入已实现, 它就退出在持列表</div>
                          </div>
                        }>
                          <span className="text-warn cursor-help text-[11px] leading-none px-1 rounded bg-warn/15 border border-warn/40 shrink-0 whitespace-nowrap">已停止</span>
                        </Tooltip>
                      )}
                      {row.extra?.okxSynced === false && (
                        <Tooltip content={
                          <div>
                            <div className="text-text-bright font-semibold mb-1">同步断连 · 数字为旧快照</div>
                            <div>{row.extra?.syncError || 'OKX 不可达或凭证失效'}</div>
                            <div className="mt-1 text-text-dim text-[10.5px]">盈亏非实时。常见原因: 代理软件没开 → 打开后到 设置→代理 点自动探测</div>
                          </div>
                        }>
                          <span className="text-warn cursor-help text-[11px] leading-none px-1 rounded bg-warn/15 border border-warn/40 shrink-0">⚠ 同步断</span>
                        </Tooltip>
                      )}
                      {insights.overlapRowIds.has(row.id) && (
                        <Tooltip content={
                          <div>
                            <div className="text-text-bright font-semibold mb-1">同源风险家族</div>
                            <div className="flex flex-col gap-0.5">
                              {(insights.overlapByRow[row.id] || []).map(f => (
                                <div key={f.fam} className="text-text">
                                  · {f.fam} 合计 {f.pct.toFixed(1)}%
                                  {f.others.length > 0 && (
                                    <span className="text-text-dim">
                                      {' '}—— 还有 {f.others.slice(0, 3).join('、')}
                                      {f.others.length > 3 ? ` 等${f.others.length}项` : ''}
                                    </span>
                                  )}
                                </div>
                              ))}
                            </div>
                            <div className="text-text-dim mt-1.5 text-[10.5px] leading-snug">
                              这几行是同一块风险(按穿透后的行业归的，不看名字)：分开看像分散，合起来才是真实敞口
                            </div>
                          </div>
                        }>
                          <span className="inline-flex items-center gap-0.5 text-[9.5px] font-semibold px-1 py-[1px] rounded shrink-0 cursor-help"
                            style={{ color: '#e58a8a', background: '#e58a8a18', border: '1px solid #e58a8a40' }}>↔ 同源</span>
                        </Tooltip>
                      )}
                    </div>
                    <span className="font-mono text-[10px] text-text-muted truncate">{row.code}</span>
                  </div>
                  <div className="text-right flex flex-col items-end">
                    <span className="font-mono text-[12.5px] text-text-bright tabular-nums">
                      ¥{fmtMoney(row.mv)}
                    </span>
                    {row.extra?.currency && row.extra.currency !== 'CNY' && row.extra?.originalMarketValue != null && (
                      <FxHint extra={row.extra}>
                        <span className="font-mono text-[10px] text-text-muted tabular-nums cursor-help">
                          {formatCurrencyMoney(row.extra.currency, row.extra.originalMarketValue)}
                        </span>
                      </FxHint>
                    )}
                    {row.type === 'R' && row.extra?.okxTotalBudgetUsdt > 0 && row.extra?.okxAvailableUsdt != null && (
                      <Tooltip content={
                        <div className="leading-relaxed">
                          <div className="text-text-bright font-semibold mb-1">
                            马丁策略预算 {row.extra.okxBudgetSource === 'manual' ? '(手填)' : '(算法估算)'}
                          </div>
                          <div>首单 {row.extra.okxInitOrderAmt}U · 安全单基础 {row.extra.okxSafetyOrderAmt}U × {row.extra.okxMaxSafetyOrders} 档 · 量倍数 {row.extra.okxVolMult}</div>
                          <div className="mt-1 text-[10.5px]">
                            已投 <span className="text-text font-mono">{row.extra.okxInvestmentUsdt}U</span>
                            <span className="mx-1">/</span>
                            预算 <span className="text-text font-mono">{row.extra.okxTotalBudgetUsdt}U</span>
                            <span className="mx-1">·</span>
                            待投 <span className="text-bull-bright font-mono">{row.extra.okxAvailableUsdt}U</span>
                          </div>
                          {row.extra.okxBudgetSource !== 'manual' && (
                            <div className="mt-1 text-[10px] text-warn">
                              OKX raw 没"总预算"字段, 算法反推可能不准. 点击下面"改预算"用 OKX 客户端实际值覆盖.
                            </div>
                          )}
                        </div>
                      }>
                        <span
                          onClick={async (e) => {
                            e.stopPropagation()
                            const cur = row.extra.okxTotalBudgetUsdt
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
                              const d = await fetchJSON('/api/assets')
                              setAssets(d.assets || [])
                            } catch (err) { alert('保存失败: ' + (err?.message || '')) }
                          }}
                          className="font-mono text-[10px] text-text-muted tabular-nums cursor-pointer hover:text-accent mt-0.5 whitespace-nowrap"
                          title="点击改总预算">
                          投 {row.extra.okxInvestmentUsdt}U / {row.extra.okxTotalBudgetUsdt}U <span className="text-bull-bright">余 {row.extra.okxAvailableUsdt}U</span>{row.extra.okxBudgetSource !== 'manual' && (
                            <span className="text-warn ml-1" title="算法估算, 可能不准">~</span>
                          )}
                        </span>
                      </Tooltip>
                    )}
                  </div>
                  <div className="text-right flex flex-col items-end licai-md-only">
                    <span className="font-mono text-[11px] text-text-dim tabular-nums">
                      ¥{fmtMoney(row.cost)}
                    </span>
                    {row.extra?.currency && row.extra.currency !== 'CNY' && row.extra?.originalCostValue != null && (
                      <FxHint extra={row.extra}>
                        <span className="font-mono text-[9.5px] text-text-muted tabular-nums cursor-help">
                          {formatCurrencyMoney(row.extra.currency, row.extra.originalCostValue)}
                        </span>
                      </FxHint>
                    )}
                  </div>
                  <div className="text-right flex flex-col items-end">
                    {row.type === 'M' && row.extra?.monthlyInterestEst ? (
                      <Tooltip content={
                        <div className="leading-relaxed">
                          <div className="text-text-bright font-semibold mb-1">月息流估算</div>
                          <div>当前余额 ¥{fmtMoney(row.mv)} × 年化 {(row.extra.annualYield * 100).toFixed(2)}% / 12</div>
                          <div className="mt-1 text-[10.5px] text-text-dim">实际利率每天微变，估算 ±5% 误差</div>
                          <div className="mt-1 text-[10.5px] text-bull-bright">
                            日息 ≈ +¥{(row.extra.dailyInterestEst || 0).toFixed(2)} · 年息 ≈ +¥{fmtMoney(row.extra.yearlyInterestEst || 0)}
                          </div>
                        </div>
                      }>
                        <div className="flex flex-col items-end cursor-help">
                          <span className="font-mono text-[12.5px] font-semibold tabular-nums text-bull-bright">
                            ≈ +¥{fmtMoney(row.extra.monthlyInterestEst)}/月
                          </span>
                          <span className="font-mono text-[10px] text-text-dim">
                            年化 {(row.extra.annualYield * 100).toFixed(2)}%
                          </span>
                        </div>
                      </Tooltip>
                    ) : (
                      <>
                        <span className={`font-mono text-[12.5px] font-semibold tabular-nums ${priceColor(row.pnl)}`}>
                          {row.pnl != null ? (row.pnl >= 0 ? '+' : '') + fmtMoney(row.pnl) : '--'}
                        </span>
                        <span className={`font-mono text-[10px] opacity-85 ${priceColor(row.pnlPct)}`}>
                          {row.pnlPct != null ? fmtPct(row.pnlPct) : ''}
                        </span>
                        {row.type === 'A' && Math.abs(carryByCode[row.code] || 0) > 0.5 && (() => {
                          const total = (row.pnl || 0) + carryByCode[row.code]
                          return (
                            <Tooltip content="全周期真实盈亏 = 当前持仓浮动 + 历史已实现(清仓段/部分卖出)。清仓后又买回的票, 行内浮动只算这手, 这里把历史亏赚补回来。">
                              <div className={`font-mono text-[9.5px] ${priceColor(total)} opacity-90 cursor-help`}>
                                真实 {total >= 0 ? '+' : ''}{fmtMoney(total)}
                              </div>
                            </Tooltip>
                          )
                        })()}
                        {row.type !== 'A' && row.type !== 'M' && Math.abs((row._raw?.realized_pnl || 0) - (row._raw?.cycle_realized || 0)) > 0.5 && (() => {
                          const total = (row.pnl || 0) + (row._raw.realized_pnl || 0) - (row._raw.cycle_realized || 0)
                          return (
                            <Tooltip content="全周期真实盈亏 = 当前浮动 + 已实现(卖出/赎回的配对盈亏 + 利息分红)。卖掉部分的亏赚不在浮动里, 这里补齐——卖亏了再低位买回时, 浮动转正但真实口径仍记着那笔亏。">
                              <div className={`font-mono text-[9.5px] ${priceColor(total)} opacity-90 cursor-help`}>
                                真实 {total >= 0 ? '+' : ''}{fmtMoney(total)}
                              </div>
                            </Tooltip>
                          )
                        })()}
                      </>
                    )}
                  </div>
                  <div className="text-right">
                    {row.type === 'M' && row.extra?.dailyInterestEst ? (
                      <Tooltip content={
                        <div>
                          <div className="text-text-bright font-semibold mb-1">日息估算</div>
                          <div>当前余额 × 年化 / 365</div>
                          <div className="mt-1 text-text-dim text-[10.5px]">货币基金每日结息</div>
                        </div>
                      }>
                        <span className="font-mono text-[11px] text-bull-bright cursor-help">
                          +¥{row.extra.dailyInterestEst.toFixed(2)}
                        </span>
                      </Tooltip>
                    ) : row.type === 'F' && row.extra?.proxyChangePct != null ? (
                      <ProxyPulse
                        change={row.extra.proxyChangePct}
                        label={row.extra.proxyLabel}
                        details={row.extra.proxyDetails}
                        fallbackToday={row.today} />
                    ) : (
                      <TodayPulse change={row.today} />
                    )}
                  </div>
                  <div className="text-right licai-md-only">
                    <WeightBar weight={row.mv / (agg.totalMv || 1)} color={TYPE_COLOR[row.type]} />
                  </div>
                  <div className="relative pl-1 md:pl-2">
                    {/* TypeMiniInfo: 桌面始终可见 (不再 hover 隐藏); 移动隐藏 */}
                    <div className="hidden md:block">
                      <TypeMiniInfo row={row} />
                    </div>
                    {/* RowActions: 桌面 hover 显示, 绝对覆盖右半. 容器 pointer-events-none 让事件
                        穿透到 TypeMiniInfo (反推 tooltip 之类), 子元素重置 auto 接收点击.
                        hover 时给个 surface-2 渐变遮罩, 与 row hover 背景色一致, 视觉自然. */}
                    <div className="md:absolute md:inset-y-0 md:right-0 flex items-center md:pr-3 md:pointer-events-none [&>div]:pointer-events-auto transition-opacity"
                      style={{
                        background: hoverId === row.id
                          ? 'linear-gradient(to right, transparent 0%, var(--color-surface-2) 18%, var(--color-surface-2) 100%)'
                          : 'transparent',
                        transition: 'background .18s',
                      }}>
                      <RowActions row={row} visible={hoverId === row.id}
                        onEdit={handleEdit} onHistory={onHistory} onRemove={removeAsset}
                        onAddLot={handleAddLot} onReduceLot={handleReduceLot}
                        onShowActions={handleShowActions}
                        onCashAdjust={(r) => setCashAdjustAsset(r._raw)}
                        onKline={setKlineHolding}
                        onThesis={setThesisTarget} hasThesis={thesisCodes.has(row.code)}
                        onQuality={setQualityTarget} />
                    </div>
                  </div>
                </div>
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
        onHistory={onHistory} onKline={setKlineHolding} />
    </section>
  )
}
