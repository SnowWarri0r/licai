import { useEffect, useState } from 'react'
import { prefetchJSON } from '../../hooks/useApi'

// 公司画像: 细分行业 + 一句话简介 + 完整简介 + 主营构成(营收占比/毛利率)。
// 榜单个股面板和个股详情弹窗共用 —— 原来只有榜单面板有, 从持仓/问答点开的详情弹窗里看不到公司是做什么的。
export function useCompany(code) {
  const [co, setCo] = useState(null)
  useEffect(() => {
    setCo(null)
    if (!code) return
    let alive = true
    prefetchJSON(`/api/market/company/${encodeURIComponent(code)}`)
      .then(d => { if (alive && d && d.industry) setCo(d) })       // 抓不到就静默不显示
      .catch(() => {})
    return () => { alive = false }
  }, [code])
  return co
}

export function CompanyDetail({ co }) {
  if (!co) return null
  return (
    <>
      {co.profile && (
        <p className="text-[11px] text-text-dim leading-relaxed m-0 mb-2 whitespace-pre-wrap">{co.profile}</p>
      )}
      {(co.main_business || []).length > 0 && (
        <div className="text-[10.5px]">
          <div className="text-text-muted mb-1">
            主营构成{co.report_date ? ` · ${co.report_date}` : ''}
            <span className="text-text-dim ml-2">营收占比 / 毛利率</span>
          </div>
          {co.main_business.map((m, i) => (
            <div key={i} className="flex items-center gap-2 py-[1px]">
              <span className="text-text-dim w-44 truncate" title={m['项目']}>{m['项目']}</span>
              <span className="font-mono text-text">{m['营收占比%'] != null ? `${m['营收占比%']}%` : '—'}</span>
              <span className="font-mono text-text-muted">
                {m['毛利率%'] != null ? `毛利 ${m['毛利率%']}%` : ''}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="text-[10px] text-text-muted mt-2 pt-1.5 border-t border-border-subtle">
        {[co.employees ? `员工 ${co.employees}` : '', co.controller ? `实控 ${co.controller}` : '']
          .filter(Boolean).join(' · ') || '数据来自公开披露'}
      </div>
    </>
  )
}

// 详情弹窗用: 一行"细分行业 · 简介 ›", 点开展开完整简介和主营构成
export function CompanyStrip({ code }) {
  const co = useCompany(code)
  const [open, setOpen] = useState(false)
  if (!co) return null
  return (
    <div className="mb-3 rounded-lg border border-border-subtle bg-surface/40">
      <button onClick={() => setOpen(v => !v)} title={open ? '收起' : '点开看完整简介与主营构成'}
        className="w-full flex items-baseline gap-2 px-3 py-1.5 text-left cursor-pointer min-w-0">
        <span className="text-[11px] text-accent whitespace-nowrap shrink-0">{co.industry}</span>
        {co.brief && <span className="text-[11px] text-text-dim truncate min-w-0">{co.brief}</span>}
        <span className="ml-auto text-[11px] text-text-muted shrink-0">{open ? '收起 ⌄' : '公司简介 ›'}</span>
      </button>
      {open && (
        <div className="px-3 pb-2.5 pt-1 border-t border-border-subtle max-h-60 overflow-y-auto">
          <CompanyDetail co={co} />
        </div>
      )}
    </div>
  )
}
