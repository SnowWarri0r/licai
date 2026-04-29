export default function Header({ marketOpen, lastUpdate, onRefresh, onSettings }) {
  return (
    <header className="sticky top-0 z-50 border-b border-border bg-surface/80 backdrop-blur-xl">
      <div className="max-w-[1440px] mx-auto px-4 h-12 flex items-center justify-between">
        <div className="flex items-center gap-3">
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
