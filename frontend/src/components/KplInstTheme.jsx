import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

// 增仓=红(资金流入), 减仓=绿 —— 沿用A股红涨绿跌
const yiColor = (v) => v == null ? 'text-text-dim' : v > 0 ? 'text-bear-bright' : v < 0 ? 'text-bull-bright' : 'text-text-dim'
const fmtYi = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${v}亿`

// 开盘啦登录态失效时的统一提示(不静默空, 引导去设置更新 Token)
function NeedLogin({ what }) {
  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <h3 className="text-[14px] font-semibold text-text-bright m-0 mb-1.5">{what}</h3>
      <div className="text-[11.5px] text-text-muted leading-relaxed">
        开盘啦登录态已失效,去<span className="text-accent">设置</span>里更新 Token 后可见。
      </div>
    </div>
  )
}

export default function KplInstTheme() {
  const [pos, setPos] = useState(null)
  const [theme, setTheme] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      fetchJSON('/api/market/kpl-inst-position').then(setPos).catch(() => {}),
      fetchJSON('/api/market/kpl-hot-theme').then(setTheme).catch(() => {}),
    ]).finally(() => setLoading(false))
  }, [])

  if (loading) return <SkeletonCard rows={6} label="机构风向加载中" />
  // 两个都没配/失效 → 一个登录提示; 都没数据 → 不显示
  if (pos?.need_login && theme?.need_login) return <NeedLogin what="机构增仓 · 本月热门题材" />
  const hasPos = pos && pos['有数据']
  const hasTheme = theme && theme['有数据']
  if (!hasPos && !hasTheme && !pos?.need_login) return null

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5 space-y-4">
      {/* 本月热门题材 */}
      {hasTheme && (
        <div>
          <div className="flex items-baseline gap-2 mb-2 flex-wrap">
            <h3 className="text-[14px] font-semibold text-text-bright m-0">本月热门题材</h3>
            <span className="text-[10.5px] text-text-muted">开盘啦 · 按热度降序</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {theme['题材榜'].map((t) => (
              <span key={t['代码']} className="text-[11.5px] bg-surface-3 rounded px-2 py-1">
                <span className="text-text-muted font-mono mr-1">{t['排名']}</span>
                <span className="text-text-bright">{t['题材']}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 机构增仓 / 减仓 */}
      {hasPos && (
        <div>
          <div className="flex items-baseline gap-2 mb-2 flex-wrap">
            <h3 className="text-[14px] font-semibold text-text-bright m-0">机构增仓</h3>
            <span className="text-[10.5px] text-text-muted">
              开盘啦 · {pos.date} 季报 · 行业板块 · 全市场机构增仓合计
              <span className={yiColor(pos['全市场机构增仓合计亿'])}> {fmtYi(pos['全市场机构增仓合计亿'])}</span>
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <PosList title="增仓榜" rows={pos['增仓榜']} />
            <PosList title="减仓榜" rows={pos['减仓榜']} />
          </div>
          <div className="text-[10px] text-text-muted mt-2 pt-1.5 border-t border-border-subtle">
            季报持仓口径(中线机构搬仓方向), 区别于龙虎榜机构席位(短线)。已披露客观数据, 非买卖建议。
          </div>
        </div>
      )}
    </div>
  )
}

function PosList({ title, rows }) {
  if (!rows || !rows.length) return null
  return (
    <div>
      <div className="text-[11px] text-text-muted mb-1.5">{title}</div>
      <div className="space-y-1">
        {rows.slice(0, 8).map((r) => (
          <div key={r['代码']} className="flex items-baseline justify-between gap-2 text-[12px]">
            <span className="text-text-bright truncate">{r['名称']}</span>
            <span className="flex items-baseline gap-2 shrink-0 font-mono">
              {r['持仓占流通%'] != null && (
                <span className="text-text-dim text-[10.5px]">占流通{r['持仓占流通%']}%</span>
              )}
              <span className={yiColor(r['增仓金额亿'])}>{fmtYi(r['增仓金额亿'])}</span>
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
