import { useCallback, useState } from 'react'

// 持仓页的弹窗/内联表单开关。原来是 9 个各管一个的 useState:
//   addTarget / editAsset / addLotAsset / reduceAsset / cashAdjustAsset /
//   actionsAsset / klineHolding / thesisTarget / qualityTarget
// 它们的形状完全一样 —— "空 = 关, 有值 = 开且值就是要操作的那一行"。9 份同构
// 状态摊在主组件里, 每加一个弹窗就要再摊一份, 而且"同时只该开一个"这件事
// 没有任何地方表达, 靠调用方自觉。
//
// 收成一张 key → payload 的表: show/hide 两个动作管全部, 新增弹窗不用动 state。
export function usePortfolioModals() {
  const [open, setOpen] = useState({})

  // payload 默认 true, 给「不需要带数据、只要开着」的弹窗用(比如添加菜单)
  const show = useCallback((key, payload = true) => {
    setOpen(o => ({ ...o, [key]: payload }))
  }, [])

  const hide = useCallback((key) => {
    setOpen(o => {
      if (!(key in o)) return o        // 已经是关的: 返回原对象, 不触发重渲染
      const next = { ...o }
      delete next[key]
      return next
    })
  }, [])

  return { open, show, hide }
}
