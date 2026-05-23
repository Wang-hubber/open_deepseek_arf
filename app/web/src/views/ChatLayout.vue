<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import StatusBar from '@/components/StatusBar.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import ResourcePanel from '@/components/ResourcePanel.vue'
import { useSessionStore } from '@/stores/sessions'
import { useChatStore } from '@/stores/chat'
import { useAppStore } from '@/stores/app'
import { useApi } from '@/composables/useApi'
import type { ChatMessage } from '@/types'

const route = useRoute()
const sessionStore = useSessionStore()
const chatStore = useChatStore()
const appStore = useAppStore()
const api = useApi()

const showResources = ref(false)

function toggleResources() {
  showResources.value = !showResources.value
}

async function loadHistory() {
  // Always ensure active session exists
  if (!sessionStore.activeSession) {
    sessionStore.activeSession = { id: 'default', title: 'ARF Assistant', created_at: new Date().toISOString(), message_count: 0 }
  }
  try {
    const messages = await api.get<ChatMessage[]>('/api/sessions/active/messages')
    if (messages?.length) {
      chatStore.renderFromHistory(messages)
    }
  } catch { /* non-critical */ }
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

// Reload history when navigating back to /
watch(() => route.path, async (to, from) => {
  if (to === '/' && from !== '/') {
    await loadHistory()
  }
})

onUnmounted(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
})
</script>

<template>
  <div id="main-layout" :class="{ 'resources-hidden': !showResources }">
    <StatusBar
      :show-resources="showResources"
      @toggle-resources="toggleResources"
    />
    <ChatPanel />
    <ResourcePanel v-if="showResources" />
  </div>
</template>

<style scoped>
#main-layout {
  display: grid;
  grid-template-columns: 1fr 280px;
  grid-template-rows: 40px 1fr;
  height: 100vh;
  background: var(--bg-root);
}
#main-layout.resources-hidden {
  grid-template-columns: 1fr;
}

@media (max-width: 900px) {
  #main-layout {
    grid-template-columns: 1fr;
  }
}

@media (min-width: 1600px) {
  #main-layout:not(.resources-hidden) {
    grid-template-columns: 1fr 320px;
  }
}
</style>
