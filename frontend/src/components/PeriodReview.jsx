import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

// A股配色: 涨红(bear) 跌绿(bull)
const pctCls = (v) => v == null ? 'text-text-muted' : v > 0 ? 'text-bear' : v < 0 ? 'text-bull' : 'text-text-dim'
const pct = (v, d = 1) => v == null ? '--' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`
const rate = (v) => v == null ? '--' : `${Math.round(v * 100)}%`
const wan = (v) => v == null ? '--' : Math.abs(v) >= 10000 ? `${(v / 10000).toFixed(2)}万` : v.toFixed(0)
const signed = (v) => v == null ? '--' : `${v > 0 ? '+' : v < 0 ? '-' : ''}${wan(Math.abs(v))}`
const MKT = { A: 'A股', HK: '港股', US: '美股', ETF: '场内ETF' }
const FEW = 5

function Stat({ label, value, sub, cls = 'text-text-bright' }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-text-muted">{label}</div>
      <div className={`text-[15px] font-mono font-semibold ${cls}`}>{value}</div>
      {sub && <div className="text-[10px] text-text-muted leading-snug">{sub}</div>}
    </div>
  )
}

// 期间复盘: 按年/月汇总交易次数与换手、费用、持有期分布、买点之后的结果
export default function PeriodReview() {
  const [period, setPeriod] = useState('')
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(false)

  const load = useCallback((p) => {
    setLoading(true); setErr(false)
    fetchJSON(`/api/portfolio/period-review${p ? `?period=${encodeURIComponent(p)}` : ''}`)
      .then(r => { setD(r); if (!p && r?.period) setPeriod(r.period) })
      .catch(() => setErr(true)).finally(() => setLoading(false))
  }, [])
  useEffect(() => { load('') }, [load])

  const pick = (p) => { setPeriod(p); load(p) }
  const tr = d?.trading, fe = d?.fees, ho = d?.holding, en = d?.entries

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">期间复盘</h3>
          <span className="text-[10.5px] text-text-muted">交易次数与换手 · 费用 · 拿了多久 · 买点之后</span>
        </div>
        {d && !d.empty && (
          <select value={period} onChange={(e) => pick(e.target.value)} aria-label="选择期间"
            className="bg-surface-3 border border-border-subtle rounded px-1.5 py-0.5 text-[11.5px] text-text">
            <optgroup label="按年">{d.years.map(y => <option key={y} value={y}>{y} 年</option>)}</optgroup>
            <optgroup label="按月">{d.months.map(m => <option key={m} value={m}>{m}</option>)}</optgroup>
          </select>
        )}
      </div>

      {loading && <SkeletonCard bare rows={4} label="汇总成交中…" />}
      {!loading && err && <div className="text-text-dim text-[12px]">期间复盘暂不可用</div>}
      {!loading && d?.empty && <div className="text-text-dim text-[12px]">{d.note}</div>}

      {!loading && d && !d.empty && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-3 mb-4">
            <Stat label="买入 / 卖出决策" value={`${tr.n_buy} / ${tr.n_sell}`}
              sub={`成交 ${wan(tr.buy_amt)} / ${wan(tr.sell_amt)}`} />
            <Stat label="换手率" value={tr.turnover_pct == null ? '--' : `${tr.turnover_pct.toFixed(0)}%`}
              sub={tr.turnover_note} />
            <Stat label="交易费用" value={wan(fe.total)}
              sub={fe.pct_of_traded == null ? null : `占成交额 ${fe.pct_of_traded.toFixed(3)}%`} />
            <Stat label="已实现毛利(未扣费)" value={signed(fe.realized_gross)} cls={pctCls(fe.realized_gross)}
              sub={fe.pct_of_realized == null ? '毛利不为正, 不算费用占比' : `费用吃掉其中 ${fe.pct_of_realized.toFixed(1)}%`} />
          </div>

          <div className="grid md:grid-cols-2 gap-x-6 gap-y-4">
            <div>
              <div className="text-[10.5px] text-text-muted tracking-wider mb-1">
                卖出按持有天数分档 · {ho.n_sells} 次卖出{ho.median_days != null && `, 中位 ${ho.median_days} 天`}
              </div>
              <table className="w-full text-[11.5px]">
                <thead>
                  <tr className="text-[10px] text-text-muted text-right">
                    <th className="text-left font-normal py-0.5">持有</th>
                    <th className="font-normal">次数</th>
                    <th className="font-normal">成本</th>
                    <th className="font-normal">已实现毛利</th>
                    <th className="font-normal">收益率</th>
                    <th className="font-normal">赚钱占比</th>
                  </tr>
                </thead>
                <tbody>
                  {ho.groups.map(g => (
                    <tr key={g.label} className={`text-right border-t border-border-subtle/60 ${g.n < FEW ? 'opacity-55' : ''}`}
                      title={g.n && g.n < FEW ? `只有 ${g.n} 次, 偶然性很大` : ''}>
                      <td className="text-left py-1 text-text">{g.label}</td>
                      <td className="font-mono text-text-dim">{g.n || '--'}</td>
                      <td className="font-mono text-text-dim">{g.n ? wan(g.cost) : '--'}</td>
                      <td className={`font-mono font-semibold ${pctCls(g.pnl)}`}>{g.n ? signed(g.pnl) : '--'}</td>
                      <td className={`font-mono ${pctCls(g.ret_pct)}`}>{pct(g.ret_pct, 2)}</td>
                      <td className="font-mono text-text-dim">{rate(g.win)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="space-y-4">
              <div>
                <div className="text-[10.5px] text-text-muted tracking-wider mb-1">分市场</div>
                <table className="w-full text-[11.5px]">
                  <thead>
                    <tr className="text-[10px] text-text-muted text-right">
                      <th className="text-left font-normal py-0.5">市场</th>
                      <th className="font-normal">买 / 卖次数</th>
                      <th className="font-normal">买入额</th>
                      <th className="font-normal">卖出额</th>
                      <th className="font-normal">费用</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(tr.by_market).map(([k, m]) => (
                      <tr key={k} className="text-right border-t border-border-subtle/60">
                        <td className="text-left py-1 text-text">{MKT[k] || k}</td>
                        <td className="font-mono text-text-dim">{m.n_buy} / {m.n_sell}</td>
                        <td className="font-mono text-text-dim">{wan(m.buy_amt)}</td>
                        <td className="font-mono text-text-dim">{wan(m.sell_amt)}</td>
                        <td className="font-mono text-text-dim">{wan(m.fee)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div>
                <div className="text-[10.5px] text-text-muted tracking-wider mb-1">本期买入之后(A 股个股 + 场内 ETF)</div>
                {en.pending ? <div className="text-[11.5px] text-text-muted">买点回看首次计算中(约 1 分钟), 稍后切换一下期间即可看到</div>
                  : en.n === 0 ? <div className="text-[11.5px] text-text-muted">本期没有可回看的买入</div> : (
                  <div className={`text-[12px] text-text-dim leading-relaxed ${en.n_done < FEW ? 'opacity-70' : ''}`}>
                    {en.n} 次买入, 5 日平均 <span className={`font-mono ${pctCls(en.mean5_pct)}`}>{pct(en.mean5_pct)}</span>;
                    已满 20 个交易日 {en.n_done} 次{en.n_done > 0 && <>
                      , 20 日平均 <span className={`font-mono font-semibold ${pctCls(en.mean20_pct)}`}>{pct(en.mean20_pct)}</span>
                      , 中位 <span className={`font-mono ${pctCls(en.median20_pct)}`}>{pct(en.median20_pct)}</span>
                      , 赚钱的占 {rate(en.win20)}
                      {en.mean_ex20_pct != null && <>, 相对中证1000 平均 <span className={`font-mono ${pctCls(en.mean_ex20_pct)}`}>{pct(en.mean_ex20_pct)}</span></>}
                    </>}。分组明细见下方买点回看。
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="text-[10px] text-text-muted leading-relaxed mt-3">{d.note}</div>
        </>
      )}
    </div>
  )
}
