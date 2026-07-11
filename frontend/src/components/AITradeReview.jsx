import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import DailyReview from './DailyReview'
import SkeletonCard from './Skeleton'

const PERIODS = [
  { key: 'day', label: '今日', desc: '当日' },
  { key: 'week', label: '周', desc: '本周' },
  { key: 'month', label: '月', desc: '本月' },
  { key: 'all', label: '总览', desc: '全周期' },
]

export default function AITradeReview() {
  const [period, setPeriod] = useState('day')
  const [cache, setCache] = useState({})   // period -> data
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(false)

  const load = useCallback((p, force = false) => {
    if (!force && cache[p]) return
    setLoading(true); setErr(false)
    fetchJSON(`/api/portfolio/trade-review-ai?period=${p}${force ? '&force=1' : ''}`)
      .then(d => setCache(prev => ({ ...prev, [p]: d })))
      .catch(() => setErr(true))
      .finally(() => setLoading(false))
  }, [cache])

  useEffect(() => { load(period) }, [period, load])

  const d = cache[period]

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-baseline justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">复盘</h3>
          <span className="text-[10.5px] text-text-muted">组合归因 · 交易纪律 · 照镜子</span>
        </div>
        <div className="flex gap-1">
          {PERIODS.map(p => (
            <button key={p.key} onClick={() => setPeriod(p.key)}
              className={`text-[11px] px-2 py-0.5 rounded border ${period === p.key ? 'bg-accent/20 text-accent border-accent/40' : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {period === 'day' && (
        <div className="mb-4 pb-3 border-b border-border-subtle">
          <DailyReview bare />
        </div>
      )}

      {period === 'day' && <div className="text-[11px] text-text-muted mb-1.5 tracking-wider">当日交易纪律</div>}

      {loading && <SkeletonCard bare rows={5} label="AI 复盘中…（约 30–60 秒）" />}

      {!loading && err && (
        <div className="flex items-center justify-between">
          <span className="text-text-dim text-[12px]">AI 复盘暂不可用</span>
          <button onClick={() => load(period, true)} className="text-[11px] px-2.5 py-1 rounded border border-border text-text-dim hover:text-text">重试</button>
        </div>
      )}

      {!loading && !err && d && d.empty && (
        <div className="text-center py-6 text-text-dim text-[13px]">{d.summary || '该时间窗无交易'}</div>
      )}

      {!loading && !err && d && !d.empty && (
        <>
          {/* 期间交易笔数 (日/周/月) */}
          {period !== 'all' && d.n_trades != null && (
            <div className="text-[11px] text-text-muted mb-2">
              {d.period_label} · {d.n_trades} 笔（{d.n_buy} 买 {d.n_sell} 卖）
            </div>
          )}

          {/* 定性 */}
          {d.summary && (
            <div className="mb-3 px-3 py-2.5 rounded-lg bg-accent/10 border border-accent/30">
              <div className="text-[10px] text-accent/80 mb-0.5 tracking-wider">{period === 'all' ? '一句话定性' : '本期点评'}</div>
              <div className="text-[13px] text-text-bright leading-relaxed">{d.summary}</div>
            </div>
          )}

          {/* 做对的 */}
          {(d.good || []).length > 0 && (
            <div className="mb-3 px-3 py-2.5 rounded-lg bg-bull/8 border border-bull/25">
              <div className="text-[10px] text-bull-bright/90 mb-1.5 tracking-wider">做对的</div>
              <ul className="space-y-1">
                {d.good.map((g, i) => (
                  <li key={i} className="text-[11.5px] text-text-dim flex gap-1.5 leading-relaxed">
                    <span className="text-bull-bright shrink-0">✓</span><span>{g}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 要改的 */}
          {(d.discipline || []).length > 0 && (
            <div className="space-y-2.5 mb-3">
              <div className="text-[10px] text-bear-bright/80 tracking-wider mb-0.5">要改的</div>
              {d.discipline.map((x, i) => (
                <div key={i} className="border-l-2 border-bear-bright/50 pl-3">
                  <div className="text-[13px] font-semibold text-text-bright flex items-center gap-1.5">
                    <span className="text-bear-bright text-[12px]">⚠</span>{x.problem}
                  </div>
                  {x.evidence && <div className="text-[11.5px] text-text-dim mt-0.5 leading-relaxed">{x.evidence}</div>}
                  {x.why && <div className="text-[11.5px] text-text-muted mt-1 leading-relaxed">↳ {x.why}</div>}
                </div>
              ))}
            </div>
          )}

          {/* 爱在冰川视角 */}
          {(d.binchuan || []).length > 0 && (
            <div className="mb-3 px-3 py-2.5 rounded-lg bg-info/8 border border-info/25">
              <div className="text-[10px] text-info/90 mb-1.5 tracking-wider">交易哲学对照</div>
              <div className="space-y-2">
                {d.binchuan.map((b, i) => {
                  const hit = b.verdict === '契合'
                  return (
                    <div key={i} className="text-[11.5px] leading-relaxed">
                      <div className="flex items-center gap-1.5">
                        <span className={`text-[10px] px-1 rounded shrink-0 ${hit ? 'bg-bull/15 text-bull-bright' : 'bg-bear/15 text-bear-bright'}`}>
                          {hit ? '契合' : '违背'}
                        </span>
                        <span className="text-text-bright">{b.principle}</span>
                      </div>
                      {b.detail && <div className="text-text-dim mt-0.5 pl-1">{b.detail}</div>}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* 今日市场风格对照(仅当日复盘且当天有市场画像时) */}
          {d.market_fit && (
            <div className="mb-3 px-3 py-2.5 rounded-lg bg-accent/8 border border-accent/25">
              <div className="text-[10px] text-accent/90 mb-1 tracking-wider">操作 vs 今日市场风格</div>
              <div className="text-[12px] text-text-dim leading-relaxed">{d.market_fit}</div>
            </div>
          )}

          {/* 正文 */}
          {d.narrative && (
            <div className="text-[12.5px] text-text leading-relaxed whitespace-pre-line border-t border-border-subtle pt-3">
              {d.narrative}
            </div>
          )}

          <div className="flex items-center justify-between pt-2.5 mt-2 border-t border-border-subtle">
            <span className="text-[10px] text-text-muted">基于真实流水复盘历史交易 · 仅客观回顾，不构成买卖建议</span>
            <button onClick={() => load(period, true)} className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text hover:border-accent/40 shrink-0">重新复盘</button>
          </div>
        </>
      )}
    </div>
  )
}
