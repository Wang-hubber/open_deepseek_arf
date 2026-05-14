import { ref } from 'vue'

export function useWebSocket() {
  const connected = ref(false)
  let ws: WebSocket | null = null
  let reconnectDelay = 2000
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let destroyed = false
  let onReconnect: (() => void) | null = null

  function connect(reconnectCallback?: () => void) {
    if (reconnectCallback) onReconnect = reconnectCallback
    if (destroyed) return
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${window.location.host}/ws`

    try {
      ws = new WebSocket(url)
      ws.onopen = () => {
        const wasConnected = connected.value
        connected.value = true
        reconnectDelay = 2000
        if (wasConnected && onReconnect) onReconnect()
      }
      ws.onclose = () => {
        connected.value = false
        ws = null
        if (!destroyed) {
          reconnectTimer = setTimeout(connect, reconnectDelay)
          reconnectDelay = Math.min(reconnectDelay * 1.5, 30000)
        }
      }
      ws.onerror = () => {
        if (ws) ws.close()
      }
    } catch {
      if (!destroyed) {
        reconnectTimer = setTimeout(connect, reconnectDelay)
      }
    }
  }

  function disconnect() {
    destroyed = true
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (ws) {
      ws.onclose = null
      ws.close()
      ws = null
    }
    connected.value = false
  }

  return { connected, connect, disconnect }
}
