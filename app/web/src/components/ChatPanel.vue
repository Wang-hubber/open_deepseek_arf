<script setup lang="ts">
import { ref, reactive, computed, nextTick, watch } from 'vue'
import { useChatStore } from '@/stores/chat'
import { useSessionStore } from '@/stores/sessions'
import { useAppStore } from '@/stores/app'
import { useChat } from '@/composables/useChat'
import { useTrace } from '@/composables/useTrace'
import { useApi } from '@/composables/useApi'
import { useI18n } from '@/composables/useI18n'
import MessageBubble from './MessageBubble.vue'
import ToolCard from './ToolCard.vue'
import type { ToolCallRecord, ChatMessage } from '@/types'

const { t } = useI18n()

const chatStore = useChatStore()
const sessionStore = useSessionStore()
const appStore = useAppStore()
const { submitFeedback } = useTrace()

const {
  streamingText,
  streamingReasoning,
  toolCalls,
  isStreaming,
  streamError,
  sendMessage,
  abort,
  setCallbacks,
} = useChat()

const textarea = ref<HTMLTextAreaElement | null>(null)
const chatHistoryEl = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const userScrolledUp = ref(false)
const inputText = ref('')
const uploading = ref(false)

// Drag state
const dragOver = ref(false)
let dragCounter = 0

const MAX_UPLOAD_BYTES = 15 * 1024 * 1024

// Current streaming message display state
const currentReasoning = ref('')
const currentText = ref('')
const currentToolCalls = ref<ToolCallRecord[]>([])
const currentError = ref('')

// Track completed messages before streaming started
const baseMessageCount = ref(0)

// Sync baseMessageCount when renderFromHistory is called (e.g. nav back)
watch(() => chatStore.renderVersion, () => {
  if (!isStreaming.value) {
    baseMessageCount.value = chatStore.displayMessages.length
  }
})

// Feedback state: map of assistant_message_index → { rating, text }
const feedbackMap = reactive<Record<number, { rating: number; text?: string }>>({})

// Compute which assistant message index each displayMessage corresponds to
const assistantIndices = computed(() => {
  let count = 0
  return chatStore.displayMessages.map(m => {
    if (m.role === 'assistant' && m.content) return count++
    return -1
  })
})

const currentSessionId = computed(() => sessionStore.activeSession?.id || '')

async function handleThumbsUp(msgIdx: number) {
  const sid = currentSessionId.value
  if (!sid) return
  feedbackMap[msgIdx] = { rating: 1 }
  await submitFeedback(sid, msgIdx, 1)
}

async function handleThumbsDown(msgIdx: number, text: string) {
  const sid = currentSessionId.value
  if (!sid) return
  feedbackMap[msgIdx] = { rating: -1, text }
  await submitFeedback(sid, msgIdx, -1, text)
}

// Set up callbacks for streaming
setCallbacks({
  onToolCall(name, args, id) {
    currentToolCalls.value.push({
      id, name, arguments: args, status: 'executing',
    })
  },
  onToolResult(id, result, tool) {
    const tc = currentToolCalls.value.find(t => t.id === id)
    if (tc) {
      let isError = false, errorMsg = ''
      try { const p = JSON.parse(result); if (p?.error) { isError = true; errorMsg = p.error } } catch {}
      if (isError) { tc.status = 'failed'; tc.error = errorMsg }
      else { tc.status = 'completed'; tc.result = result }
    }

    // Reload resources when file_writer creates tools/skills
    if (tool === 'file_writer') {
      try {
        const r = JSON.parse(result)
        if (r.path && (r.path.includes('/tools/') || r.path.includes('/skills/'))) {
          // Resource panel will auto-reload on next interval
        }
      } catch {}
    }
  },
  onDone(history) {
    // Save the final message to chatStore
    const finalContent = currentText.value
    if (finalContent || currentReasoning.value) {
      chatStore.displayMessages.push({
        role: 'assistant',
        content: finalContent,
        thinking: currentReasoning.value || undefined,
        toolCalls: currentToolCalls.value.length > 0 ? [...currentToolCalls.value] : undefined,
      })
    }
    currentText.value = ''
    currentReasoning.value = ''
    currentToolCalls.value = []
    currentError.value = ''
    baseMessageCount.value = chatStore.displayMessages.length
    // Auto-rename after first exchange (server-side auto-gen handles the common case;
    // this client-side fallback catches edge cases like slow LLM or fast model unavailable)
    const session = sessionStore.activeSession
    if (session && session.title === '新会话' && chatStore.chatHistory.length >= 2) {
      sessionStore.generateActiveTitle()
    }
    appStore.refreshUsage()
  },
})

// Watch streaming state
watch(streamingText, (val) => { currentText.value = val })
watch(streamingReasoning, (val) => { currentReasoning.value = val })
watch(streamError, (val) => { currentError.value = val })

// Sync baseMessageCount when messages change outside streaming (archive view, new session, etc.)
watch(() => chatStore.displayMessages.length, (len) => {
  if (!isStreaming.value) {
    baseMessageCount.value = len
  }
})

function onScroll() {
  const el = chatHistoryEl.value
  if (!el) return
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight
  userScrolledUp.value = dist > 60
}

function scrollToBottom(force = false) {
  if (force || !userScrolledUp.value) {
    nextTick(() => {
      const el = chatHistoryEl.value
      if (el) el.scrollTop = el.scrollHeight
    })
  }
}

// Watch for new messages and scroll (respect user scroll-up preference)
watch(() => chatStore.displayMessages.length, () => scrollToBottom())
watch(currentText, () => scrollToBottom())
watch(currentToolCalls, () => scrollToBottom(), { deep: true })

// Input auto-resize
watch(inputText, () => {
  nextTick(() => {
    const ta = textarea.value
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'
    }
  })
})

function buildMessage(): string {
  let attachCtx = ''
  const valid = chatStore.attachments.filter(a => !a.error)
  if (valid.length > 0) {
    attachCtx = '[Uploaded files]\n'
    for (const a of valid) {
      attachCtx += `File: ${a.path} (${formatBytes(a.size)})\n`
      if (a.preview) attachCtx += `Preview:\n${a.preview}\n`
    }
    attachCtx += '\n'
  }

  const text = inputText.value.trim()
  let fullText = attachCtx + text
  if (!text && attachCtx) fullText = attachCtx + 'Please review the uploaded files above.'
  return fullText
}

async function handleSend() {
  if (isStreaming.value) return
  const fullText = buildMessage()
  if (!fullText) return

  const displayText = inputText.value.trim() || '(uploaded files)'

  inputText.value = ''
  textarea.value!.style.height = 'auto'

  chatStore.addUserMsg(displayText)
  chatStore.clearAttachments()
  baseMessageCount.value = chatStore.displayMessages.length

  currentText.value = ''
  currentReasoning.value = ''
  currentToolCalls.value = []
  currentError.value = ''

  userScrolledUp.value = false
  scrollToBottom(true)

  await sendMessage(fullText)
  textarea.value?.focus()
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

async function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) {
    for (let i = 0; i < input.files.length; i++) {
      await uploadFile(input.files[i])
    }
  }
  input.value = ''
}

async function uploadFile(file: File) {
  if (file.size > MAX_UPLOAD_BYTES) {
    chatStore.addAttachment({
      filename: file.name, size: file.size, error: 'Exceeds 15 MB limit',
      path: '', preview: '', content_type: '',
    })
    return
  }

  uploading.value = true

  try {
    const { upload } = useApi()
    const data = await upload(file)
    chatStore.addAttachment({
      filename: data.filename, size: data.size, path: data.path,
      preview: data.preview, content_type: data.content_type, error: '',
    })
  } catch (e: any) {
    chatStore.addAttachment({
      filename: file.name, size: file.size, error: e.message,
      path: '', preview: '', content_type: '',
    })
  } finally {
    uploading.value = false
  }
}

function removeAttachment(index: number) {
  chatStore.removeAttachment(index)
}

function onDragEnter(e: DragEvent) {
  e.preventDefault()
  dragCounter++
  dragOver.value = true
}
function onDragLeave(e: DragEvent) {
  e.preventDefault()
  dragCounter--
  if (dragCounter <= 0) { dragCounter = 0; dragOver.value = false }
}
function onDragOver(e: DragEvent) { e.preventDefault() }
function onDrop(e: DragEvent) {
  e.preventDefault()
  dragCounter = 0
  dragOver.value = false
  if (e.dataTransfer?.files) {
    for (let i = 0; i < e.dataTransfer.files.length; i++) {
      uploadFile(e.dataTransfer.files[i])
    }
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(2) + ' MB'
}

function formatMarkdown(text: string): string {
  let html = escapeHtml(text)
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>')
  html = html.replace(/\n/g, '<br>')
  return html
}

function escapeHtml(s: string): string {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
</script>

<template>
  <main id="chat-panel">
    <div id="drag-overlay" :class="{ show: dragOver }">
      <div class="drag-text">{{ t('chat.dragText') }}</div>
    </div>

    <div
      id="chat-history"
      ref="chatHistoryEl"
      @scroll="onScroll"
      @dragenter="onDragEnter"
      @dragleave="onDragLeave"
      @dragover="onDragOver"
      @drop="onDrop"
    >
      <!-- Past messages -->
      <MessageBubble
        v-for="(msg, idx) in chatStore.displayMessages.slice(0, baseMessageCount)"
        :key="idx"
        :role="msg.role"
        :content="msg.content"
        :thinking="msg.thinking"
        :tool-calls="msg.toolCalls"
        :message-index="assistantIndices[idx] >= 0 ? assistantIndices[idx] : undefined"
        :session-id="currentSessionId"
        :feedback="assistantIndices[idx] >= 0 ? feedbackMap[assistantIndices[idx]] ?? null : null"
        @thumbs-up="handleThumbsUp"
        @thumbs-down="handleThumbsDown"
      />

      <!-- Current streaming message -->
      <div v-if="isStreaming || currentText || currentReasoning || currentToolCalls.length > 0" class="chat-msg assistant">
        <div class="bubble">
          <!-- Thinking -->
          <div v-if="currentReasoning" class="thinking-section">
            <div class="thinking-header" @click="(e: Event) => (e.target as HTMLElement).closest('.thinking-section')?.querySelector('.thinking-body')?.classList.toggle('open')">
              <span class="th-arrow open">▶</span>
              <span>{{ t('chat.thinking') }}</span>
            </div>
            <div class="thinking-body open" v-html="formatMarkdown(currentReasoning)"></div>
          </div>

          <!-- Tool cards (between thinking and text) -->
          <ToolCard
            v-for="tc in currentToolCalls"
            :key="tc.id"
            :name="tc.name"
            :arguments="tc.arguments"
            :status="tc.status"
            :result="tc.result"
            :error="tc.error"
          />

          <!-- Text -->
          <div v-if="currentText || (isStreaming && !currentReasoning)" class="text-segment">
            <span v-html="formatMarkdown(currentText)"></span>
            <span v-if="isStreaming" class="streaming-cursor">|</span>
          </div>

          <!-- Error -->
          <div v-if="currentError" class="error-banner">{{ currentError }}</div>
        </div>
      </div>

      <div v-if="chatStore.displayMessages.length === 0 && !isStreaming" class="chat-empty">
        <div class="welcome-card">
          <div class="wc-icon">◈</div>
          <h2 class="wc-greeting">{{ t('chat.emptyGreeting') }}</h2>
          <div class="wc-features">
            <div class="wc-item">
              <span class="wc-dot"></span>
              <span>{{ t('chat.emptyFeature1') }}</span>
            </div>
            <div class="wc-item">
              <span class="wc-dot"></span>
              <span>{{ t('chat.emptyFeature2') }}</span>
            </div>
            <div class="wc-item">
              <span class="wc-dot"></span>
              <span>{{ t('chat.emptyFeature3') }}</span>
            </div>
            <div class="wc-item">
              <span class="wc-dot"></span>
              <span>{{ t('chat.emptyFeature4') }}</span>
            </div>
          </div>
          <p class="wc-cta">{{ t('chat.emptyCta') }}</p>
        </div>
      </div>
    </div>

    <!-- Attachment tags -->
    <div id="attach-tags">
      <div v-for="(att, idx) in chatStore.attachments" :key="idx" class="attach-tag" :class="{ error: att.error }">
        <span class="at-name" :title="att.filename">{{ att.filename }}</span>
        <span class="at-size">{{ att.error || formatBytes(att.size) }}</span>
        <span class="at-remove" @click="removeAttachment(idx)">&times;</span>
      </div>
    </div>

    <!-- Input area -->
    <div id="chat-input-wrap"
      @dragenter="onDragEnter" @dragleave="onDragLeave" @dragover="onDragOver" @drop="onDrop"
    >
      <input ref="fileInput" type="file" style="display:none" multiple @change="handleFileSelect" />
      <button id="btn-upload" :class="{ uploading }" :title="t('chat.uploadTitle')" @click="fileInput?.click()">+</button>
      <textarea
        id="chat-textarea"
        ref="textarea"
        v-model="inputText"
        :placeholder="t('chat.placeholder')"
        rows="1"
        @keydown="handleKeydown"
      ></textarea>
      <button v-if="isStreaming" id="btn-stop" @click="abort">{{ t('chat.stop') }}</button>
      <button v-else id="btn-send" @click="handleSend">{{ t('chat.send') }}</button>
    </div>
  </main>
</template>

<style scoped>
#chat-panel {
  grid-column: 1; grid-row: 2;
  display: flex; flex-direction: column; min-width: 0; overflow: hidden;
  position: relative; background: var(--bg-root);
}
#chat-history { flex: 1; overflow-y: auto; padding: 24px 28px; scroll-behavior: smooth; }

#viewing-banner {
  display: flex; align-items: center; justify-content: center; gap: 10px;
  padding: 7px 20px; background: var(--warning-bg); color: var(--warning-text);
  font-size: 12px; border-bottom: 1px solid var(--warning-border); flex-shrink: 0;
  font-weight: 500;
}
.vb-close {
  background: none; border: none; color: var(--warning-text); cursor: pointer;
  font-size: 15px; font-weight: bold; padding: 2px 6px; border-radius: 4px;
  transition: all var(--transition);
}
.vb-close:hover { background: rgba(245,158,11,0.15); }

#chat-input-wrap {
  display: flex; align-items: flex-end;
  border-top: 1px solid var(--border); padding: 14px 20px;
  background: var(--bg-panel);
  gap: 10px;
}
#chat-textarea {
  flex: 1; border: 1px solid var(--border); border-radius: var(--radius-lg);
  padding: 12px 16px; resize: none; font-size: 14px; outline: none;
  min-height: 46px; max-height: 130px; font-family: inherit; line-height: 1.45;
  background: var(--bg-input); color: var(--text-primary);
  transition: all var(--transition);
}
#chat-textarea::placeholder { color: var(--text-muted); }
#chat-textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(99,102,241,0.12), 0 0 20px rgba(99,102,241,0.08);
}
#chat-textarea:disabled { background: var(--bg-root); color: var(--text-muted); opacity: 0.6; }

#btn-send {
  padding: 10px 22px;
  background: var(--accent-gradient); color: var(--text-on-accent);
  border: none; border-radius: var(--radius-lg); cursor: pointer; font-size: 14px;
  font-weight: 600; white-space: nowrap;
  transition: all var(--transition);
  align-self: flex-end; min-height: 46px;
  box-shadow: 0 2px 8px rgba(99,102,241,0.25);
}
#btn-send:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(99,102,241,0.35);
}
#btn-send:active { transform: scale(0.97); }
#btn-send:disabled { opacity: 0.35; cursor: not-allowed; transform: none; box-shadow: none; }

#btn-stop {
  padding: 10px 22px;
  background: rgba(239,68,68,0.12); color: var(--error-text);
  border: 1px solid var(--error-border); border-radius: var(--radius-lg);
  cursor: pointer; font-size: 14px; font-weight: 600; white-space: nowrap;
  transition: all var(--transition);
  align-self: flex-end; min-height: 46px;
}
#btn-stop:hover { background: rgba(239,68,68,0.22); }
#btn-stop:active { transform: scale(0.97); }

#btn-upload {
  align-self: flex-end; padding: 12px 14px; background: transparent;
  color: var(--text-secondary); border: 1px solid var(--border);
  border-radius: var(--radius-lg); cursor: pointer; font-size: 18px;
  transition: all var(--transition); line-height: 1; min-height: 46px;
}
#btn-upload:hover { color: var(--accent); border-color: var(--accent); background: var(--accent-light); }
#btn-upload.uploading { opacity: 0.4; pointer-events: none; }

#drag-overlay {
  position: absolute; inset: 0;
  background: rgba(99,102,241,0.08); border: 2px dashed var(--accent);
  opacity: 0; pointer-events: none; z-index: 20;
  display: flex; align-items: center; justify-content: center;
  transition: opacity 0.2s ease;
  backdrop-filter: blur(2px);
}
#drag-overlay.show { opacity: 1; }
.drag-text {
  background: var(--accent-gradient); color: #fff; padding: 14px 32px;
  border-radius: var(--radius-lg); font-size: 15px; font-weight: 600;
  box-shadow: 0 4px 20px rgba(99,102,241,0.3);
}

.chat-empty {
  display: flex; justify-content: center; align-items: center;
  padding: 40px 20px; min-height: 100%;
}
.welcome-card {
  max-width: 460px; width: 100%;
  padding: 48px 40px 40px;
  background: var(--bg-card);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-xl);
  box-shadow: 0 4px 24px rgba(0,0,0,0.12);
  text-align: center;
}
.wc-icon {
  font-size: 32px; color: var(--accent); margin-bottom: 16px;
  opacity: 0.8;
}
.wc-greeting {
  font-size: 16px; font-weight: 600; color: var(--text-primary);
  margin: 0 0 28px; line-height: 1.5;
}
.wc-features {
  text-align: left; margin-bottom: 28px;
}
.wc-item {
  display: flex; align-items: baseline; gap: 10px;
  padding: 6px 0; font-size: 13px; color: var(--text-secondary);
  line-height: 1.6;
}
.wc-dot {
  flex-shrink: 0; width: 6px; height: 6px; margin-top: 8px;
  border-radius: 50%; background: var(--accent);
  opacity: 0.6;
}
.wc-cta {
  margin: 0; font-size: 13px; color: var(--text-muted);
  font-style: italic;
}
</style>
