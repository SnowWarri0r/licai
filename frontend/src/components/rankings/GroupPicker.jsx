import { useState, useEffect, useRef } from 'react'
import { WL_DEFAULT } from './shared'

// 分组下拉(多选)。观察池行内的 ⋯ 与 K线面板的「分组」共用同一份 —— 两处各写一遍必然漂开。
// groups=已有真实分组名, current=该票当前所属分组数组, onSet(下一份数组) 整套落库。
// 为什么给全集而不是"增删某一个": 和后端一个口径, 连点/重发都是同一个结果。
export default function GroupPicker({ groups, current, onSet, onClose, className = '' }) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const rootRef = useRef(null)
  const mine = current || []

  // 点菜单外面就收起(以前只有 onMouseLeave: 鼠标不划出去就一直挂着, 只能再点一次按钮
  // 才关 —— 而正在输入新组名时连 mouseleave 都被禁掉了, 更关不上)。
  // 触发按钮自己带 data-grp-trigger: 点它要让它自己 toggle, 这里不能抢着先关(否则
  // 关掉又被 toggle 打开, 表现成点了没反应)。
  useEffect(() => {
    const onDown = (e) => {
      if (rootRef.current?.contains(e.target)) return
      if (e.target.closest?.('[data-grp-trigger]')) return
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
  const submit = () => {
    const g = name.trim().slice(0, 20)
    if (!g) return
    setCreating(false); setName('')
    if (!mine.includes(g)) onSet([...mine, g])
  }
  return (
    <div ref={rootRef} onClick={(e) => e.stopPropagation()}
      className={`z-30 w-44 bg-surface-2 border border-border rounded-lg shadow-xl py-1 ${className}`}>
      <div className="px-2.5 py-1 text-[10px] text-text-muted">所属分组 · 可多选</div>
      {/* 「自选」也是一个可勾的组, 排在最前(它是默认组)。勾了「金矿」不会把「自选」顶掉,
          两个可以同时勾上 —— 这就是"同时在自选和某个分组里"。 */}
      {[WL_DEFAULT, ...(groups || []).filter(g => g !== WL_DEFAULT)].map(g => {
        const on = mine.includes(g)
        const last = on && mine.length === 1        // 最后一个组不给取消: 取消了它哪都不在
        return (
          // 勾完不关菜单: 多选要能连着点几个, 点一下就收起来等于还是单选
          <button key={g} disabled={last}
            title={last ? '至少留一个分组; 想彻底不跟了就点标题栏的 ★ 移出观察池' : ''}
            onClick={(e) => { e.stopPropagation(); onSet(on ? mine.filter(x => x !== g) : [...mine, g]) }}
            className={`w-full text-left px-2.5 py-1 text-[11px] hover:bg-surface-3/80
                        disabled:hover:bg-transparent ${on ? 'text-accent' : 'text-text-dim'}`}>
            <span className="inline-block w-3">{on ? '✓' : ''}</span>{g}
            {g === WL_DEFAULT && <span className="text-text-muted ml-1 text-[9px]">默认</span>}
          </button>
        )
      })}
      {creating ? (
        // 不用 window.prompt: Chrome 在用户勾过「阻止此页面创建更多对话框」之后,
        // prompt() 会直接返回 null 且不弹窗 —— 表现就是"点了没反应"。内联输入没这问题,
        // 也能进自动化验证。
        <div className="flex items-center gap-1 px-2 py-1.5 border-t border-border-subtle mt-1">
          <input autoFocus value={name} maxLength={20} placeholder="新分组名"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter') submit()
              if (e.key === 'Escape') { setCreating(false); setName('') }
            }}
            className="flex-1 min-w-0 bg-surface-3 border border-border-subtle rounded px-1.5 py-0.5
                       text-[11px] text-text outline-none focus:border-accent/50" />
          <button onClick={submit} disabled={!name.trim()}
            className="text-[11px] px-1.5 py-0.5 rounded text-accent hover:bg-accent/10 disabled:opacity-40">
            建
          </button>
        </div>
      ) : (
        <button onClick={(e) => { e.stopPropagation(); setCreating(true) }}
          className="w-full text-left px-2.5 py-1 text-[11px] text-accent hover:bg-surface-3/80
                     border-t border-border-subtle mt-1">
          + 新建分组…
        </button>
      )}
    </div>
  )
}
