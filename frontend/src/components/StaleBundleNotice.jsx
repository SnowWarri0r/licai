import { useState, useEffect } from 'react'

/** 前端更新了但这个页面还跑着旧 JS 时，提示一下。
 *
 * 为什么要有: static/assets 是从磁盘直接伺服的, 重新构建立刻生效, 但**已经开着的标签页
 * 不会自己换 bundle**。于是旧 JS 对着新 API 跑, 症状千奇百怪(实测过一次: 观察池的分组
 * chip 变成一排永不刷新的幽灵, 计数停在 0)。这种"看着像新的其实是旧的"最难查 —— 界面
 * 没有任何地方告诉你版本对不上。
 *
 * 判据: import.meta.url 就是当前这份 bundle 的真实文件名, 拿它跟服务端 index.html 里
 * 现在引用的那个比。不同就提示, 不自动刷 —— 自动刷会打断正在输入的提问。
 */
const MINE = (import.meta.url || '').split('/').pop()      // index-xxxx.js
const POLL_MS = 60000

export default function StaleBundleNotice() {
  const [stale, setStale] = useState('')

  useEffect(() => {
    if (!MINE) return                                       // dev 模式(非 hash 文件名)不管
    let alive = true
    const check = async () => {
      try {
        const r = await fetch('/api/app-version', { cache: 'no-store' })
        const d = await r.json()
        // 服务端拿不到文件名时给空串, 这种情况别乱报。
        // 又变回一致(比如构建被回滚了)就把提示收掉, 不留个点了没用的按钮在角上。
        if (alive && d?.bundle) setStale(d.bundle === MINE ? '' : d.bundle)
      } catch { /* 离线/后端重启中: 下一轮再看 */ }
    }
    check()
    const t = setInterval(check, POLL_MS)
    // 标签页切来切去时也查一次: 后台标签的定时器被浏览器降频到分钟级, 只靠轮询会让
    // "切回来还是旧版"多停留一会儿。不筛 visible/hidden —— 多一次 GET 而已, 少一个分支
    document.addEventListener('visibilitychange', check)
    return () => { alive = false; clearInterval(t); document.removeEventListener('visibilitychange', check) }
  }, [])

  if (!stale) return null
  return (
    <button onClick={() => window.location.reload()}
      title={`当前 ${MINE} → 最新 ${stale}`}
      className="fixed bottom-3 right-3 z-50 text-[11px] px-2.5 py-1.5 rounded-lg
                 bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 shadow-xl">
      前端已更新, 这个页面还是旧版 · 点此刷新
    </button>
  )
}
