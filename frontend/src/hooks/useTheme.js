import { useCallback, useEffect, useState } from 'react'

// 主题偏好持久化 key。index.html 里有一段内联脚本在 React 挂载前同步读它, 避免刷新
// 时先闪一下默认暗色再切亮色。
const STORAGE_KEY = 'licai-theme'
const THEME_COLOR = { dark: '#06080f', light: '#f6f1e8' }

function readTheme() {
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
}

function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t)
  try { localStorage.setItem(STORAGE_KEY, t) } catch { /* 隐私模式等场景忽略 */ }
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', THEME_COLOR[t])
}

// 全局主题状态: 多个组件各自调用也没关系, 通过监听 <html data-theme> 的变化互相同步,
// 不需要 Context/Provider。K线图等 canvas 图表库拿不到 CSS 变量, 也读这个 theme 值
// 自己挑一份解析好的颜色。
export function useTheme() {
  const [theme, setThemeState] = useState(readTheme)

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeState(readTheme()))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])

  const setTheme = useCallback((t) => { applyTheme(t); setThemeState(t) }, [])
  const toggleTheme = useCallback(() => setTheme(readTheme() === 'dark' ? 'light' : 'dark'), [setTheme])

  return { theme, setTheme, toggleTheme }
}
