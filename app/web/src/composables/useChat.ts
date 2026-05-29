import { ref } from 'vue'
import { useChatStore } from '@/stores/chat'
import type { SSEEvent, ChatMessage } from '@/types'

export function useChat() {
  const chatStore = useChatStore()

  // Reactive streaming state for the active message
  const streamingText = ref('')
  const streamingReasoning = ref('')
  const toolCalls = ref<{ id: string; name: string; args: string; status: string; result?: string; error?: string }[]>([])
  const isStreaming = ref(false)
  const streamError = ref('')
  const pendingApproval = ref<{ decision_id: string; tool_name: string; params: Record<string, unknown> } | null>(null)
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

  async function approve(decisionId: string, approved: boolean) {
    pendingApproval.value = null
    await fetch('/api/chat/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision_id: decisionId, approved }),
    })
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
        if (evt.result === 'success') {
          tc.status = 'completed'
          tc.result = (evt as any).content || evt.result
        } else {
          tc.status = 'failed'
          tc.error = (evt as any).error_msg || evt.result
        }
      }
      if (onToolResult) onToolResult(id, (evt as any).content || evt.result, evt.tool || '')
    } else if (evt.type === 'approval_required') {
      pendingApproval.value = {
        decision_id: evt.decision_id,
        tool_name: evt.tool_name,
        params: evt.params,
      }
    } else if (evt.type === 'approval_resolved') {
      pendingApproval.value = null
      if (!evt.approved) {
        streamError.value = `Tool "${evt.tool_name}" was denied: ${evt.reason || 'no reason given'}`
      }
    } else if (evt.type === 'guard_block') {
      streamError.value = `Blocked: ${evt.tool_name} — ${evt.reason || 'security policy'}`
      console.warn('Guard blocked:', evt.tool_name, evt.reason)
    } else if (evt.type === 'guard_pass') {
      console.debug('Guard pass:', evt.tool_name)
    } else if (evt.type === 'agent_switch') {
      chatStore.setActiveAgent(evt.to || '')
    } else if (evt.type === 'done') {
      isStreaming.value = false
      chatStore.setHistory(evt.history || [])
      if (onDone) onDone(evt.history)
    } else if (evt.type === 'error') {
      isStreaming.value = false
      streamError.value = evt.detail || 'Unknown error'
    } else if (evt.type === 'cancelled') {
      isStreaming.value = false
    }
  }

  return {
    streamingText,
    streamingReasoning,
    toolCalls,
    isStreaming,
    streamError,
    pendingApproval,
    sendMessage,
    abort,
    approve,
    setCallbacks,
  }
}
