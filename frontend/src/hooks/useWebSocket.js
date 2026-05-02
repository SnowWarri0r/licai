import { useEffect, useRef, useCallback } from 'react'

export function useWebSocket(onMessage) {
  const wsRef = useRef(null)
  const reconnectDelay = useRef(1000)
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  const connect = useCallback(() => {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${location.host}/ws`)

    ws.onopen = () => { reconnectDelay.current = 1000 }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type !== 'heartbeat' && msg.type !== 'pong') {
          onMessageRef.current?.(msg)
        }
      } catch {}
    }

    ws.onclose = () => {
      setTimeout(connect, reconnectDelay.current)
      reconnectDelay.current = Math.min(reconnectDelay.current * 2, 30000)
    }

    ws.onerror = () => ws.close()

    // Keepalive
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, 25000)

    wsRef.current = ws
    return () => { clearInterval(ping) }
  }, [])

  useEffect(() => {
    const cleanup = connect()
    return () => {
      cleanup?.()
      wsRef.current?.close()
    }
  }, [connect])
}
