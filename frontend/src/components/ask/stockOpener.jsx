import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'
import StockKlineModal from '../StockKlineModal'

// 任何地方都能打开个股详情弹窗: openStock(code, name)。
// 问答答案里的"名称(代码)"和嵌入的图都走这里 —— 原来只有持仓页/榜单各自用本地 state 开弹窗,
// 答案里提到的票只能自己去别的页面搜。
const EVENT = 'licai:open-stock'

export function openStock(code, name = '') {
  if (!code) return
  window.dispatchEvent(new CustomEvent(EVENT, { detail: { code: String(code), name } }))
}

// 挂在 App 根上一次。先用代码和名字立刻开弹窗, 再补持仓信息(成本线/买卖点/场内 ETF 的资产流水)。
export function GlobalStockModal() {
  const [holding, setHolding] = useState(null)

  useEffect(() => {
    const onOpen = (e) => {
      const { code, name } = e.detail || {}
      setHolding({ stock_code: code, stock_name: name || code, cost_price: 0, shares: 0 })
      fetchJSON(`/api/market/stock-card/${encodeURIComponent(code)}`)
        .then(card => setHolding(h => (h && h.stock_code === code ? { ...h, ...card, stock_code: code } : h)))
        .catch(() => {})
    }
    window.addEventListener(EVENT, onOpen)
    return () => window.removeEventListener(EVENT, onOpen)
  }, [])

  if (!holding) return null
  // key: 换股票时整只重建, 弹窗内部按 code 取数的 effect 不会串到上一只。
  // asset_id 后到不用重建: 弹窗取流水的 effect 依赖里有 assetId, 会自己重取。
  return <StockKlineModal key={holding.stock_code} holding={holding}
    onClose={() => setHolding(null)} />
}
