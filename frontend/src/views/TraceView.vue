<script setup lang="ts">
import { ref, computed, onMounted, watch, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { TooltipComponent, GridComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useTrace } from '@/composables/useTrace'
import CollapsibleSection from '@/components/CollapsibleSection.vue'
import type { TraceEvent, TraceEventGroup, FeedbackItem, ParsedTraceMetadata } from '@/types'

echarts.use([BarChart, LineChart, TooltipComponent, GridComponent, LegendComponent, CanvasRenderer])

const router = useRouter()

const { sessions, events, summary, loading, fetchSessions, fetchSessionDetail, fetchSummary, fetchFeedback, exportTrace } = useTrace()

const selectedSession = ref<string | null>(null)
const feedback = ref<FeedbackItem[]>([])
const chartContainer = ref<HTMLElement | null>(null)
const chartInstance = shallowRef<echarts.ECharts | null>(null)

onMounted(() => {
  fetchSessions()
  fetchSummary()
})

watch(selectedSession, async (sid) => {
  if (sid) {
    await fetchSessionDetail(sid)
    feedback.value = await fetchFeedback(sid)
  }
})

function parseMeta(evt: TraceEvent): ParsedTraceMetadata {
  if (!evt.metadata) return {}
  try {
    return JSON.parse(evt.metadata)
  } catch {
    return {}
  }
}

const turnGroups = computed<TraceEventGroup[]>(() => {
  const m = new Map<number, TraceEvent[]>()
  for (const e of events.value) {
    const t = e.turn
    if (!m.has(t)) m.set(t, [])
    m.get(t)!.push(e)
  }
  return [...m.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([turn, evs]) => {
      const group: TraceEventGroup = { turn, events: evs, toolCalls: [], hooks: [] }
      for (const e of evs) {
        switch (e.node) {
          case 'classify': group.classify = e; break
          case 'call_model': group.modelCall = e; break
          case 'execute_tools': group.toolCalls.push(e); break
          case 'hook': group.hooks.push(e); break
          case 'respond': group.respond = e; break
          case 'recovery': group.recovery = e; break
        }
      }
      return group
    })
})

interface TimelineItem { event: TraceEvent; time: Date; timeStr: string; meta: ParsedTraceMetadata }

const timeline = computed<TimelineItem[]>(() => {
  const items: TimelineItem[] = []
  for (const e of events.value) {
    const time = e.created_at ? new Date(e.created_at) : new Date()
    items.push({
      event: e, time,
      timeStr: time.toLocaleTimeString('zh-CN', { hour12: false }) + '.' + String(time.getMilliseconds()).padStart(3, '0'),
      meta: parseMeta(e),
    })
  }
  items.sort((a, b) => a.time.getTime() - b.time.getTime())
  return items
})

function formatMs(ms: number | undefined) {
  if (!ms) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

function statusBadgeClass(status: string) {
  if (status === 'error' || status === 'blocked_by_hook') return 'badge-error'
  if (status === 'skipped') return 'badge-skip'
  return 'badge-ok'
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    ok: 'OK', error: 'Error', skipped: 'Skipped',
    blocked_by_hook: 'Blocked', inject: 'Inject',
  }
  return labels[status] || status
}

function eventTypeIcon(evt: TraceEvent): string {
  if (evt.node) {
    const icons: Record<string, string> = { classify: '🎯', call_model: '🧠', execute_tools: '🔧', respond: '✅', recovery: '🔄', hook: '🔗' }
    return icons[evt.node] || '⚙️'
  }
  const lc = evt as any
  const icons: Record<string, string> = {
    'lifecycle.session_start': '🚀', 'lifecycle.session_end': '🏁',
    'lifecycle.hook_execution': '🪝', 'lifecycle.handoff': '🤝',
    'lifecycle.compaction': '📦', 'lifecycle.prompt_snapshot': '📸',
    'lifecycle.model_switch': '🔄', 'lifecycle.init': '⚡', 'lifecycle.config': '⚙️',
  }
  return icons[lc.event_type] || '📌'
}

function eventTypeLabel(evt: TraceEvent): string {
  if (evt.node) return evt.node
  return ((evt as any).event_type || '').replace('lifecycle.', '').replace('graph.', '')
}

function eventBorderColor(evt: TraceEvent): string {
  const m: Record<string, string> = { classify: 'var(--accent)', call_model: '#6366f1', execute_tools: 'var(--success)', respond: 'var(--success)', recovery: '#f59e0b', hook: '#8b5cf6' }
  if (evt.node) return m[evt.node] || '#646480'
  const lm: Record<string, string> = {
    'lifecycle.session_start': '#3b82f6', 'lifecycle.session_end': '#6366f1',
    'lifecycle.hook_execution': '#a78bfa', 'lifecycle.handoff': '#06b6d4',
    'lifecycle.compaction': '#f97316', 'lifecycle.prompt_snapshot': '#64748b',
    'lifecycle.model_switch': '#eab308', 'lifecycle.init': '#22c55e', 'lifecycle.config': '#64748b',
  }
  return lm[(evt as any).event_type] || '#646480'
}

function copyEventJson(evt: TraceEvent) {
  navigator.clipboard.writeText(JSON.stringify(evt, null, 2))
}

function formatMetaJson(meta: ParsedTraceMetadata, evt: TraceEvent): string {
  const enriched: any = { ...(meta || {}) }
  if (evt.model && !('model' in enriched)) enriched.model = evt.model
  if (evt.tool_name && !('tool_name' in enriched)) enriched.tool_name = evt.tool_name
  if (evt.error_msg) enriched.error_msg = evt.error_msg
  return JSON.stringify(enriched, null, 2)
}

function bgColor(status: string) {
  if (status === 'error') return '#ef4444'
  if (status === 'blocked_by_hook') return '#f59e0b'
  if (status === 'skipped') return '#646480'
  return '#22c55e'
}

const sessionStats = computed(() => {
  let modelCalls = 0, toolCalls = 0, hookOk = 0, hookErr = 0, hookBlocked = 0
  for (const e of events.value) {
    if (e.node === 'call_model') modelCalls++
    if (e.node === 'execute_tools') toolCalls++
    if (e.node === 'hook' || (e as any).event_type === 'lifecycle.hook_execution') {
      if (e.status === 'ok') hookOk++
      else if (e.status === 'error') hookErr++
      else if (e.status === 'blocked_by_hook') hookBlocked++
    }
  }
  return { modelCalls, toolCalls, hookOk, hookErr, hookBlocked, totalEvents: events.value.length }
})

function nodeIcon(node: string) { return eventTypeIcon({ node } as TraceEvent) }

watch(timeline, () => {
  if (chartContainer.value) renderChart()
})

function renderChart() {
  const el = chartContainer.value
  if (!el || timeline.value.length === 0) return

  if (chartInstance.value) {
    chartInstance.value.dispose()
  }

  const chart = echarts.init(el!, undefined, { renderer: 'canvas' })
  chartInstance.value = chart

  const categories: string[] = []
  const durations: number[] = []
  const colors: string[] = []

  for (const item of timeline.value) {
    const e = item.event
    if (e.duration_ms && e.duration_ms > 0) {
      const label = e.tool_name
        ? `${item.timeStr} ${eventTypeLabel(e)}:${e.tool_name}`
        : `${item.timeStr} ${eventTypeLabel(e)}`
      categories.push(label)
      durations.push(e.duration_ms)
      colors.push(bgColor(e.status))
    }
  }

  if (categories.length === 0) return

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(17,17,34,0.95)',
      borderColor: 'rgba(255,255,255,0.06)',
      textStyle: { color: '#e4e4ed', fontSize: 12 },
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params
        return `${p.name}<br/>Duration: ${formatMs(p.value)}`
      },
    },
    grid: { left: '30%', right: '8%', top: 10, bottom: 10 },
    xAxis: {
      type: 'value',
      axisLabel: { color: '#9d9db8', fontSize: 10, formatter: (v: number) => formatMs(v) },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
    },
    yAxis: {
      type: 'category',
      data: categories,
      axisLabel: { color: '#9d9db8', fontSize: 10 },
      inverse: true,
    },
    series: [{
      type: 'bar',
      data: durations.map((v, i) => ({
        value: v,
        itemStyle: { color: colors[i], borderRadius: [0, 3, 3, 0] },
      })),
      barMaxWidth: 18,
    }],
  })

  const ro = new ResizeObserver(() => chart.resize())
  ro.observe(el)
}

function handleExport() {
  if (selectedSession.value) exportTrace(selectedSession.value)
}

function tokensDisplay(evt: TraceEvent): string {
  const parts: string[] = []
  if (evt.prompt_tokens) parts.push(`P:${evt.prompt_tokens.toLocaleString()}`)
  if (evt.completion_tokens) parts.push(`C:${evt.completion_tokens.toLocaleString()}`)
  if (parts.length === 0 && evt.total_tokens) return `${evt.total_tokens.toLocaleString()} tok`
  return parts.join(' ')
}
</script>

<template>
  <div class="tv-layout">
    <!-- Left sidebar -->
    <aside class="tv-sidebar">
      <div class="tv-sidebar-header">
        <button class="tv-back" @click="router.replace('/')">← 返回</button>
        <h2 class="tv-title">Trace 追踪</h2>
      </div>
      <div v-if="summary" class="tv-summary">
        <div class="tv-stat"><span>{{ summary.total_sessions }}</span> 会话</div>
        <div class="tv-stat"><span>{{ (summary.total_tokens || 0).toLocaleString() }}</span> tok</div>
        <div class="tv-stat up">{{ summary.thumbs_up || 0 }} 👍</div>
        <div class="tv-stat down">{{ summary.thumbs_down || 0 }} 👎</div>
      </div>
      <div v-if="loading && !sessions.length" class="tv-empty">加载中...</div>
      <div v-else-if="!sessions.length" class="tv-empty">暂无 trace 数据</div>
      <div v-else class="tv-session-list">
        <div
          v-for="s in sessions" :key="s.session_id"
          class="tv-session-item"
          :class="{ active: selectedSession === s.session_id }"
          @click="selectedSession = s.session_id"
        >
          <div class="tv-si-title">{{ s.title || s.session_id }}</div>
          <div class="tv-si-meta">
            {{ s.event_count }} 事件 · {{ (s.total_tokens || 0).toLocaleString() }} tok · {{ formatMs(s.total_duration_ms) }}
          </div>
        </div>
      </div>
    </aside>

    <!-- Right main -->
    <main class="tv-main">
      <template v-if="!selectedSession">
        <div class="tv-placeholder">选择一个会话查看 Trace 详情</div>
      </template>
      <template v-else>
        <div class="tv-detail-header">
          <h3>{{ selectedSession }}</h3>
          <div class="tv-header-actions">
            <button class="sb-btn" @click="handleExport">导出 JSON</button>
          </div>
        </div>

        <div v-if="loading" class="tv-empty">加载中...</div>

        <!-- Session stats chips -->
        <div v-if="sessionStats.totalEvents" class="tv-session-stats">
          <span class="tv-stat-chip">📊 {{ sessionStats.totalEvents }} 事件</span>
          <span class="tv-stat-chip">🧠 {{ sessionStats.modelCalls }} 模型</span>
          <span class="tv-stat-chip">🔧 {{ sessionStats.toolCalls }} 工具</span>
          <span class="tv-stat-chip">🪝 {{ sessionStats.hookOk + sessionStats.hookErr + sessionStats.hookBlocked }} hooks</span>
          <span v-if="sessionStats.hookErr" class="tv-stat-chip tv-stat-chip-err">{{ sessionStats.hookErr }} err</span>
          <span v-if="sessionStats.hookBlocked" class="tv-stat-chip tv-stat-chip-warn">{{ sessionStats.hookBlocked }} blocked</span>
        </div>

        <!-- Duration chart overview -->
        <div v-if="timeline.length" ref="chartContainer" class="tv-chart-container"></div>

        <!-- Unified timeline -->
        <div class="tv-timeline">
          <div v-for="(item, idx) in timeline" :key="item.event.id ?? idx" class="tv-timeline-item">
            <div class="tv-tl-time">
              <span class="tv-tl-time-text">{{ item.timeStr }}</span>
              <span v-if="item.event.turn" class="tv-tl-turn">T{{ item.event.turn }}</span>
            </div>
            <div
              class="tv-card"
              :style="{ borderLeftColor: eventBorderColor(item.event) }"
            >
              <div class="tv-card-head">
                <span class="tv-card-icon">{{ eventTypeIcon(item.event) }}</span>
                <span class="tv-card-node">{{ eventTypeLabel(item.event) }}</span>
                <span v-if="item.event.model" class="tv-card-model-name">{{ item.event.model }}</span>
                <span v-if="item.event.tool_name" class="tv-card-model-name">{{ item.event.tool_name }}</span>
                <span :class="'tv-badge ' + statusBadgeClass(item.event.status)">{{ statusLabel(item.event.status) }}</span>
                <span v-if="item.event.duration_ms" class="tv-card-dur">{{ formatMs(item.event.duration_ms) }}</span>
                <span v-if="item.event.total_tokens" class="tv-card-tokens">{{ tokensDisplay(item.event) }}</span>
                <button class="tv-copy-btn" title="复制 JSON" @click.stop="copyEventJson(item.event)">⎘</button>
              </div>
              <CollapsibleSection title="Metadata" :defaultOpen="false">
                <pre class="tv-snippet">{{ formatMetaJson(item.meta, item.event) }}</pre>
              </CollapsibleSection>
            </div>
          </div>
        </div>

        <!-- Feedback -->
        <div v-if="feedback.length" class="tv-feedback">
          <h4>反馈记录</h4>
          <div v-for="fb in feedback" :key="fb.id" class="tv-fb-item">
            <span class="tv-fb-rating">{{ fb.rating === 1 ? '👍' : '👎' }}</span>
            <span class="tv-fb-msg">消息 #{{ fb.message_index + 1 }}</span>
            <span v-if="fb.feedback_text" class="tv-fb-text"> — "{{ fb.feedback_text }}"</span>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>

<style scoped>
/* ── Layout ── */
.tv-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  height: 100vh;
  background: var(--bg-primary, #0a0a1a);
  color: var(--text-primary);
}

/* ── Sidebar ── */
.tv-sidebar {
  border-right: 1px solid var(--border-glass, rgba(255,255,255,0.06));
  display: flex; flex-direction: column;
  background: rgba(7,7,16,0.8);
  overflow: hidden;
}
.tv-sidebar-header {
  padding: 16px; border-bottom: 1px solid var(--border-glass, rgba(255,255,255,0.06));
  display: flex; flex-direction: column; gap: 10px;
}
.tv-back {
  background: none; border: 1px solid rgba(255,255,255,0.1);
  color: var(--text-secondary); cursor: pointer; font-size: 12px;
  padding: 4px 12px; border-radius: var(--radius-sm); align-self: flex-start;
  transition: all var(--transition);
}
.tv-back:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }
.tv-title { margin: 0; font-size: 15px; }
.tv-summary {
  display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 16px;
  border-bottom: 1px solid var(--border-glass, rgba(255,255,255,0.06));
  font-size: 11px;
}
.tv-stat { color: var(--text-secondary); }
.tv-stat span { color: var(--text-primary); font-weight: 700; }
.tv-stat.up { color: var(--success); }
.tv-stat.down { color: var(--danger, #ef4444); }
.tv-empty { padding: 24px 16px; color: var(--text-secondary); font-size: 13px; text-align: center; }
.tv-session-list { flex: 1; overflow-y: auto; }
.tv-session-item {
  padding: 12px 16px; cursor: pointer;
  border-bottom: 1px solid rgba(255,255,255,0.03);
  transition: background var(--transition, 0.15s);
}
.tv-session-item:hover { background: rgba(255,255,255,0.04); }
.tv-session-item.active { background: rgba(255,255,255,0.06); border-left: 3px solid var(--accent); }
.tv-si-title {
  font-size: 13px; font-weight: 600; margin-bottom: 3px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.tv-si-meta { font-size: 11px; color: var(--text-secondary); }

/* ── Main ── */
.tv-main {
  padding: 20px 24px; overflow-y: auto;
  display: flex; flex-direction: column;
}
.tv-placeholder {
  margin: auto; color: var(--text-secondary); font-size: 14px;
}
.tv-detail-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px;
}
.tv-detail-header h3 { margin: 0; font-size: 14px; font-family: monospace; }
.tv-header-actions { display: flex; gap: 8px; }

/* ── Chart ── */
.tv-chart-container {
  width: 100%; height: 200px; margin-bottom: 20px;
  background: var(--bg-card, #181830);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-md);
  overflow: hidden;
}

/* ── Session stats ── */
.tv-session-stats { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.tv-stat-chip { font-size: 11px; padding: 2px 8px; border-radius: var(--radius-full, 99px); background: rgba(255,255,255,0.05); color: var(--text-secondary); }
.tv-stat-chip-err { background: rgba(239,68,68,0.12); color: #ef4444; }
.tv-stat-chip-warn { background: rgba(245,158,11,0.12); color: #f59e0b; }

/* ── Unified Timeline ── */
.tv-timeline { flex: 1; position: relative; padding-left: 20px; }
.tv-timeline::before { content: ''; position: absolute; left: 8px; top: 0; bottom: 0; width: 2px; background: linear-gradient(to bottom, rgba(255,255,255,0.08), rgba(255,255,255,0.02)); }
.tv-timeline-item { position: relative; margin-bottom: 10px; padding-left: 18px; }
.tv-timeline-item::before { content: ''; position: absolute; left: -15px; top: 10px; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
.tv-tl-time { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; font-family: 'JetBrains Mono', monospace; }
.tv-tl-time-text { font-size: 11px; color: var(--text-muted); }
.tv-tl-turn { font-size: 10px; color: var(--accent); background: rgba(99,102,241,0.12); padding: 0 5px; border-radius: var(--radius-full, 99px); }

/* Copy button */
.tv-copy-btn { margin-left: auto; background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 12px; padding: 2px 6px; border-radius: 4px; opacity: 0; transition: opacity 0.15s; }
.tv-card-head:hover .tv-copy-btn { opacity: 1; }
.tv-copy-btn:hover { background: rgba(255,255,255,0.08); color: var(--text-primary); }

/* ── Cards ── */
.tv-card {
  background: var(--bg-card, #181830);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.tv-card-classify { border-left: 3px solid var(--accent); }
.tv-card-model { border-left: 3px solid #6366f1; }
.tv-card-tool { border-left: 3px solid var(--success); }
.tv-card-tool:has(.badge-error) { border-left-color: var(--error); }
.tv-card-hook { border-left: 3px solid #8b5cf6; }
.tv-card-respond { border-left: 3px solid var(--success); }
.tv-card-recovery { border-left: 3px solid var(--warning, #f59e0b); }

.tv-card-head {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; font-size: 12px;
}
.tv-card-icon { width: 18px; text-align: center; flex-shrink: 0; }
.tv-card-node {
  width: 85px; color: var(--text-secondary); font-family: monospace; font-size: 11px;
  flex-shrink: 0;
}
.tv-card-model-name {
  color: var(--accent); font-size: 10px; font-family: monospace;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 160px;
}
.tv-card-dur {
  margin-left: auto; color: var(--text-muted); font-size: 11px;
  font-family: monospace; white-space: nowrap;
}
.tv-card-tokens {
  color: var(--text-muted); font-size: 10px; font-family: monospace; white-space: nowrap;
}
.tv-badge {
  font-size: 10px; padding: 1px 7px; border-radius: var(--radius-full);
  font-weight: 600; white-space: nowrap;
}
.badge-ok { background: var(--success-bg, rgba(34,197,94,0.1)); color: var(--success); }
.badge-error { background: var(--error-bg, rgba(239,68,68,0.1)); color: var(--error); }
.badge-skip { background: rgba(255,255,255,0.05); color: var(--text-muted); }
.badge-cat {
  background: rgba(99,102,241,0.12); color: var(--accent);
  font-family: monospace; font-size: 9px; text-transform: uppercase;
}

/* ── Metadata content ── */
.tv-meta-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
}
.tv-meta-item {
  display: flex; flex-direction: column; gap: 2px;
}
.tv-meta-label {
  font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px;
}
.tv-meta-value {
  font-size: 12px; color: var(--text-primary); font-family: monospace;
}

.tv-snippet {
  margin: 0; font-size: 11px; line-height: 1.5; color: var(--text-secondary);
  white-space: pre-wrap; word-break: break-all;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  max-height: 200px; overflow-y: auto;
}
.tv-snippet-error { color: var(--error-text); }

.tv-hook-msg {
  font-size: 12px; color: var(--text-secondary); line-height: 1.5;
}

/* ── Feedback ── */
.tv-feedback {
  margin-top: 24px; padding-top: 16px;
  border-top: 1px solid var(--border-glass);
}
.tv-feedback h4 { margin: 0 0 10px; font-size: 13px; }
.tv-fb-item { padding: 4px 0; font-size: 12px; color: var(--text-secondary); }
.tv-fb-rating { margin-right: 6px; }
.tv-fb-text { color: var(--text-primary); font-style: italic; }

/* ── Buttons ── */
.sb-btn {
  background: none; border: 1px solid rgba(255,255,255,0.1);
  color: var(--text-secondary); cursor: pointer; font-size: 12px;
  padding: 4px 12px; border-radius: var(--radius-sm);
  transition: all var(--transition);
}
.sb-btn:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }

/* ── Responsive ── */
@media (max-width: 900px) {
  .tv-layout { grid-template-columns: 1fr; }
  .tv-sidebar { display: none; }
}
@media (max-width: 480px) {
  .tv-main { padding: 16px; }
  .tv-card-head { flex-wrap: wrap; gap: 4px; }
  .tv-card-node { width: auto; }
  .tv-chart-container { height: 150px; }
}
@media (min-width: 1600px) {
  .tv-layout { grid-template-columns: 340px 1fr; }
}
</style>
