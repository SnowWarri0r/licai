import { useState, useEffect, useMemo } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtMoney } from '../helpers'

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
const TEMPLATE_MATRIX = {
  defensive: {
    small: { M: 15, W: 50, A: 12, H: 0, U: 0,  F: 23, C: 0 },
    mid:   { M: 12, W: 48, A: 10, H: 1, U: 4,  F: 25, C: 0 },
    large: { M: 10, W: 45, A: 10, H: 3, U: 7,  F: 25, C: 0 },
    xl:    { M: 10, W: 42, A: 10, H: 5, U: 10, F: 23, C: 0 },
  },
  balanced: {
    small: { M: 10, W: 32, A: 28, H: 0, U: 0,  F: 25, C: 5 },
    mid:   { M: 8,  W: 28, A: 25, H: 3, U: 6,  F: 25, C: 5 },
    large: { M: 8,  W: 25, A: 22, H: 6, U: 12, F: 22, C: 5 },
    xl:    { M: 7,  W: 22, A: 18, H: 8, U: 18, F: 22, C: 5 },
  },
  aggressive: {
    small: { M: 5, W: 12, A: 38, H: 0, U: 0,  F: 35, C: 10 },
    mid:   { M: 5, W: 12, A: 32, H: 2, U: 6,  F: 33, C: 10 },
    large: { M: 5, W: 10, A: 28, H: 5, U: 12, F: 30, C: 10 },
    xl:    { M: 5, W: 10, A: 22, H: 8, U: 20, F: 25, C: 10 },
  },
}

const TEMPLATE_META = {
  defensive: {
    label: '保守型',
    desc: '稳收益、低回撤。理财/现金占大头，权益少配',
    notes: [
      '现金应急 + 短债理财占 ~60%，覆盖 1-2 年开支',
      '权益单板块 ≤ 25%，加密不参与',
      '海外随资金量上行做地域分散，不做激进押注',
    ],
  },
  balanced: {
    label: '平衡型',
    desc: '收益/风险均衡。权益与稳健资产五五开',
    notes: [
      '基金建议黄金 ~10% + 海外 ~10% + A股宽基 ~10%',
      'A股单板块 ≤ 30%；同源族（A股+基金）合计 ≤ 35%',
      '加密 5% 卫星仓，BTC/ETH 大盘币为主',
    ],
  },
  aggressive: {
    label: '激进型',
    desc: '搏收益。权益 + 加密占大头，现金/理财仅留流动性',
    notes: [
      '需要承受 -30% 以上回撤的心理准备',
      '现金 + 理财 ≤ 17%，主要用作机会子弹',
      'A股可单押 1-2 行业但单板块 ≤ 35%',
    ],
  },
}

const TYPE_LABEL = {
  M: '现金', W: '理财', A: 'A 股', H: '港股', U: '美股', F: '基金', C: '加密',
}
const TYPE_COLOR = {
  M: '#7a9b8e', // sage 现金
  W: '#5fa86c', // green 理财
  A: '#c8a876', // gold A股
  H: '#b87a8a', // rose 港股
  U: '#6b8eb3', // steel blue 美股
  F: '#85a0b4', // info 基金
  C: '#d4a05c', // amber 加密
}
const ROW_ORDER = ['M', 'W', 'A', 'H', 'U', 'F', 'C']

export default function AllocationAdvisor() {
  const [holdings, setHoldings] = useState([])
  const [assets, setAssets] = useState([])
  const [tpl, setTpl] = useState(() => localStorage.getItem('allocTemplate') || 'balanced')

  useEffect(() => {
    fetchJSON('/api/portfolio').then(setHoldings).catch(() => {})
    fetchJSON('/api/assets').then(d => setAssets(d.assets || [])).catch(() => {})
  }, [])

  const pickTpl = (k) => { setTpl(k); localStorage.setItem('allocTemplate', k) }

  // 当前各类市值. 股票按 stock_code 前缀拆 A/H/U; BOT 归入 C.
  const current = useMemo(() => {
    const buckets = { A: 0, H: 0, U: 0, F: 0, W: 0, M: 0, C: 0 }
    for (const h of holdings) {
      const v = h.market_value != null ? h.market_value : (h.current_price || 0) * h.shares
      const code = String(h.stock_code || '').toUpperCase()
      if (code.startsWith('HK.')) buckets.H += v
      else if (code.startsWith('US.')) buckets.U += v
      else buckets.A += v
    }
    for (const a of assets) {
      const t = a.asset_type
      const v = a.current_value || 0
      if (t === 'FUND') buckets.F += v
      else if (t === 'CRYPTO' || t === 'BOT') buckets.C += v
      else if (t === 'WEALTH') buckets.W += v
      else if (t === 'CASH') buckets.M += v
    }
    const total = Object.values(buckets).reduce((s, v) => s + v, 0)
    return { buckets, total }
  }, [holdings, assets])

  if (current.total === 0) return null

  const tier = tierOf(current.total)
  const targets = TEMPLATE_MATRIX[tpl][tier.key]
  const meta = TEMPLATE_META[tpl]

  const rows = ROW_ORDER.map(k => {
    const targetPct = targets[k]
    const currentVal = current.buckets[k]
    const currentPct = (currentVal / current.total) * 100
    const targetVal = (targetPct / 100) * current.total
    const delta = targetVal - currentVal
    return { key: k, targetPct, currentPct, currentVal, targetVal, delta }
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
              <div className="flex items-center gap-1.5">
                <span className="inline-block w-2 h-2 rounded-sm" style={{ background: color }} />
                <span className="text-text-bright font-medium">{TYPE_LABEL[r.key]}</span>
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
