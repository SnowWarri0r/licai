import { useState, useEffect, useMemo } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { fmtMoney } from '../helpers'

// Templates target percentages for the 5 tracked categories.
// M = 现金 (T+0 货币基金 / 活期) — 应急 + 缓冲；W = 理财 (T+30 锁定)
const TEMPLATES = {
  defensive: {
    label: '保守型',
    desc: '稳收益、低回撤。理财/现金占大头，权益少配',
    targets: { M: 15, W: 50, A: 12, F: 23, C: 0 },
    notes: [
      '现金 15%：3-6 个月生活费的应急储备',
      '理财 50%：覆盖 1-2 年内可能动用的钱',
      '权益（A股 + 基金）合计 ≤ 35%，单板块不超过 25%',
      '不持有加密；预留现金等市场恐慌时再加权益',
    ],
  },
  balanced: {
    label: '平衡型',
    desc: '收益/风险均衡。权益与稳健资产五五开',
    targets: { M: 8, W: 30, A: 28, F: 29, C: 5 },
    notes: [
      '现金 8%：足够 1-2 月开支 + 抓机会的子弹',
      '基金建议黄金 ~10% + 海外 ~10% + A股宽基 ~10%',
      'A股单板块 ≤ 30%；同源族（A股+基金）合计 ≤ 35%',
      '加密 5% 卫星仓，BTC/ETH 大盘币为主',
    ],
  },
  aggressive: {
    label: '激进型',
    desc: '搏收益。权益 + 加密占大头，现金/理财仅留流动性',
    targets: { M: 5, W: 12, A: 38, F: 35, C: 10 },
    notes: [
      '需要承受 -30% 以上回撤的心理准备',
      '现金 + 理财 ≤ 17%，主要用作机会子弹',
      'A股可单押 1-2 行业但单板块 ≤ 35%',
      '基金重点配海外（QDII/纳指）+ A股宽基对冲行业 beta',
    ],
  },
}

const TYPE_LABEL = {
  A: 'A股', F: '基金', W: '理财', M: '现金', C: '加密',
}
const TYPE_COLOR = {
  A: '#c8a876', F: '#85a0b4', W: '#5fa86c', M: '#7a9b8e', C: '#d4a05c',
}

export default function AllocationAdvisor() {
  const [holdings, setHoldings] = useState([])
  const [assets, setAssets] = useState([])
  const [tpl, setTpl] = useState(() => localStorage.getItem('allocTemplate') || 'balanced')

  useEffect(() => {
    fetchJSON('/api/portfolio').then(setHoldings).catch(() => {})
    fetchJSON('/api/assets').then(d => setAssets(d.assets || [])).catch(() => {})
  }, [])

  const pickTpl = (k) => { setTpl(k); localStorage.setItem('allocTemplate', k) }

  // Compute current value per category. BOT (OKX 网格等) 本质是加密仓位，归入 C。
  // CASH = T+0 货币基金/活期流动性；W = 理财 (T+30 锁定)
  const current = useMemo(() => {
    const buckets = { A: 0, F: 0, W: 0, M: 0, C: 0 }
    for (const h of holdings) buckets.A += h.market_value || (h.current_price || 0) * h.shares
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

  const template = TEMPLATES[tpl]
  const rows = ['M', 'W', 'A', 'F', 'C'].map(k => {
    const targetPct = template.targets[k]
    const currentVal = current.buckets[k]
    const currentPct = (currentVal / current.total) * 100
    const targetVal = (targetPct / 100) * current.total
    const delta = targetVal - currentVal  // 正=该加；负=该减
    return { key: k, targetPct, currentPct, currentVal, targetVal, delta }
  })

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      <div className="px-5 py-3 border-b border-border flex items-center justify-between flex-wrap gap-2"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">配置建议</h3>
          <span className="text-[11px] text-text-dim">目标 vs 当前 · 总额 ¥{fmtMoney(current.total)}</span>
        </div>
        <div className="flex gap-1.5">
          {Object.entries(TEMPLATES).map(([k, t]) => (
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

      <div className="px-5 py-2 text-[11.5px] text-text-dim border-b border-border-subtle bg-surface-2/30">
        <span className="text-text">{template.label}</span>
        <span className="mx-1.5">·</span>
        {template.desc}
      </div>

      <div className="grid px-5 py-1.5 text-[10.5px] text-text-dim tracking-wider font-medium border-b border-border-subtle"
        style={{ gridTemplateColumns: '14% 14% 14% 28% 30%' }}>
        <div>类别</div>
        <div className="text-right">目标</div>
        <div className="text-right">当前</div>
        <div className="text-center">对比</div>
        <div className="text-right">建议调整</div>
      </div>

      <div className="divide-y divide-border-subtle">
        {rows.map(r => {
          const color = TYPE_COLOR[r.key]
          const cPct = Math.min(r.currentPct, 100)
          const tPct = Math.min(r.targetPct, 100)
          const overweight = r.currentPct > r.targetPct
          return (
            <div key={r.key} className="grid px-5 py-2.5 items-center text-[12px]"
              style={{ gridTemplateColumns: '14% 14% 14% 28% 30%' }}>
              <div className="flex items-center gap-1.5">
                <span className="inline-block w-2 h-2 rounded-sm" style={{ background: color }} />
                <span className="text-text-bright font-medium">{TYPE_LABEL[r.key]}</span>
              </div>
              <div className="text-right font-mono text-text">{r.targetPct}%</div>
              <div className="text-right font-mono text-text">{r.currentPct.toFixed(1)}%</div>
              <div className="px-3">
                <div className="relative h-3 rounded-sm bg-surface-3 overflow-hidden">
                  {/* current bar */}
                  <div className="absolute top-0 left-0 h-full"
                    style={{ width: cPct + '%', background: color, opacity: 0.35 }} />
                  {/* target marker */}
                  <div className="absolute top-0 h-full border-r-2" style={{
                    left: 'calc(' + tPct + '% - 1px)',
                    width: '2px',
                    borderColor: color,
                  }} />
                </div>
              </div>
              <div className="text-right">
                {Math.abs(r.delta) < 100 ? (
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

      <div className="px-5 py-2.5 bg-surface-2/40 border-t border-border-subtle">
        <div className="text-[10.5px] text-text-dim mb-1">📋 注意事项</div>
        <ul className="m-0 pl-4 space-y-0.5 text-[11px] text-text leading-relaxed list-disc">
          {template.notes.map((n, i) => <li key={i}>{n}</li>)}
        </ul>
        <div className="text-[10px] text-text-muted mt-2 italic">
          * 模板配比不构成投资建议，仅作起点参考。OKX 网格 / DCA 机器人按底层标的归入加密大类。
        </div>
      </div>
    </section>
  )
}
