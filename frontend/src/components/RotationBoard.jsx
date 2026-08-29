// 轮动板: 榜单左栏只有 420px, 九条线挤成五排小胶囊, 每个胶囊里塞名字+亿数+涨幅+涨停+箭头 ——
// 密是密全了, 但看不出哪条粗哪条细, 更看不出走势。而右栏在没选股票时整片是空的。
// 所以把"钱堆在哪 / 谁在接力"搬到右栏铺开: 一条线一行, 成交额给条形(粗细一眼可比),
// 份额给迷你走势(五天的形状), 底下挂领头三只(点了直接看它的 K 线)。
// 左栏那排胶囊留着当筛选器, 只保留名字和箭头。

const pctColor = (v) => (v == null || v === 0 ? 'text-text-dim' : v > 0 ? 'text-bear-bright' : 'text-bull-bright')

// 份额五日形状。只画相对高低, 不标刻度 —— 它回答的是"在爬还是在退", 具体数字看右边的数。
function Spark({ series, w = 62, h = 18 }) {
  const vals = (series || []).map(s => s.share_pct).filter(v => v != null)
  if (vals.length < 2) return <span className="inline-block" style={{ width: w, height: h }} />
  const min = Math.min(...vals), max = Math.max(...vals)
  const span = max - min || 1
  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * (w - 2) + 1
    const y = h - 1 - ((v - min) / span) * (h - 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const up = vals[vals.length - 1] >= vals[0]
  const color = up ? '#cf5c5c' : '#5fa86c'
  const [lx, ly] = pts.split(' ').pop().split(',')
  return (
    <svg width={w} height={h} className="shrink-0 block">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.2" strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r="1.8" fill={color} />
    </svg>
  )
}

export default function RotationBoard({ scope, kind, groups, trend, hotTag,
                                        onPickTag, onKindChange, onPickStock }) {
  const rows = (groups || []).slice(0, 12)
  const maxAmt = Math.max(1, ...rows.map(g => g.amt_yi || 0))
  const trendOf = (name) => (trend?.rows || []).find(t => t.name === name)
  const isAmt = scope === 'by_amount'

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex items-baseline gap-2 px-4 py-2.5 border-b border-border-subtle shrink-0">
        <span className="text-[13px] font-semibold text-text-bright">
          {isAmt ? '今天的钱堆在哪条线上' : '今天涨的是哪条线'}
        </span>
        <span className="text-[10.5px] text-text-muted">
          {isAmt ? '按成交额榜前 100 只聚' : '按涨幅榜前 100 只聚'}
          {trend?.dates?.length ? ` · 走势近 ${trend.dates.length} 日` : ''}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {['概念', '行业'].map(k => (
            <button key={k} onClick={() => onKindChange?.(k)}
              className={`text-[11px] px-2 py-0.5 rounded border ${kind === k ? 'bg-accent/20 text-accent border-accent/40' : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
              {k}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto">
        {rows.map(g => {
          const tr = trendOf(g.name)
          const on = hotTag === g.name
          return (
            <div key={g.name}
              className={`px-4 py-2 border-b border-border-subtle/60 ${on ? 'bg-accent/10' : 'hover:bg-surface-3/40'}`}>
              {/* 限宽: 铺满 950px 的话名字在最左、数字在最右, 眼睛得横扫一整行才对得上 */}
              <div className="flex items-center gap-3 max-w-[720px]">
                {/* 名字: 点一下把左边的榜筛到这条线 */}
                <button onClick={() => onPickTag?.(on ? '' : g.name)}
                  title={(g.aliases || []).length ? `同义并入: ${g.aliases.join('、')}` : '点一下只看这条线的票'}
                  className={`text-[12.5px] w-[104px] shrink-0 text-left truncate ${on ? 'text-accent' : 'text-text-bright hover:text-accent'}`}>
                  {g.name}
                </button>
                <span className="text-[10px] text-text-muted font-mono w-[54px] shrink-0">
                  {g.n}只{g.limit_n > 0 ? ` 停${g.limit_n}` : ''}
                </span>
                {/* 成交额条: 粗细就是这条线的钱, 一眼比得出来 */}
                <div className="w-[210px] shrink-0 flex items-center gap-2">
                  <div className="h-[7px] flex-1 rounded-sm bg-surface-3 overflow-hidden">
                    <div className="h-full rounded-sm bg-accent/55"
                      style={{ width: `${Math.max(2, (g.amt_yi / maxAmt) * 100)}%` }} />
                  </div>
                  <span className="text-[11px] font-mono text-text-dim w-[52px] text-right shrink-0">
                    {g.amt_yi >= 100 ? Math.round(g.amt_yi) : g.amt_yi}亿
                  </span>
                </div>
                <span className={`text-[11.5px] font-mono w-[52px] text-right shrink-0 ${pctColor(g.avg_pct)}`}>
                  {g.avg_pct >= 0 ? '+' : ''}{g.avg_pct}%
                </span>
                {/* 份额走势 + 较上日变化: 全市场放量时绝对值一起涨, 份额才看得出钱在挪 */}
                <Spark series={tr?.series} />
                {/* 变化 + 判词 + 这条曲线是按几只票算的 —— 口径贴着数字放, 别飘到行尾 */}
                <span className="w-[112px] shrink-0 text-right leading-tight">
                  {tr && tr.d1_share_pp != null ? (
                    <>
                      <span className={`text-[11px] font-mono ${tr.d1_share_pp > 0 ? 'text-bear-bright' : tr.d1_share_pp < 0 ? 'text-bull-bright' : 'text-text-dim'}`}>
                        {tr.d1_share_pp > 0 ? '↑' : tr.d1_share_pp < 0 ? '↓' : '·'}{Math.abs(tr.d1_share_pp)}
                      </span>
                      <span className="text-[9.5px] text-text-muted ml-1">{tr.label}</span>
                      <span className="block text-[9px] text-text-muted"
                        title="曲线只统计全程都有本地日线的成分股, 缺一天的整只剔掉">
                        按 {tr.basket_n}/{tr.total_n} 只算
                      </span>
                    </>
                  ) : <span className="text-[9.5px] text-text-muted">走势数据不足</span>}
                </span>
              </div>
              <div className="flex items-center gap-2 mt-1 pl-[4px] max-w-[720px]">
                <span className="text-[9.5px] text-text-muted shrink-0">领头</span>
                {(g.tops || []).map(t => (
                  <button key={t.code} onClick={() => onPickStock?.({ code: t.code, name: t.name, pct: t.pct })}
                    className="text-[10.5px] text-text-dim hover:text-accent whitespace-nowrap">
                    {t.name} <span className={pctColor(t.pct)}>{t.pct >= 0 ? '+' : ''}{t.pct}%</span>
                    <span className="text-text-muted"> {t.amt_yi}亿</span>
                  </button>
                ))}
              </div>
            </div>
          )
        })}
      </div>

      <div className="shrink-0 px-4 py-1.5 border-t border-border-subtle text-[9.5px] text-text-muted leading-relaxed">
        <div>
          条形=这条线在榜上的成交额 · 曲线=它占榜单成交额的比重近几日的形状 · ↑↓=比重较上一日的变化(百分点)
          {trend?.today_partial ? ' · 今天还没收盘, 最后一格是半天的量' : ''}
        </div>
        <div className="text-text-dim">
          各条线互相重叠(一只票挂多个概念), 百分比别横向加总。纯客观数据, 不构成买卖建议。
        </div>
        {!trend?.rows?.length && trend?.note && <div className="mt-0.5">{trend.note}</div>}
      </div>
    </div>
  )
}
