import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'

// 图片预览: 点图在应用内全屏遮罩预览, 不再跳新页。点遮罩/图/×、Esc 都可关闭。
export default function ImageZoom({ src, alt = '', className = '', imgProps = {} }) {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <>
      <img src={src} alt={alt} loading="lazy" onClick={() => setOpen(true)}
        className={`cursor-zoom-in ${className}`} {...imgProps} />
      {open && createPortal(
        <div className="fixed inset-0 z-[300] flex items-center justify-center bg-black/85 backdrop-blur-sm p-4 sm:p-8 cursor-zoom-out"
          onClick={() => setOpen(false)}>
          <img src={src} alt={alt} className="max-w-full max-h-full object-contain rounded-lg shadow-2xl" />
          <button onClick={() => setOpen(false)} aria-label="关闭"
            className="absolute top-3 right-4 text-white/70 hover:text-white text-[30px] leading-none">×</button>
        </div>,
        document.body
      )}
    </>
  )
}
