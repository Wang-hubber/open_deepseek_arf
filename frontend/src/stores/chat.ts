import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ChatMessage, Attachment, ToolCallRecord, SSEEvent } from '@/types'

export const useChatStore = defineStore('chat', () => {
  const chatHistory = ref<ChatMessage[]>([])
  const displayMessages = ref<{ role: string; content: string; thinking?: string; toolCalls?: ToolCallRecord[] }[]>([])
  const isStreaming = ref(false)
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

  function clearMessages() {
    displayMessages.value = []
    chatHistory.value = []
  }

  function setHistory(history: ChatMessage[]) {
    chatHistory.value = history
  }

  // Build display messages from history (for returning from archive view)
  function renderFromHistory(messages: ChatMessage[]) {
    clearMessages()
    for (const m of messages) {
      if (m.role === 'user') addUserMsg(m.content)
      else if (m.role === 'assistant') addAssistantMsg(m.content, m.reasoning_content)
    }
    chatHistory.value = messages.filter(m => m.role === 'user' || m.role === 'assistant')
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

  return {
    chatHistory,
    displayMessages,
    isStreaming,
    attachments,
    userScrolledUp,
    addUserMsg,
    addSystemMsg,
    addAssistantMsg,
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
