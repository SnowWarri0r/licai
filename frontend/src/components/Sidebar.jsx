const ICONS = {
  portfolio: <><rect x="4" y="8" width="16" height="11" rx="2" /><path d="M9 8V6a2 2 0 012-2h2a2 2 0 012 2v2M4 13h16" /></>,
  sector: <><rect x="4" y="4" width="7" height="7" rx="1" /><rect x="13" y="4" width="7" height="7" rx="1" /><rect x="4" y="13" width="7" height="7" rx="1" /><rect x="13" y="13" width="7" height="7" rx="1" /></>,
  rankings: <><path d="M8 4h8v4a4 4 0 01-8 0z" /><path d="M8 5H5v1a3 3 0 003 3M16 5h3v1a3 3 0 01-3 3M10 15h4M9 19.5h6M12 15v4.5" /></>,
  macro: <><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" /><path d="M12 12v9M4 7.5l8 4.5 8-4.5" /></>,
  news: <><rect x="5" y="4" width="14" height="16" rx="2" /><path d="M8 9h8M8 12h8M8 15h5" /></>,
  review: <><path d="M7 4h8l4 4v12H7zM15 4v4h4M10 13h6M10 16.5h4" /></>,
  ask: <><rect x="4" y="5" width="16" height="12" rx="2" /><path d="M8 21l4-4M8 9h8M8 12h5" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" /></>,
  cashflow: <><path d="M3 7h18v10H3z" /><circle cx="12" cy="12" r="2.5" /><path d="M7 12h.01M17 12h.01" /></>,
  performance: <><path d="M4 19h16" /><path d="M6 16V9M11 16V5M16 16v-4" /></>,
  allocation: <><circle cx="12" cy="12" r="8" /><path d="M12 4v8l7 3" /></>,
  open: <><circle cx="12" cy="12" r="8" /><path d="M12 8v4l3 2" /><path d="M12 2v2M22 12h-2" /></>,
  capital: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>,
}

// 顶层按「我的钱 / 看市场」分组 —— 边界是"跟我的钱有关 vs 无关",
// 这条界最硬, 不容易再退化成杂物抽屉。group 为 null 的是不归属两大类的独立项。
// 同时是 App.jsx 里合法 view key 的唯一来源(VIEWS 从这里派生) —— 两份手抄的列表
// 一旦漂移是静默的: NAV 有而 VIEWS 无 → 点了跳回持仓; VIEWS 有而 NAV 无 → 一个进不去的页。
// 组件文件里多导出一个常量, 代价是本文件在 dev 下退回整页刷新(拿不到 fast refresh);
// 为此把导航表挪去单独模块并不划算 —— 它和侧栏是同一件事, 分开反而更容易漂。
// eslint-disable-next-line react-refresh/only-export-components
export const NAV = [
  { group: '我的', items: [
    { key: 'portfolio',   label: '持仓' },
    { key: 'cashflow',    label: '资金·现金流' },
    { key: 'performance', label: '绩效·基准' },
    { key: 'allocation',  label: '配置建议' },
    { key: 'review',      label: '复盘' },
  ] },
  { group: '市场', items: [
    { key: 'open',     label: '开盘·情绪' },
    { key: 'sector',   label: '板块' },
    { key: 'rankings', label: '榜单' },
    { key: 'capital',  label: '资金·机构' },
    { key: 'macro',    label: '宏观' },
    { key: 'news',     label: '资讯' },
  ] },
  { group: null, items: [
    { key: 'ask',      label: '问问市场' },
    { key: 'settings', label: '设置' },
  ] },
]

export default function Sidebar({ active, onNav, open, onToggle }) {
  return (
    <aside className={`shrink-0 border-r border-border bg-surface/60 backdrop-blur-xl flex flex-col transition-[width] duration-200 ${open ? 'w-44' : 'w-14'}`}>
      <button onClick={onToggle} title={open ? '收起' : '展开'}
        className="h-11 flex items-center gap-2 px-4 text-text-dim hover:text-text border-b border-border-subtle">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
          <path d="M4 6h16M4 12h16M4 18h16" />
        </svg>
        {open && <span className="text-[12px]">收起</span>}
      </button>

      <nav className="flex-1 py-2 overflow-y-auto">
        {NAV.map((sec, si) => (
          <div key={sec.group || `misc-${si}`}>
            {/* 展开时显示分组标题; 折叠时只剩图标, 文字无处安放 → 退化成一条分隔线。
                分隔线的职责是"隔开两组", 首组之前无组可隔 —— 所以 si > 0 才画,
                否则会在收起按钮的 border-b 下面多出一条什么都没隔开的发丝线。 */}
            {sec.group
              ? (open
                  ? <div className="px-4 pt-3 pb-1 text-[10px] tracking-wider text-text-muted">{sec.group}</div>
                  : si > 0 && <div className="mx-3 my-2 border-t border-border-subtle" />)
              : si > 0 && <div className="mx-3 my-2 border-t border-border-subtle" />}
            {sec.items.map(n => {
              const on = active === n.key
              return (
                <button key={n.key} onClick={() => onNav(n.key)} title={n.label}
                  className={`w-full flex items-center gap-3 px-4 h-11 text-left transition-colors
                    ${on ? 'text-accent bg-accent/12 border-r-2 border-accent' : 'text-text-dim hover:text-text hover:bg-surface-3/50 border-r-2 border-transparent'}`}>
                  <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                    {ICONS[n.key]}
                  </svg>
                  {open && <span className="text-[13px] font-medium">{n.label}</span>}
                </button>
              )
            })}
          </div>
        ))}
      </nav>
    </aside>
  )
}
