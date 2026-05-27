import { useState, useEffect, useMemo } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtMoney, fundPassthroughBucketDetailed } from '../helpers'

// 按总资产分档 — 海外配置随资金量上行 (开户 / 汇兑 / 单笔门槛对小资金不划算).
const TIERS = [
  { key: 'small', max: 300_000,    label: '< ¥30 万',
    desc: '起步段：先把 A 股 + 基金做扎实，海外暂不建议' },
  { key: 'mid',   max: 1_000_000,  label: '¥30-100 万',
    desc: '中小段：海外 5-12% 起步，先美股宽基 + 香港龙头做地域分散' },
  { key: 'large', max: 5_000_000,  label: '¥100-500 万',
    desc: '中大段：海外 10-22%，可港美分仓，行业 ETF 切板块' },
  { key: 'xl',    max: Infinity,   label: '> ¥500 万',
    desc: '大资金段：海外 18-30%，地域 + 货币 + 资产类双重分散' },
]

function tierOf(total) {
  return TIERS.find(t => total < t.max) || TIERS[TIERS.length - 1]
}

// 模板矩阵: [模板][分档] = { M, W, A, H, U, F, C } (合计 100%)
// 设计原则:
//   - 保守: 现金 + 理财 ≥ 60%, 海外占权益不超过 1/3
//   - 平衡: 权益与稳健 50:50, 海外随分档拉升
//   - 激进: 权益 + 加密 ≥ 70%, 海外可达 28%
// 10 桶按"实质"穿透模板. ETF/QDII 已按底层归到对应桶, 不再有粗糙的"F 基金"桶.
// 桶: M=现金 W=银证理财 BND=债基 A=A股 H=港股 U=美股 OS=其它海外 CMD=商品 CRY=加密 F=兜底
const TEMPLATE_MATRIX = {
  defensive: {
    small: { M: 20, W: 45, BND: 16, A: 12, H: 0, U: 3,  OS: 0, CMD: 4, CRY: 0,  F: 0 },
    mid:   { M: 21, W: 42, BND: 16, A: 10, H: 1, U: 5,  OS: 0, CMD: 5, CRY: 0,  F: 0 },
    large: { M: 20, W: 38, BND: 16, A: 10, H: 2, U: 8,  OS: 1, CMD: 5, CRY: 0,  F: 0 },
    xl:    { M: 20, W: 35, BND: 15, A: 10, H: 3, U: 10, OS: 2, CMD: 5, CRY: 0,  F: 0 },
  },
  balanced: {
    small: { M: 23, W: 32, BND: 8,  A: 22, H: 0, U: 6,  OS: 0, CMD: 6, CRY: 3,  F: 0 },
    mid:   { M: 22, W: 25, BND: 8,  A: 20, H: 3, U: 10, OS: 2, CMD: 6, CRY: 4,  F: 0 },
    large: { M: 21, W: 22, BND: 7,  A: 18, H: 5, U: 14, OS: 2, CMD: 6, CRY: 5,  F: 0 },
    xl:    { M: 20, W: 20, BND: 6,  A: 15, H: 7, U: 18, OS: 3, CMD: 6, CRY: 5,  F: 0 },
  },
  aggressive: {
    small: { M: 27, W: 10, BND: 5,  A: 32, H: 0, U: 10, OS: 0, CMD: 6, CRY: 10, F: 0 },
    mid:   { M: 22, W: 8,  BND: 5,  A: 28, H: 4, U: 14, OS: 2, CMD: 7, CRY: 10, F: 0 },
    large: { M: 20, W: 7,  BND: 4,  A: 24, H: 6, U: 18, OS: 3, CMD: 8, CRY: 10, F: 0 },
    xl:    { M: 18, W: 6,  BND: 4,  A: 20, H: 8, U: 22, OS: 4, CMD: 8, CRY: 10, F: 0 },
  },
}

const TEMPLATE_META = {
  defensive: {
    label: '保守型',
    desc: '稳收益、低回撤。理财/债基/现金 占大头, 权益少配',
    notes: [
      '现金 + 理财 + 债基 占 ~75%, 覆盖 1-2 年开支',
      '权益单板块 ≤ 15%; 加密不参与',
      '美股小资金也建议保留 3-10% 做地域分散',
    ],
  },
  balanced: {
    label: '平衡型',
    desc: '收益/风险均衡。权益(含海外)+ 商品 + 加密 占 40-55%',
    notes: [
      '小资金 (<¥30 万) 也保留美股 6%、商品 6%、加密 3%',
      'A股单板块 ≤ 30%; 同源族 (A股+基金穿透) 合计 ≤ 35%',
      '加密 3-5% 卫星仓, BTC/ETH 大盘币为主',
    ],
  },
  aggressive: {
    label: '激进型',
    desc: '搏收益。权益 + 加密占大头, 现金/理财仅留流动性',
    notes: [
      '需要承受 -30% 以上回撤的心理准备',
      '理财 + 债基 ≤ 15%, 主要用作机会子弹',
      'A股可单押 1-2 行业但单板块 ≤ 35%',
    ],
  },
}

const TYPE_LABEL = {
  M: '现金', W: '理财', BND: '债基', A: 'A 股', H: '港股',
  U: '美股', OS: '其它海外', CMD: '商品', CRY: '加密', F: '基金兜底',
}
const TYPE_COLOR = {
  M:   '#7a9b8e', // sage 现金
  W:   '#5fa86c', // green 理财
  BND: '#7fb085', // 浅 green 债基
  A:   '#c8a876', // gold A股
  H:   '#b87a8a', // rose 港股
  U:   '#6b8eb3', // steel blue 美股
  OS:  '#8aa0b8', // 浅 steel 其它海外
  CMD: '#d4b85c', // amber gold 商品
  CRY: '#d47a5c', // burnt orange 加密
  F:   '#85a0b4', // info 基金兜底
}
const ROW_ORDER = ['M', 'W', 'BND', 'A', 'H', 'U', 'OS', 'CMD', 'CRY', 'F']

export default function AllocationAdvisor() {
  const [holdings, setHoldings] = useState([])
  const [assets, setAssets] = useState([])
  const [cashflow, setCashflow] = useState(null)
  const [tpl, setTpl] = useState(() => localStorage.getItem('allocTemplate') || 'balanced')

  useEffect(() => {
    fetchJSON('/api/portfolio').then(setHoldings).catch(() => {})
    fetchJSON('/api/assets').then(d => setAssets(d.assets || [])).catch(() => {})
    fetchJSON('/api/cashflow/summary').then(setCashflow).catch(() => {})
  }, [])

  const pickTpl = (k) => { setTpl(k); localStorage.setItem('allocTemplate', k) }

  // 当前各类市值. 股票按 stock_code 前缀拆 A/H/U; BOT 归入 C.
  // FUND 按 fundPassthroughBucketDetailed 穿透到对应大类 (海外/港股/商品/债/货币 都分流),
  // 兜底归 F. 给两个数: passthrough (穿透后, 用于警告判断) + raw (展示用, 看实际是基金还是直接持有).
  const current = useMemo(() => {
    const zero = () => ({ M: 0, W: 0, BND: 0, A: 0, H: 0, U: 0, OS: 0, CMD: 0, CRY: 0, F: 0 })
    const buckets = zero()        // 载体视图: FUND 全归 F, 不穿透 (此处不再展示, 但保留汇总)
    const passthrough = zero()    // 穿透视图: 主展示
    const fundOrigin = {}         // bucket → ¥ from FUND passthrough (用于"含基金 ¥X" 标签)
    for (const h of holdings) {
      const v = h.market_value != null ? h.market_value : (h.current_price || 0) * h.shares
      const code = String(h.stock_code || '').toUpperCase()
      let k
      if (code.startsWith('HK.')) k = 'H'
      else if (code.startsWith('US.')) k = 'U'
      else k = 'A'
      buckets[k] += v
      passthrough[k] += v
    }
    for (const a of assets) {
      const t = a.asset_type
      const v = a.current_value || 0
      if (t === 'FUND') {
        buckets.F += v
        const target = fundPassthroughBucketDetailed(a.name || '')
        passthrough[target] = (passthrough[target] || 0) + v
        fundOrigin[target] = (fundOrigin[target] || 0) + v
      } else if (t === 'CRYPTO' || t === 'BOT') {
        buckets.CRY += v; passthrough.CRY += v
      } else if (t === 'WEALTH') {
        buckets.W += v; passthrough.W += v
      } else if (t === 'CASH') {
        buckets.M += v; passthrough.M += v
      }
    }
    const total = Object.values(buckets).reduce((s, v) => s + v, 0)
    return { buckets, passthrough, fundOrigin, total }
  }, [holdings, assets])

  if (current.total === 0) return null

  const tier = tierOf(current.total)
  const targets = TEMPLATE_MATRIX[tpl][tier.key]
  const meta = TEMPLATE_META[tpl]

  const rows = ROW_ORDER.map(k => {
    const targetPct = targets[k]
    const currentVal = current.passthrough[k]   // 穿透后 (海外/商品/债 ETF 各归大类)
    const currentPct = (currentVal / current.total) * 100
    const targetVal = (targetPct / 100) * current.total
    const delta = targetVal - currentVal
    const viaFundMv = current.fundOrigin[k] || 0  // 含多少是通过 FUND 穿透来的
    return { key: k, targetPct, currentPct, currentVal, targetVal, delta, viaFundMv }
  })

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-center justify-between flex-wrap gap-2"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2 flex-wrap">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">配置建议</h3>
          <span className="text-[11px] text-text-dim">
            目标 vs 当前 · 总额 ¥{fmtMoney(current.total)}
          </span>
          <span className="text-[10.5px] px-1.5 py-[1px] rounded border border-info/40 bg-info/10 text-info font-mono">
            {tier.label}
          </span>
        </div>
        <div className="flex gap-1.5">
          {Object.entries(TEMPLATE_META).map(([k, t]) => (
            <button key={k} onClick={() => pickTpl(k)}
              className="px-2.5 py-[3px] rounded-md text-[11px] border transition-colors cursor-pointer"
              style={{
                borderColor: tpl === k ? 'var(--color-accent)' : 'var(--color-border-med)',
                background: tpl === k ? 'var(--color-accent)1a' : 'transparent',
                color: tpl === k ? 'var(--color-accent)' : 'var(--color-text-dim)',
              }}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="px-3 md:px-5 py-2 text-[11.5px] text-text-dim border-b border-border-subtle bg-surface-2/30">
        <span className="text-text">{meta.label}</span>
        <span className="mx-1.5">·</span>
        {meta.desc}
        <span className="mx-1.5">·</span>
        <span className="text-info">{tier.desc}</span>
      </div>

      {cashflow && cashflow.avg_net > 0 && (
        <div className="px-3 md:px-5 py-2.5 border-b border-border-subtle bg-info/5">
          <div className="flex items-baseline justify-between flex-wrap gap-2 mb-1.5">
            <span className="text-[11.5px] text-text-dim">
              月均可投 (近 {cashflow.window} 月净储蓄)
            </span>
            <span className="font-mono font-semibold text-[14px] text-info">
              ¥{fmtMoney(cashflow.avg_net)}
            </span>
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-text-dim">
            {ROW_ORDER.filter(k => targets[k] > 0).map(k => (
              <span key={k}>
                <span className="inline-block w-1.5 h-1.5 rounded-sm mr-1 align-middle" style={{ background: TYPE_COLOR[k] }} />
                {TYPE_LABEL[k]} <span className="text-text font-mono">¥{fmtMoney(cashflow.avg_net * targets[k] / 100)}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="licai-alloc-row px-3 md:px-5 py-1.5 text-[10.5px] text-text-dim tracking-wider font-medium border-b border-border-subtle">
        <div>类别</div>
        <div className="text-right licai-md-only">目标</div>
        <div className="text-right">当前<span className="md:hidden text-text-muted"> / 目标</span></div>
        <div className="text-center licai-md-only">对比</div>
        <div className="text-right">建议调整</div>
      </div>

      <div className="divide-y divide-border-subtle">
        {rows.map(r => {
          const color = TYPE_COLOR[r.key]
          const cPct = Math.min(r.currentPct, 100)
          const tPct = Math.min(r.targetPct, 100)
          const overweight = r.currentPct > r.targetPct
          // 目标 = 0 且当前 = 0: 灰显, 不算调整
          const skip = r.targetPct === 0 && r.currentPct === 0
          return (
            <div key={r.key}
              className={`licai-alloc-row px-3 md:px-5 py-2.5 items-center text-[12px] ${skip ? 'opacity-50' : ''}`}>
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="inline-block w-2 h-2 rounded-sm" style={{ background: color }} />
                <span className="text-text-bright font-medium">{TYPE_LABEL[r.key]}</span>
                {r.viaFundMv > 0 && (
                  <span className="text-[9.5px] text-info bg-info/10 border border-info/30 rounded px-1 py-[1px] font-mono"
                    title={`含 ¥${fmtMoney(r.viaFundMv)} 通过基金穿透得到`}>
                    含基金 ¥{fmtMoney(r.viaFundMv)}
                  </span>
                )}
              </div>
              <div className="text-right font-mono text-text licai-md-only">{r.targetPct}%</div>
              <div className="text-right font-mono text-text">
                {r.currentPct.toFixed(1)}%
                <span className="md:hidden text-text-muted"> / {r.targetPct}%</span>
              </div>
              <div className="px-3 licai-md-only">
                <div className="relative h-3 rounded-sm bg-surface-3 overflow-hidden">
                  <div className="absolute top-0 left-0 h-full"
                    style={{ width: cPct + '%', background: color, opacity: 0.35 }} />
                  <div className="absolute top-0 h-full border-r-2" style={{
                    left: 'calc(' + tPct + '% - 1px)',
                    width: '2px',
                    borderColor: color,
                  }} />
                </div>
              </div>
              <div className="text-right">
                {skip ? (
                  <span className="text-text-dim text-[11px]">--</span>
                ) : Math.abs(r.delta) < 100 ? (
                  <span className="text-text-dim text-[11px]">≈ 已对齐</span>
                ) : (
                  <span className={`font-mono text-[11.5px] ${overweight ? 'text-bear-bright' : 'text-bull-bright'}`}>
                    {overweight ? '减仓 ' : '加仓 '}
                    ¥{fmtMoney(Math.abs(r.delta))}
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div className="px-3 md:px-5 py-2.5 bg-surface-2/40 border-t border-border-subtle">
        <div className="text-[10.5px] text-text-dim mb-1">📋 注意事项</div>
        <ul className="m-0 pl-4 space-y-0.5 text-[11px] text-text leading-relaxed list-disc">
          {meta.notes.map((n, i) => <li key={i}>{n}</li>)}
        </ul>
        <div className="text-[10px] text-text-muted mt-2 italic">
          * 模板配比不构成投资建议，仅作起点参考。OKX 网格 / DCA 机器人按底层标的归入加密大类。海外配置随总资产分档自动调整。
        </div>
      </div>
    </section>
  )
}
