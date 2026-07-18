import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'

function pctCls(v) {
  if (v > 0) return 'text-bear'
  if (v < 0) return 'text-bull'
  return 'text-text-dim'
}

// 龙虎榜席位追踪弹窗: 某营业部/名号 近90天上榜明细 + 客观统计(纯历史描述, 非买卖建议)
export default function SeatHistoryModal({ seat, onClose }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    setData(null); setErr('')
    fetchJSON(`/api/market/seat-history?q=${encodeURIComponent(seat)}`)
      .then(d => { if (alive) d?.rows ? setData(d) : setErr(d?.note || d?.error || '无数据') })
      .catch(e => alive && setErr(e?.message || '加载失败'))
    const onEsc = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onEsc)
    return () => { alive = false; window.removeEventListener('keydown', onEsc) }
  }, [seat, onClose])

  const st = data?.stats

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}
      style={{ background: 'rgba(0,0,0,0.55)' }}>
      <div onClick={e => e.stopPropagation()}
        className="bg-surface-2 border border-border rounded-xl w-full max-w-2xl max-h-[80vh] flex flex-col overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border-subtle flex items-baseline gap-2">
          <span className="text-[13.5px] font-semibold text-text-bright truncate">{seat}</span>
          <span className="text-[10.5px] text-text-dim shrink-0">近{data?.窗口天数 || 90}天上榜记录</span>
          <button onClick={onClose} className="ml-auto text-text-dim hover:text-text text-[16px] leading-none px-1 cursor-pointer">×</button>
        </div>

        {st && (
          <div className="px-4 py-2 border-b border-border-subtle flex gap-4 flex-wrap text-[11px] text-text-muted">
            <span>上榜 <span className="text-text-bright font-mono">{st['上榜次数']}</span> 次</span>
            <span>净买入 <span className="text-text-bright font-mono">{st['净买入次数']}</span> 次</span>
            {st['净买入后1日红盘率%'] != null && (
              <span>净买后1日红盘率 <span className="text-text-bright font-mono">{st['净买入后1日红盘率%']}%</span>
                （平均 <span className={`font-mono ${pctCls(st['净买入后1日平均%'])}`}>{st['净买入后1日平均%'] >= 0 ? '+' : ''}{st['净买入后1日平均%']}%</span>）</span>
            )}
            {st['净买入后5日红盘率%'] != null && (
              <span>后5日红盘率 <span className="text-text-bright font-mono">{st['净买入后5日红盘率%']}%</span></span>
            )}
          </div>
        )}

        <div className="flex-1 overflow-y-auto min-h-0">
          {!data && !err && <div className="text-center py-10 text-[12px] text-text-dim">加载席位历史…</div>}
          {err && <div className="text-center py-10 text-[12px] text-text-dim px-6">{err}</div>}
          {data?.rows?.map((r, i) => (
            <div key={i} className="flex items-baseline gap-2 px-4 py-1.5 border-b border-border-subtle/50 text-[12px]">
              <span className="font-mono text-text-muted text-[10.5px] shrink-0">{r.日期.slice(5)}</span>
              <span className="text-text-bright shrink-0">{r.name}</span>
              <span className="font-mono text-[10.5px] text-text-muted shrink-0">{r.code}</span>
              <span className={`font-mono text-[10.5px] shrink-0 ${pctCls(r['当日涨跌%'])}`}>{r['当日涨跌%'] >= 0 ? '+' : ''}{r['当日涨跌%']}%</span>
              <span className="min-w-0 flex-1 truncate text-[10px] text-text-dim" title={r.上榜原因}>{r.上榜原因}</span>
              <span className={`font-mono font-semibold shrink-0 ${r.净额万 > 0 ? 'text-bear' : 'text-bull'}`}>
                {r.净额万 > 0 ? '买' : '卖'} {(Math.abs(r.净额万) / 1e4).toFixed(2)}亿
              </span>
              <span className="font-mono text-[10.5px] shrink-0 w-[72px] text-right">
                后1日 <span className={pctCls(r['后1日%'])}>{r['后1日%'] == null ? '—' : `${r['后1日%'] >= 0 ? '+' : ''}${r['后1日%']}%`}</span>
              </span>
            </div>
          ))}
        </div>

        <div className="px-4 py-1.5 border-t border-border-subtle text-[9.5px] text-text-muted leading-relaxed shrink-0">
          {data?.note || '交易所披露的营业部上榜明细。名号映射来自公开名录会漂移; 红盘率为纯历史统计, 不构成任何买卖建议。'}
        </div>
      </div>
    </div>
  )
}
