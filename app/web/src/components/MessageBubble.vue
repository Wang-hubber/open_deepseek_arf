<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from '@/composables/useI18n'
import type { ToolCallRecord } from '@/types'
import ToolCard from './ToolCard.vue'
import FeedbackPopup from './FeedbackPopup.vue'

const { t } = useI18n()

const props = defineProps<{
  role: string
  content: string
  thinking?: string
  toolCalls?: ToolCallRecord[]
  messageIndex?: number
  sessionId?: string
  feedback?: { rating: number; text?: string } | null
}>()

const emit = defineEmits<{
  thumbsUp: [index: number]
  thumbsDown: [index: number, text: string]
}>()

const showFeedbackPopup = ref(false)

function onThumbsUp() {
  if (props.messageIndex === undefined) return
  emit('thumbsUp', props.messageIndex)
}

function onThumbsDown() {
  if (props.messageIndex === undefined) return
  showFeedbackPopup.value = true
}

function onFeedbackSubmit(text: string) {
  if (props.messageIndex === undefined) return
  showFeedbackPopup.value = false
  emit('thumbsDown', props.messageIndex, text)
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
  <div class="chat-msg" :class="role">
    <div class="bubble">
      <!-- Thinking (collapsible) -->
      <div v-if="thinking" class="thinking-section">
        <div class="thinking-header" @click="(e: Event) => {
          const section = (e.target as HTMLElement).closest('.thinking-section')
          const body = section?.querySelector('.thinking-body')
          const arrow = section?.querySelector('.th-arrow')
          body?.classList.toggle('open')
          arrow?.classList.toggle('open')
        }">
          <span class="th-arrow">▶</span>
          <span>{{ t('common.thinking') }}</span>
        </div>
        <div class="thinking-body" v-html="formatMarkdown(thinking)"></div>
      </div>

      <!-- Tool cards (between thinking and text) -->
      <ToolCard
        v-for="tc in toolCalls"
        :key="tc.id"
        :name="tc.name"
        :arguments="tc.arguments"
        :status="tc.status"
        :result="tc.result"
        :error="tc.error"
        :turn="tc.turn"
      />

      <!-- Text content -->
      <div v-if="content" v-html="formatMarkdown(content)"></div>

      <!-- Feedback buttons (assistant only) -->
      <div v-if="role === 'assistant' && messageIndex !== undefined && content" class="feedback-row">
        <button
          class="fb-btn"
          :class="{ active: feedback?.rating === 1 }"
          title="赞"
          :disabled="!!feedback"
          @click="onThumbsUp"
        >👍</button>
        <button
          class="fb-btn"
          :class="{ active: feedback?.rating === -1 }"
          title="踩"
          :disabled="!!feedback"
          @click="onThumbsDown"
        >👎</button>
      </div>
      <FeedbackPopup
        v-if="showFeedbackPopup"
        @submit="onFeedbackSubmit"
        @close="showFeedbackPopup = false"
      />
    </div>
  </div>
</template>

<style scoped>
/* Uses global .chat-msg, .bubble styles from variables.css */

.feedback-row {
  display: flex; gap: 4px; margin-top: 6px; padding-top: 6px;
  border-top: 1px solid rgba(255,255,255,0.04);
}
.fb-btn {
  background: none; border: none; cursor: pointer; font-size: 14px;
  padding: 2px 6px; border-radius: 4px; opacity: 0.4;
  transition: opacity var(--transition, 0.15s);
}
.fb-btn:hover { opacity: 0.8; }
.fb-btn.active { opacity: 1; }
.fb-btn:disabled { cursor: default; }
</style>
