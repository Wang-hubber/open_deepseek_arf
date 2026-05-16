import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import type { SessionInfo, ActiveSession, ArchivedSession } from '@/types'

export const useSessionStore = defineStore('sessions', () => {
  const api = useApi()
  const sessions = ref<SessionInfo[]>([])
  const activeSession = ref<ActiveSession | null>(null)
  const viewingArchiveId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref(false)
  // Lazy session creation: set when user clicks "New Session", cleared on first message
  const pendingNewSession = ref(false)
  const pendingPlaceholder = ref('')

  const isViewingArchive = () => viewingArchiveId.value !== null

  async function loadSessions() {
    if (loading.value) return
    loading.value = true
    error.value = false

    try {
      const [archives, active] = await Promise.all([
        api.get<SessionInfo[]>('/api/sessions'),
        api.get<ActiveSession | null>('/api/sessions/active'),
      ])
      sessions.value = archives || []
      activeSession.value = active
    } catch {
      error.value = true
    } finally {
      loading.value = false
    }
  }

  async function createSession() {
    const data = await api.post<ActiveSession>('/api/sessions')
    activeSession.value = data
    viewingArchiveId.value = null
    return data
  }

  async function fetchArchive(sessionId: string): Promise<ArchivedSession> {
    return await api.get<ArchivedSession>(`/api/sessions/${encodeURIComponent(sessionId)}`)
  }

  async function viewArchive(sessionId: string): Promise<ArchivedSession> {
    viewingArchiveId.value = sessionId
    return await fetchArchive(sessionId)
  }

  function returnToActive() {
    viewingArchiveId.value = null
  }

  async function deleteSession(sessionId: string, isActive: boolean) {
    if (isActive) {
      // Always archive the current session first (POST creates a new empty
      // session and archives the current one if it has >=2 messages).
      await api.post('/api/sessions')
      // Delete the archive that was just created (or the existing one).
      try { await api.del(`/api/sessions/${encodeURIComponent(sessionId)}`) } catch { /* may not exist if <2 msgs */ }
      activeSession.value = null
      viewingArchiveId.value = null
      await loadSessions()
      if (sessions.value.length > 0) {
        return sessions.value[0]
      }
      return null
    } else {
      await api.del(`/api/sessions/${encodeURIComponent(sessionId)}`)
      if (viewingArchiveId.value === sessionId) {
        viewingArchiveId.value = null
      }
      await loadSessions()
      return null
    }
  }

  function hasActiveWithMessages(): boolean {
    return !!(activeSession.value && activeSession.value.message_count >= 2)
  }

  function totalCount(): number {
    return sessions.value.length + (hasActiveWithMessages() ? 1 : 0)
  }

  async function generateActiveTitle() {
    if (!activeSession.value) return
    try {
      const data = await api.post<{ title: string }>('/api/sessions/active/title')
      if (data.title) {
        activeSession.value.title = data.title
      }
    } catch { /* silent */ }
  }

  function startNewSession(placeholder: string) {
    pendingNewSession.value = true
    pendingPlaceholder.value = placeholder
  }

  function confirmNewSession(id: string, title: string) {
    pendingNewSession.value = false
    pendingPlaceholder.value = ''
    activeSession.value = { id, title, created_at: new Date().toISOString(), message_count: 0 }
  }

  function isPendingNewSession() {
    return pendingNewSession.value
  }

  return {
    sessions,
    activeSession,
    viewingArchiveId,
    loading,
    error,
    pendingNewSession,
    pendingPlaceholder,
    isViewingArchive,
    loadSessions,
    createSession,
    viewArchive,
    fetchArchive,
    returnToActive,
    deleteSession,
    hasActiveWithMessages,
    totalCount,
    generateActiveTitle,
    startNewSession,
    confirmNewSession,
    isPendingNewSession,
  }
})
