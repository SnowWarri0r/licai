import { useState, useEffect } from 'react'
import { prefetchJSON } from '../../hooks/useApi'
import ProKline from '../ProKline'
import StockAskModal from '../StockAskModal'
import GroupPicker from './GroupPicker'
import { pctColor, boardOf, WL_DEFAULT } from './shared'

// 右侧面板: 选中股票看 K线(铺满); 想问就点"问 AI"或底部输入框 → 弹出式对话(与问问市场样式一致)
export default function StockPanel({ stock, watched, onToggleWatch, groups, myGroups, onSetGroups }) {
  const [askOpen, setAskOpen] = useState(false)
  const [seed, setSeed] = useState('')
  const [draft, setDraft] = useState('')
  const [co, setCo] = useState(null)          // 公司画像(细分行业/一句话主营/简介/主营构成)
  const [coOpen, setCoOpen] = useState(false)
  const [grpOpen, setGrpOpen] = useState(false)   // 分组下拉

  // 切换股票: 关弹窗、清空草稿
  useEffect(() => { setAskOpen(false); setSeed(''); setDraft('') }, [stock])

  // 公司画像: 榜单只有粗板块(「半导体」), 这里补三级细分 + 做啥的。抓不到就静默不显示。
  useEffect(() => {
    setCo(null); setCoOpen(false)
    const code = stock?.code
    if (!code) return
    let alive = true
    prefetchJSON(`/api/market/company/${encodeURIComponent(code)}`)
      .then(d => { if (alive && d && d.industry) setCo(d) })
      .catch(() => {})
    return () => { alive = false }
  }, [stock?.code])

  const openAsk = (question = '') => { setSeed(question); setAskOpen(true) }
  const submitDraft = () => { const t = draft.trim(); if (t) { openAsk(t); setDraft('') } }

  if (!stock) {
    return (
      <div className="h-full flex items-center justify-center text-center px-6">
        <div className="text-text-muted text-[13px] leading-relaxed">
          点左侧任意一只股票看 K 线<br />
          <span className="text-[11px] text-text-dim">想问什么(为什么涨/量价/消息)点「问 AI」</span>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      {/* 窄面板下这一行原来是逐个折行的: 股票名折成两行、「问 AI 分析」也折成两行, 整个
          头部撑高两倍还把 K 线挤下去。所以: 一律不折行(flex-nowrap), 按信息优先级逐级隐藏
          —— 简介最先丢, 然后细分行业, 再然后板块; 名字/涨跌/按钮永远留着。
          横向只用 overflow-x-clip: 用 overflow-hidden 会连纵向一起裁, 把「分组」下拉菜单
          剪得只剩露在头部里的 7px —— 表现就是"点了没反应"(实测)。clip 不像 hidden 那样
          强迫另一个轴变成 auto, 所以纵向仍是 visible, 菜单能垂下来。
          用容器查询(@container)而不是屏幕断点: 决定挤不挤的是这块面板的宽度, 不是窗口宽度
          —— 左边榜单栏占掉的宽度是固定的, 但侧边栏能收起, 同一个窗口宽度下面板可宽可窄。 */}
      <div className="@container flex flex-nowrap items-baseline gap-2 px-4 py-2 border-b
                      border-border-subtle shrink-0 overflow-x-clip">
        {/* 名字不给 shrink-0: 面板窄到连按钮都放不下时, 与其把右边的按钮挤出可视区(overflow
            被裁掉就等于按钮消失了), 不如让名字自己截断。简介比名字长得多, flex 收缩按基准
            尺寸分摊, 所以有简介时先收简介, 轮不到名字。 */}
        <span className="text-[14px] font-semibold text-text-bright whitespace-nowrap truncate min-w-0">
          {stock.name}
        </span>
        <span className="text-[11px] font-mono text-text-muted whitespace-nowrap shrink-0">{stock.code}</span>
        {stock.pct != null && (
          <span className={`text-[13px] font-mono font-semibold whitespace-nowrap shrink-0 ${pctColor(stock.pct)}`}>
            {stock.pct >= 0 ? '+' : ''}{stock.pct}%
          </span>
        )}
        {/* 板块 + 细分行业: 异动/龙虎榜等榜单行不带「行业」字段, 板块按代码前缀现算总是有;
            细分行业(三级)拉到就替掉粗行业, 拉不到退回榜单给的粗行业 */}
        <span className="hidden @md:inline text-[10.5px] text-text-dim ml-1 whitespace-nowrap shrink-0">
          {boardOf(stock.code)}
        </span>
        {(co?.industry || stock['行业']) && (
          <span className="hidden @lg:inline text-[10.5px] text-text-dim whitespace-nowrap shrink-0">
            · {co?.industry || stock['行业']}
          </span>
        )}
        {co?.brief && (
          <button onClick={() => setCoOpen(v => !v)} title="点开看完整简介与主营构成"
            className="hidden @2xl:block text-[10.5px] text-text-muted hover:text-text truncate
                       min-w-0 max-w-[22rem] cursor-pointer text-left">
            {co.brief} <span className="text-text-dim">{coOpen ? '⌄' : '›'}</span>
          </button>
        )}
        <button onClick={() => onToggleWatch(stock)}
          title={watched ? '移出观察池' : '加进观察池的默认组「自选」(想直接归到别的组用右边那个)'}
          className={`ml-auto text-[15px] leading-none px-1.5 py-0.5 rounded cursor-pointer shrink-0 ${watched ? 'text-accent' : 'text-text-dim hover:text-accent'}`}>
          {watched ? '★' : '☆'}
        </button>
        {onSetGroups && (
          <div className="relative shrink-0">
            {/* 胶囊上只写得下一个组名, 多的用 +N 收着(hover 看全) —— 一票可以在好几个组里 */}
            <button data-grp-trigger onClick={() => setGrpOpen(v => !v)}
              title={(myGroups || []).length
                ? `所属分组: ${(myGroups || []).join(' · ')} —— 点开可多选`
                : '选分组直接观察(可多选) —— 勾了别的组不会把「自选」顶掉, 可以同时在'}
              className={`text-[10.5px] px-1.5 py-0.5 rounded border cursor-pointer whitespace-nowrap ${
                watched ? 'border-border-subtle text-text-dim hover:text-accent hover:border-accent/40'
                        : 'border-accent/40 text-accent hover:bg-accent/10'}`}>
              {(myGroups || []).length
                ? `${myGroups[0]}${myGroups.length > 1 ? ` +${myGroups.length - 1}` : ''}`
                : (watched ? WL_DEFAULT : '+ 分组')}<span className="hidden @2xl:inline">{
                (myGroups || []).length || watched ? '' : '观察'}</span>
              <span className="text-text-muted ml-0.5">⌄</span>
            </button>
            {grpOpen && (
              <GroupPicker groups={groups} current={myGroups} className="absolute right-0 top-full mt-1"
                onSet={(gs) => onSetGroups(stock.code, gs)} onClose={() => setGrpOpen(false)} />
            )}
          </div>
        )}
        <button onClick={() => openAsk('')}
          className="text-[11px] px-2.5 py-1 rounded-lg bg-accent/20 text-accent border
                     border-accent/40 hover:bg-accent/30 whitespace-nowrap shrink-0">
          问 AI<span className="hidden @xl:inline"> 分析</span>
        </button>
      </div>

      {/* 公司详情: 完整简介 + 主营构成(营收占比/毛利率). 默认收起, 不挤压 K 线 */}
      {coOpen && co && (
        <div className="px-4 py-2.5 border-b border-border-subtle bg-surface-2/40 shrink-0 max-h-52 overflow-y-auto">
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
        </div>
      )}

      {/* K线铺满面板 */}
      <div className="flex-1 min-h-0 px-3 py-2">
        <ProKline code={stock.code} fill lhbDate={stock._lhbDate || ''} />
      </div>

      {/* 底部快捷提问: 回车/点问 → 弹出对话 */}
      <div className="shrink-0 border-t border-border px-3 py-2 flex gap-2">
        <input value={draft} onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) submitDraft() }}
          placeholder={`想问点 ${stock.name} 什么?例: 今天为什么这么走 / 量价怎么看`}
          className="flex-1 text-[12px] px-3 py-2 rounded-lg bg-surface-3 border border-border text-text placeholder:text-text-muted focus:border-accent/50 outline-none" />
        <button onClick={submitDraft} disabled={!draft.trim()}
          className="text-[12px] px-3.5 py-2 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed">
          问
        </button>
      </div>

      {askOpen && <StockAskModal stock={stock} initialQuestion={seed} onClose={() => setAskOpen(false)} />}
    </div>
  )
}
