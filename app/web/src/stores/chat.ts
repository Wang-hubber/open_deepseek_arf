import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ChatMessage, Attachment, ToolCallRecord, SSEEvent } from '@/types'

export const useChatStore = defineStore('chat', () => {
  const chatHistory = ref<ChatMessage[]>([])
  const displayMessages = ref<{ role: string; content: string; thinking?: string; toolCalls?: ToolCallRecord[] }[]>([])
  const isStreaming = ref(false)
  const activeAgentName = ref('')
  const currentHandoff = ref<{ from: string; to: string } | null>(null)
  const attachments = ref<Attachment[]>([])
  const userScrolledUp = ref(false)

  function addUserMsg(text: string) {
    displayMessages.value.push({ role: 'user', content: text })
  }

  function addSystemMsg(content: string) {
    displayMessages.value.push({ role: 'system', content })
  }

  function addAssistantMsg(content: string, thinking?: string) {
    displayMessages.value.push({ role: 'assistant', content, thinking })
  }

  const renderVersion = ref(0)

  function clearMessages() {
    displayMessages.value = []
    chatHistory.value = []
    renderVersion.value++
  }

  function setHistory(history: ChatMessage[]) {
    chatHistory.value = history
  }

  // Build display messages from history (for viewing past sessions)
  function renderFromHistory(messages: ChatMessage[]) {
    clearMessages()
    let pendingToolCalls: ToolCallRecord[] = []
    for (const m of messages) {
      if (m.role === 'user') {
        // flush any pending tool calls before the next user message
        if (pendingToolCalls.length && displayMessages.value.length > 0) {
          const last = displayMessages.value[displayMessages.value.length - 1]
          if (last.role === 'assistant') {
            last.toolCalls = [...pendingToolCalls]
          }
        }
        pendingToolCalls = []
        addUserMsg(m.content)
      } else if (m.role === 'assistant') {
        // flush previous tool calls onto the last assistant message
        if (pendingToolCalls.length && displayMessages.value.length > 0) {
          const prev = displayMessages.value[displayMessages.value.length - 1]
          if (prev.role === 'assistant') {
            prev.toolCalls = [...pendingToolCalls]
          }
        }
        pendingToolCalls = []
        // This assistant may carry its own tool_calls from graph state
        if (m.tool_calls?.length) {
          pendingToolCalls = m.tool_calls.map((tc: any) => ({
            id: tc.id || '',
            name: tc.function?.name || tc.name || '',
            arguments: tc.function?.arguments || tc.arguments || '{}',
            status: 'completed' as const,
          }))
        }
        addAssistantMsg(m.content, m.reasoning_content)
      } else if (m.role === 'tool_call') {
        // Legacy format: separate tool_call message from session_history
        pendingToolCalls.push({
          id: (m as any).tool_call_id || '',
          name: (m as any).name || '',
          arguments: (m as any).arguments || '{}',
          status: 'executing' as const,
        })
      } else if (m.role === 'system') {
        addSystemMsg(m.content)
      } else if (m.role === 'tool_result') {
        const callId = (m as any).tool_call_id || ''
        const tc = pendingToolCalls.find(t => t.id === callId)
        if (tc) {
          tc.status = 'completed'
          tc.result = m.content
        }
        if (displayMessages.value.length > 0) {
          const last = displayMessages.value[displayMessages.value.length - 1]
          if (last.role === 'assistant' && last.toolCalls) {
            const mtc = last.toolCalls.find(t => t.id === callId)
            if (mtc) {
              mtc.status = 'completed'
              mtc.result = m.content
            }
          }
        }
      } else if (m.role === 'tool') {
        // Current framework format: role="tool" with tool_call_id
        const callId = (m as any).tool_call_id || ''
        const tc = pendingToolCalls.find(t => t.id === callId)
        if (tc) {
          tc.status = 'completed'
          tc.result = m.content
        }
        if (displayMessages.value.length > 0) {
          const last = displayMessages.value[displayMessages.value.length - 1]
          if (last.role === 'assistant' && last.toolCalls) {
            const mtc = last.toolCalls.find(t => t.id === callId)
            if (mtc) {
              mtc.status = 'completed'
              mtc.result = m.content
            }
          }
        }
      }
    }
    // flush remaining pending tool calls
    if (pendingToolCalls.length && displayMessages.value.length > 0) {
      const last = displayMessages.value[displayMessages.value.length - 1]
      if (last.role === 'assistant') {
        last.toolCalls = pendingToolCalls.filter(t => t.status !== 'executing' || t.id)
        // Mark unmatched executing ones as completed (results may have been filtered)
        for (const tc of last.toolCalls) {
          if (tc.status === 'executing') tc.status = 'completed'
        }
      }
    }
    chatHistory.value = messages.filter(m => m.role === 'user' || m.role === 'assistant')
    renderVersion.value++
  }

  function startStreaming() {
    isStreaming.value = true
  }

  function stopStreaming() {
    isStreaming.value = false
  }

  function addAttachment(att: Attachment) {
    attachments.value.push(att)
  }

  function removeAttachment(index: number) {
    attachments.value.splice(index, 1)
  }

  function clearAttachments() {
    attachments.value = []
  }

  function setActiveAgent(name: string) {
    activeAgentName.value = name
  }

  return {
    chatHistory,
    displayMessages,
    isStreaming,
    activeAgentName,
    currentHandoff,
    setActiveAgent,
    attachments,
    userScrolledUp,
    addUserMsg,
    addSystemMsg,
    addAssistantMsg,
    renderVersion,
    clearMessages,
    setHistory,
    renderFromHistory,
    startStreaming,
    stopStreaming,
    addAttachment,
    removeAttachment,
    clearAttachments,
  }
})
