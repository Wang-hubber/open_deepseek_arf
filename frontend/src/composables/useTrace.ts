import { ref } from 'vue'
import { useApi } from './useApi'
import type { TraceSession, TraceEvent, TraceSummary, FeedbackItem } from '@/types'

export function useTrace() {
  const api = useApi()
  const sessions = ref<TraceSession[]>([])
  const events = ref<TraceEvent[]>([])
  const summary = ref<TraceSummary | null>(null)
  const loading = ref(false)

  async function fetchSessions(limit = 20) {
    loading.value = true
    try {
      const res: any = await api.get(`/api/traces/sessions?limit=${limit}`)
      sessions.value = res.sessions || []
    } catch {
      sessions.value = []
    } finally {
      loading.value = false
    }
  }

  async function fetchSessionDetail(sessionId: string) {
    loading.value = true
    try {
      const res: any = await api.get(`/api/traces/sessions/${sessionId}`)
      events.value = res.events || []
    } catch {
      events.value = []
    } finally {
      loading.value = false
    }
  }

  async function fetchSummary() {
    try {
      const res: any = await api.get('/api/traces/summary')
      summary.value = res
    } catch {
      summary.value = null
    }
  }

  async function submitFeedback(sessionId: string, messageIndex: number, rating: number, feedbackText = '') {
    const res: any = await api.post('/api/feedback', {
      session_id: sessionId,
      message_index: messageIndex,
      rating,
      feedback_text: feedbackText,
    })
    return res.ok
  }

  async function fetchFeedback(sessionId: string): Promise<FeedbackItem[]> {
    try {
      const res: any = await api.get(`/api/feedback/${sessionId}`)
      return res.feedback || []
    } catch {
      return []
    }
  }

  function exportTrace(sessionId: string) {
    const token = localStorage.getItem('arf_token')
    const url = `/api/traces/export?session_id=${encodeURIComponent(sessionId)}`
    fetch(url, {
      headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    })
      .then(r => r.json())
      .then(data => {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
        const a = document.createElement('a')
        a.href = URL.createObjectURL(blob)
        a.download = `trace-${sessionId}.json`
        a.click()
        URL.revokeObjectURL(a.href)
      })
      .catch(err => console.error('Export failed:', err))
  }

  return {
    sessions,
    events,
    summary,
    loading,
    fetchSessions,
    fetchSessionDetail,
    fetchSummary,
    submitFeedback,
    fetchFeedback,
    exportTrace,
  }
}
