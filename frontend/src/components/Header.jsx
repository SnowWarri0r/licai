export default function Header({ marketOpen, lastUpdate, onRefresh, onSettings, theme, onToggleTheme }) {
  return (
    <header className="sticky top-0 z-50 border-b border-border bg-surface/80 backdrop-blur-xl">
      <div className="max-w-[1440px] mx-auto px-3 md:px-4 h-12 flex items-center justify-between">
        <div className="flex items-center gap-2 md:gap-3">
          <h1 className="text-[15px] font-semibold tracking-tight text-accent">
            理财助手
          </h1>
          <span className={`
            inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium tracking-wide
            ${marketOpen
              ? 'bg-bull/15 text-bull-bright border border-bull-border'
              : 'bg-surface-3 text-text-dim border border-border'}
          `}>
            <span className={`w-1.5 h-1.5 rounded-full ${marketOpen ? 'bg-bull animate-pulse' : 'bg-text-muted'}`} />
            {marketOpen ? '交易中' : '已收盘'}
          </span>
        </div>

        <div className="flex items-center gap-2 text-[12px] text-text-dim">
          {lastUpdate && (
            <span className="hidden sm:inline font-mono">
              {lastUpdate.toLocaleTimeString('zh-CN')}
            </span>
          )}
          <button onClick={onToggleTheme} title={theme === 'light' ? '切换为暗色' : '切换为浅色'}
            className="w-7 h-7 flex items-center justify-center rounded-md border border-border text-text-dim hover:text-text hover:border-text-muted transition-colors cursor-pointer">
            {theme === 'light' ? (
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
                <path d="M20.7 15.3A8.5 8.5 0 0 1 8.7 3.3a8.5 8.5 0 1 0 12 12Z" />
              </svg>
            )}
          </button>
          <button onClick={onRefresh}
            className="px-3 py-1 rounded-md border border-border text-text-dim hover:text-text hover:border-text-muted transition-colors cursor-pointer">
            刷新
          </button>
          <button onClick={onSettings}
            className="px-3 py-1 rounded-md border border-border text-text-dim hover:text-text hover:border-text-muted transition-colors cursor-pointer">
            设置
          </button>
        </div>
      </div>
    </header>
  )
}
