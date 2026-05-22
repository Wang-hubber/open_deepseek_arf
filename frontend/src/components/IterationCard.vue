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
</script>

<template>
  <div class="ic-root" :class="{ expanded }">
    <div class="ic-header" @click="expanded = !expanded">
      <span class="ic-arrow" :class="{ open: expanded }">▶</span>
      <span class="ic-icon">{{ iteration.isFinal ? '✅' : '🔄' }}</span>
      <span class="ic-label">
        {{ iteration.isFinal ? t('trace.finalReply') : `${t('trace.iteration')} ${iteration.index}` }}
      </span>
      <span v-if="!iteration.isFinal && toolCount" class="ic-tool-count">
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
