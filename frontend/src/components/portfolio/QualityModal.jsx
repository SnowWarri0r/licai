import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'
import { SCREEN_TONE } from './constants'

export function QualityModal({ row, onClose }) {
  const code = row.code
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [cap, setCap] = useState(null)      // 资本配置台账: 另一个 5-10 秒的多源请求, 各自到各自渲染
  const [capErr, setCapErr] = useState('')
  useEffect(() => {
    let alive = true
    setD(null); setErr(''); setCap(null); setCapErr('')
    const nm = encodeURIComponent(row.name || '')
    fetchJSON(`/api/market/quality-screen/${encodeURIComponent(code)}?name=${nm}`)
      .then(r => { if (alive) setD(r) })
      .catch(e => { if (alive) setErr(String(e?.message || e)) })
    fetchJSON(`/api/market/capital-allocation/${encodeURIComponent(code)}?name=${nm}`)
      .then(r => { if (alive) setCap(r) })
      .catch(e => { if (alive) setCapErr(String(e?.message || e)) })
    return () => { alive = false }
  }, [code, row.name])
  const head = d?.结论 === '排除' ? 'text-bear-bright' : d?.结论 === '未被排除' ? 'text-bull' : 'text-text-muted'
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl w-full max-w-lg max-h-[85vh] overflow-y-auto p-4 md:p-5" onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline gap-2 mb-1">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">去劣筛选</h3>
          <span className="text-[12px] text-text-bright">{row.name}</span>
          <span className="font-mono text-[10.5px] text-text-muted">{code}</span>
          {d?.行业 && <span className="text-[10.5px] text-text-dim">{d.行业}</span>}
          <button onClick={onClose} className="ml-auto text-text-muted hover:text-text cursor-pointer">✕</button>
        </div>
        <p className="text-[10.5px] text-text-muted mb-2">7 条硬指标排除"不是一流公司"。这是排除法 —— <span className="text-text-dim">未被排除不等于值得买</span>, 它只说这 7 条没抓住它。</p>
        {!d && !err && <p className="text-[11px] text-text-muted">取多年财报中… (要拉 30 期年报, 约 5-10 秒)</p>}
        {err && <p className="text-[11px] text-bear-bright">取数失败: {err}</p>}
        {d && (
          <>
            <div className="flex items-baseline gap-2 mb-2">
              <span className={`text-[13px] font-semibold ${head}`}>{d.结论}</span>
              <span className="text-[10.5px] text-text-dim">{d.说明}</span>
            </div>
            {d.适配提醒 && (
              <p className="text-[10.5px] text-warn m-0 mb-2 leading-relaxed">适配提醒: {d.适配提醒}</p>
            )}
            <table className="w-full text-[11px]">
              <tbody>
                {(d.逐条 || []).map(x => (
                  <tr key={x.指标} className="border-t border-border-subtle">
                    <td className="py-1 pr-2 text-text">{x.指标}</td>
                    <td className="py-1 pr-2 text-text-dim font-mono text-right whitespace-nowrap">{x.值 == null ? '—' : x.值}</td>
                    <td className="py-1 pr-2 text-text-muted whitespace-nowrap text-[10px]">{x.门限}</td>
                    <td className={`py-1 font-semibold whitespace-nowrap ${SCREEN_TONE[x.结论] || ''}`}>{x.结论}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(d.逐条 || []).filter(x => x.说明).map(x => (
              <p key={x.指标} className="text-[10px] text-text-muted m-0 mt-1 leading-relaxed">
                <span className="text-text-dim">{x.指标}:</span> {x.说明}
              </p>
            ))}
            {(d.取数缺口 || []).length > 0 && (
              <p className="text-[10px] text-bear-bright m-0 mt-1.5">取数失败: {d.取数缺口.join('; ')}</p>
            )}
            <p className="text-[10px] text-text-muted m-0 mt-2 leading-relaxed">{d.口径}</p>
            {d.股本口径 && <p className="text-[10px] text-text-muted m-0 leading-relaxed">股本对比: {d.股本口径}</p>}
          </>
        )}

        <div className="mt-3 pt-3 border-t border-border">
          <div className="flex items-baseline gap-2 mb-1.5">
            <span className="text-[12px] font-semibold text-text-bright">资本配置台账</span>
            <span className="text-[10px] text-text-muted">从股东拿到多少现金, 分红回购还了多少</span>
          </div>
          {!cap && !capErr && <p className="text-[11px] text-text-muted m-0">取分红/融资/回购记录中…</p>}
          {capErr && <p className="text-[11px] text-bear-bright m-0">取数失败: {capErr}</p>}
          {cap && (
            <>
              <p className="text-[11px] text-text m-0 leading-relaxed">{cap.一句话}</p>
              {(cap.回购逐笔 || []).length > 0 && (
                <table className="w-full text-[11px] mt-1.5">
                  <tbody>
                    <tr className="text-text-muted text-[10px]">
                      <td className="py-0.5">回购</td><td className="text-right">金额</td>
                      <td className="text-right">均价</td><td className="text-right">回购PB</td>
                    </tr>
                    {cap.回购逐笔.map(b => (
                      <tr key={b.起始} className="border-t border-border-subtle">
                        <td className="py-1 pr-2 font-mono text-[10.5px] text-text-dim whitespace-nowrap">{b.起始}</td>
                        <td className="py-1 pr-2 text-right font-mono text-text-dim">{b.金额亿}亿</td>
                        <td className="py-1 pr-2 text-right font-mono text-text-dim">{b.均价}</td>
                        <td className={`py-1 text-right font-mono ${b.回购PB != null && cap.当前PB != null && b.回购PB > cap.当前PB ? 'text-warn' : 'text-text-dim'}`}>
                          {b.回购PB == null ? '—' : b.回购PB}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {(cap.回购逐笔 || []).length > 0 && cap.当前PB != null && (
                <p className="text-[10px] text-text-muted m-0 mt-1">当前PB {cap.当前PB} —— 回购PB 高于它的那几笔(标黄)是买在比现在更贵的位置。不拿回购均价直接和现价比涨跌: 中间的分红送转会让这个比较失真。</p>
              )}
              {(cap.取数缺口 || []).length > 0 && (
                <p className="text-[10px] text-bear-bright m-0 mt-1">取数失败: {cap.取数缺口.join('; ')}</p>
              )}
              <p className="text-[10px] text-text-muted m-0 mt-1 leading-relaxed">{cap.拿?.口径}</p>
              <p className="text-[10px] text-text-muted m-0 leading-relaxed">「拿得多还得少」本身不是缺点 —— 扩张期公司本该融资, 关键看融来的钱变成了什么, 台账答不了这一步。</p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
