import { useState, useEffect, useRef } from 'react'
import { fetchJSON, prefetchJSON } from '../hooks/useApi'
import ProKline from './ProKline'
import StockAskModal from './StockAskModal'

const TABS = [
  { key: 'watch', label: '自选' },
  { key: 'changes', label: '异动' },
  { key: 'gainers', label: '涨幅' },
  { key: 'by_amount', label: '成交额' },
  { key: 'lhb', label: '龙虎榜' },
  { key: 'structure', label: '蓄势/强势' },
  { key: 'inst', label: '机构' },
  { key: 'earnings', label: '业绩' },
]

function pctColor(v) {
  if (v > 0) return 'text-bear'
  if (v < 0) return 'text-bull'
  return 'text-text-dim'
}

// 按代码前缀分板块。场内基金(1x/5x)不属于任何"板块", 单独标出 —— 588xxx 是科创板
// 主题 ETF, 标成「主板」是分类错误。榜单列表只有个股, 不受这条影响。
function boardOf(code) {
  const c = String(code || '')
  if (/^[15]\d{5}$/.test(c)) return '场内基金'
  if (c.startsWith('688') || c.startsWith('689')) return '科创板'
  if (c.startsWith('30')) return '创业板'
  // 北交所: 8xxxxx(83/87/88) / 4xxxxx(老三板迁移) / 920xxx(新代码段) —— 与后端
  // market_data._is_bj_share 同口径; 漏掉 920 会把北交所票错标成主板
  if (c.startsWith('920') || c[0] === '8' || c[0] === '4') return '北交所'
  return '主板'
}
const BOARDS = ['全部', '主板', '创业板', '科创板', '北交所']

// 右侧面板: 选中股票看 K线(铺满); 想问就点"问 AI"或底部输入框 → 弹出式对话(与问问市场样式一致)
// 分组下拉。自选页行内的 ⋯ 与 K线面板的「分组」共用同一份 —— 两处各写一遍必然漂开。
// groups=已有分组名, current=该票当前分组(''=未分组), onPick(名字) 落库。
function GroupPicker({ groups, current, onPick, onClose, className = '' }) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const submit = () => {
    const g = name.trim().slice(0, 20)
    if (!g) return
    setCreating(false); setName('')
    onClose(); onPick(g)
  }
  return (
    // 输入中不要用 onMouseLeave 关掉: 打字时鼠标一飘出去就前功尽弃
    <div className={`z-30 w-44 bg-surface-2 border border-border rounded-lg shadow-xl py-1 ${className}`}
      onMouseLeave={creating ? undefined : onClose} onClick={(e) => e.stopPropagation()}>
      <div className="px-2.5 py-1 text-[10px] text-text-muted">移到分组</div>
      {[...new Set([...(groups || []), ''])].map(g => (
        <button key={g || '_none'}
          onClick={(e) => { e.stopPropagation(); onClose(); onPick(g) }}
          className={`w-full text-left px-2.5 py-1 text-[11px] hover:bg-surface-3/80 ${
            (current || '') === g ? 'text-accent' : 'text-text-dim'}`}>
          {g || '未分组'}{(current || '') === g ? ' ✓' : ''}
        </button>
      ))}
      {creating ? (
        // 不用 window.prompt: Chrome 在用户勾过「阻止此页面创建更多对话框」之后,
        // prompt() 会直接返回 null 且不弹窗 —— 表现就是"点了没反应"。内联输入没这问题,
        // 也能进自动化验证。
        <div className="flex items-center gap-1 px-2 py-1.5 border-t border-border-subtle mt-1">
          <input autoFocus value={name} maxLength={20} placeholder="新分组名"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter') submit()
              if (e.key === 'Escape') { setCreating(false); setName('') }
            }}
            className="flex-1 min-w-0 bg-surface-3 border border-border-subtle rounded px-1.5 py-0.5
                       text-[11px] text-text outline-none focus:border-accent/50" />
          <button onClick={submit} disabled={!name.trim()}
            className="text-[11px] px-1.5 py-0.5 rounded text-accent hover:bg-accent/10 disabled:opacity-40">
            建
          </button>
        </div>
      ) : (
        <button onClick={(e) => { e.stopPropagation(); setCreating(true) }}
          className="w-full text-left px-2.5 py-1 text-[11px] text-accent hover:bg-surface-3/80
                     border-t border-border-subtle mt-1">
          + 新建分组…
        </button>
      )}
    </div>
  )
}


function StockPanel({ stock, watched, onToggleWatch, groups, group, onPickGroup }) {
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
      <div className="flex items-baseline gap-2 px-4 py-2 border-b border-border-subtle shrink-0">
        <span className="text-[14px] font-semibold text-text-bright">{stock.name}</span>
        <span className="text-[11px] font-mono text-text-muted">{stock.code}</span>
        {stock.pct != null && (
          <span className={`text-[13px] font-mono font-semibold ${pctColor(stock.pct)}`}>
            {stock.pct >= 0 ? '+' : ''}{stock.pct}%
          </span>
        )}
        {/* 板块 + 细分行业: 异动/龙虎榜等榜单行不带「行业」字段, 板块按代码前缀现算总是有;
            细分行业(三级)拉到就替掉粗行业, 拉不到退回榜单给的粗行业 */}
        <span className="text-[10.5px] text-text-dim ml-1">{boardOf(stock.code)}</span>
        {(co?.industry || stock['行业']) && (
          <span className="text-[10.5px] text-text-dim">· {co?.industry || stock['行业']}</span>
        )}
        {co?.brief && (
          <button onClick={() => setCoOpen(v => !v)} title="点开看完整简介与主营构成"
            className="text-[10.5px] text-text-muted hover:text-text truncate max-w-[22rem] cursor-pointer text-left">
            {co.brief} <span className="text-text-dim">{coOpen ? '⌄' : '›'}</span>
          </button>
        )}
        <button onClick={() => onToggleWatch(stock)}
          title={watched ? '移出自选' : '加入自选但不分组(想直接归组用右边的「+ 分组观察」)'}
          className={`ml-auto text-[15px] leading-none px-1.5 py-0.5 rounded cursor-pointer ${watched ? 'text-accent' : 'text-text-dim hover:text-accent'}`}>
          {watched ? '★' : '☆'}
        </button>
        {onPickGroup && (
          <div className="relative">
            <button onClick={() => setGrpOpen(v => !v)}
              title={watched ? '改分组' : '选个分组直接观察(会一并加入自选 —— 分组就是自选里的标签)'}
              className={`text-[10.5px] px-1.5 py-0.5 rounded border cursor-pointer whitespace-nowrap ${
                watched ? 'border-border-subtle text-text-dim hover:text-accent hover:border-accent/40'
                        : 'border-accent/40 text-accent hover:bg-accent/10'}`}>
              {watched ? (group || '未分组') : '+ 分组观察'} <span className="text-text-muted">⌄</span>
            </button>
            {grpOpen && (
              <GroupPicker groups={groups} current={group} className="absolute right-0 top-full mt-1"
                onPick={(g) => onPickGroup(stock.code, g)} onClose={() => setGrpOpen(false)} />
            )}
          </div>
        )}
        <button onClick={() => openAsk('')}
          className="text-[11px] px-2.5 py-1 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30">
          问 AI 分析
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

export default function Rankings() {
  const [tab, setTab] = useState(() => {
    // deep-link: #rankings?t=inst 直达指定页签(旧 coiled/unbroken 併入 structure)
    const q = new URLSearchParams((window.location.hash.split('?')[1] || ''))
    let t = q.get('t')
    if (t === 'coiled' || t === 'unbroken') t = 'structure'
    return TABS.some(x => x.key === t) ? t : 'gainers'
  })
  const [board, setBoard] = useState('全部')
  const [data, setData] = useState(null)
  const [structure, setStructure] = useState(null)
  const [phaseFilter, setPhaseFilter] = useState('全部')   // 全部 | 强势 | 蓄势
  const [indFilter, setIndFilter] = useState('全部')       // 行业快捷筛选
  const [inst, setInst] = useState(null)
  const [instSide, setInstSide] = useState('net_buy')   // net_buy | net_sell
  const [earnings, setEarnings] = useState(null)
  const [earnSide, setEarnSide] = useState('预喜')       // 预喜 | 预警 | 持仓关联
  const [lhbDaily, setLhbDaily] = useState(null)         // 最新披露日龙虎榜全榜单
  const [watch, setWatch] = useState(null)               // 自选池(全量视图)
  const [watchSet, setWatchSet] = useState(new Set())    // 自选代码集(☆按钮状态)
  const [wlMeta, setWlMeta] = useState({ groups: [], byCode: {} })  // 分组标签(轻端点, 各页签通用)
  const [changes, setChanges] = useState(null)           // 盘口异动事件流
  const [chGroup, setChGroup] = useState('全部')          // 异动组: 全部/拉升/跳水/竞价
  const [chKind, setChKind] = useState('全部')            // 异动组内按事件类型细分
  const [sq, setSq] = useState('')                       // 自由查股输入
  const [sqCands, setSqCands] = useState([])             // 搜索候选
  const sqTimer = useRef(null)
  const sqSeq = useRef(0)                                 // 请求序号, 丢弃乱序返回
  const [sqBusy, setSqBusy] = useState(false)
  const [wlGroup, setWlGroup] = useState('全部')          // 自选分组 chips 当前筛选
  const [dragCode, setDragCode] = useState('')            // 正在拖动的自选代码
  const [grpMenu, setGrpMenu] = useState('')              // 展开「移到分组」菜单的代码
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(false)
  const [selected, setSelected] = useState(null)
  const listRef = useRef([])
  const indsRef = useRef(['全部'])
  const chKindsRef = useRef(['全部'])
  const tabRef = useRef('gainers')
  const deepSelRef = useRef(new URLSearchParams(window.location.hash.split('?')[1] || '').get('s') || '')

  // deep-link: #rankings?t=lhb&s=688008 榜单加载完自动选中该股
  useEffect(() => {
    if (!deepSelRef.current) return
    const r = listRef.current.find(x => x.code === deepSelRef.current)
    if (r) { deepSelRef.current = ''; setSelected(r) }
  })

  const load = () => {
    setLoading(true); setErr(false)
    const req = tab === 'structure'
      ? fetchJSON('/api/market/structure').then(d => { if (d.error) setErr(true); else setStructure(d) })
      : tab === 'inst'
      ? fetchJSON('/api/market/inst-flow?top=40').then(d => { if (d.error) setErr(true); else setInst(d) })
      : tab === 'earnings'
      ? fetchJSON('/api/market/earnings?top=100').then(d => { if (d.error) setErr(true); else setEarnings(d) })
      : tab === 'lhb'
      ? fetchJSON('/api/market/lhb-daily').then(d => { if (d.error) setErr(true); else setLhbDaily(d) })
      : tab === 'watch'
      ? fetchJSON('/api/market/watchlist').then(d => { if (d.error) setErr(true); else setWatch(d) })
      : tab === 'changes'
      ? fetchJSON(`/api/market/changes?group=${encodeURIComponent(chGroup)}`).then(d => { if (d.error) setErr(true); else setChanges(d) })
      : fetchJSON('/api/market/rankings?limit=100').then(d => { if (d.error) setErr(true); else setData(d) })
    req.catch(() => setErr(true)).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])
  // 自选代码集(☆按钮状态), 轻端点
  useEffect(() => {
    reloadWlMeta()
  }, [])
  // 分组标签走轻端点: 看 K 线时 watch(全量视图)还没加载, 分组下拉不能依赖它
  const reloadWlMeta = () => fetchJSON('/api/market/watchlist?lite=1').then(d => {
    setWatchSet(new Set(d?.codes || []))
    setWlMeta({ groups: d?.groups || [], byCode: d?.by_code || {} })
  }).catch(() => {})
  const groupOf = (code) => wlMeta.byCode[code] || ''

  // 自由查股: 防抖搜索 → 候选下拉 → 选中进右侧面板(与榜单行同一套 K线/浮层/问AI)
  const onSearch = (v) => {
    setSq(v)
    if (sqTimer.current) clearTimeout(sqTimer.current)
    const t = v.trim()
    if (!t) { setSqCands([]); setSqBusy(false); sqSeq.current++; return }
    // 去抖 180ms(原 300ms): 后端已按查询词缓存 45s, 回退删字基本秒回, 不必等那么久。
    // seq 防乱序: 快速输入时多个请求并发, 只认最后一次的结果, 否则短前缀的旧结果会
    // 盖掉长前缀的新结果。加载期间保留上一次候选, 避免列表闪空。
    const my = ++sqSeq.current
    setSqBusy(true)
    sqTimer.current = setTimeout(() => {
      fetchJSON(`/api/market/stock-search?q=${encodeURIComponent(t)}`)
        .then(d => { if (my === sqSeq.current) setSqCands(d?.candidates || []) })
        .catch(() => { if (my === sqSeq.current) setSqCands([]) })
        .finally(() => { if (my === sqSeq.current) setSqBusy(false) })
    }, 180)
  }
  const pickCand = (c) => {
    setSelected({ code: String(c.code), name: c.name || String(c.code), pct: c.pct ?? 0 })
    setSq(''); setSqCands([])
  }

  // 自选重排。scope 随视图: 「全部」写全局位次, 选中分组写组内位次(两套独立)。
  const wlScope = () => (wlGroup === '全部' ? 'global' : 'group')
  const wlScopeGroup = () => (wlGroup === '全部' ? '' : (wlGroup === '未分组' ? '' : wlGroup))

  // 当前"显示顺序"的代码序列 —— 必须与界面一致, 不能用 watch.rows 的数组原始顺序:
  // 乐观更新只改行上的位次字段、不重排数组, 拿原始顺序算会导致第二次 ↑↓ 写回和上次
  // 相同的结果, 表现为"点一次之后就没用了"。
  const wlVisibleCodes = () => {
    let rs = ((watch?.rows) || []).filter(r => r.source !== '持仓')
    if (wlGroup !== '全部') rs = rs.filter(r => (r['分组'] || '') === wlScopeGroup())
    const key = wlGroup === '全部' ? 'sort_order' : 'group_order'
    return [...rs].sort((a, b) => (a[key] || 0) - (b[key] || 0)).map(r => r.code)
  }

  const commitOrder = async (codes) => {
    const scope = wlScope()
    const group = wlScopeGroup()
    const key = scope === 'global' ? 'sort_order' : 'group_order'
    // 乐观更新: 只改本视图对应的位次字段(另一套不动), 列表随即按它重排
    setWatch(prev => {
      if (!prev?.rows) return prev
      const pos = new Map(codes.map((c, i) => [c, i]))
      const rows = prev.rows.map(r => (pos.has(r.code)
        ? { ...r, [key]: pos.get(r.code), ...(scope === 'group' ? { 分组: group } : {}) }
        : r))
      return { ...prev, rows }
    })
    try {
      await fetchJSON('/api/market/watchlist-order', {
        method: 'PUT', body: JSON.stringify({ scope, group, codes }),
      })
    } catch { load() }
  }

  // ↑↓ 一格: 在当前视图的显示顺序里换位
  const nudge = (code, dir) => {
    const codes = wlVisibleCodes()
    const i = codes.indexOf(code)
    const j = i + dir
    if (i < 0 || j < 0 || j >= codes.length) return
    ;[codes[i], codes[j]] = [codes[j], codes[i]]
    commitOrder(codes)
  }

  // 拖放: 把 dragCode 插到 targetCode 之前
  const dropOn = (targetCode) => {
    const from = dragCode
    setDragCode('')
    if (!from || from === targetCode) return
    const codes = wlVisibleCodes().filter(c => c !== from)
    const at = codes.indexOf(targetCode)
    codes.splice(at < 0 ? codes.length : at, 0, from)
    commitOrder(codes)
  }

  const moveToGroup = async (code, group) => {
    try {
      await fetchJSON(`/api/market/watchlist/${code}/group`, {
        method: 'PUT', body: JSON.stringify({ group }),
      })
    } catch { /* 失败下面照样重拉 */ }
    reloadWlMeta()      // 面板上那颗分组胶囊要立刻跟着变
    load()
  }

  // 选组即入池: 分组是 watchlist 表上的 grp 字段, 没有"不在自选里的分组"。所以在
  // K线面板上选分组时, 没加自选的先加, 再打标签 —— 用户不必先想"加自选"这一步。
  const pickGroup = async (code, g) => {
    if (!watchSet.has(code)) {
      try {
        await fetchJSON(`/api/market/watchlist/${code}`, { method: 'POST' })
        setWatchSet(prev => new Set(prev).add(code))
      } catch { /* 加失败下面 moveToGroup 也会失败, 统一由 reloadWlMeta 校正 */ }
    }
    await moveToGroup(code, g)
  }

  const toggleWatch = (stock) => {
    const code = stock.code
    const on = watchSet.has(code)
    fetchJSON(`/api/market/watchlist/${code}`, { method: on ? 'DELETE' : 'POST' })
      .then(() => {
        setWatchSet(prev => { const s = new Set(prev); on ? s.delete(code) : s.add(code); return s })
        setWlMeta(m => {
          const byCode = { ...m.byCode }
          if (on) delete byCode[code]; else byCode[code] = byCode[code] || ''
          return { ...m, byCode }
        })
        setWatch(null)                                  // 下次进自选页重拉
        if (tabRef.current === 'watch') load()
      }).catch(() => {})
  }
  // ←→ 切分类时把选中 chip 滚进可视区
  useEffect(() => {
    try { document.querySelector(`[data-ind="${indFilter}"]`)?.scrollIntoView({ inline: 'nearest', block: 'nearest' }) } catch { /* 行业名含引号等极端情况忽略 */ }
  }, [indFilter])
  useEffect(() => {
    try { document.querySelector(`[data-chkind="${chKind}"]`)?.scrollIntoView({ inline: 'nearest', block: 'nearest' }) } catch { /* ignore */ }
  }, [chKind])
  // ↑↓ 翻股时把选中行滚进可视区(键盘翻到列表可视区外时跟随滚动)
  useEffect(() => {
    if (!selected?.code) return
    try { document.querySelector(`[data-row="${selected.code}"]`)?.scrollIntoView({ block: 'nearest' }) } catch { /* ignore */ }
  }, [selected])
  // K线预取: 选中行变化 → 顺序预取光标附近(下3只/上1只)的K线首屏, 方向键翻股秒开;
  // 刚进页签还没选中时预取前3行。一次只发一个请求, 不挤占当前选中股的加载。
  useEffect(() => {
    const arr = listRef.current || []
    if (!arr.length) return
    const i = selected?.code ? arr.findIndex(x => x.code === selected.code) : -1
    const idxs = i >= 0 ? [i + 1, i + 2, i - 1, i + 3] : [0, 1, 2]
    const codes = [...new Set(idxs.map(j => arr[j]?.code).filter(Boolean))]
    let stop = false
    ;(async () => {
      for (const c of codes) {
        if (stop) return
        try { await prefetchJSON(`/api/market/history/${encodeURIComponent(c)}?days=250`) } catch { /* 预取失败静默, 正式加载会重试 */ }
      }
    })()
    return () => { stop = true }
  }, [selected, tab, loading])
  // 切到结构/机构/业绩 tab 时懒加载(服务端有缓存, 之后秒回)
  useEffect(() => { if ((tab === 'structure' && !structure) || (tab === 'inst' && !inst) || (tab === 'earnings' && !earnings) || (tab === 'lhb' && !lhbDaily) || (tab === 'watch' && !watch)) load() }, [tab])   // eslint-disable-line react-hooks/exhaustive-deps
  // 异动页: 进页签/换组立即拉 + 60s 静默轮询(服务端45s缓存, 盘中事件流持续滚动, 不闪加载态)
  useEffect(() => {
    if (tab !== 'changes') return
    let alive = true
    const pull = () => fetchJSON(`/api/market/changes?group=${encodeURIComponent(chGroup)}`)
      .then(d => { if (alive && !d.error) setChanges(d) }).catch(() => {})
    pull()
    const t = setInterval(pull, 60000)
    return () => { alive = false; clearInterval(t) }
  }, [tab, chGroup])

  // ↑↓ 翻K线, ←→ 切行业分类(结构页); 输入框聚焦时不劫持
  useEffect(() => {
    const onKey = (e) => {
      const isUD = e.key === 'ArrowDown' || e.key === 'ArrowUp'
      const isLR = e.key === 'ArrowLeft' || e.key === 'ArrowRight'
      if (!isUD && !isLR) return
      const tag = (document.activeElement?.tagName || '').toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return
      if (isUD) {
        setSelected(prev => {
          if (!listRef.current?.length) return prev
          const arr = listRef.current
          // 定位当前行优先用唯一键 _k: 异动榜同一只票会出现多条事件, 只按 code 找会永远
          // 命中第一条, 于是「下一条」还是同一只票, 方向键原地打转下不去。
          let i = prev?._k ? arr.findIndex(x => x._k === prev._k) : -1
          if (i < 0) i = arr.findIndex(x => x.code === prev?.code)
          const ni = e.key === 'ArrowDown' ? Math.min(i + 1, arr.length - 1) : Math.max(i - 1, 0)
          return arr[ni] || prev
        })
        e.preventDefault()
      } else if (tabRef.current === 'structure') {
        setIndFilter(prev => {
          const arr = indsRef.current
          if (arr.length < 2) return prev
          const i = Math.max(arr.indexOf(prev), 0)
          return arr[(i + (e.key === 'ArrowRight' ? 1 : -1) + arr.length) % arr.length]
        })
        e.preventDefault()
      } else if (tabRef.current === 'changes') {
        setChKind(prev => {
          const arr = chKindsRef.current
          if (arr.length < 2) return prev
          const i = Math.max(arr.indexOf(prev), 0)
          return arr[(i + (e.key === 'ArrowRight' ? 1 : -1) + arr.length) % arr.length]
        })
        e.preventDefault()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const rawList = tab === 'inst' ? ((inst && inst[instSide]) || []).map(r => ({ ...r, pct: r['距最近上榜%'] }))
    : tab === 'changes' ? ((changes?.rows) || []).filter(r => chKind === '全部' || r['类型'] === chKind).slice(0, 120).map((r, i) => ({ ...r, _k: `${r.code}-${r.时间}-${i}` }))
    : tab === 'watch' ? (() => {
        const rs = (watch?.rows) || []
        const held = rs.filter(r => r.source === '持仓')
        // 分组 chips 只筛手动自选; 持仓组是虚拟组(现取), 不参与分组
        let manual = rs.filter(r => r.source !== '持仓')
        if (wlGroup !== '全部') manual = manual.filter(r => (r['分组'] || '') === (wlGroup === '未分组' ? '' : wlGroup))
        // 排序键随视图切换: 「全部」用全局位次, 选中某分组用组内位次 —— 两套独立,
        // 所以组内调顺序不会打乱「全部」, 改分组标签也不会让票在「全部」里跳位置。
        // 必须在本地排: ↑↓/拖动是乐观更新(只改行上的位次字段), 否则要等下次拉取才动。
        manual = [...manual].sort((a, b) => (wlGroup === '全部'
          ? (a.sort_order || 0) - (b.sort_order || 0)
          : (a.group_order || 0) - (b.group_order || 0)))
        // 显式编号: 行号渲染兜底用的是数组下标 i+1, 而数组里混着分组标题行, 标题占掉
        // index 0 会让第一只票显示成 2。这里按"只数着真实行"自己编。
        let sn = 0
        const merged = []
        if (held.length && wlGroup === '全部') {
          merged.push({ _wh: true, 标题: `持仓 ${held.length}`, 说明: '现取, 清仓即消失' })
          merged.push(...held.map(r => ({ ...r, _idx: ++sn })))
        }
        if (manual.length) {
          merged.push({ _wh: true, 标题: `自选 ${manual.length}${wlGroup !== '全部' ? ` · ${wlGroup}` : ''}`, 说明: '在看未必持有; 拖 ⠿ 或点 ↑↓ 调顺序' })
          merged.push(...manual.map(r => ({ ...r, _idx: ++sn })))
        }
        return merged
      })()
    : tab === 'lhb' ? ((lhbDaily?.rows) || []).map(r => ({ ...r, pct: r['涨跌幅'], _lhbDate: lhbDaily.date }))
    : tab === 'earnings' ? (
        (earnSide === '持仓关联'
          ? [...(earnings?.['持仓关联预喜'] || []), ...(earnings?.['持仓关联预警'] || [])]
          : (earnings && earnings[earnSide]) || []
        ).map(r => ({ ...r, pct: r['幅度%'] }))
      )
    : tab === 'structure' ? []
    : ((data && data[tab]) || [])
  // 结构页: 行业分组 → 组头行 + 个股行 摊平成一个列表(板块/阶段筛选后空组不显示)
  let _sn = 0
  const structList = tab !== 'structure' ? [] : (structure?.groups || []).flatMap(g => {
    if (indFilter !== '全部' && g.行业 !== indFilter) return []
    let rs = g.rows || []
    if (phaseFilter !== '全部') rs = rs.filter(r => r.phase === phaseFilter)
    if (board !== '全部') rs = rs.filter(r => boardOf(r.code) === board)
    if (!rs.length) return []
    const nQ = rs.filter(r => r.phase === '强势').length
    return [{ _gheader: true, 行业: g.行业, n: rs.length, n_强势: nQ, n_蓄势: rs.length - nQ },
            ...rs.map(r => ({ ...r, _idx: ++_sn }))]
  })
  const list = tab === 'structure' ? structList
    : board === '全部' ? rawList : rawList.filter(r => boardOf(r.code) === board)
  listRef.current = list.filter(r => !r._gheader && !r._wh)
  indsRef.current = ['全部', ...(structure?.groups || []).map(g => g.行业)]
  chKindsRef.current = ['全部', ...(changes?.kinds || []).map(k => k.kind)]
  tabRef.current = tab

  return (
    <div className="bg-surface-2 border border-border rounded-xl overflow-hidden flex flex-col lg:flex-row h-[calc(100vh-11rem)] min-h-[480px]">
      <div className="lg:w-[420px] shrink-0 flex flex-col border-b lg:border-b-0 lg:border-r border-border min-h-0">
        <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border-subtle">
          <div className="no-scrollbar flex items-center gap-1 overflow-x-auto min-w-0 flex-1">
            {TABS.map(t => (
              <button key={t.key} onClick={() => setTab(t.key)}
                className={`text-[12px] px-2 py-1 rounded border whitespace-nowrap shrink-0 ${tab === t.key ? 'bg-accent/20 text-accent border-accent/40' : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
                {t.label}
              </button>
            ))}
          </div>
          <div className="relative shrink-0">
            <input value={sq} onChange={e => onSearch(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && sqCands.length) pickCand(sqCands[0]); if (e.key === 'Escape') { setSq(''); setSqCands([]) } }}
              placeholder="查任意股票" title="代码/名称/拼音子串, 选中后与榜单一样看K线/分时/龙虎榜/问AI"
              className="w-[86px] focus:w-[130px] transition-all text-[11px] px-2 py-1 rounded bg-surface-3 border border-border text-text placeholder:text-text-muted focus:border-accent/50 outline-none" />
            {sqBusy && sqCands.length === 0 && sq.trim() && (
              <div className="absolute right-0 top-full mt-1 z-30 w-56 bg-surface-2 border border-border rounded-lg px-2.5 py-1.5 text-[11px] text-text-muted shadow-xl">
                查询中…
              </div>
            )}
            {sqCands.length > 0 && (
              <div className={`absolute right-0 top-full mt-1 z-30 w-56 bg-surface-2 border border-border rounded-lg overflow-hidden shadow-xl ${sqBusy ? 'opacity-60' : ''}`}>
                {sqCands.map(c => (
                  <button key={c.code} onClick={() => pickCand(c)}
                    className="w-full flex items-baseline gap-2 px-2.5 py-1.5 text-left hover:bg-surface-3/80 border-b border-border-subtle/50">
                    <span className="text-[12px] text-text-bright truncate">{c.name || c.code}</span>
                    <span className="text-[10px] font-mono text-text-muted shrink-0">{c.code}</span>
                    {c.pct != null && (
                      <span className={`ml-auto text-[11px] font-mono shrink-0 ${pctColor(c.pct)}`}>{c.pct >= 0 ? '+' : ''}{c.pct}%</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
          <span className="text-[10px] text-text-muted whitespace-nowrap shrink-0">{(tab === 'structure' ? structure?.as_of : tab === 'lhb' ? lhbDaily?.date : data?.as_of)?.slice(5, 11) || ''}</span>
          <button onClick={load} title="刷新" className="text-[10.5px] px-1.5 py-0.5 rounded border border-border text-text-dim hover:text-text shrink-0">刷新</button>
        </div>

        {/* 板块筛选 */}
        <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border-subtle flex-wrap">
          {tab === 'watch' && (
            <>
              {['全部', ...(wlMeta.groups || []), '未分组'].map(g => {
                const n = g === '全部'
                  ? ((watch?.rows) || []).filter(r => r.source !== '持仓').length
                  : ((watch?.rows) || []).filter(r => r.source !== '持仓'
                      && (r['分组'] || '') === (g === '未分组' ? '' : g)).length
                if (g === '未分组' && n === 0) return null
                return (
                  <button key={g} onClick={() => setWlGroup(g)}
                    className={`text-[11px] px-2 py-0.5 rounded ${wlGroup === g ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
                    {g}<span className="text-text-muted ml-1">{n}</span>
                  </button>
                )
              })}
              <span className="text-text-muted mx-0.5">·</span>
              <span className="text-[10px] text-text-muted">行右侧 ⠿ 拖动排序 · ↑↓ 微调 · ⋯ 移到分组</span>
            </>
          )}
          {tab === 'structure' && (
            <>
              {['全部', '强势', '蓄势'].map(k => (
                <button key={k} onClick={() => setPhaseFilter(k)}
                  className={`text-[11px] px-2 py-0.5 rounded ${phaseFilter === k ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
                  {k}
                </button>
              ))}
              <span className="text-text-muted mx-0.5">·</span>
            </>
          )}
          {tab === 'changes' && (
            <>
              {['全部', '拉升', '跳水', '竞价'].map(k => (
                <button key={k} onClick={() => { setChGroup(k); setChKind('全部') }}
                  className={`text-[11px] px-2 py-0.5 rounded ${chGroup === k ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
                  {k}
                </button>
              ))}
              <span className="text-text-muted mx-0.5">·</span>
            </>
          )}
          {tab === 'inst' && (
            <>
              {[['net_buy', '机构净买入'], ['net_sell', '机构净卖出']].map(([k, lb]) => (
                <button key={k} onClick={() => setInstSide(k)}
                  className={`text-[11px] px-2 py-0.5 rounded ${instSide === k ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
                  {lb}
                </button>
              ))}
              <span className="text-text-muted mx-0.5">·</span>
            </>
          )}
          {tab === 'earnings' && (
            <>
              {[['预喜', `预喜 ${earnings?.['n_预喜'] ?? ''}`], ['预警', `预警 ${earnings?.['n_预警'] ?? ''}`], ['持仓关联', '持仓关联']].map(([k, lb]) => (
                <button key={k} onClick={() => setEarnSide(k)}
                  className={`text-[11px] px-2 py-0.5 rounded ${earnSide === k ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
                  {lb}
                </button>
              ))}
              <span className="text-text-muted mx-0.5">·</span>
            </>
          )}
          {BOARDS.map(b => (
            <button key={b} onClick={() => setBoard(b)}
              className={`text-[11px] px-2 py-0.5 rounded ${board === b ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
              {b}{b !== '全部' && rawList.length > 0 ? ` ${rawList.filter(r => boardOf(r.code) === b).length}` : ''}
            </button>
          ))}
        </div>

        {/* 事件类型快捷条(异动页): 组内再按具体事件细分, 带当前流内计数 */}
        {tab === 'changes' && (changes?.kinds || []).length > 0 && (
          <div className="no-scrollbar flex gap-1 px-3 py-1.5 border-b border-border-subtle overflow-x-auto whitespace-nowrap shrink-0">
            <button data-chkind="全部" onClick={() => setChKind('全部')}
              className={`text-[10.5px] px-1.5 py-0.5 rounded shrink-0 ${chKind === '全部' ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
              全部
            </button>
            {changes.kinds.map(k => (
              <button key={k.kind} data-chkind={k.kind} onClick={() => setChKind(k.kind)}
                className={`text-[10.5px] px-1.5 py-0.5 rounded shrink-0 ${chKind === k.kind ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
                {k.kind} <span className={k.up ? 'text-bear' : 'text-bull'}>{k.n}</span>
              </button>
            ))}
          </div>
        )}

        {/* 行业快捷条(结构页): 点行业只看该组, 不用往下翻 */}
        {tab === 'structure' && (structure?.groups || []).length > 0 && (
          <div className="no-scrollbar flex gap-1 px-3 py-1.5 border-b border-border-subtle overflow-x-auto whitespace-nowrap shrink-0">
            <button data-ind="全部" onClick={() => setIndFilter('全部')}
              className={`text-[10.5px] px-1.5 py-0.5 rounded shrink-0 ${indFilter === '全部' ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
              全部
            </button>
            {structure.groups.map(g => (
              <button key={g.行业} data-ind={g.行业} onClick={() => setIndFilter(g.行业)}
                className={`text-[10.5px] px-1.5 py-0.5 rounded shrink-0 ${indFilter === g.行业 ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
                {g.行业} {g.n}
              </button>
            ))}
          </div>
        )}

        <div className="flex-1 overflow-y-auto min-h-0">
          {!loading && !err && list.length === 0 && (
            <div className="text-center py-8 text-text-dim text-[12px] px-4 leading-relaxed">
              {tab === 'structure' ? '今天龙头池里没有满足条件的蓄势/强势结构（大波动市里稀缺属正常）'
                : tab === 'lhb' ? (lhbDaily?.note || '近10天无龙虎榜披露数据')
                : tab === 'watch' ? '空——持有的 A 股个股会自动出现在这里(持仓组); 想额外跟踪没持有的票, 在榜单点开右上角 ☆ 加入'
                : tab === 'changes' ? `当前筛选下暂无异动事件${board !== '全部' ? `(${board})` : ''}——每类只保留当天最新60条, 少数派事件可能已滚出窗口`
                : `榜单 top100 里暂无${board}标的`}
            </div>
          )}
          {loading && <div className="text-center py-8 text-text-dim text-[12px]">{tab === 'structure' ? '全市场扫描中…（首扫约1分钟, 之后10分钟缓存秒开）' : '加载榜单…'}</div>}
          {err && <div className="text-center py-8 text-text-dim text-[12px]">榜单源暂不可达（东财抖动），<button onClick={load} className="text-accent">重试</button></div>}
          {!loading && !err && list.map((r, i) => {
            if (r._wh) {
              return (
                <div key={`wh-${r.标题}`}
                  className="px-3 py-1 text-[10px] border-t border-b border-border-subtle flex items-baseline gap-2 sticky top-0 z-10"
                  style={{ background: 'var(--color-surface-2)' }}>
                  <span className="font-semibold text-accent/90">{r.标题}</span>
                  <span className="text-text-muted">{r.说明}</span>
                </div>
              )
            }
            if (r._gheader) {
              return (
                <div key={`g-${r.行业}`}
                  className="px-3 py-1 text-[10px] text-accent/90 border-t border-b border-border-subtle flex items-baseline gap-2 sticky top-0 z-10"
                  style={{ background: 'var(--color-surface-2)' }}>
                  <span className="font-semibold">{r.行业}</span>
                  <span className="text-text-muted">{r.n}只</span>
                  {r.n_强势 > 0 && <span className="text-bear-bright">强势{r.n_强势}</span>}
                  {r.n_蓄势 > 0 && <span className="text-text-dim">蓄势{r.n_蓄势}</span>}
                </div>
              )
            }
            const active = selected?.code === r.code
            const wlCtl = tab === 'watch' && r.source !== '持仓'   // 手动自选才可排序/分组
            return (
              <div key={r._k || r.code} className="relative group/row"
                draggable={wlCtl}
                onDragStart={wlCtl ? (e) => { setDragCode(r.code); e.dataTransfer.effectAllowed = 'move' } : undefined}
                onDragOver={wlCtl ? (e) => e.preventDefault() : undefined}
                onDrop={wlCtl ? (e) => { e.preventDefault(); dropOn(r.code) } : undefined}>
              <button data-row={r.code} onClick={() => setSelected(r)} title={r['AI理由'] || r['上榜原因'] || undefined}
                className={`w-full flex items-center gap-2 px-3 py-1.5 text-left border-b border-border-subtle/60 ${active ? 'bg-accent/15' : 'hover:bg-surface-3/60'}`}>
                <span className="text-[10px] font-mono text-text-muted w-5 shrink-0 text-right">{r._idx ?? i + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="text-[12.5px] text-text-bright truncate">{r.name}</span>
                    {tab === 'structure' && r.phase && (
                      <span className={`text-[8.5px] px-1 rounded shrink-0 ${r.phase === '强势' ? 'bg-bear/15 text-bear-bright' : 'bg-accent/15 text-accent'}`}>{r.phase}</span>
                    )}
                    {tab === 'changes' && (
                      <span className={`text-[8.5px] px-1 rounded shrink-0 ${r.up ? 'bg-bear/15 text-bear-bright' : 'bg-bull/15 text-bull-bright'}`}>{r['类型']}</span>
                    )}
                    {tab === 'changes' && r.n_today >= 3 && (
                      <span className="text-[8.5px] px-1 rounded bg-accent/15 text-accent shrink-0" title="该股今日在当前事件流内反复触发异动">今日{r.n_today}次</span>
                    )}
                    {tab === 'watch' && r.source === '持仓' && (
                      <span className="text-[8.5px] px-1 rounded bg-accent/20 text-accent shrink-0" title="当前持仓, 自动跟踪">持</span>
                    )}
                    {r.is_new && <span className="text-[8.5px] px-1 rounded bg-accent/15 text-accent shrink-0" title="上市前5日无涨跌幅限制">新</span>}
                    {r.is_st && <span className="text-[8.5px] px-1 rounded bg-bear/15 text-bear-bright shrink-0">ST</span>}
                  </span>
                  <span className={`text-[10px] text-text-muted font-mono ${tab === 'lhb' || tab === 'changes' ? 'block truncate' : ''}`}>
                    {boardOf(r.code)} · {r.code} · {tab === 'inst'
                      ? `净买 ${r['机构净买亿']}亿 · 上榜${r['上榜次数']}次`
                      : tab === 'lhb'
                      ? (r['解读'] || r['上榜原因'] || '—')
                      : tab === 'changes'
                      ? (r['描述'] || '—')
                      : (r['行业'] || '—')}
                    {tab === 'watch' && r['业绩预告'] && (
                      <span className="ml-1 px-1 rounded bg-accent/15 text-accent text-[9px] whitespace-nowrap">{r['业绩预告']}</span>
                    )}
                    {tab === 'earnings' && r['持仓关联'] && (
                      <span className="ml-1 px-1 rounded bg-accent/15 text-accent text-[9px]">{r['持仓关联']}</span>
                    )}
                  </span>
                  {tab === 'structure' && (
                    <span className="block text-[9.5px] text-text-dim truncate">
                      {r['标签']}{r['业绩预告'] ? ` · ${r['业绩预告']}` : ''}
                    </span>
                  )}
                  {tab === 'watch' && (
                    <span className="block text-[9.5px] text-text-dim truncate">
                      {r['结构'] || '结构无显著形态'}
                    </span>
                  )}
                </span>
                <span className="text-right shrink-0">
                  {tab === 'changes'
                    ? (r.pct != null
                        ? <span className={`block text-[12.5px] font-mono font-semibold ${pctColor(r.pct)}`}>{r.pct >= 0 ? '+' : ''}{r.pct}%</span>
                        : <span className="block text-[11.5px] font-mono text-text-dim">{r['时间']}</span>)
                    : <span className={`block text-[12.5px] font-mono font-semibold ${pctColor(r.pct)}`}>{r.pct >= 0 ? '+' : ''}{r.pct}%</span>}
                  <span className="block text-[10px] text-text-muted font-mono">
                    {tab === 'structure'
                      ? (r.phase === '强势'
                          ? `距高${r['距60日高%']}%·超额${r['近10日超额%'] >= 0 ? '+' : ''}${r['近10日超额%']}%`
                          : `${r['AI置信'] != null ? `AI${r['AI置信']}·` : ''}横盘${r['横盘日']}日`)
                      : tab === 'inst'
                      ? `${(r['最近上榜'] || '').slice(5)}上榜·至今`
                      : tab === 'changes'
                      ? (r.pct != null ? r['时间'] : '')
                      : tab === 'lhb'
                      ? `净买 ${r['净买额亿'] >= 0 ? '+' : ''}${r['净买额亿']}亿`
                      : tab === 'watch'
                      ? (r.source === '持仓'
                          ? `浮盈${r['浮盈%'] != null ? (r['浮盈%'] >= 0 ? '+' : '') + r['浮盈%'] + '%' : '—'}${r['持有天数'] != null ? ` · 持${r['持有天数']}日` : ''}`
                          : (r['自选以来%'] != null ? `自选(${(r.added_at || '').slice(5)})以来${r['自选以来%'] >= 0 ? '+' : ''}${r['自选以来%']}%` : `${(r.added_at || '').slice(5)}加自选`))
                      : tab === 'earnings'
                      ? `${r['类型']}·${(r['披露日'] || '').slice(5)}披露`
                      : tab === 'by_amount'
                      ? `${r['成交额亿']}亿`
                      : r.is_new ? '新股·无涨停'
                      : (r['涨停占比%'] != null ? `占停${r['涨停占比%']}%` : `量比${r['量比'] ?? '—'}`)}
                  </span>
                </span>
              </button>
              {wlCtl && (
                <span className="absolute right-1.5 top-1/2 -translate-y-1/2 z-10 hidden group-hover/row:flex items-center gap-0.5
                                 bg-surface-2/95 rounded px-1 py-0.5 border border-border-subtle">
                  <span title="按住拖动排序" className="cursor-grab text-text-muted px-0.5 select-none">⠿</span>
                  <button title="上移" onClick={(e) => { e.stopPropagation(); nudge(r.code, -1) }}
                    className="text-[11px] leading-none px-1 text-text-dim hover:text-accent">↑</button>
                  <button title="下移" onClick={(e) => { e.stopPropagation(); nudge(r.code, 1) }}
                    className="text-[11px] leading-none px-1 text-text-dim hover:text-accent">↓</button>
                  <button title="移到分组" onClick={(e) => { e.stopPropagation(); setGrpMenu(r.code) }}
                    className="text-[11px] leading-none px-1 text-text-dim hover:text-accent">⋯</button>
                </span>
              )}
              {grpMenu === r.code && (
                <GroupPicker groups={wlMeta.groups} current={r['分组']}
                  className="absolute right-1.5 top-full -mt-1"
                  onPick={(g) => moveToGroup(r.code, g)} onClose={() => setGrpMenu('')} />
              )}
              </div>
            )
          })}

        </div>

        {tab === 'changes' && (changes?.rows || []).length > 0 && (
          <div className="shrink-0 border-t border-border-subtle px-3 py-1.5">
            {(changes?.hot || []).length > 0 && (
              <div className="text-[9.5px] text-text-muted mb-1">
                今日最活跃:{' '}
                {changes.hot.map(h => (
                  <button key={h.code} onClick={() => setSelected({ code: h.code, name: h.name })}
                    className="text-accent hover:underline mr-2">{h.name} {h.n}次</button>
                ))}
              </div>
            )}
            {(changes?.buckets || []).length > 1 && (() => {
              const bs = changes.buckets
              // 开方比例尺: 开盘档事件量常是盘中档的几十倍, 线性刻度会把其余档压成平线
              const sc = v => Math.sqrt(Math.max(v, 0))
              const maxv = Math.max(...bs.map(b => sc(Math.max(b.up, b.down))), 1)
              const bw = 4, mid = 14, amp = 12
              return (
                <div className="flex items-center gap-2 mb-1">
                  <svg width={bs.length * (bw + 1)} height={28} className="shrink-0">
                    {bs.map((b, i) => (
                      <g key={b.t}>
                        {b.up > 0 && <rect x={i * (bw + 1)} y={mid - sc(b.up) / maxv * amp} width={bw} height={Math.max(sc(b.up) / maxv * amp, 0.5)} fill="#cf5c5c" opacity="0.85"><title>{b.t} 拉升类 {b.up}</title></rect>}
                        {b.down > 0 && <rect x={i * (bw + 1)} y={mid} width={bw} height={Math.max(sc(b.down) / maxv * amp, 0.5)} fill="#5fa86c" opacity="0.85"><title>{b.t} 跳水类 {b.down}</title></rect>}
                      </g>
                    ))}
                  </svg>
                  <span className="text-[9.5px] text-text-muted leading-tight">
                    全天脉搏 {bs[0].t}–{bs[bs.length - 1].t}(5分钟/档)
                    <br />近30分钟 <span className="text-bear">拉升 {changes?.pulse?.['近30分钟拉升类']}</span> vs <span className="text-bull">跳水 {changes?.pulse?.['近30分钟跳水类']}</span>
                  </span>
                </div>
              )
            })()}
            <div className="text-[9.5px] text-text-muted leading-relaxed">
              交易所盘口异动(东财), 盘中60s自动刷新, 收盘后为当日全程 · 竞价类=9:15-9:25集合竞价产物 · 纯客观事件, 非买卖建议
            </div>
          </div>
        )}
        {tab === 'watch' && !loading && (watch?.rows || []).length > 0 && (
          <div className="shrink-0 px-3 py-1.5 border-t border-border-subtle text-[9.5px] text-text-muted leading-relaxed">
            自选=纯跟踪清单（在看但未必持有），副行是当下K线结构形态与业绩预告 · 选中后点右上 ★ 移出 · 纯客观结构描述，非买卖建议
          </div>
        )}
        {tab === 'lhb' && !loading && (
          <div className="shrink-0 px-3 py-1.5 border-t border-border-subtle text-[9.5px] text-text-muted leading-relaxed">
            {lhbDaily?.date || '最新披露日'} 全部上榜个股（涨跌幅偏离/换手/振幅触发交易所披露，盘后约17点起更新）· 按龙虎榜净买额排序，同股多榜单口径取金额最大一条 · 点个股直接弹开该日买卖前五席位 · 纯客观数据，非买卖建议
          </div>
        )}
        {tab === 'inst' && !loading && (
          <div className="shrink-0 px-3 py-1.5 border-t border-border-subtle text-[9.5px] text-text-muted leading-relaxed">
            近{inst?.window_days || 30}天龙虎榜机构专用席位统计（上榜日才披露，抽样非全量）· 主数字=现价较最近上榜日收盘的涨跌：净买入+至今大跌="机构接在山顶"，净卖出+至今大跌="机构跑对了" · 纯客观数字，非买卖建议
          </div>
        )}
        {tab === 'earnings' && !loading && (
          <div className="shrink-0 px-3 py-1.5 border-t border-border-subtle text-[9.5px] text-text-muted leading-relaxed">
            最新报告期（{earnings?.period || '中报'}）业绩预告，全市场已披露 {earnings?.total ?? '—'} 家 · 主数字=归母净利同比变动中值% · 未披露≠业绩差（预告只对大幅变动强制），正式财报以披露日公告为准 · 持仓关联=直持或经由在持ETF前十大成分 · 纯客观数据，非买卖建议
          </div>
        )}
        {tab === 'structure' && !loading && (
          <div className="shrink-0 px-3 py-1.5 border-t border-border-subtle text-[9.5px] text-text-muted leading-relaxed">
            结构观察池（按行业分组，同行业强势多=主线在推进、蓄势多=可能在孕育）：<span className="text-bear-bright">强势</span>=K线没砸下去（距60日高≤12%、近10日无大阴、上行结构未破位、不跑输沪深300）；<span className="text-accent">蓄势</span>=安静横盘基座（AI看图复核）· 带业绩预告凭据 · ↑↓ 翻K线、←→ 切行业 · 结构完好只是当下事实，随时可能被砸 · 纯客观结构，非买卖建议
          </div>
        )}
      </div>

      <div className="flex-1 min-h-0 min-w-0">
        <StockPanel stock={selected} watched={selected ? watchSet.has(selected.code) : false}
          onToggleWatch={toggleWatch} groups={wlMeta.groups} onPickGroup={pickGroup}
          group={selected ? groupOf(selected.code) : ''} />
      </div>
    </div>
  )
}
