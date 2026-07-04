const ICONS = {
  dashboard: <><path d="M4 4v16h16" /><path d="M7 14l3-3 3 2 4-6" /><path d="M15.5 7H18v2.5" /></>,
  portfolio: <><rect x="4" y="8" width="16" height="11" rx="2" /><path d="M9 8V6a2 2 0 012-2h2a2 2 0 012 2v2M4 13h16" /></>,
  rankings: <><path d="M8 4h8v4a4 4 0 01-8 0z" /><path d="M8 5H5v1a3 3 0 003 3M16 5h3v1a3 3 0 01-3 3M10 15h4M9 19.5h6M12 15v4.5" /></>,
  ask: <><rect x="4" y="5" width="16" height="12" rx="2" /><path d="M8 21l4-4M8 9h8M8 12h5" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" /></>,
}

const NAV = [
  { key: 'dashboard', label: '仪表盘' },
  { key: 'portfolio', label: '持仓' },
  { key: 'rankings', label: '榜单' },
  { key: 'ask', label: '问问市场' },
  { key: 'settings', label: '设置' },
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

      <nav className="flex-1 py-2">
        {NAV.map(n => {
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
      </nav>
    </aside>
  )
}
