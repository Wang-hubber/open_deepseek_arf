<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import StatusBar from '@/components/StatusBar.vue'
import SessionPanel from '@/components/SessionPanel.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import ResourcePanel from '@/components/ResourcePanel.vue'
import UsageBar from '@/components/UsageBar.vue'
import { useSessionStore } from '@/stores/sessions'
import { useChatStore } from '@/stores/chat'
import { useAppStore } from '@/stores/app'
import { useApi } from '@/composables/useApi'
import { useWebSocket } from '@/composables/useWebSocket'
import type { ChatMessage } from '@/types'

const sessionStore = useSessionStore()
const chatStore = useChatStore()
const appStore = useAppStore()
const api = useApi()
const { connect, disconnect } = useWebSocket()

// ── Mobile sidebar overlays ─────────────────────
const showMobileSessions = ref(false)
const showMobileResources = ref(false)

function toggleMobileSessions() {
  showMobileSessions.value = !showMobileSessions.value
  if (showMobileSessions.value) showMobileResources.value = false
}
function toggleMobileResources() {
  showMobileResources.value = !showMobileResources.value
  if (showMobileResources.value) showMobileSessions.value = false
}
function closeOverlays() {
  showMobileSessions.value = false
  showMobileResources.value = false
}

// Warn before closing tab when there's an active conversation
function onBeforeUnload(e: BeforeUnloadEvent) {
  if (chatStore.displayMessages.length > 0) {
    e.preventDefault()
  }
}

// Save state when tab becomes hidden (background)
function onVisibilityChange() {
  if (document.visibilityState === 'hidden' && chatStore.displayMessages.length > 0) {
    sessionStorage.setItem('arf_msg_count', String(chatStore.displayMessages.length))
  }
}

onMounted(async () => {
  try {
    const prefs = await api.get<{ language?: string }>('/api/preferences')
    if (prefs?.language) {
      appStore.setLanguage(prefs.language)
    }
  } catch {
    // non-critical; use localStorage default
  }

  await sessionStore.loadSessions(true)

  if (sessionStore.activeSession && sessionStore.activeSession.message_count > 0) {
    try {
      const messages = await api.get<ChatMessage[]>('/api/sessions/active/messages')
      if (messages?.length) chatStore.renderFromHistory(messages)
    } catch {
      // non-critical
    }
  } else if (!sessionStore.activeSession && !sessionStore.isViewingArchive()) {
    // No active session — show placeholder for lazy creation on first message
    const hhmm = `${String(new Date().getHours()).padStart(2, '0')}:${String(new Date().getMinutes()).padStart(2, '0')}`
    sessionStore.startNewSession(`新会话 · ${hhmm}`)
  }

  connect(() => {
    sessionStore.loadSessions()
    appStore.refreshUsage()
  })

  window.addEventListener('beforeunload', onBeforeUnload)
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onUnmounted(() => {
  disconnect()
  window.removeEventListener('beforeunload', onBeforeUnload)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <div id="main-layout">
    <StatusBar
      :show-mobile-sessions="showMobileSessions"
      :show-mobile-resources="showMobileResources"
      @toggle-sessions="toggleMobileSessions"
      @toggle-resources="toggleMobileResources"
    />

    <!-- Mobile backdrop -->
    <div
      v-if="showMobileSessions || showMobileResources"
      class="mobile-overlay-backdrop"
      @click="closeOverlays"
    ></div>

    <SessionPanel
      :overlay="showMobileSessions"
      @close-overlay="closeOverlays"
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
  grid-template-columns: 260px 1fr 280px;
  grid-template-rows: 44px 1fr auto;
  height: 100vh;
  background: var(--bg-root);
}
#main-layout :deep(#usage-bar) {
  grid-column: 1 / -1;
}

/* ── Mobile overlay backdrop ──────────────────── */
.mobile-overlay-backdrop {
  display: none;
}

/* ── Tablet: hide right resource panel ────────── */
@media (max-width: 900px) {
  #main-layout {
    grid-template-columns: 260px 1fr;
  }
  #main-layout :deep(#resource-panel-right) {
    display: none;
  }
}

/* ── Mobile: hide both sidebars, full-width chat ─ */
@media (max-width: 640px) {
  #main-layout {
    grid-template-columns: 1fr;
  }
  #main-layout :deep(#session-panel) {
    display: none;
  }
  #main-layout :deep(#resource-panel-right) {
    display: none;
  }

  .mobile-overlay-backdrop {
    display: block;
    position: fixed; inset: 0; z-index: 40;
    background: rgba(0,0,0,0.5);
    backdrop-filter: blur(2px);
    -webkit-backdrop-filter: blur(2px);
  }
}

/* ── Wide screens: larger sidebars ────────────── */
@media (min-width: 1600px) {
  #main-layout {
    grid-template-columns: 300px 1fr 320px;
  }
}
</style>
