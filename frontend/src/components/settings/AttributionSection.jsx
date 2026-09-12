/* 开源许可署名。
   K 线用 TradingView Lightweight Charts (Apache-2.0)。它的许可要求「在用户可见的
   页面上给出署名 + tradingview.com 链接」—— 图上那枚角标只是满足要求的一种方式,
   我们把角标关了(layout.attributionLogo = false), 所以署名必须落在这里。
   要动这段先看 ProKline.jsx 里的说明。 */
export function AttributionSection() {
  return (
    <div>
      <div className="text-[12px] text-text-dim mb-2 tracking-wide">开源许可</div>
      <p className="text-[11.5px] text-text-muted leading-relaxed">
        K 线图表由{' '}
        <a href="https://www.tradingview.com/lightweight-charts/" target="_blank" rel="noreferrer"
          className="text-accent hover:underline">TradingView Lightweight Charts</a>
        {' '}提供（Apache-2.0）。图表技术与金融数据可视化方案来自{' '}
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer"
          className="text-accent hover:underline">TradingView</a>。
      </p>
      <p className="text-[11.5px] text-text-muted leading-relaxed mt-1">
        本项目以 AGPL-3.0 开源：{' '}
        <a href="https://github.com/SnowWarri0r/licai" target="_blank" rel="noreferrer"
          className="text-accent hover:underline">github.com/SnowWarri0r/licai</a>
      </p>
    </div>
  )
}
