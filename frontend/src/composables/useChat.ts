import { ref } from 'vue'
import { useSessionStore } from '@/stores/sessions'
import { useChatStore } from '@/stores/chat'
import type { SSEEvent, ChatMessage } from '@/types'

interface ActiveMessage {
  appendReasoning(reasoning: string): void
  appendText(chunk: string): void
  addToolCard(name: string, args: string, id: string): ToolController | null
  finalize(errorText?: string | null, newHistory?: ChatMessage[]): void
  getToolController(id: string): ToolController | null
}

interface ToolController {
  complete(result: string): void
  fail(error: string): void
}

export function useChat() {
  const chatStore = useChatStore()
  const sessionStore = useSessionStore()

  // Reactive streaming state for the active message
  const streamingText = ref('')
  const streamingReasoning = ref('')
  const toolCalls = ref<{ id: string; name: string; args: string; status: string; result?: string; error?: string }[]>([])
  const isStreaming = ref(false)
  const streamError = ref('')
  let abortController: AbortController | null = null
  let streamReader: ReadableStreamDefaultReader<Uint8Array> | null = null

  // Callbacks that the component sets
  let onToolCall: ((name: string, args: string, id: string) => void) | null = null
  let onToolResult: ((id: string, result: string, tool: string) => void) | null = null
  let onDone: ((history?: ChatMessage[]) => void) | null = null

  function setCallbacks(callbacks: {
    onToolCall?: (name: string, args: string, id: string) => void
    onToolResult?: (id: string, result: string, tool: string) => void
    onDone?: (history?: ChatMessage[]) => void
  }) {
    if (callbacks.onToolCall) onToolCall = callbacks.onToolCall
    if (callbacks.onToolResult) onToolResult = callbacks.onToolResult
    if (callbacks.onDone) onDone = callbacks.onDone
  }

  async function sendMessage(text: string) {
    if (isStreaming.value) return
    isStreaming.value = true
    streamError.value = ''
    streamingText.value = ''
    streamingReasoning.value = ''
    toolCalls.value = []

    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }

      abortController = new AbortController()

      const res = await fetch('/api/chat', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          message: text,
          history: chatStore.chatHistory,
          stream: true,
          new_session: sessionStore.isPendingNewSession(),
        }),
        signal: abortController.signal,
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Request failed' }))
        throw new Error(err.detail || `HTTP ${res.status}`)
      }

      streamReader = res.body!.getReader()
      await readStream(streamReader)
    } catch (e: any) {
      if (e.name === 'AbortError') {
        // User stopped generation — keep partial content, no error
      } else {
        streamError.value = e.message || 'Unknown error'
      }
    } finally {
      isStreaming.value = false
      abortController = null
      streamReader = null
    }
  }

  function abort() {
    abortController?.abort()
    streamReader?.cancel()
  }

  async function readStream(reader: ReadableStreamDefaultReader<Uint8Array>) {
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        if (buffer.trim().startsWith('data: ')) {
          try { handleEvent(JSON.parse(buffer.trim().slice(6))) } catch { /* ignore */ }
        }
        break
      }

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()!

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try { handleEvent(JSON.parse(line.slice(6))) } catch { /* ignore */ }
        }
      }
    }
  }

  function handleEvent(evt: SSEEvent) {
    if (evt.type === 'chunk') {
      if (evt.reasoning) streamingReasoning.value += evt.reasoning
      if (evt.content) streamingText.value += evt.content
    } else if (evt.type === 'tool_call') {
      const name = evt.name || evt.tool || 'unknown'
      const args = evt.arguments || ''
      const id = evt.id
      toolCalls.value.push({ id, name, args, status: 'executing' })
      if (onToolCall) onToolCall(name, args, id)
    } else if (evt.type === 'tool_result') {
      const id = evt.id
      const tc = toolCalls.value.find(t => t.id === id)
      if (tc) {
        let isError = false
        let errorMsg = ''
        try {
          const p = JSON.parse(evt.result)
          if (p && p.error) { isError = true; errorMsg = p.error }
        } catch { /* not JSON */ }
        if (isError) {
          tc.status = 'failed'
          tc.error = errorMsg
        } else {
          tc.status = 'completed'
          tc.result = evt.result
        }
      }
      if (onToolResult) onToolResult(id, evt.result, evt.tool || '')
    } else if (evt.type === 'done') {
      isStreaming.value = false
      chatStore.setHistory(evt.history || [])
      // Confirm lazy session creation — replace frontend placeholder with real session
      if (sessionStore.isPendingNewSession() && evt.session_id) {
        sessionStore.confirmNewSession(evt.session_id, evt.title || evt.session_id)
      }
      if (evt.title && sessionStore.activeSession) {
        sessionStore.activeSession.title = evt.title
      }
      if (onDone) onDone(evt.history)
      sessionStore.loadSessions()
    } else if (evt.type === 'error') {
      isStreaming.value = false
      streamError.value = evt.detail || 'Unknown error'
    }
  }

  return {
    streamingText,
    streamingReasoning,
    toolCalls,
    isStreaming,
    streamError,
    sendMessage,
    abort,
    setCallbacks,
  }
}
