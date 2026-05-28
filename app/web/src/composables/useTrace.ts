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

  function collectHooks(events: AgentEvent[]): { pre: any[]; post: any[] } {
    const pre: any[] = []; const post: any[] = []
    for (const e of events) {
      if (e.type !== 'hook_start' && e.type !== 'hook_end') continue
      const eventName = dataField(e, 'event', '')
      const hook = {
        ...e, node: 'hook', event_type: 'lifecycle.hook_execution',
        metadata: JSON.stringify({
          hook_event: eventName,
          hook_status: e.type === 'hook_end'
            ? (dataField(e, 'failed', 0) === 0 ? 'ok' : 'partial_failure')
            : 'running',
          hook_message: e.type === 'hook_end'
            ? `passed: ${dataField(e, 'passed', 0)}, failed: ${dataField(e, 'failed', 0)}`
            : '',
        }),
      } as any
      if (eventName.startsWith('pre_')) pre.push(hook)
      else post.push(hook)
    }
    return { pre, post }
  }

  function buildIterations(turnEvents: AgentEvent[]): Iteration[] {
    // Find model_call_start events as iteration boundaries
    const modelStarts: AgentEvent[] = []
    for (const e of turnEvents) {
      if (e.type === 'model_call_start') modelStarts.push(e)
    }
    // Also include model_call_end without start (shouldn't happen but be safe)
    const modelEnds: AgentEvent[] = []
    for (const e of turnEvents) {
      if (e.type === 'model_call_end') modelEnds.push(e)
    }

    // Use model_call_start as boundaries
    const boundaries = modelStarts.length > 0 ? modelStarts : modelEnds
    if (boundaries.length === 0) {
      // No model calls — just pair any tool calls
      const toolPairs = pairToolCalls(turnEvents)
      const hooks = collectHooks(turnEvents)
      if (toolPairs.length === 0 && hooks.pre.length === 0 && hooks.post.length === 0) return []
      return [{
        index: 1, toolCalls: toolPairs,
        guardEvents: [], approvalEvents: [],
        preToolUseHooks: hooks.pre, afterToolHooks: hooks.post, isFinal: true,
      }]
    }

    const iterations: Iteration[] = []
    for (let bi = 0; bi < boundaries.length; bi++) {
      const bTs = ts(boundaries[bi])
      const nextTs = bi + 1 < boundaries.length ? ts(boundaries[bi + 1]) : Infinity

      // Events in [bTs, nextTs)
      const inRange: AgentEvent[] = []
      for (const e of turnEvents) {
        const et = ts(e)
        if (et >= bTs && et < nextTs) inRange.push(e)
      }

      // Find model_call_end in this range for response text
      const mcEnd = inRange.find(e => e.type === 'model_call_end')
      const content = mcEnd ? dataField(mcEnd, 'content', '') : ''

      // Tool calls in range
      const toolEvents = inRange.filter(e => e.type === 'tool_call_start' || e.type === 'tool_call_end')
      const toolPairs = pairToolCalls(toolEvents)

      // Guard & approval events in range
      const guardEvents = inRange.filter(e =>
        e.type === 'guard_block' || e.type === 'guard_pass'
      )
      const approvalEvents = inRange.filter(e =>
        e.type === 'approval_required' || e.type === 'approval_resolved'
      )

      // Protection events (rate limiter + circuit breaker)
      const protectionEvents = inRange.filter(e =>
        e.type === 'rate_limited' || e.type === 'circuit_opened' ||
        e.type === 'circuit_half_open' || e.type === 'circuit_closed' ||
        e.type === 'breaker_blocked'
      )

      // Hooks in range
      const hooks = collectHooks(inRange)

      // Reasoning block: show model content as thinking/response
      const reasoningEvent = content ? ({
        type: 'model_call_end', data: {}, turn: boundaries[bi].turn,
        timestamp: boundaries[bi].timestamp, node: 'call_model', duration_ms: 0,
        metadata: JSON.stringify({ model_output_snippet: content.slice(0, 3000) }),
      } as any) : null

      const isLast = bi === boundaries.length - 1
      const hasToolCalls = toolPairs.length > 0
      const hasContent = !!content

      iterations.push({
        index: bi + 1,
        internalTurn: boundaries[bi].turn,
        reasoning: reasoningEvent || undefined,
        preToolUseHooks: hooks.pre as any,
        toolCalls: toolPairs,
        guardEvents,
        approvalEvents,
        protectionEvents,
        afterToolHooks: hooks.post as any,
        isFinal: isLast && hasContent && !hasToolCalls,
      })
    }

    return iterations
  }

  function pairToolCalls(events: AgentEvent[]): ToolCallPair[] {
    const toolPairs: ToolCallPair[] = []
    const pending = new Map<string, AgentEvent>()
    for (const e of events) {
      if (e.type === 'tool_call_start') {
        const id = dataField(e, 'id', '') || dataField(e, 'tool_name', '')
        if (id) pending.set(id, e)
      } else if (e.type === 'tool_call_end') {
        const id = dataField(e, 'id', '') || dataField(e, 'tool_name', '')
        const start = id ? (pending.get(id) || pending.values().next().value) : pending.values().next().value
        if (start) {
          if (id) pending.delete(id); else pending.clear()
          toolPairs.push({
            call: {
              ...start, node: 'execute_tools' as any,
              tool_name: dataField(start, 'tool_name', 'unknown'),
              duration_ms: dataField(e, 'duration_ms', 0),
              status: dataField(e, 'success', false) ? 'ok' : 'error',
              error_msg: dataField(e, 'error', ''),
              metadata: JSON.stringify({ tool_input_snippet: dataField(start, 'arguments', '') }),
            } as any,
            result: {
              ...e, node: 'execute_tools' as any,
              tool_name: dataField(e, 'tool_name', ''),
              status: dataField(e, 'success', false) ? 'ok' : 'error',
              metadata: JSON.stringify({ tool_output_snippet: dataField(e, 'result', '') }),
            } as any,
          })
        }
      }
    }
    return toolPairs
  }

  function groupSessionEvents(
    sessionId: string,
    rawEvents: TraceEvent[],
    title?: string,
  ): StructuredSession {
    const evts = rawEvents.map(e => asAgentEvent(e as any))
    const sorted = [...evts].sort((a, b) => ts(a) - ts(b))

    if (sorted.length === 0) {
      return {
        sessionId, title,
        turnStart: { events: [], durationMs: 0 },
        turns: [],
        stats: { totalTurns: 0, totalTokens: 0, totalDurationMs: 0 },
      }
    }

    // Group events by interaction round → internal turn
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
    if (roundNums.length === 0) {
      return {
        sessionId, title,
        turnStart: { events: [], durationMs: 0 },
        turns: [],
        stats: { totalTurns: 0, totalTokens: 0, totalDurationMs: 0 },
      }
    }

    // Session start from first round's first turn
    const firstTurnMap = roundMap.get(roundNums[0])!
    const firstTurnKeys = [...firstTurnMap.keys()].sort((a, b) => a - b)
    const firstTurnEvents = firstTurnMap.get(firstTurnKeys[0]) || []
    const turnStart: TurnStart = {
      events: firstTurnEvents.filter(e => e.type === 'session_start') as any,
      durationMs: computeDuration(firstTurnEvents),
    }

    // Build Turn objects — each = one user interaction round
    const turns: Turn[] = []
    for (let ri = 0; ri < roundNums.length; ri++) {
      const rn = roundNums[ri]
      const turnMapInRound = roundMap.get(rn)!
      const tns = [...turnMapInRound.keys()].sort((a, b) => a - b)

      // Gather all events for this round
      const roundEvents: AgentEvent[] = []
      for (const t of tns) roundEvents.push(...(turnMapInRound.get(t) || []))

      // Skip round 0 if only session_start
      if (rn === 0 && roundEvents.every(e => e.type === 'session_start')) continue

      const userInputEvt = roundEvents.find(e => e.type === 'user_input')
      const inputSnippet = userInputEvt ? dataField(userInputEvt, 'content', `Round ${rn}`) : `Round ${rn}`

      const input: TurnInput = {
        type: 'user',
        snippet: inputSnippet,
        timestamp: roundEvents.length > 0 ? new Date(ts(roundEvents[0])).toISOString() : new Date().toISOString(),
      }

      // Build iterations per internal turn
      const allIterations: Iteration[] = []
      for (let ti = 0; ti < tns.length; ti++) {
        const itTurnEvents = turnMapInRound.get(tns[ti]) || []
        const iters = buildIterations(itTurnEvents)
        for (const iter of iters) {
          iter.internalTurn = tns[ti]
        }
        allIterations.push(...iters)
      }

      // Hooks for this round
      const postModelHooks: any[] = []
      const sessionEndHooks: any[] = []
      for (const e of roundEvents) {
        if (e.type === 'hook_start' || e.type === 'hook_end') {
          const eventName = dataField(e, 'event', '')
          const hookEvent: any = {
            ...e, node: 'hook', event_type: 'lifecycle.hook_execution',
            metadata: JSON.stringify({
              hook_event: eventName,
              hook_status: e.type === 'hook_end' ? 'ok' : 'running',
            }),
          }
          if (eventName === 'post_model_call') postModelHooks.push(hookEvent as any)
          else if (eventName.includes('session_end') || eventName === 'session_end') sessionEndHooks.push(hookEvent as any)
        }
      }

      turns.push({
        turnIndex: rn,
        input, iterations: allIterations,
        postModelHooks,
        sessionEndHooks: sessionEndHooks.length > 0 ? sessionEndHooks : undefined,
        stats: {
          totalTokens: sumTokens(roundEvents),
          iterationCount: tns.length,
          durationMs: computeDuration(roundEvents),
        },
      })
    }

    return {
      sessionId, title, turnStart, turns,
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
      session_id: sessionId, message_index: messageIndex, rating, feedback_text: feedbackText,
    })
    return res.ok
  }

  async function fetchFeedback(sessionId: string): Promise<FeedbackItem[]> {
    try {
      const res: any = await api.get(`/api/feedback/${sessionId}`)
      return res.feedback || []
    } catch { return [] }
  }

  function exportTrace(sessionId: string) {
    const token = localStorage.getItem('arf_token')
    fetch(`/api/traces/export?session_id=${encodeURIComponent(sessionId)}`, {
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
