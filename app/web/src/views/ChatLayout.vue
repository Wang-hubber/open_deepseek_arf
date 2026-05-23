<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import StatusBar from '@/components/StatusBar.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import ResourcePanel from '@/components/ResourcePanel.vue'
import UsageBar from '@/components/UsageBar.vue'
import { useSessionStore } from '@/stores/sessions'
import { useChatStore } from '@/stores/chat'
import { useAppStore } from '@/stores/app'
import { useApi } from '@/composables/useApi'
import type { ChatMessage } from '@/types'

const sessionStore = useSessionStore()
const chatStore = useChatStore()
const appStore = useAppStore()
const api = useApi()

// Single infinite session — no sidebar needed
const showMobileResources = ref(false)

function toggleMobileResources() {
  showMobileResources.value = !showMobileResources.value
}
function closeOverlays() {
  showMobileResources.value = false
}

function onBeforeUnload(e: BeforeUnloadEvent) {
  if (chatStore.displayMessages.length > 0) {
    e.preventDefault()
  }
}

onMounted(async () => {
  // Preferences
  try {
    const prefs = await api.get<{ language?: string }>('/api/preferences')
    if (prefs?.language) {
      appStore.setLanguage(prefs.language)
    }
  } catch { /* non-critical */ }

  // Ensure default session exists
  sessionStore.activeSession = { id: 'default', title: 'ARF Assistant', created_at: new Date().toISOString(), message_count: 0 }

  // Load history if available
  try {
    const messages = await api.get<ChatMessage[]>('/api/sessions/active/messages')
    if (messages?.length) {
      chatStore.renderFromHistory(messages)
    }
  } catch { /* non-critical */ }

  window.addEventListener('beforeunload', onBeforeUnload)
})

onUnmounted(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
})
</script>

<template>
  <div id="main-layout">
    <StatusBar
      :show-mobile-resources="showMobileResources"
      @toggle-resources="toggleMobileResources"
    />
    <ChatPanel />
    <ResourcePanel
      :overlay="showMobileResources"
      @close-overlay="closeOverlays"
    />
    <UsageBar />
  </div>
</template>

<style scoped>
#main-layout {
  display: grid;
  grid-template-columns: 1fr 280px;
  grid-template-rows: 44px 1fr auto;
  height: 100vh;
  background: var(--bg-root);
}
#main-layout :deep(#usage-bar) {
  grid-column: 1 / -1;
}

@media (max-width: 900px) {
  #main-layout {
    grid-template-columns: 1fr;
  }
  #main-layout :deep(#resource-panel-right) {
    display: none;
  }
}

@media (min-width: 1600px) {
  #main-layout {
    grid-template-columns: 1fr 320px;
  }
}
</style>
