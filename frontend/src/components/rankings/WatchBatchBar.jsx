import { useState, useEffect, useRef } from 'react'
import { WL_DEFAULT } from './shared'

// 观察池批量编辑的底栏: 已选计数 + 全选/清空 + 四个动作。
// curGroup = 当前视图的真实分组名(「全部」视图为空串) —— 「移到」「移出本组」只在
// 看某个组时有意义, 「全部」视图下不出现。
export default function WatchBatchBar({ n, total, groups, curGroup, busy, msg,
  onAll, onClear, onApply, onDone }) {
  const [menu, setMenu] = useState('')          // '' | 'add' | 'move'
  // 移出观察池要点两次: 第一次记下当时的已选数, 勾选一变确认态就自动失效
  const [armN, setArmN] = useState(-1)
  const arm = armN === n
  const none = n === 0 || busy
  const btn = 'text-[11px] px-2 py-0.5 rounded border border-border-subtle hover:border-accent/50 hover:text-accent disabled:opacity-40 disabled:hover:border-border-subtle disabled:hover:text-inherit'
  return (
    <div className="shrink-0 border-t border-border-subtle px-3 py-1.5 relative">
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[11px] text-text-bright">已选 {n}</span>
        <button onClick={n === total ? onClear : onAll} disabled={busy || !total}
          className="text-[11px] text-accent hover:underline disabled:opacity-40">
          {n === total && total > 0 ? '清空' : `全选本页 ${total}`}
        </button>
        <span className="flex-1" />
        <button className={btn} disabled={none} onClick={() => setMenu(m => (m === 'add' ? '' : 'add'))}
          data-batch-trigger>加入分组</button>
        {curGroup && (
          <button className={btn} disabled={none} onClick={() => setMenu(m => (m === 'move' ? '' : 'move'))}
            data-batch-trigger>移到分组</button>
        )}
        {curGroup && (
          <button className={btn} disabled={none} onClick={() => onApply('remove_groups', [curGroup])}
            title={`退出「${curGroup}」; 一个组都不剩的票落回「${WL_DEFAULT}」`}>移出本组</button>
        )}
        <button disabled={none}
          onClick={() => { if (arm) { setArmN(-1); onApply('remove', []) } else setArmN(n) }}
          className={`${btn} ${arm ? 'border-bear/60 text-bear-bright' : ''}`}>
          {arm ? `确认移出 ${n} 只` : '移出观察池'}
        </button>
        <button onClick={onDone} className="text-[11px] px-2 py-0.5 rounded bg-accent/15 text-accent">完成</button>
      </div>
      {msg && <div className="text-[10px] text-text-muted mt-1">{msg}</div>}
      {menu && (
        <BatchGroupMenu groups={groups} exclude={menu === 'move' ? curGroup : ''}
          title={menu === 'add' ? `把 ${n} 只加入(原分组保留)` : `把 ${n} 只从「${curGroup}」移到`}
          onPick={(g) => { setMenu(''); onApply(menu === 'add' ? 'add_groups' : 'move', [g]) }}
          onClose={() => setMenu('')} />
      )}
    </div>
  )
}

function BatchGroupMenu({ groups, exclude, title, onPick, onClose }) {
  const [name, setName] = useState('')
  const rootRef = useRef(null)
  useEffect(() => {
    const onDown = (e) => {
      if (rootRef.current?.contains(e.target)) return
      if (e.target.closest?.('[data-batch-trigger]')) return
      onClose()
    }
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('pointerdown', onDown, true)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown, true)
      document.removeEventListener('keydown', onKey)
    }
  }, [onClose])
  const list = [WL_DEFAULT, ...(groups || []).filter(g => g !== WL_DEFAULT)].filter(g => g !== exclude)
  const submit = () => { const g = name.trim().slice(0, 20); if (g && g !== exclude) onPick(g) }
  return (
    <div ref={rootRef}
      className="absolute right-3 bottom-full mb-1 z-30 w-48 bg-surface-2 border border-border rounded-lg shadow-xl py-1">
      <div className="px-2.5 py-1 text-[10px] text-text-muted">{title}</div>
      <div className="max-h-56 overflow-y-auto">
        {list.map(g => (
          <button key={g} onClick={() => onPick(g)}
            className="w-full text-left px-2.5 py-1 text-[11px] text-text-dim hover:text-accent hover:bg-surface-3/80">
            {g}{g === WL_DEFAULT && <span className="text-text-muted ml-1 text-[9px]">默认</span>}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-1 px-2 py-1.5 border-t border-border-subtle mt-1">
        <input value={name} maxLength={20} placeholder="新分组名"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Enter') submit() }}
          className="flex-1 min-w-0 bg-surface-3 border border-border-subtle rounded px-1.5 py-0.5
                     text-[11px] text-text outline-none focus:border-accent/50" />
        <button onClick={submit} disabled={!name.trim() || name.trim() === exclude}
          className="text-[11px] px-1.5 py-0.5 rounded text-accent hover:bg-accent/10 disabled:opacity-40">
          建
        </button>
      </div>
    </div>
  )
}
