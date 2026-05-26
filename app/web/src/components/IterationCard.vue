<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from '@/composables/useI18n'
import type { Iteration } from '@/types'
import ReasoningBlock from './ReasoningBlock.vue'
import HookGroup from './HookGroup.vue'
import ToolCallCard from './ToolCallCard.vue'

const { t } = useI18n()

const props = defineProps<{
  iteration: Iteration
}>()

const expanded = ref(false)

function formatMs(ms: number | undefined): string {
  if (!ms) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

const iterDuration = computed(() => {
  let total = props.iteration.reasoning?.duration_ms || 0
  for (const tc of props.iteration.toolCalls) {
    total += tc.call.duration_ms || 0
    if (tc.result?.duration_ms) total += tc.result.duration_ms
  }
  return total
})

const toolCount = computed(() => props.iteration.toolCalls.length)

function eventData(evt: any, key: string, fallback: any = ''): any {
  // TraceEvent uses metadata (string), AgentEvent uses data (dict)
  const d = evt?.data
  if (d && typeof d === 'object') return d[key] ?? fallback
  const m = evt?.metadata
  if (m && typeof m === 'string') {
    try { const p = JSON.parse(m); return p[key] ?? fallback } catch { return fallback }
  }
  return fallback
}
</script>

<template>
  <div class="ic-root" :class="{ expanded }">
    <div class="ic-header" @click="expanded = !expanded">
      <span class="ic-arrow" :class="{ open: expanded }">▶</span>
      <span class="ic-icon">{{ iteration.isFinal ? '✅' : '🔄' }}</span>
      <span class="ic-label">
        <template v-if="iteration.isFinal">{{ t('trace.finalReply') }}</template>
        <template v-else>{{ t('trace.iteration') }} {{ iteration.index }}</template>
        <template v-if="iteration.internalTurn != null"> · T{{ iteration.internalTurn }}</template>
      </span>
      <span v-if="toolCount" class="ic-tool-count">
        🧠 → {{ toolCount > 1 ? `🔧×${toolCount}` : '🔧' }}
      </span>
      <span v-if="iterDuration" class="ic-dur">{{ formatMs(iterDuration) }}</span>
    </div>
    <div v-if="expanded" class="ic-body">
      <ReasoningBlock v-if="iteration.reasoning" :event="iteration.reasoning" />
      <HookGroup
        v-if="iteration.preToolUseHooks.length > 0 || !iteration.isFinal"
        :hooks="iteration.preToolUseHooks"
        :title="t('trace.preToolHooks')"
      />
      <div v-if="iteration.guardEvents.length" class="guard-events">
        <div v-for="(ge, i) in iteration.guardEvents" :key="i" class="guard-line" :class="ge.type">
          <span class="ge-icon">{{ ge.type === 'guard_block' ? '🛡️✗' : '🛡️✓' }}</span>
          <span class="ge-tool">{{ eventData(ge, 'tool_name', '?') }}</span>
          <span v-if="ge.type === 'guard_block'" class="ge-reason">{{ eventData(ge, 'guard') }}: {{ eventData(ge, 'reason') }}</span>
          <span v-else class="ge-reason">passed</span>
        </div>
      </div>
      <div v-if="iteration.approvalEvents.length" class="approval-events">
        <div v-for="(ae, i) in iteration.approvalEvents" :key="i" class="approval-line" :class="ae.type">
          <span class="ae-icon">{{ ae.type === 'approval_required' ? '⏳' : eventData(ae, 'approved') ? '✅' : '❌' }}</span>
          <span class="ae-tool">{{ eventData(ae, 'tool_name', '?') }}</span>
          <span v-if="ae.type === 'approval_required'" class="ae-reason">等待审批</span>
          <span v-else class="ae-reason">{{ eventData(ae, 'reason') }}</span>
        </div>
      </div>
      <ToolCallCard
        v-for="(tc, i) in iteration.toolCalls"
        :key="i"
        :tool-call="tc"
      />
      <HookGroup
        v-if="iteration.afterToolHooks.length > 0 || !iteration.isFinal"
        :hooks="iteration.afterToolHooks"
        :title="t('trace.afterToolHooks')"
      />
    </div>
  </div>
</template>

<style scoped>
.guard-events, .approval-events {
  margin: 4px 0 4px 8px;
  display: flex; flex-direction: column; gap: 2px;
}
.guard-line, .approval-line {
  font-size: 12px; padding: 2px 8px; border-radius: 4px;
  display: flex; align-items: center; gap: 6px;
  opacity: 0.9;
}
.guard-line { background: rgba(99,102,241,0.08); }
.guard-line.guard_block { background: rgba(239,68,68,0.08); }
.approval-line { background: rgba(245,158,11,0.08); }
.approval-line.approval_resolved { background: rgba(34,197,94,0.06); }
.ge-icon, .ae-icon { font-size: 11px; flex-shrink: 0; }
.ge-tool, .ae-tool { font-weight: 600; font-family: monospace; color: var(--text-primary); }
.ge-reason, .ae-reason { color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.ic-root {
  border-bottom: 1px solid var(--border-light);
  overflow: hidden;
  font-size: 12px;
}
.ic-root:last-child { border-bottom: none; }
.ic-header {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 12px; cursor: pointer; user-select: none;
  color: var(--text-secondary); transition: background var(--transition);
}
.ic-header:hover { background: var(--bg-hover); }
.ic-arrow { font-size: 7px; transition: transform var(--transition); flex-shrink: 0; color: var(--text-muted); }
.ic-arrow.open { transform: rotate(90deg); }
.ic-icon { font-size: 11px; flex-shrink: 0; }
.ic-label { font-weight: 600; font-size: 11px; }
.ic-tool-count { font-size: 10px; color: var(--text-muted); }
.ic-dur {
  margin-left: auto; font-family: monospace; font-size: 10px;
  color: var(--text-muted);
}
.ic-body { padding: 6px 12px 8px; }
</style>
