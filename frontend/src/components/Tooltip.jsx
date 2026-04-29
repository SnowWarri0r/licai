import { useState, useRef, useEffect, useLayoutEffect } from 'react'
import { createPortal } from 'react-dom'

/**
 * 轻量 Tooltip — 渲染到 document.body 顶层，逃逸父级 overflow:hidden / transform 限制.
 * - hover ~150ms 后显示, 离开 ~80ms 后隐藏
 * - 自动 flip: 顶部空间不够翻到底；底部空间不够翻到顶；左右同理
 * - 自动 clamp: 横向超出视口边缘自动拉回
 * - 默认 maxWidth 360px; string 内容自动 nowrap
 *
 * Props:
 *   - content: JSX | string
 *   - position: 'top' | 'bottom' | 'left' | 'right' (默认 top)
 *   - delay: hover 显示延迟 ms (默认 150)
 *   - maxWidth: 内容最大宽度 (默认 360px)
 */
export default function Tooltip({
  children, content, position = 'top', delay = 150, maxWidth = 360,
}) {
  const [shown, setShown] = useState(false)
  const [coords, setCoords] = useState({ top: -9999, left: -9999, ready: false })
  const triggerRef = useRef(null)
  const tipRef = useRef(null)
  const showTimer = useRef()
  const hideTimer = useRef()

  useEffect(() => () => {
    clearTimeout(showTimer.current)
    clearTimeout(hideTimer.current)
  }, [])

  // 显示后用 trigger + tip 实际尺寸算坐标 + 自动 flip + clamp
  useLayoutEffect(() => {
    if (!shown || !triggerRef.current || !tipRef.current) {
      setCoords({ top: -9999, left: -9999, ready: false })
      return
    }
    const tr = triggerRef.current.getBoundingClientRect()
    const tip = tipRef.current.getBoundingClientRect()
    const margin = 6
    const pad = 8
    const vw = window.innerWidth
    const vh = window.innerHeight

    const compute = (pos) => {
      switch (pos) {
        case 'top':
          return {
            top: tr.top - tip.height - margin,
            left: tr.left + tr.width / 2 - tip.width / 2,
            fit: tr.top - tip.height - margin >= pad,
            flip: 'bottom',
          }
        case 'bottom':
          return {
            top: tr.bottom + margin,
            left: tr.left + tr.width / 2 - tip.width / 2,
            fit: tr.bottom + tip.height + margin <= vh - pad,
            flip: 'top',
          }
        case 'left':
          return {
            top: tr.top + tr.height / 2 - tip.height / 2,
            left: tr.left - tip.width - margin,
            fit: tr.left - tip.width - margin >= pad,
            flip: 'right',
          }
        case 'right':
          return {
            top: tr.top + tr.height / 2 - tip.height / 2,
            left: tr.right + margin,
            fit: tr.right + tip.width + margin <= vw - pad,
            flip: 'left',
          }
      }
    }

    let p = compute(position)
    if (!p.fit) {
      const flipped = compute(p.flip)
      if (flipped.fit) p = flipped
    }

    // viewport clamp
    const top = Math.max(pad, Math.min(p.top, vh - tip.height - pad))
    const left = Math.max(pad, Math.min(p.left, vw - tip.width - pad))
    setCoords({ top, left, ready: true })
  }, [shown, position, content])

  const onEnter = () => {
    clearTimeout(hideTimer.current)
    showTimer.current = setTimeout(() => setShown(true), delay)
  }
  const onLeave = () => {
    clearTimeout(showTimer.current)
    hideTimer.current = setTimeout(() => setShown(false), 80)
  }

  return (
    <span ref={triggerRef} className="inline-flex" onMouseEnter={onEnter} onMouseLeave={onLeave}>
      {children}
      {shown && content != null && createPortal(
        <span
          ref={tipRef}
          className="fixed z-[9999] px-3 py-2 rounded-md text-[11px] leading-relaxed pointer-events-none"
          style={{
            top: coords.top,
            left: coords.left,
            background: 'var(--color-surface-3)',
            border: '1px solid var(--color-border-med)',
            color: 'var(--color-text-bright)',
            boxShadow: '0 6px 18px rgba(0,0,0,.5)',
            maxWidth,
            width: 'max-content',
            whiteSpace: typeof content === 'string' ? 'nowrap' : 'normal',
            opacity: coords.ready ? 0 : 0,
            animation: coords.ready ? 'tip-in 0.12s ease-out forwards' : 'none',
          }}>
          {content}
        </span>,
        document.body
      )}
    </span>
  )
}
