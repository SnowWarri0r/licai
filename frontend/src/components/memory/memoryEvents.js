import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'

// 记忆页里批了/删了一条, 侧栏角标要跟着变。用一个 window 事件, 不必把计数提到 App 的状态里层层传。
const EV = 'licai:memory-changed'

export const memoryChanged = () => window.dispatchEvent(new Event(EV))

// 侧栏「AI 记忆」角标: 待确认画像 + 待复核画像 + 待审规则。启动时、每 10 分钟、页内有改动时各取一次。
export function useMemoryBadge() {
  const [n, setN] = useState(0)
  useEffect(() => {
    const load = () => fetchJSON('/api/settings/memory/badge')
      .then(d => setN((d.pending || 0) + (d.stale || 0) + (d.rules || 0))).catch(() => {})
    load()
    const t = setInterval(load, 10 * 60 * 1000)
    window.addEventListener(EV, load)
    return () => { clearInterval(t); window.removeEventListener(EV, load) }
  }, [])
  return n
}
