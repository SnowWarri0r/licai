import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

// A股配色: 涨红(bear) 跌绿(bull)
const pctCls = (v) => v == null ? 'text-text-muted' : v > 0 ? 'text-bear' : v < 0 ? 'text-bull' : 'text-text-dim'
const pct = (v, d = 1) => v == null ? '--' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`
const rate = (v) => v == null ? '--' : `${Math.round(v * 100)}%`
const TONE = { pos: 'text-bear', neg: 'text-bull', muted: 'text-text-dim' }
const FEW = 5   // 已满 20 日少于这个数的组, 数字只是个例

function GroupTable({ title, groups, compare }) {
  if (!groups?.length) return null
  return (
    <div className="mb-3">
      <div className="text-[10.5px] text-text-muted tracking-wider mb-1">{title}</div>
      <table className="w-full text-[11.5px]">
        <thead>
          <tr className="text-[10px] text-text-muted text-right">
            <th className="text-left font-normal py-0.5">分组</th>
            <th className="font-normal" title="斜杠后 = 其中已满 20 个交易日的次数(20 日各列只按这些算); 相同时只显示一个">次数<span className="text-text-muted">/满20日</span></th>
            <th className="font-normal">5日平均</th>
            <th className="font-normal">20日平均</th>
            <th className="font-normal">中位</th>
            <th className="font-normal">{compare ? '跑赢占比' : '赚钱占比'}</th>
            {compare && <th className="font-normal pl-3 border-l border-border-subtle">{compare.head}</th>}
          </tr>
        </thead>
        <tbody>
          {groups.map(g => {
            const few = g.n_done < FEW
            return (
              <tr key={g.label} className={`text-right border-t border-border-subtle/60 ${few ? 'opacity-55' : ''}`}
                title={(g.desc ? g.desc + '。' : '') + (few ? `已满 20 日的只有 ${g.n_done} 次, 偶然性很大` : '')}>
                <td className="text-left py-1 text-text">{g.label}</td>
                <td className="font-mono text-text-dim">{g.n}{g.n_done !== g.n && <span className="text-text-muted text-[10px]">/{g.n_done}</span>}</td>
                <td className={`font-mono ${pctCls(g.mean5_pct)}`}>{pct(g.mean5_pct)}</td>
                <td className={`font-mono font-semibold ${pctCls(g.mean20_pct)}`}>{pct(g.mean20_pct)}</td>
                <td className={`font-mono ${pctCls(g.median20_pct)}`}>{pct(g.median20_pct)}</td>
                <td className="font-mono text-text-dim">{rate(g.win20)}</td>
                {compare && <td className="pl-3 border-l border-border-subtle text-text-dim">{compare.cell(g)}</td>}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Entries({ rows }) {
  return (
    <div className="max-h-[420px] overflow-y-auto">
      <table className="w-full text-[11px]">
        <thead className="sticky top-0 bg-surface-2">
          <tr className="text-[10px] text-text-muted text-right">
            <th className="text-left font-normal py-0.5">日期</th>
            <th className="text-left font-normal">标的</th>
            <th className="font-normal">买入价</th>
            <th className="font-normal">较前收</th>
            <th className="font-normal">120日位置</th>
            <th className="font-normal">前20日</th>
            <th className="text-left font-normal pl-3">买入前一日</th>
            <th className="font-normal">5日</th>
            <th className="font-normal">20日</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(e => (
            <tr key={`${e.date}-${e.code}`} className="text-right border-t border-border-subtle/60">
              <td className="text-left py-0.5 font-mono text-text-dim">{e.date.slice(2)}</td>
              <td className="text-left text-text">{e.name}{e.kind === 'etf' && <span className="text-[9px] text-text-muted ml-1">ETF</span>}</td>
              <td className="font-mono text-text-dim" title={e.approx ? '成交价与前复权K线对不上, 20日结果改用当天收盘计' : ''}>{e.price}{e.approx && '*'}</td>
              <td className={`font-mono ${pctCls(e.chg_at_buy_pct)}`}>{pct(e.chg_at_buy_pct)}</td>
              <td className="font-mono text-text-dim">{e.pos120 == null ? '--' : `${Math.round(e.pos120 * 100)}%`}</td>
              <td className={`font-mono ${pctCls(e.ret20_before_pct)}`}>{pct(e.ret20_before_pct)}</td>
              <td className="text-left pl-3 text-text-dim">
                {[...(e.patterns || []), e.risk_decile != null ? `风险${e.risk_decile}/10` : null].filter(Boolean).join(' · ') || <span className="text-text-muted">--</span>}
              </td>
              <td className={`font-mono ${pctCls(e.r5_pct)}`}>{pct(e.r5_pct)}</td>
              <td className={`font-mono ${pctCls(e.r20_pct)}`}>{pct(e.r20_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// 买点回看: 每笔主动买入按「买入前一天的状态」分组, 看之后 5/20 个交易日的实际结果
export default function EntryReview() {
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(false)
  const [showAll, setShowAll] = useState(false)

  const load = useCallback((force = false) => {
    setLoading(true); setErr(false)
    fetchJSON(`/api/portfolio/entry-review${force ? '?force=1' : ''}`)
      .then(setD).catch(() => setErr(true)).finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const t = d?.total
  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">买点回看</h3>
          <span className="text-[10.5px] text-text-muted">每笔买入按买入前一天的状态分组 · 看之后实际走成什么样</span>
        </div>
        {!loading && <button onClick={() => load(true)} className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text hover:border-accent/40">重新计算</button>}
      </div>

      {loading && <SkeletonCard bare rows={5} label="逐笔回放 K 线中…(首次约 1 分钟)" />}
      {!loading && err && <div className="text-text-dim text-[12px]">买点回看暂不可用</div>}
      {!loading && d?.empty && <div className="text-text-dim text-[12px]">{d.note}</div>}

      {!loading && d && !d.empty && (
        <>
          <div className="text-[12px] text-text-dim mb-2">
            {d.since} 以来 <span className="text-text-bright font-semibold">{t.n}</span> 次买入(个股 {d.n_stock} · 场内ETF {d.n_etf}),
            已满 20 个交易日 {t.n_done} 次: 之后 20 日平均 <span className={`font-mono font-semibold ${pctCls(t.mean20_pct)}`}>{pct(t.mean20_pct)}</span>,
            中位 <span className={`font-mono ${pctCls(t.median20_pct)}`}>{pct(t.median20_pct)}</span>, 赚钱的占 {rate(t.win20)}
          </div>

          {(d.highlights || []).length > 0 && (
            <div className="mb-3 px-3 py-2.5 rounded-lg bg-accent/10 border border-accent/30 space-y-1">
              <div className="text-[10px] text-accent/80 tracking-wider">差别最大的几组</div>
              {d.highlights.map((h, i) => <div key={i} className="text-[12px] text-text-bright leading-relaxed">{h}</div>)}
            </div>
          )}

          <div className="grid md:grid-cols-2 gap-x-6">
            <div>
              {d.price_groups.map(pg => <GroupTable key={pg.title} title={`${pg.title}(个股+ETF, 绝对涨跌)`} groups={pg.groups} />)}
            </div>
            <div>
              {d.has_analyzer ? (
                <>
                  <GroupTable title="个股: 买入前一日的暴跌风险档(相对中证1000超额)" groups={d.risk_groups}
                    compare={{ head: '全市场同档历史', cell: g => g.base ? <span>60日暴跌 <span className="font-mono">{(g.base.crash_rate * 100).toFixed(1)}%</span> <span className="text-text-muted">(平时 {(g.base.base_crash * 100).toFixed(1)}%)</span> · 中位 <span className={`font-mono ${pctCls(g.base.median_excess_pct)}`}>{pct(g.base.median_excess_pct)}</span></span> : '--' }} />
                  <GroupTable title="个股: 买入前一日的量价形态(相对中证1000超额)" groups={d.pattern_groups}
                    compare={{ head: '全市场同形态 20日', cell: g => g.base ? <span><span className={`font-mono ${TONE[g.base.tone] || ''}`}>{pct(g.base.excess20_pct, 2)}</span> <span className="text-text-muted">跑赢 {rate(g.base.win20)}</span></span> : <span className="text-text-muted">--</span> }} />
                </>
              ) : (
                <div className="text-[11px] text-text-muted">未安装量价分析插件, 只按价格状态分组。</div>
              )}
            </div>
          </div>

          <div className="border-t border-border-subtle pt-2 mt-1">
            <button onClick={() => setShowAll(v => !v)} className="text-[11px] text-text-dim hover:text-text mb-1.5">
              {showAll ? '收起明细' : `展开 ${d.entries.length} 次买入明细`}
            </button>
            {showAll && <Entries rows={d.entries} />}
          </div>

          <div className="text-[10px] text-text-muted leading-relaxed mt-2">{d.note}</div>
        </>
      )}
    </div>
  )
}
