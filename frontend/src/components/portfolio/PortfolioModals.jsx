import StockKlineModal from '../StockKlineModal'
import { QualityModal } from './QualityModal'
import { ThesisModal } from './ThesisModal'
import { AssetActionsModal } from './actions'
import { TYPE_META, TYPE_ORDER } from './constants'
import { AddAShareForm } from './forms/AddAShareForm'
import { AddAssetForm } from './forms/AddAssetForm'
import { EditAssetRow } from './forms/EditAssetRow'
import { AddLotRow, CashAdjustRow, ReduceLotRow } from './forms/lots'

// 持仓页的全部弹窗与内联表单, 一处收口。开关状态来自 usePortfolioModals 的
// open/show/hide, 这里只负责"开着的那个渲染成什么"。
//
// 放在主组件里的时候这段是 75 行、11 个各带 onDone/onCancel 的条件块, 夹在
// 工具栏和列表之间 —— 读主组件时它是纯噪音, 而改弹窗时又要在 800 行里翻。
export function PortfolioModals({
  open, show, hide, brokers,
  onAdd,                 // A股 新增完成: 通知外层刷新 holdings
  onAssetsChanged,       // 资产类改动: 重载 /api/assets
  onRealizedChanged,     // 影响已实现的改动(加减仓/流水)
  onThesisSaved,         // 买入逻辑存好: 重拉有逻辑的代码集合
}) {
  const addTarget = open.add
  return (
    <>
      {addTarget === 'menu' && (
        <div className="px-3 md:px-6 py-3 border-b border-border bg-surface-2/60 flex flex-wrap gap-2 items-center">
          <span className="text-[11px] text-text-dim mr-2">添加到哪一类?</span>
          {[
            ['A:A', 'A股'],
            ['A:HK', '港股'],
            ['A:US', '美股'],
            ...TYPE_ORDER.filter(t => t !== 'A').map(t => [t, TYPE_META[t].label]),
          ].map(([target, label]) => (
            <button key={target} onClick={() => show('add', target)}
              className="px-3 py-1 rounded border border-border-med text-[12px] text-text hover:border-accent hover:text-accent transition-colors cursor-pointer">
              + {label}
            </button>
          ))}
          <button onClick={() => hide('add')}
            className="ml-auto text-[11px] text-text-dim hover:text-text cursor-pointer">取消</button>
        </div>
      )}

      {String(addTarget || '').startsWith('A:') && (
        <AddAShareForm initialMarket={addTarget.split(':')[1]}
          brokers={brokers}
          onDone={() => { hide('add'); onAdd?.() }} onCancel={() => hide('add')} />
      )}
      {(addTarget === 'F' || addTarget === 'C' || addTarget === 'R' || addTarget === 'W' || addTarget === 'M') && (
        <AddAssetForm typeKey={addTarget}
          brokers={brokers}
          onDone={() => { hide('add'); onAssetsChanged() }}
          onCancel={() => hide('add')} />
      )}

      {open.edit && (
        <EditAssetRow asset={open.edit}
          brokers={brokers}
          onDone={() => { hide('edit'); onAssetsChanged() }}
          onCancel={() => hide('edit')} />
      )}

      {open.addLot && (
        <AddLotRow asset={open.addLot}
          brokers={brokers}
          onDone={() => { hide('addLot'); onRealizedChanged() }}
          onCancel={() => hide('addLot')} />
      )}

      {open.reduce && (
        <ReduceLotRow asset={open.reduce}
          onDone={() => { hide('reduce'); onRealizedChanged() }}
          onCancel={() => hide('reduce')} />
      )}

      {open.cashAdjust && (
        <CashAdjustRow asset={open.cashAdjust}
          onDone={() => { hide('cashAdjust'); onAssetsChanged() }}
          onCancel={() => hide('cashAdjust')} />
      )}

      {open.actions && (
        <AssetActionsModal asset={open.actions}
          onClose={() => hide('actions')}
          onChanged={() => onRealizedChanged()} />
      )}

      {open.kline && (
        <StockKlineModal holding={open.kline} onClose={() => hide('kline')} />
      )}
      {open.thesis && (
        <ThesisModal row={open.thesis} onClose={() => hide('thesis')}
          onSaved={() => { onThesisSaved(); hide('thesis') }} />
      )}
      {open.quality && (
        <QualityModal row={open.quality} onClose={() => hide('quality')} />
      )}
    </>
  )
}
