import { useState, useEffect, useMemo } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtMoney } from '../helpers'

// A 股内部 11 个行业模板. 跟 UnifiedPortfolio 里 aShareCategoryOf 的 id 对齐.
// 三档对应 AllocationAdvisor 的 defensive/balanced/aggressive.
const SECTOR_TEMPLATE = {
  defensive: {
    finance: 25, consumer: 18, medical: 14, energy: 10, tech: 8,
    newenergy: 6, metals: 5, auto: 5, materials: 4, machinery: 3, realestate: 2,
  },
  balanced: {
    finance: 18, consumer: 14, tech: 14, medical: 12, newenergy: 10,
    metals: 8, energy: 6, auto: 6, machinery: 4, materials: 4, realestate: 4,
  },
  aggressive: {
    tech: 22, newenergy: 18, medical: 12, consumer: 10, auto: 10,
    metals: 8, finance: 8, machinery: 4, materials: 3, energy: 3, realestate: 2,
  },
}

const SECTOR_LABEL = {
  finance: '金融', consumer: '消费', medical: '医药', tech: '科技',
  newenergy: '电气新能源', metals: '有色金属', energy: '能源',
  auto: '汽车', machinery: '机械', materials: '材料', realestate: '地产',
  other: '其他',
}

// 跟 UnifiedPortfolio.aShareCategoryOf 同步. 保持一份独立的好处: 这组件可以独立 import.
const SECTOR_REGEX = [
  // 注: 贵金属 (黄金/白银) 不在 metals 里 — 跟铜/铝/锌等工业金属走势不一样,
  // 黄金 ETF 在 fundToSector 已经被拦截到 null (归大类 C 商品桶).
  ['metals',    /有色|铜(?!陵)?|铝|锌|镍|铅|稀土|钼|铜陵|钢铁/],
  ['newenergy', /电气设备|电源设备|储能|光伏|锂电|动力电池|电池|风电设备|新能源(?!车)/],
  ['energy',    /石油|煤|燃气|电力|核电|风电|公用事业|采掘/],
  ['finance',   /银行|证券|保险|期货|信托|非银金融/],
  ['realestate',/地产|置业|建设|建筑/],
  ['tech',      /科技|半导体|芯片|软件|信息|电子|通信|计算|AIDC|算力|数字|云计算|传媒|AI(?!P)/],
  ['consumer',  /消费|食品|酒|乳业|家电|家用电器|零售|百货|医美|纺织|休闲服务/],
  ['medical',   /医药|生物|医疗|制药|疫苗/],
  ['auto',      /汽车|新能源车|整车/],
  ['materials', /钢|水泥|玻璃|化工|塑料|纤维|建筑材料|建筑装饰|轻工制造/],
  ['machinery', /机械|装备|工程|重工|军工|国防/],
]

function sectorIdOf(name, sector) {
  const probe = sector || name || ''
  for (const [id, rx] of SECTOR_REGEX) if (rx.test(probe)) return id
  return 'other'
}

// 基金 → A 股内部行业映射. 海外 / 债券 / 货币 / 商品(非金) 不进 A 股 sector 桶, 返回 null.
// 宽基 (沪深300/中证500/上证50/创业板/科创50): 视作"已分散", 不归入任何单一行业, 返回 'broad'
// 调用方拿到 'broad' 后按各行业目标比例等比加权(粗略代表广覆盖).
function fundToSector(name = '') {
  if (/QDII|纳斯达克|纳指|标普|美股|港股|恒生|中概|海外|日经|越南|印度|欧洲/.test(name)) return null
  if (/债|利率|稳健增利/.test(name)) return null
  if (/货币|活期|余额宝|现金/.test(name)) return null
  // 贵金属 / 商品: 不归 A 股板块 (跟工业金属走势不同), 由大类 C 桶承接
  if (/黄金|白银|金\s*ETF|银\s*ETF|原油|石油\s*ETF|商品/.test(name)) return null
  if (/沪深300|中证500|中证1000|上证50|创业板(?!AI)|科创50|A股宽基/.test(name)) return 'broad'
  return sectorIdOf(name, '')
}

// AllocationAdvisor 大类模板, 决定"A 股大类应占总仓位多少 %". 跟 AllocationAdvisor.TEMPLATE_MATRIX 同步.
// 跟 AllocationAdvisor.TEMPLATE_MATRIX 的 A 行同步.
const A_TARGET_PCT = {
  defensive:  { small: 12, mid: 10, large: 10, xl: 10 },
  balanced:   { small: 22, mid: 20, large: 18, xl: 15 },
  aggressive: { small: 32, mid: 28, large: 24, xl: 20 },
}

function tierKey(total) {
  if (total < 300_000) return 'small'
  if (total < 1_000_000) return 'mid'
  if (total < 5_000_000) return 'large'
  return 'xl'
}

export default function AShareSectorGap() {
  const [holdings, setHoldings] = useState([])
  const [assets, setAssets] = useState([])
  // 跟 AllocationAdvisor 共用 localStorage key, 让两个组件保持同一档模板
  const [tpl, setTpl] = useState(() => localStorage.getItem('allocTemplate') || 'balanced')

  useEffect(() => {
    fetchJSON('/api/portfolio').then(setHoldings).catch(() => {})
    fetchJSON('/api/assets').then(d => setAssets(d.assets || [])).catch(() => {})
    const onStorage = () => setTpl(localStorage.getItem('allocTemplate') || 'balanced')
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const { aMv, fundExposureMv, totalMv, sectorMv, fundSourceById } = useMemo(() => {
    const sec = {}
    const fundSrc = {}      // sector_id → [基金名] 用来 UI 标"通过基金"
    let aStock = 0
    let fundEx = 0
    let total = 0
    for (const h of holdings) {
      const code = String(h.stock_code || '').toUpperCase()
      const mv = h.market_value != null ? h.market_value : (h.current_price || 0) * h.shares
      total += mv
      if (code.startsWith('HK.') || code.startsWith('US.')) continue
      aStock += mv
      const sid = sectorIdOf(h.stock_name, h.sector)
      sec[sid] = (sec[sid] || 0) + mv
    }
    const broadFundMv = []
    for (const a of assets) {
      const mv = a.current_value || 0
      total += mv
      if (a.asset_type !== 'FUND' || mv <= 0) continue
      const sid = fundToSector(a.name || '')
      if (sid == null) continue           // 海外 / 债券 / 货币: 不进 A 股桶
      if (sid === 'broad') {
        broadFundMv.push({ name: a.name, mv })
        continue
      }
      sec[sid] = (sec[sid] || 0) + mv
      fundEx += mv
      ;(fundSrc[sid] = fundSrc[sid] || []).push(a.name)
    }
    // 宽基: 按"平衡"模板各行业权重等比分摊到 11 个 sector 里
    if (broadFundMv.length > 0) {
      const totalBroad = broadFundMv.reduce((s, x) => s + x.mv, 0)
      const tplTargets = SECTOR_TEMPLATE.balanced
      for (const [sid, pct] of Object.entries(tplTargets)) {
        const share = totalBroad * pct / 100
        sec[sid] = (sec[sid] || 0) + share
        fundEx += share
        for (const f of broadFundMv) (fundSrc[sid] = fundSrc[sid] || []).push(f.name + '(宽基)')
      }
    }
    return { aMv: aStock, fundExposureMv: fundEx, totalMv: total, sectorMv: sec, fundSourceById: fundSrc }
  }, [holdings, assets])

  const exposureMv = aMv + fundExposureMv

  if (exposureMv === 0) return null

  const tier = tierKey(totalMv)
  // "A 股大类"目标 = A 股直接 + 通过基金获得的 A 股行业敞口. 跟 AllocationAdvisor 大类一致.
  const aTargetPct = A_TARGET_PCT[tpl]?.[tier] ?? 22
  const aTargetMv = totalMv * aTargetPct / 100
  const aGap = aTargetMv - exposureMv   // > 0 表示 A 股该加仓多少钱

  const targets = SECTOR_TEMPLATE[tpl] || SECTOR_TEMPLATE.balanced

  // 当前已暴露的最大行业(用于集中度警告). 分母用合并后的 exposureMv.
  const sortedCurrent = Object.entries(sectorMv).sort((a, b) => b[1] - a[1])
  const topSectorId = sortedCurrent[0]?.[0]
  const topSectorPct = sortedCurrent[0] ? (sortedCurrent[0][1] / exposureMv) * 100 : 0
  const concentrationWarn = topSectorPct >= 50

  // 缺口: 模板目标 - 当前占比. 正 = 缺, 负 = 超配.
  const gapRows = Object.entries(targets).map(([id, targetPct]) => {
    const currentMv = sectorMv[id] || 0
    const currentPct = exposureMv > 0 ? (currentMv / exposureMv) * 100 : 0
    const gapPct = targetPct - currentPct
    const adviceMv = aGap > 0
      ? aGap * (targetPct / 100) + Math.max(0, currentMv === 0 ? exposureMv * (targetPct / 100) : 0)
      : Math.max(0, gapPct / 100) * exposureMv
    return {
      id, targetPct, currentPct, currentMv, gapPct, adviceMv,
      missing: currentMv === 0,
      viaFund: (fundSourceById[id] || []).length > 0 && (sectorMv[id] || 0) > 0
        && (currentMv - (fundSourceById[id] ? sectorMv[id] : 0) <= 0)
        ? fundSourceById[id]
        : null,
    }
  })

  const missingSectors = gapRows.filter(r => r.missing && r.targetPct >= 4)
    .sort((a, b) => b.targetPct - a.targetPct)
  const underweightSectors = gapRows.filter(r => !r.missing && r.gapPct >= 3)
    .sort((a, b) => b.gapPct - a.gapPct)
  const overweightSectors = gapRows.filter(r => r.gapPct <= -5)
    .sort((a, b) => a.gapPct - b.gapPct)

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-baseline justify-between flex-wrap gap-2"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2 flex-wrap">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">A 股行业缺口</h3>
          <span className="text-[11px] text-text-dim">
            敞口 ¥{fmtMoney(exposureMv)}
            {fundExposureMv > 0 && (
              <span className="text-text-muted"> (直 ¥{fmtMoney(aMv)} + 基金 ¥{fmtMoney(fundExposureMv)})</span>
            )}
            <span className="mx-1">·</span>
            {aGap > 100 ? <span className="text-bull">该加仓 ¥{fmtMoney(aGap)}</span> : <span>已对齐</span>}
          </span>
        </div>
        <span className="text-[10.5px] text-text-muted">
          模板跟随上方"{tpl === 'defensive' ? '保守' : tpl === 'aggressive' ? '激进' : '平衡'}"
        </span>
      </div>

      {concentrationWarn && (
        <div className="px-3 md:px-5 py-2 bg-bear/8 border-b border-border-subtle">
          <div className="text-[11.5px] text-bear-bright">
            ⚠️ 集中度风险: 「{SECTOR_LABEL[topSectorId]}」占你 A 股 {topSectorPct.toFixed(0)}% (模板目标 {targets[topSectorId] || 0}%)
          </div>
          <div className="text-[10.5px] text-text-muted mt-0.5">
            单一行业押注过重, 反弹错配 / 板块下杀放大回撤. 建议先把缺失行业补起来分散.
          </div>
        </div>
      )}

      {missingSectors.length > 0 && (
        <div className="px-3 md:px-5 py-2.5 border-b border-border-subtle">
          <div className="text-[10.5px] text-text-dim mb-1.5 tracking-wider">
            完全没暴露的行业 ({missingSectors.length})
          </div>
          <div className="flex flex-wrap gap-1.5">
            {missingSectors.map(r => (
              <div key={r.id} className="flex items-baseline gap-1 px-2 py-[3px] rounded-md bg-bull/8 border border-bull/30 text-[11px]">
                <span className="text-text-bright">{SECTOR_LABEL[r.id]}</span>
                <span className="text-text-muted text-[10px]">目标 {r.targetPct}%</span>
                {r.adviceMv >= 100 && (
                  <span className="text-bull-bright font-mono text-[10.5px]">+¥{fmtMoney(r.adviceMv)}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {underweightSectors.length > 0 && (
        <div className="px-3 md:px-5 py-2.5 border-b border-border-subtle">
          <div className="text-[10.5px] text-text-dim mb-1.5 tracking-wider">低配 (缺口 ≥ 3%)</div>
          <div className="flex flex-wrap gap-1.5">
            {underweightSectors.map(r => (
              <div key={r.id} className="flex items-baseline gap-1 px-2 py-[3px] rounded-md bg-surface-3 border border-border-subtle text-[11px]">
                <span className="text-text">{SECTOR_LABEL[r.id]}</span>
                <span className="text-text-muted text-[10px] font-mono">
                  {r.currentPct.toFixed(0)}% / {r.targetPct}%
                </span>
                {r.viaFund && (
                  <span className="text-[9.5px] px-1 rounded bg-info/15 text-info border border-info/30"
                    title={'目前敞口全部由基金提供: ' + r.viaFund.join(', ')}>
                    基金
                  </span>
                )}
                {r.adviceMv >= 100 && (
                  <span className="text-info font-mono text-[10.5px]">+¥{fmtMoney(r.adviceMv)}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {overweightSectors.length > 0 && (
        <div className="px-3 md:px-5 py-2.5 border-b border-border-subtle">
          <div className="text-[10.5px] text-text-dim mb-1.5 tracking-wider">超配 (超过模板 ≥ 5%)</div>
          <div className="flex flex-wrap gap-1.5">
            {overweightSectors.map(r => (
              <div key={r.id} className="flex items-baseline gap-1 px-2 py-[3px] rounded-md bg-bear/8 border border-bear/30 text-[11px]">
                <span className="text-text-bright">{SECTOR_LABEL[r.id]}</span>
                <span className="text-text-muted text-[10px] font-mono">
                  {r.currentPct.toFixed(0)}% / {r.targetPct}%
                </span>
                <span className="text-bear text-[10.5px]">超 {Math.abs(r.gapPct).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="px-3 md:px-5 py-2 bg-surface-2/40 text-[10.5px] text-text-muted leading-relaxed">
        敞口 = A 股直接持仓 + 行业型基金(半导体/黄金/AI 等). 海外/债券/货币不进 A 股桶, 宽基按平衡模板均摊.
        ↓ 在下方「板块机会」里挑缺失行业里 5 日动量为正的板块, 选个股或对应 ETF — 不直接推荐, 结构是工具, 选股交给你.
      </div>
    </section>
  )
}
