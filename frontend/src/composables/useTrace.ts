import { ref, computed } from 'vue'
import { useApi } from './useApi'
import type {
  TraceSession, TraceEvent, TraceSummary, FeedbackItem,
  StructuredSession, Turn, TurnInput, Iteration, ToolCallPair, TurnStart,
} from '@/types'

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

  // ── Turn grouping ────────────────────────────────────────────────────────

  function parseMeta(evt: TraceEvent): Record<string, any> {
    if (!evt.metadata) return {}
    try { return JSON.parse(evt.metadata) } catch { return {} }
  }

  function sumTokens(events: TraceEvent[]): number {
    return events.reduce((s, e) => s + (e.total_tokens || 0), 0)
  }

  function computeDuration(events: TraceEvent[]): number {
    if (events.length === 0) return 0
    const times = events
      .map(e => e.created_at ? new Date(e.created_at).getTime() : 0)
      .filter(t => t > 0)
    if (times.length < 2) return events.reduce((s, e) => s + (e.duration_ms || 0), 0)
    return Math.max(...times) - Math.min(...times)
  }

  function buildIterations(events: TraceEvent[]): Iteration[] {
    const callModels = events.filter(e => e.node === 'call_model')
    const respondEvent = events.find(e => e.node === 'respond')
    const iterations: Iteration[] = []

    for (let i = 0; i < callModels.length; i++) {
      const cm = callModels[i]
      const isFinal = i === callModels.length - 1 && respondEvent !== undefined

      const toolsInTurn = events.filter(e =>
        e.node === 'execute_tools' && e.turn === cm.turn
      )

      const preToolUseHooks = events.filter(e => {
        if ((e as any).event_type !== 'lifecycle.hook_execution') return false
        const meta = parseMeta(e)
        return meta.hook_event === 'PreToolUse' && e.turn === cm.turn
      })

      const afterToolHooks = events.filter(e => {
        if ((e as any).event_type !== 'lifecycle.hook_execution') return false
        const meta = parseMeta(e)
        return meta.hook_event === 'PostToolUse' && e.turn === cm.turn
      })

      const toolCalls: ToolCallPair[] = toolsInTurn.map(t => ({ call: t }))

      iterations.push({
        index: i + 1,
        reasoning: cm,
        preToolUseHooks,
        toolCalls,
        afterToolHooks,
        isFinal,
      })
    }

    if (callModels.length === 0 && respondEvent) {
      iterations.push({
        index: 1,
        reasoning: undefined,
        preToolUseHooks: [],
        toolCalls: [],
        afterToolHooks: [],
        isFinal: true,
      })
    }

    return iterations
  }

  function groupSessionEvents(
    sessionId: string,
    events: TraceEvent[],
    title?: string,
  ): StructuredSession {
    const sorted = [...events].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
    )

    if (sorted.length === 0) {
      return {
        sessionId, title,
        turnStart: { events: [], durationMs: 0 },
        turns: [],
        stats: { totalTurns: 0, totalTokens: 0, totalDurationMs: 0 },
      }
    }

    interface Boundary { index: number; type: 'user' | 'agent'; snippet: string; sourceEvent?: TraceEvent }
    const boundaries: Boundary[] = []
    let lastInputSnippet = ''

    for (let i = 0; i < sorted.length; i++) {
      const evt = sorted[i]

      if ((evt as any).event_type === 'lifecycle.handoff') {
        const meta = parseMeta(evt)
        boundaries.push({
          index: i, type: 'agent',
          snippet: meta.intent || meta.phase || 'Agent handoff',
          sourceEvent: evt,
        })
        continue
      }

      if (evt.node === 'call_model') {
        const meta = parseMeta(evt)
        const snippet = meta.model_input_snippet || ''
        if (snippet && snippet !== lastInputSnippet) {
          lastInputSnippet = snippet
          boundaries.push({ index: i, type: 'user', snippet, sourceEvent: evt })
        }
      }
    }

    if (boundaries.length === 0) {
      return {
        sessionId, title,
        turnStart: { events: sorted, durationMs: computeDuration(sorted) },
        turns: [],
        stats: {
          totalTurns: 0,
          totalTokens: sumTokens(sorted),
          totalDurationMs: computeDuration(sorted),
        },
      }
    }

    const firstIdx = boundaries[0].index
    const turnStartEvents = sorted.slice(0, firstIdx)
    const turnStart: TurnStart = {
      events: turnStartEvents,
      durationMs: computeDuration(turnStartEvents),
    }

    const turns: Turn[] = []
    for (let b = 0; b < boundaries.length; b++) {
      const startIdx = boundaries[b].index
      const endIdx = b + 1 < boundaries.length ? boundaries[b + 1].index : sorted.length
      const turnEvents = sorted.slice(startIdx, endIdx)

      const input: TurnInput = {
        type: boundaries[b].type,
        snippet: boundaries[b].snippet,
        timestamp: sorted[startIdx].created_at,
        sourceEvent: boundaries[b].sourceEvent,
      }

      const iterations = buildIterations(turnEvents)

      const postModelHooks = turnEvents.filter(e => {
        if ((e as any).event_type !== 'lifecycle.hook_execution') return false
        return parseMeta(e).hook_event === 'PostModelCall'
      })

      const sessionEndHooks = turnEvents.filter(e => {
        if ((e as any).event_type !== 'lifecycle.hook_execution') return false
        return parseMeta(e).hook_event === 'SessionEnd'
      })

      const stats = {
        totalTokens: sumTokens(turnEvents),
        iterationCount: iterations.filter(it => !it.isFinal).length,
        durationMs: computeDuration(turnEvents),
      }

      turns.push({
        turnIndex: b + 1,
        input, iterations,
        postModelHooks,
        sessionEndHooks: sessionEndHooks.length > 0 ? sessionEndHooks : undefined,
        stats,
      })
    }

    return {
      sessionId, title,
      turnStart, turns,
      stats: {
        totalTurns: turns.length,
        totalTokens: sumTokens(sorted),
        totalDurationMs: computeDuration(sorted),
      },
    }
  }

  const structuredSession = computed<StructuredSession | null>(() => {
    if (events.value.length === 0) return null
    const sid = events.value[0]?.session_id || ''
    const session = sessions.value.find(s => s.session_id === sid)
    return groupSessionEvents(sid, events.value, session?.title)
  })

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

  function exportStructured(sessionId: string) {
    if (!structuredSession.value) return
    const blob = new Blob([JSON.stringify(structuredSession.value, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `trace-structured-${sessionId}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return {
    sessions, events, summary, loading,
    structuredSession,
    fetchSessions, fetchSessionDetail, fetchSummary,
    submitFeedback, fetchFeedback,
    exportTrace, exportStructured,
  }
}
