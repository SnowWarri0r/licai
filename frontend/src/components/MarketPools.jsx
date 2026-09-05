import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'
import StockKlineModal from './StockKlineModal'

// 股池: 上一个交易日各格子里的票, 今天怎么样了。
//
// 这里刻意给**真实成分**(代码/名称/连板/几进几/今日涨幅), 不给"情绪偏强"这类词 ——
// 池子里有哪几只、走成什么样自己看得见, 结论才可能被驳回。
//
// 三个池的分法是关键: 「昨日首板」按自算的 进 轴判(这波第一次涨停), 不是只看连板数=1。
// 后者会把"连板数=1 但已经 9天5板"的高位老票混进来 —— 9-02 那天 39 只里混了 8 只, 而这批
// 老票和真新面孔的次日表现常常相反, 混着算就把两边都抹平了。

const pctCls = (v) => v == null ? 'text-text-dim' : v > 0 ? 'text-bear-bright' : v < 0 ? 'text-bull-bright' : 'text-text-dim'
const fmt = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`

const NOTE = {
  '昨日首板': '这波第一次涨停的新面孔',
  '昨日连板': '昨天已经是 2 板及以上',
  '昨日反复板': '昨天不连板, 但这一波已涨停过几次(高位老票)',
}

function Pool({ g, open, onToggle, onPick }) {
  const s = g.统计
  const rows = open ? g.成分 : g.成分.slice(0, 5)
  return (
    <div className="border border-border rounded-lg overflow-hidden bg-surface-3/30">
      <button onClick={onToggle}
        className="w-full text-left px-3 py-2 flex items-baseline gap-2 hover:bg-surface-3/60">
        <span className="text-[10px] text-text-muted">{open ? '▾' : '▸'}</span>
        <span className="text-[12.5px] font-semibold text-text-bright">{g.池}</span>
        <span className="text-[10.5px] text-text-muted">{g.只数}只</span>
        {s ? (
          <span className="ml-auto flex items-baseline gap-2 text-[11px] font-mono">
            <span className={pctCls(s['今日平均%'])}>均{fmt(s['今日平均%'])}</span>
            <span className={`${pctCls(s['今日中位%'])} text-[10px]`}>中位{fmt(s['今日中位%'])}</span>
            <span className="text-[10px] text-text-dim">红{s.红盘}/{s.取到行情}</span>
          </span>
        ) : <span className="ml-auto text-[10px] text-text-dim">没取到行情</span>}
      </button>
      <div className="px-3 pb-1 -mt-1 text-[9.5px] text-text-muted">{NOTE[g.池] || ''}</div>
      <div className="px-1 pb-2">
        {rows.map(x => (
          <button key={x.代码} onClick={() => onPick?.(x)}
            className="w-full flex items-center gap-2 px-2 py-[3px] rounded hover:bg-surface-3/70 text-left">
            <span className="text-[11.5px] text-text w-[68px] shrink-0 truncate">{x.名称}</span>
            <span className="text-[9.5px] text-text-muted font-mono w-[52px] shrink-0">
              {x.几进几 || `${x.连板}板`}
            </span>
            <span className="text-[9.5px] text-text-dim truncate flex-1 min-w-0">{x.题 || ''}</span>
            <span className={`text-[11.5px] font-mono w-[58px] text-right shrink-0 ${pctCls(x.今日涨幅)}`}>
              {fmt(x.今日涨幅)}
            </span>
          </button>
        ))}
        {!open && g.成分.length > 5 && (
          <div className="px-2 pt-1 text-[10px] text-text-muted">还有 {g.成分.length - 5} 只, 点标题展开</div>
        )}
      </div>
    </div>
  )
}

export default function MarketPools({ onPick }) {
  const [d, setD] = useState(null)
  const [mig, setMig] = useState(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState('')
  // 池里的票都不是持仓, 所以 cost_price 给 0(弹窗按"没有成本线"处理)。
  // onPick 传了就交给外面(将来嵌到别处可复用), 没传就自己开 K 线弹窗 —— 组件自带出口,
  // 免得又出现"留了口子但没人接"的情况。
  const [kline, setKline] = useState(null)
  const pick = onPick || (x => setKline({
    stock_code: x.代码, stock_name: x.名称, cost_price: 0, shares: 0,
    // 表头的现价/涨跌幅读的是这两个字段。不传的话涨跌幅位就是一个 -- ,
    // 而现价只是碰巧被 TDX 盘口兜住了(TDX 断了照样没有)。
    current_price: x.现价, price_change_pct: x.今日涨幅,
  }))

  const [bt, setBt] = useState(null)

  useEffect(() => {
    fetchJSON('/api/market/pools').then(setD).catch(e => setErr(String(e)))
    fetchJSON('/api/market/ladder-migration').then(setMig).catch(() => {})
    fetchJSON('/api/market/pool-backtest').then(setBt).catch(() => {})
  }, [])

  if (err) return null
  if (!d) return <SkeletonCard rows={7} label="在算昨天那批今天怎么样" />
  if (!d.可用) return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 text-[11.5px] text-text-muted">
      股池: {d.note || '暂无数据'}
    </div>
  )

  const rows = mig?.可用 ? (mig.梯队迁移 || []) : []

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline gap-2 mb-3 flex-wrap">
        <h3 className="text-[14px] font-semibold text-text-bright m-0">大盘 · 股池</h3>
        <span className="text-[10.5px] text-text-muted">
          {d.锚点日} 涨停的票, 今天走成什么样 · 取数 {d.取数时刻}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {d.池.map(g => (
          <Pool key={g.池} g={g} open={open === g.池}
            onToggle={() => setOpen(open === g.池 ? '' : g.池)} onPick={pick} />
        ))}
      </div>

      {rows.length > 0 && (
        <div className="mt-4">
          <div className="text-[11.5px] text-text-bright mb-1.5">
            连板梯队迁移
            <span className="text-[10px] text-text-muted ml-2">
              {mig.上一归类日} → {mig.date} · 只看在不在涨停名单, 不含价格
            </span>
          </div>
          <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-x-3 text-[11px] items-center">
            <div className="text-[10px] text-text-muted pb-1">上日</div>
            <div className="text-[10px] text-text-muted pb-1">接力率</div>
            <div className="text-[10px] text-text-muted pb-1 text-right">仍涨停</div>
            <div className="text-[10px] text-text-muted pb-1 text-right">掉出</div>
            <div className="text-[10px] text-text-muted pb-1 text-right" title="掉出后两个交易日内回到涨停名单">反包</div>
            {rows.map(r => (
              <div key={r.上日连板数} className="contents">
                <div className="text-text py-[3px] font-mono">{r.上日连板数}板</div>
                <div className="py-[3px] flex items-center gap-2">
                  <div className="h-[8px] rounded-sm bg-accent/55 shrink-0"
                    style={{ width: `${Math.max(2, r['接力率%'])}%`, maxWidth: '70%' }} />
                  <span className="text-[10.5px] font-mono text-text-dim">{r['接力率%']}%</span>
                </div>
                <div className="text-right font-mono text-text-dim py-[3px]">{r.今日仍涨停}/{r.上日只数}</div>
                <div className="text-right font-mono text-text-muted py-[3px]">{r.掉出}</div>
                <div className="text-right font-mono text-text-muted py-[3px]">{r.掉出后两日内反包}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 历史均值: 单日那 28/9/7 只说明不了任何事, 这栏是判断今天算大还是算小的标尺。
          「超额」列减掉了当天全市场涨停股的平均次日收益 —— 大盘涨的日子每个池都好看。 */}
      {bt?.可用 && (
        <div className="mt-4 pt-3 border-t border-border-subtle">
          <div className="text-[11.5px] text-text-bright mb-1.5">
            历史分池 · 次日兑现
            <span className="text-[10px] text-text-muted ml-2">
              回放 {bt.参与统计的天数} 个交易日 · 覆盖率 {bt['覆盖率%']}%
              {bt.幅度对照通过 ? ' · 幅度对照已过' : ' · 幅度对照未过, 别当结论'}
            </span>
          </div>
          <div className="grid grid-cols-[auto_auto_auto_auto_auto] gap-x-4 text-[11px] items-center">
            <div className="text-[10px] text-text-muted pb-1">池</div>
            <div className="text-[10px] text-text-muted pb-1 text-right">样本</div>
            <div className="text-[10px] text-text-muted pb-1 text-right" title="次日开盘相对涨停日收盘">次日开盘</div>
            <div className="text-[10px] text-text-muted pb-1 text-right" title="次日收盘相对涨停日收盘">次日收盘</div>
            <div className="text-[10px] text-text-muted pb-1 text-right"
              title="减去当天全市场涨停股的平均次日收益。没有这一列, 池子间差异会被大盘涨跌淹没">超额pp</div>
            {bt.分池.map(x => (
              <div key={x.池} className="contents">
                <div className="text-text py-[3px]">{x.池}</div>
                <div className="text-right font-mono text-text-muted py-[3px]">{x.样本}</div>
                {x.结论 ? (
                  <div className="col-span-3 text-right text-[10px] text-text-dim py-[3px]">{x.结论}</div>
                ) : (
                  <>
                    <div className={`text-right font-mono py-[3px] ${pctCls(x['次日开盘溢价%'])}`}>{fmt(x['次日开盘溢价%'])}</div>
                    <div className={`text-right font-mono py-[3px] ${pctCls(x['次日收盘涨跌%'])}`}>{fmt(x['次日收盘涨跌%'])}</div>
                    <div className={`text-right font-mono py-[3px] ${pctCls(x['超出同日涨停均值pp'])}`}>
                      {x['超出同日涨停均值pp'] > 0 ? '+' : ''}{x['超出同日涨停均值pp']}
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
          <div className="mt-1.5 text-[9.5px] text-text-muted leading-relaxed">
            「超额pp」才是能比的那一列: 减掉了当天全市场涨停股的平均次日收益 —— 首板 +1.46% 在大盘也涨的日子里等于零信息。
            四个池的次日开盘一律高于收盘, 说明高开低走是常态。
            ⚠ 高板池带生存者偏差: 3板的票是已经通过两次接力筛选剩下的, 只能说"已经连上去的那批次日更强", 事前挑不出来。
          </div>
        </div>
      )}

      <div className="mt-3 text-[9.5px] text-text-muted leading-relaxed">
        「昨日首板」按 N天M板 自算判定(这波第一次涨停), 不是只看连板数=1 ——
        后者会把"连板数=1 但已 9天5板"的高位老票混进来, 那批就是「昨日反复板」, 两者表现常相反。
        自算口径命中率 97.8%, 会把少数高位老票读成新面孔。今日涨幅为实时报价, 盘中即盘中实况。
        纯客观数据, 不构成买卖建议。
      </div>

      {kline && <StockKlineModal holding={kline} onClose={() => setKline(null)} />}
    </div>
  )
}
