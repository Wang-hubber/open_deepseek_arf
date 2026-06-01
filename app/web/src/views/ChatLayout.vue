<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, provide } from 'vue'
import { useRoute } from 'vue-router'
import StatusBar from '@/components/StatusBar.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import SidePanel from '@/components/SidePanel.vue'
import { useSessionStore } from '@/stores/sessions'
import { useChatStore } from '@/stores/chat'
import { useAppStore } from '@/stores/app'
import { useApi } from '@/composables/useApi'
import { useChat, CHAT_KEY } from '@/composables/useChat'
import type { ChatMessage } from '@/types'

const route = useRoute()
const sessionStore = useSessionStore()
const chatStore = useChatStore()
const appStore = useAppStore()
const api = useApi()

// Single useChat instance shared with ChatPanel + SidePanel
const chat = useChat()
provide(CHAT_KEY, chat)

const showSidePanel = ref(true)

function toggleSidePanel() {
  showSidePanel.value = !showSidePanel.value
}

async function loadHistory() {
  if (!sessionStore.activeSession) {
    sessionStore.activeSession = { id: 'default', created_at: new Date().toISOString(), message_count: 0 }
  }
  try {
    const messages = await api.get<ChatMessage[]>('/api/sessions/active/messages')
    console.log('[ChatLayout] loadHistory got', messages?.length || 0, 'messages')
    if (messages?.length) {
      chatStore.renderFromHistory(messages)
    }
  } catch (e) { console.log('[ChatLayout] loadHistory error:', e) }
}

function onBeforeUnload(e: BeforeUnloadEvent) {
  if (chatStore.displayMessages.length > 0) {
    e.preventDefault()
  }
}

onMounted(async () => {
  try {
    const prefs = await api.get<{ language?: string }>('/api/preferences')
    if (prefs?.language) appStore.setLanguage(prefs.language)
  } catch { /* non-critical */ }

  await loadHistory()
  window.addEventListener('beforeunload', onBeforeUnload)
})

// Reload history whenever route lands on /
watch(() => route.path, async (path) => {
  if (path === '/') {
    await loadHistory()
  }
})

onUnmounted(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
})
</script>

<template>
  <div id="main-layout" :class="{ 'side-hidden': !showSidePanel }">
    <StatusBar
      :show-side-panel="showSidePanel"
      @toggle-side-panel="toggleSidePanel"
    />
    <ChatPanel />
    <SidePanel v-if="showSidePanel" />
  </div>
</template>

<style scoped>
#main-layout {
  display: grid;
  grid-template-columns: 1fr 340px;
  grid-template-rows: 40px 1fr;
  height: 100vh;
  background: var(--bg-root);
}
#main-layout.side-hidden {
  grid-template-columns: 1fr;
}

@media (max-width: 900px) {
  #main-layout { grid-template-columns: 1fr; }
  #main-layout.side-hidden { grid-template-columns: 1fr; }
}

@media (min-width: 1600px) {
  #main-layout:not(.side-hidden) { grid-template-columns: 1fr 380px; }
}
</style>
