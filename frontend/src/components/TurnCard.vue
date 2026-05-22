<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from '@/composables/useI18n'
import type { Turn } from '@/types'
import TurnInput from './TurnInput.vue'
import IterationCard from './IterationCard.vue'
import TurnEndHooks from './TurnEndHooks.vue'

const { t } = useI18n()

const props = defineProps<{
  turn: Turn
}>()

const expanded = ref(false)

const safeSnippet = computed(() => {
  const s = props.turn?.input?.snippet
  return typeof s === 'string' ? s : String(s ?? '')
})

function toggle() {
  expanded.value = !expanded.value
}

function formatMs(ms: number | undefined): string {
  if (!ms) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}
</script>

<template>
  <div class="tc-root" :class="{ expanded }">
    <div class="tc-header" @click="toggle">
      <span class="tc-arrow" :class="{ open: expanded }">▶</span>
      <span class="tc-icon">{{ turn.input.type === 'user' ? '📥' : '🤖' }}</span>
      <span class="tc-label">
        Turn {{ turn.turnIndex }}
        <span class="tc-type">({{ turn.input.type === 'user' ? t('trace.userInput') : t('trace.agentInput') }})</span>
      </span>
      <span class="tc-snippet">{{ safeSnippet.slice(0, 60) }}{{ safeSnippet.length > 60 ? '...' : '' }}</span>
      <span class="tc-stats">
        {{ formatTokens(turn.stats.totalTokens) }} {{ t('trace.tokens') }}
        <template v-if="turn.stats.iterationCount > 0">
          · {{ turn.stats.iterationCount }} {{ t('trace.iterCount') }}
        </template>
        · {{ formatMs(turn.stats.durationMs) }}
      </span>
    </div>
    <div v-if="expanded" class="tc-body">
      <TurnInput :input="turn.input" />
      <IterationCard
        v-for="iter in turn.iterations"
        :key="iter.index"
        :iteration="iter"
      />
      <TurnEndHooks
        :post-model-hooks="turn.postModelHooks"
        :session-end-hooks="turn.sessionEndHooks"
      />
    </div>
  </div>
</template>

<style scoped>
.tc-root {
  margin-bottom: 10px;
  background: var(--bg-card);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: border-color var(--transition);
}
.tc-root.expanded { border-color: var(--accent); }

.tc-header {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 12px; cursor: pointer; user-select: none;
  color: var(--text-secondary); transition: background var(--transition);
  font-size: 12px;
}
.tc-header:hover { background: var(--bg-hover); }
.tc-arrow { font-size: 8px; transition: transform var(--transition); flex-shrink: 0; color: var(--text-muted); }
.tc-arrow.open { transform: rotate(90deg); }
.tc-icon { font-size: 14px; flex-shrink: 0; }
.tc-label { font-weight: 600; white-space: nowrap; }
.tc-type { font-weight: 400; color: var(--text-muted); }
.tc-snippet {
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--text-muted); font-size: 11px; max-width: 300px;
}
.tc-stats {
  font-family: monospace; font-size: 10px; color: var(--text-muted);
  white-space: nowrap; flex-shrink: 0;
}
.tc-body {
  border-top: 1px solid var(--border-light);
  /* Override global styles from variables.css (meant for ToolCard) */
  max-height: none; opacity: 1; overflow: visible;
}
</style>
