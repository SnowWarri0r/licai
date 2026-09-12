import { useState, useEffect, useRef } from 'react'
import { fetchJSON, prefetchJSON } from '../hooks/useApi'
import RotationBoard from './RotationBoard'
import GroupPicker from './rankings/GroupPicker'
import StockPanel from './rankings/StockPanel'
import { pctColor, boardOf, WL_ALL, WL_HELD, WL_DEFAULT, WL_LAST_KEY } from './rankings/shared'

const TABS = [
  { key: 'watch', label: '观察池' },
  { key: 'changes', label: '异动' },
  { key: 'gainers', label: '涨幅' },
  { key: 'by_amount', label: '成交额' },
  { key: 'lhb', label: '龙虎榜' },
  { key: 'structure', label: '蓄势/强势' },
  { key: 'inst', label: '机构' },
  { key: 'earnings', label: '业绩' },
  { key: 'hotrank', label: '资金热度' },
]
// 全部 9 个页签都是同一形态: 概念条 / 满宽页签行 / 板块筛选 / 左列表 + 右 K 线。
// 首版曾把资金热度与股池做成"整卡"页签(isCard: 满宽左栏、藏掉筛选行/查股/StockPanel),
// 结果切页签时整个页面骨架在跳, 且 10 个页签挤在 420px 左栏被截断。现在股池是【市场】
// 下的独立页, 资金热度是标准列表页签, 这里不再有第二种形态。

const BOARDS = ['全部', '主板', '创业板', '科创板', '北交所']

export default function Rankings() {
  const [tab, setTab] = useState(() => {
    // deep-link: #rankings?t=inst 直达指定页签(旧 coiled/unbroken 併入 structure;
    // 旧 t=pools 整页搬去了【市场·股池】, 在 App 的 view 初始化那里改派, 到不了这儿)
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
  const [hotRank, setHotRank] = useState(null)           // 东财资金人气榜(资金热度)
  const [lhbDaily, setLhbDaily] = useState(null)         // 最新披露日龙虎榜全榜单
  const [watch, setWatch] = useState(null)               // 自选池(全量视图)
  const [watchSet, setWatchSet] = useState(new Set())    // 自选代码集(☆按钮状态)
  const [wlMeta, setWlMeta] = useState({ groups: [], byCode: {} })  // 分组标签(轻端点, 各页签通用)
  const [changes, setChanges] = useState(null)           // 盘口异动事件流
  const [chGroup, setChGroup] = useState('全部')          // 异动组: 全部/拉升/跳水/竞价
  const [chKind, setChKind] = useState('全部')            // 异动组内按事件类型细分
  // 榜单是 100 只票的清单, 看不出轮动。按概念/行业聚成堆, 点一下只看那条线。
  const [tagKind, setTagKind] = useState('概念')
  const [hotTag, setHotTag] = useState('')
  // 顶部轮动条的开合记在本地: 有人一直要看, 有人只想要那一行摘要, 别每次进来都重置
  const [rotOpen, setRotOpen] = useState(() => localStorage.getItem('licai:rot-open') !== '0')
  useEffect(() => { localStorage.setItem('licai:rot-open', rotOpen ? '1' : '0') }, [rotOpen])
  const [trend, setTrend] = useState(null)      // 各条线近几日的资金曲线(接力/退潮)
  const [sq, setSq] = useState('')                       // 自由查股输入
  const [sqCands, setSqCands] = useState([])             // 搜索候选
  const sqTimer = useRef(null)
  const sqSeq = useRef(0)                                 // 请求序号, 丢弃乱序返回
  const [sqBusy, setSqBusy] = useState(false)
  // 进来落在上次看的那一组: 分组是顶层概念, 每次都被拽回「全部」不合手。
  // 没记录时落「全部」而不是「自选」—— 票都归了组的话「自选」是空的, 一进来就白屏。
  const [wlGroup, setWlGroup] = useState(() => {
    try { return localStorage.getItem(WL_LAST_KEY) || WL_ALL } catch { return WL_ALL }
  })
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
      : tab === 'hotrank'
      ? fetchJSON('/api/market/hot-rank?top=100').then(d => { if (d.error) setErr(true); else setHotRank(d) })
      : tab === 'watch'
      ? fetchJSON('/api/market/watchlist').then(d => { if (d.error) setErr(true); else setWatch(d) })
      : tab === 'changes'
      ? fetchJSON(`/api/market/changes?group=${encodeURIComponent(chGroup)}`).then(d => { if (d.error) setErr(true); else setChanges(d) })
      : fetchJSON('/api/market/rankings?limit=100').then(d => { if (d.error) setErr(true); else setData(d) })
    req.catch(() => setErr(true)).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])
  // 各条线近几日的资金曲线: 一天的快照答不了"谁在接力谁在退潮"。跟榜单分开取, 慢一点不挡榜。
  useEffect(() => {
    if (tab !== 'gainers' && tab !== 'by_amount') return
    let alive = true
    fetchJSON(`/api/market/concept-trend?scope=${tab}&kind=${encodeURIComponent(tagKind)}&days=5`)
      .then(d => { if (alive && !d?.error) setTrend(d) }).catch(() => {})
    return () => { alive = false }
  }, [tab, tagKind])
  // 自选代码集(☆按钮状态), 轻端点
  useEffect(() => {
    reloadWlMeta()
  }, [])
  // 分组标签走轻端点: 看 K 线时 watch(全量视图)还没加载, 分组下拉不能依赖它
  const reloadWlMeta = () => fetchJSON('/api/market/watchlist?lite=1').then(d => {
    setWatchSet(new Set(d?.codes || []))
    setWlMeta({ groups: d?.groups || [], byCode: d?.by_code || {} })
  }).catch(() => {})
  const groupsOf = (code) => wlMeta.byCode[code] || []      // 一票多组; 至少有一个(默认「自选」)

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

  // 重排。scope 随视图: 选中某个分组写组内位次(每组各一份), 只有「全部」写全局位次。
  // 「自选」也是真实分组, 所以它同样有自己那套组内位次。
  const wlScope = () => (wlIsRealGroup() ? 'group' : 'global')
  const pickWlGroup = (g) => {
    setWlGroup(g)
    try { localStorage.setItem(WL_LAST_KEY, g) } catch { /* 隐私模式忽略 */ }
  }
  // 记住的那个组可能已经被删空(改分组后组里没票了, chip 就不再渲染) —— 此时界面会
  // 一片空白且没有任何 chip 高亮, 退回「全部」。
  useEffect(() => {
    if (!wlIsRealGroup() || wlGroup === WL_DEFAULT) return   // 「自选」的 chip 永远在, 空了也不跳
    if (!wlMeta.groups.length) return                 // 还没拉到分组表, 先别动
    if (!wlMeta.groups.includes(wlGroup)) pickWlGroup(WL_ALL)
  }, [wlMeta.groups])   // eslint-disable-line react-hooks/exhaustive-deps

  // 当前 chip 是不是一个真实分组。只有「全部」「持仓」是视图 —— 「自选」是真实分组
  const wlIsRealGroup = () => ![WL_ALL, WL_HELD].includes(wlGroup)
  const wlScopeGroup = () => (wlIsRealGroup() ? wlGroup : '')
  // 一只票在本视图里的位次: 选中某个分组时取该组的组内位次(每组各一份), 否则全局位次
  const wlPosOf = (r) => (wlIsRealGroup()
    ? ((r.group_orders || {})[wlGroup] || 0)
    : (r.sort_order || 0))
  // 这一行属不属于当前 chip
  const wlInView = (r) => (wlGroup === WL_ALL ? true : (r['分组'] || []).includes(wlGroup))

  // 当前"显示顺序"的代码序列 —— 必须与界面一致, 不能用 watch.rows 的数组原始顺序:
  // 乐观更新只改行上的位次字段、不重排数组, 拿原始顺序算会导致第二次 ↑↓ 写回和上次
  // 相同的结果, 表现为"点一次之后就没用了"。
  const wlVisibleCodes = () => {
    const rs = ((watch?.rows) || []).filter(r => r.source !== '持仓' && wlInView(r))
    return [...rs].sort((a, b) => wlPosOf(a) - wlPosOf(b)).map(r => r.code)
  }

  const commitOrder = async (codes) => {
    const scope = wlScope()
    const group = wlScopeGroup()
    // 乐观更新: 只改本视图对应的位次(另一套不动), 列表随即按它重排
    setWatch(prev => {
      if (!prev?.rows) return prev
      const pos = new Map(codes.map((c, i) => [c, i]))
      const rows = prev.rows.map(r => {
        if (!pos.has(r.code)) return r
        if (scope === 'global') return { ...r, sort_order: pos.get(r.code) }
        // 拖进组 = 入组(不动它在别的组里的位置)
        return { ...r,
          '分组': (r['分组'] || []).includes(group) ? r['分组'] : [...(r['分组'] || []), group],
          group_orders: { ...(r.group_orders || {}), [group]: pos.get(r.code) } }
      })
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

  // 整套覆盖这只票的分组。乐观更新在前(勾一下要立刻见效), 落库失败由 reloadWlMeta 校正。
  const setGroups = async (code, groups) => {
    // 一个组都不剩就退回默认组「自选」(后端也这么归一): 否则这只票哪个分组视图都进不去,
    // 只能在「全部」里看到, 找都找不着。
    const next = [...new Set(groups || [])]
    if (!next.length) next.push(WL_DEFAULT)
    // 下拉是多选、勾完不关, 菜单里的 ✓ 得马上跟上 —— 所以先改本地 byCode
    setWlMeta(m => ({ ...m, byCode: { ...m.byCode, [code]: next } }))
    // 改分组只影响两处: 面板上的胶囊 与 观察池那一行的分组标签。一律 load() 会把当前
    // 榜单的滚动位置打回顶部(翻到半截去归组, 一归就得重新滚下来); 一律作废缓存又会让
    // "切到观察池"变成十几秒 —— 全量视图要给每只票取行情/结构/业绩预告, 实测冷启
    // 18.7s。所以就地改那一行, 只有缓存里确实没有这一行时才作废。
    let hit = false
    setWatch(w => {
      if (!w || !Array.isArray(w.rows)) return w
      const cur = w.rows.find(r => r.code === code)
      hit = !!cur
      if (!hit) return w
      // 组内位次也得跟着改: 新进的组本地也排到该组末尾(和后端一个口径), 退出的组把它的
      // 位次删掉。不改的话新组的位次是 undefined → 当 0 → 这只票先跳到组首, 等下次重拉
      // 才弹回末尾。
      const orders = { ...(cur.group_orders || {}) }
      Object.keys(orders).forEach(g => { if (!next.includes(g)) delete orders[g] })
      next.filter(g => !(g in orders)).forEach(g => {
        const seen = w.rows.map(r => (r.group_orders || {})[g]).filter(v => v != null)
        orders[g] = (seen.length ? Math.max(...seen) : -1) + 1
      })
      return { ...w,
        rows: w.rows.map(r => (r.code === code ? { ...r, '分组': next, group_orders: orders } : r)) }
    })
    try {
      await fetchJSON(`/api/market/watchlist/${code}/groups`, {
        method: 'PUT', body: JSON.stringify({ groups: next }),
      })
    } catch { /* 失败也照样刷新一遍状态, 由 reloadWlMeta 校正 */ }
    reloadWlMeta()      // 新建的组要进 chip 条 / 下拉; 落库结果也在这一步对齐
    if (!hit) {
      // 刚加进池子的票, 造不出完整一行(缺行情/结构), 只能让它下次重新拉
      setWatch(null)
      if (tabRef.current === 'watch') load()
    }
  }

  // 选组即入池: 分组挂在观察池的票上, 没有"不在池子里的分组"。所以在 K线面板上选分组时,
  // 没加进池子的先加, 再打标签 —— 用户不必先想"加自选"这一步。
  const pickGroups = async (code, gs) => {
    if (!watchSet.has(code)) {
      try {
        await fetchJSON(`/api/market/watchlist/${code}`, { method: 'POST' })
        setWatchSet(prev => new Set(prev).add(code))
      } catch { /* 加失败下面 setGroups 也会失败, 统一由 reloadWlMeta 校正 */ }
    }
    await setGroups(code, gs)
  }

  const toggleWatch = (stock) => {
    const code = stock.code
    const on = watchSet.has(code)
    fetchJSON(`/api/market/watchlist/${code}`, { method: on ? 'DELETE' : 'POST' })
      .then(() => {
        setWatchSet(prev => { const s = new Set(prev); on ? s.delete(code) : s.add(code); return s })
        setWlMeta(m => {
          const byCode = { ...m.byCode }
          // 加进来默认落「自选」组(后端 add_watchlist 也写这一行)
          if (on) delete byCode[code]; else byCode[code] = byCode[code] || [WL_DEFAULT]
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
  // 切页签时懒加载各自那份数据(服务端有缓存, 之后秒回)。
  // 涨幅/成交额也要在列: 它们共用 /api/market/rankings 那一份 data, 而挂载时的 load()
  // 只拉了 deep-link 落地的那个页签 —— 漏掉这两个的话, 从别的页签深链进来再点「涨幅」
  // 会一直是空列表, 得手按一次「刷新」才出来。所以条件是 data 没拿到, 而非某个专属 state。
  useEffect(() => { if ((tab === 'structure' && !structure) || (tab === 'inst' && !inst) || (tab === 'earnings' && !earnings) || (tab === 'lhb' && !lhbDaily) || (tab === 'watch' && !watch) || (tab === 'hotrank' && !hotRank) || ((tab === 'gainers' || tab === 'by_amount') && !data)) load() }, [tab])   // eslint-disable-line react-hooks/exhaustive-deps
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
        // 没有可翻的行就把 ↑↓ 交还给浏览器: preventDefault 必须跟"确实换了一行"绑在一起。
        // 列表为空(还在加载 / 筛到空 / 数据源挂了)时无条件 preventDefault 会既不翻行、
        // 又吞掉浏览器对超过一屏内容的原生滚动 —— 滚轮和 PageUp 还能用, 所以很难被发现。
        const arr = listRef.current
        if (!arr?.length) return
        setSelected(prev => {
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
        // 持仓是虚拟组(现取, 不参与分组), 选中它时只看持仓
        let manual = wlGroup === WL_HELD ? [] : rs.filter(r => r.source !== '持仓' && wlInView(r))
        // 排序键随视图切换: 选中某个分组用该组的组内位次, 否则用全局位次 —— 各组一份,
        // 所以在一个组里调顺序既不打乱「全部」, 也不打乱这只票在别的组里的位置。
        // 必须在本地排: ↑↓/拖动是乐观更新(只改行上的位次字段), 否则要等下次拉取才动。
        manual = [...manual].sort((a, b) => wlPosOf(a) - wlPosOf(b))
        // 显式编号: 行号渲染兜底用的是数组下标 i+1, 而数组里混着分组标题行, 标题占掉
        // index 0 会让第一只票显示成 2。这里按"只数着真实行"自己编。
        let sn = 0
        const merged = []
        if (held.length && (wlGroup === WL_ALL || wlGroup === WL_HELD)) {
          merged.push({ _wh: true, 标题: `持仓 ${held.length}`, 说明: '现取, 清仓即消失' })
          merged.push(...held.map(r => ({ ...r, _idx: ++sn })))
        }
        if (manual.length) {
          merged.push({ _wh: true,
            标题: `${wlGroup === WL_ALL ? '观察池' : wlGroup} ${manual.length}`,
            说明: '在看未必持有; 拖 ⠿ 或点 ↑↓ 调顺序' })
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
    // 资金热度: 接口返回的 pct 键名与标准行渲染器已经一致, 另有两件事要在这里归一 ——
    // _idx 取榜上真实名次: 兜底是数组下标, 一加板块筛选前导序号就重编成 1..N, 而这是
    //   人气榜, 名次就是它的主键(筛到创业板显示 1-5, 实际这几只在全榜隔得很远)。
    // mine → source='持仓': 接口算好了"这只票在不在我的持仓里"(标出持仓是这个端点存在
    //   的理由), 归到观察池那一列用的同一个字段上, 两个页签共用同一个「持」标记。
    : tab === 'hotrank' ? ((hotRank?.items) || []).map(r => ({ ...r, _idx: r.rank, source: r.mine ? '持仓' : undefined }))
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
  // 今日热堆: 同一张榜按概念/行业聚起来(后端算, 已并同义名), 点一下把榜筛到那条线上
  const groups = (data?.groups?.[tab] || {})[tagKind === '概念' ? 'concepts' : 'industries'] || []
  const hotGroup = groups.find(g => g.name === hotTag)
  const hotCodes = hotGroup ? new Set(hotGroup.codes || []) : null
  // 行内只标"进了今日前几堆"的概念 —— 一只票挂三四十个概念(实测中天科技 47 个), 全列等于没列。
  // 固定取概念堆(不跟着上面的概念/行业开关变): 切到行业视图时行内提示不该跟着消失
  const hotNames = new Set(((data?.groups?.[tab] || {}).concepts || [])
    .slice(0, 10).flatMap(g => [g.name, ...(g.aliases || [])]))
  const hotOf = (r) => (r['概念'] || []).filter(c => hotNames.has(c)).slice(0, 3)
  // 副行第三段(板块 · 代码 · ???): 各页签取各自那条信息。取不到就连分隔点一起省掉 ——
  // 资金热度那份数据里没有「行业」字段, 原来落到 '—' 兜底, 100 行整整齐齐印一列
  // 「主板 · 600127 · —」, 等于标了一个这份数据里不存在的字段(和已修掉的「量比—」同一类)。
  const metaTail = (r) => (tab === 'inst'
    ? `净买 ${r['机构净买亿']}亿 · 上榜${r['上榜次数']}次`
    : tab === 'lhb' ? (r['解读'] || r['上榜原因'] || '—')
    : tab === 'changes' ? (r['描述'] || '—')
    : (r['行业'] || ''))
  const trendOf = (name) => (trend?.rows || []).find(t => t.name === name)
  const list = tab === 'structure' ? structList
    : (() => {
        let rs = board === '全部' ? rawList : rawList.filter(r => boardOf(r.code) === board)
        if (hotCodes) rs = rs.filter(r => hotCodes.has(r.code))
        return rs
      })()
  listRef.current = list.filter(r => !r._gheader && !r._wh)
  indsRef.current = ['全部', ...(structure?.groups || []).map(g => g.行业)]
  chKindsRef.current = ['全部', ...(changes?.kinds || []).map(k => k.kind)]
  tabRef.current = tab

  return (
    // 高度由外层给(flex-1 吃掉滚动区剩下的), 不再自己算 100vh 减多少 —— 那个减数是估的
    <div className="bg-surface-2 border border-border rounded-xl overflow-hidden flex flex-col flex-1 min-h-[480px]">
      {/* 轮动条: 常驻顶部, 可收起。原来它占着右栏"没选股票"那片空白, 一点股票就被 K 线顶掉,
          想边看某只票边盯轮动只能来回切。摆到顶上之后两件事同时看得见。 */}
      {(tab === 'gainers' || tab === 'by_amount') && groups.length > 0 && (
        <RotationBoard scope={tab} kind={tagKind} groups={groups} trend={trend} hotTag={hotTag}
          collapsed={!rotOpen} onToggle={() => setRotOpen(v => !v)}
          onPickTag={setHotTag} onKindChange={(k) => { setTagKind(k); setHotTag('') }}
          onPickStock={(s) => setSelected(s)} />
      )}
      {/* 页签行: 恒定满宽, 摆在左右两栏之外。原先它住在 420px 左栏里 —— 8 个页签已经勉强,
          加到 10 个直接被截断(实测 scrollWidth 519 / clientWidth 232, 只看得见前 5 个)。
          移出来之后它的位置和宽度在所有页签间恒定, 切页签不再有骨架跳动。查股/取数日期/刷新
          跟着留在这一行(现在有地方放了); 板块筛选留在左栏 —— 它筛的是左栏那份列表。 */}
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
        {/* 取数日期按页签取各自数据源的。资金热度接口不返回日期, 这里给空串而不是落到
            兜底的 data?.as_of —— 那是 /api/market/rankings 的取数时间, 跟这张榜没关系,
            印上去等于给用户一个来自别的数据集的假日期。 */}
        <span className="text-[10px] text-text-muted whitespace-nowrap shrink-0">{(tab === 'structure' ? structure?.as_of : tab === 'lhb' ? lhbDaily?.date : tab === 'hotrank' ? '' : data?.as_of)?.slice(5, 11) || ''}</span>
        <button onClick={load} title="刷新" className="text-[10.5px] px-1.5 py-0.5 rounded border border-border text-text-dim hover:text-text shrink-0">刷新</button>
      </div>

      <div className="flex flex-col lg:flex-row flex-1 min-h-0">
      <div className="flex flex-col min-h-0 lg:w-[420px] shrink-0 border-b lg:border-b-0 lg:border-r border-border">
        {/* 板块筛选 */}
        <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border-subtle flex-wrap">
          {tab === 'watch' && (
            <>
              {/* 「自选」钉在末尾(它是默认组), 所以要从中间那段里去掉; 外面再套一层 Set
                  兜底。为什么较真: chip 的 key 就是组名, 名字重了 React 报 duplicate key,
                  而表现不是"多出一个 chip", 是渲染出一批永不刷新的幽灵 chip(计数停在 0、
                  点了也不动) —— 实测就这么出过一次。 */}
              {[...new Set([WL_ALL, WL_HELD,
                ...(wlMeta.groups || []).filter(g => g !== WL_DEFAULT), WL_DEFAULT])].map(g => {
                const rows = (watch?.rows) || []
                const manual = rows.filter(r => r.source !== '持仓')
                // 各组计数不去重: 一票多组时它在几个组里就各计一次, 加起来会超过「全部」
                // —— 这是对的, 「全部」才是去重后的池子大小。
                const n = g === WL_ALL ? rows.length
                  : g === WL_HELD ? rows.filter(r => r.source === '持仓').length
                  : manual.filter(r => (r['分组'] || []).includes(g)).length
                // 持仓是虚拟组, 空了就不占位; 自选是默认组, 始终显示(新加的票落这儿)
                if (g === WL_HELD && n === 0) return null
                return (
                  <button key={g} onClick={() => pickWlGroup(g)}
                    title={g === WL_HELD ? '现取, 清仓即消失'
                      : g === WL_DEFAULT ? '默认组: 新加进来的票落这儿。它跟别的组平级, 一只票可以同时在这儿和别的组里'
                      : g === WL_ALL ? '持仓 + 全部分组(一票多组只算一次)' : ''}
                    className={`text-[11px] px-2 py-0.5 rounded ${wlGroup === g ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
                    {g}<span className="text-text-muted ml-1">{n}</span>
                  </button>
                )
              })}
              <span className="text-text-muted mx-0.5">·</span>
              <span className="text-[10px] text-text-muted">行右侧 ⠿ 拖动排序 · ↑↓ 微调 · ⋯ 归组(可多选)</span>
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
            const mTail = metaTail(r)
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
                    {/* 「持」= 这一行是我当前持仓。判据只看行上的 source, 不看页签:
                        观察池的持仓组、资金热度榜上命中持仓的行走同一个标记, 两处长一样。 */}
                    {r.source === '持仓' && (
                      <span className="text-[8.5px] px-1 rounded bg-accent/20 text-accent shrink-0" title="当前持仓, 自动跟踪">持</span>
                    )}
                    {r.is_new && <span className="text-[8.5px] px-1 rounded bg-accent/15 text-accent shrink-0" title="上市前5日无涨跌幅限制">新</span>}
                    {r.is_st && <span className="text-[8.5px] px-1 rounded bg-bear/15 text-bear-bright shrink-0">ST</span>}
                  </span>
                  <span className={`text-[10px] text-text-muted font-mono ${tab === 'lhb' || tab === 'changes' ? 'block truncate' : ''}`}>
                    {boardOf(r.code)} · {r.code}{mTail ? ` · ${mTail}` : ''}
                    {tab === 'watch' && r['业绩预告'] && (
                      <span className="ml-1 px-1 rounded bg-accent/15 text-accent text-[9px] whitespace-nowrap">{r['业绩预告']}</span>
                    )}
                    {/* 所属分组: 一票多组时得看得见它还在哪些组里, 否则在某个组里看到一只票
                        根本不知道它同时也在别的组。当前正在看的那个组不再重复显示。 */}
                    {tab === 'watch' && (r['分组'] || []).filter(g => g !== wlGroup).length > 0 && (
                      <span className="ml-1 text-[9px] text-text-dim whitespace-nowrap"
                        title={`所属分组: ${(r['分组'] || []).join(' · ')}`}>
                        {(r['分组'] || []).filter(g => g !== wlGroup).join('·')}
                      </span>
                    )}
                    {tab === 'earnings' && r['持仓关联'] && (
                      <span className="ml-1 px-1 rounded bg-accent/15 text-accent text-[9px]">{r['持仓关联']}</span>
                    )}
                    {/* 它在今天哪条线上 —— 只标进了前几堆的概念, 这才是"为什么它在榜上" */}
                    {(tab === 'gainers' || tab === 'by_amount') && hotOf(r).length > 0 && (
                      <span className="ml-1 text-[9px] text-accent/85 whitespace-nowrap"
                        title={`所属概念: ${(r['概念'] || []).join(' · ')}`}>
                        {hotOf(r).join('·')}
                      </span>
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
                      // 资金热度接口只给 rank/price/pct, 没有量比/涨停占比。不给它一条自己的
                      // 分支就会落到最后那个兜底上, 100 行整整齐齐印一列「量比—」——
                      // 标了一个这份数据里根本不存在的字段。改印它确实有的现价。
                      : tab === 'hotrank'
                      ? `现价 ${r.price ?? '—'}`
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
                  <button title="归组(可多选)" data-grp-trigger
                    onClick={(e) => { e.stopPropagation(); setGrpMenu(m => (m === r.code ? '' : r.code)) }}
                    className="text-[11px] leading-none px-1 text-text-dim hover:text-accent">⋯</button>
                </span>
              )}
              {grpMenu === r.code && (
                // current 取 wlMeta(勾选后就地更新的那份), 不取行上的 r['分组'] ——
                // 行数据来自全量视图, 刚勾的那一下要等它重排才反映, 菜单里的 ✓ 会滞后
                <GroupPicker groups={wlMeta.groups} current={groupsOf(r.code)}
                  className="absolute right-1.5 top-full -mt-1"
                  onSet={(gs) => setGroups(r.code, gs)} onClose={() => setGrpMenu('')} />
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
            观察池=按分组管理的跟踪清单（在看但未必持有，一只票可同时属于多个组，「自选」是新加时默认落的组、与其他组平级），副行是当下K线结构形态与业绩预告 · 选中后点右上 ★ 移出 · 纯客观结构描述，非买卖建议
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
          onToggleWatch={toggleWatch} groups={wlMeta.groups} onSetGroups={pickGroups}
          myGroups={selected ? groupsOf(selected.code) : []} />
      </div>
      </div>
    </div>
  )
}
