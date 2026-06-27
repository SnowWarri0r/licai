// 统一骨架屏: 板块加载时占位, 高度接近加载后内容, 避免塌成一行字导致布局跳动。
// rows 控制行数(粗调高度); label 在右上角给一句"在做什么"; bare=true 去掉卡片外框(用于已在 section 内嵌套时)。
export default function SkeletonCard({ rows = 4, title = true, label = '', bare = false, className = '' }) {
  const body = (
    <>
      {title && (
        <div className="flex items-center gap-2 mb-3">
          <div className="skel h-3.5 w-24" />
          {label && <span className="text-[10.5px] text-text-muted ml-auto">{label}</span>}
        </div>
      )}
      <div className="space-y-2.5">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="skel h-3" style={{ width: `${88 - (i % 3) * 16}%` }} />
        ))}
      </div>
    </>
  )
  if (bare) return <div className={`py-1 ${className}`}>{body}</div>
  return (
    <div className={`rounded-xl border border-border bg-surface-2 p-4 md:p-5 ${className}`}
      style={{ animation: 'fade-up 0.3s ease-out' }}>
      {body}
    </div>
  )
}
