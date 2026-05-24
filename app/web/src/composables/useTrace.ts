import { ref, computed } from 'vue'
import { useApi } from './useApi'
import type {
  TraceSession, TraceEvent, TraceSummary, FeedbackItem,
  StructuredSession, Turn, TurnInput, Iteration, ToolCallPair, TurnStart,
} from '@/types'

// AgentEvent — the actual wire format from FileTraceStore (NOT SQL TraceEvent)
interface AgentEvent {
  type: string
  data: Record<string, any>  // includes 'round' (user interaction round)
  turn: number
  timestamp: number
  trace_id: string
  span_id: string
}

function eventRound(e: AgentEvent): number {
  const r = e.data?.round
  return (r != null && r >= 0) ? r : 0
}

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
      // events come as AgentEvent[] from FileTraceStore
      events.value = (res.events || []) as TraceEvent[]
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

  // ── AgentEvent helpers ──────────────────────────────────────────────────

  function asAgentEvent(evt: any): AgentEvent {
    return evt as AgentEvent
  }

  function ts(evt: AgentEvent): number {
    return (evt.timestamp || 0) * 1000 // seconds → ms for Date
  }

  function dataField(evt: AgentEvent, key: string, fallback: any = ''): any {
    return evt.data?.[key] ?? fallback
  }

  // ── Turn grouping (AgentEvent format) ───────────────────────────────────

  function sumTokens(evts: AgentEvent[]): number {
    let total = 0
    for (const e of evts) {
      if (e.type === 'model_call_end') {
        total += (e.data?.usage?.total_tokens || 0) as number
      }
    }
    return total
  }

  function computeDuration(evts: AgentEvent[]): number {
    if (evts.length === 0) return 0
    // Sum per-tool durations, plus time between first and last event
    let sum = 0
    for (const e of evts) {
      if (e.type === 'tool_call_end') {
        sum += (e.data?.duration_ms || 0) as number
      }
    }
    const times = evts.map(e => ts(e)).filter(t => t > 0)
    if (times.length >= 2) sum += Math.max(...times) - Math.min(...times)
    return sum
  }

  function buildIterations(turnEvents: AgentEvent[]): Iteration[] {
    // Collect thinking_delta content (accumulated before any model_call_end)
    const allThinking: string[] = []
    for (const e of turnEvents) {
      if (e.type === 'thinking_delta') {
        allThinking.push(dataField(e, 'content', '') + dataField(e, 'reasoning', ''))
      }
    }
    const reasoningText = allThinking.join('')

    // Pair tool_call_start → tool_call_end by matching name within the turn
    const toolPairs: ToolCallPair[] = []
    const pending = new Map<string, AgentEvent>() // id → start event
    for (const e of turnEvents) {
      if (e.type === 'tool_call_start') {
        const id = dataField(e, 'id', '') || dataField(e, 'tool_name', '')
        if (id) pending.set(id, e)
      } else if (e.type === 'tool_call_end') {
        const id = dataField(e, 'id', '') || dataField(e, 'tool_name', '')
        const start = id ? (pending.get(id) || pending.values().next().value) : pending.values().next().value
        if (start) {
          if (id) pending.delete(id)
          else pending.clear()
          // Build TraceEvent-compatible call artifact from start
          const callEvent: any = {
            ...start,
            node: 'execute_tools',
            tool_name: dataField(start, 'tool_name', 'unknown'),
            duration_ms: dataField(e, 'duration_ms', 0),
            status: dataField(e, 'success', false) ? 'ok' : 'error',
            error_msg: dataField(e, 'error', ''),
            metadata: JSON.stringify({
              tool_input_snippet: dataField(start, 'arguments', ''),
            }),
          }
          const resultEvent: any = {
            ...e,
            node: 'execute_tools',
            tool_name: dataField(e, 'tool_name', ''),
            status: dataField(e, 'success', false) ? 'ok' : 'error',
            metadata: JSON.stringify({
              tool_output_snippet: dataField(e, 'result', ''),
            }),
          }
          toolPairs.push({ call: callEvent, result: resultEvent })
        }
      }
    }

    // Build reasoning TraceEvent-compatible artifact
    const reasoningEvent: any = reasoningText ? {
      type: 'model_call_end',
      data: {},
      turn: turnEvents[0]?.turn || 0,
      timestamp: turnEvents[0]?.timestamp || 0,
      node: 'call_model',
      duration_ms: 0,
      metadata: JSON.stringify({ model_output_snippet: reasoningText }),
    } : null

    // Collect hook events
    const preToolUseHooks: any[] = []
    const afterToolHooks: any[] = []
    for (const e of turnEvents) {
      if (e.type === 'hook_start' || e.type === 'hook_end') {
        const hookEvent: any = {
          ...e,
          node: 'hook',
          event_type: 'lifecycle.hook_execution',
          metadata: JSON.stringify({
            hook_event: dataField(e, 'event', ''),
            hook_status: e.type === 'hook_end' ? (
              (dataField(e, 'failed', 0) === 0) ? 'ok' : 'partial_failure'
            ) : 'running',
            hook_message: e.type === 'hook_end'
              ? `passed: ${dataField(e, 'passed', 0)}, failed: ${dataField(e, 'failed', 0)}`
              : '',
          }),
        }
        if (dataField(e, 'event', '').startsWith('pre_')) {
          preToolUseHooks.push(hookEvent)
        } else {
          afterToolHooks.push(hookEvent)
        }
      }
    }

    if (toolPairs.length === 0 && !reasoningEvent && preToolUseHooks.length === 0 && afterToolHooks.length === 0) {
      return []
    }

    const iteration: Iteration = {
      index: 1,
      reasoning: reasoningEvent || undefined,
      preToolUseHooks,
      toolCalls: toolPairs,
      afterToolHooks,
      isFinal: !toolPairs.length || (turnEvents.some(e => e.type === 'model_call_end' && !dataField(e, 'content', ''))),
    }

    return [iteration]
  }

  function groupSessionEvents(
    sessionId: string,
    rawEvents: TraceEvent[],
    title?: string,
  ): StructuredSession {
    const evts = rawEvents.map(e => asAgentEvent(e as any))

    // Sort by timestamp
    const sorted = [...evts].sort((a, b) => ts(a) - ts(b))

    if (sorted.length === 0) {
      return {
        sessionId, title,
        turnStart: { events: [], durationMs: 0 },
        turns: [],
        stats: { totalTurns: 0, totalTokens: 0, totalDurationMs: 0 },
      }
    }

    // Group events by interaction round (user interaction), then by turn within each round
    const roundMap = new Map<number, Map<number, AgentEvent[]>>()
    for (const e of sorted) {
      const r = eventRound(e)
      const t = e.turn || 0
      if (!roundMap.has(r)) roundMap.set(r, new Map())
      const turnMap = roundMap.get(r)!
      if (!turnMap.has(t)) turnMap.set(t, [])
      turnMap.get(t)!.push(e)
    }

    const roundNums = [...roundMap.keys()].sort((a, b) => a - b)
    // Flatten: all turns across all rounds, ordered by round then turn
    const turnNums: { round: number; turn: number }[] = []
    for (const r of roundNums) {
      const turnMap = roundMap.get(r)!
      const tns = [...turnMap.keys()].sort((a, b) => a - b)
      for (const t of tns) {
        turnNums.push({ round: r, turn: t })
      }
    }
    if (turnNums.length === 0 || roundNums.length === 0) {
      return {
        sessionId, title,
        turnStart: { events: [], durationMs: 0 },
        turns: [],
        stats: { totalTurns: 0, totalTokens: 0, totalDurationMs: 0 },
      }
    }

    // First turn's first round events for session start
    const firstRT = turnNums[0]
    const firstTurnEvents = roundMap.get(firstRT.round)?.get(firstRT.turn) || []
    const turnStartEvents: any[] = firstTurnEvents.filter(e => e.type === 'session_start')
    const turnStart: TurnStart = {
      events: turnStartEvents,
      durationMs: computeDuration(firstTurnEvents),
    }

    // Build Turn objects — each = one user interaction round containing internal turns
    const turns: Turn[] = []
    for (let ri = 0; ri < roundNums.length; ri++) {
      const rn = roundNums[ri]
      const turnMapInRound = roundMap.get(rn)!
      const tns = [...turnMapInRound.keys()].sort((a, b) => a - b)

      // Gather all events for this round
      const roundEvents: AgentEvent[] = []
      for (const t of tns) {
        roundEvents.push(...(turnMapInRound.get(t) || []))
      }
      // Skip round 0 if it only has session_start
      if (rn === 0 && roundEvents.every(e => e.type === 'session_start')) continue

      const tn = tns[0]
      const turnEvents = turnMapInRound.get(tn) || []
      // Skip turn 0 (session_start only)
      if (tn === 0 && turnEvents.every(e => e.type === 'session_start')) continue

      // Extract user input from user_input trace event
      const userInputEvt = turnEvents.find(e => e.type === 'user_input')
      const inputSnippet = userInputEvt ? dataField(userInputEvt, 'content', `Turn ${tn}`) : `Turn ${tn}`

      const input: TurnInput = {
        type: 'user',
        snippet: inputSnippet,
        timestamp: turnEvents.length > 0
          ? new Date(ts(turnEvents[0])).toISOString()
          : new Date().toISOString(),
      }

      // Build iterations from ALL events in this round (across internal turns)
      const iterations = buildIterations(roundEvents)

      // Collect post-model hooks and session-end hooks for this round
      const postModelHooks: any[] = []
      const sessionEndHooks: any[] = []
      for (const e of roundEvents) {
        if (e.type === 'hook_start' || e.type === 'hook_end') {
          const eventName = dataField(e, 'event', '')
          const hookEvent: any = {
            ...e,
            node: 'hook',
            event_type: 'lifecycle.hook_execution',
            metadata: JSON.stringify({
              hook_event: eventName,
              hook_status: e.type === 'hook_end' ? 'ok' : 'running',
            }),
          }
          if (eventName === 'post_model_call') {
            postModelHooks.push(hookEvent)
          } else if (eventName.includes('session_end') || eventName === 'session_end') {
            sessionEndHooks.push(hookEvent)
          }
        }
      }

      const internalTurnCount = tns.length
      const stats = {
        totalTokens: sumTokens(roundEvents),
        iterationCount: internalTurnCount,
        durationMs: computeDuration(roundEvents),
      }

      turns.push({
        turnIndex: rn,
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
    const raw = events.value as any[]
    const sid = raw[0]?.session_id || raw[0]?.data?.session_id || ''
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
