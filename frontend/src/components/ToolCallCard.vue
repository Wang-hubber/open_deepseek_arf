<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from '@/composables/useI18n'
import type { ToolCallPair } from '@/types'

const { t } = useI18n()

const props = defineProps<{
  toolCall: ToolCallPair
}>()

const expanded = ref(false)

function parseMeta(evt: { metadata?: string }): Record<string, any> {
  if (!evt.metadata) return {}
  try { return JSON.parse(evt.metadata) } catch { return {} }
}

function formatMs(ms: number | undefined): string {
  if (!ms) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

function statusBadge(status: string): string {
  if (status === 'error' || status === 'blocked_by_hook') return 'badge-error'
  if (status === 'skipped') return 'badge-skip'
  return 'badge-ok'
}

const toolName = props.toolCall.call.tool_name || 'unknown'
const meta = parseMeta(props.toolCall.call)
const toolInput = meta.tool_input_snippet || ''
const toolOutput = props.toolCall.result ? parseMeta(props.toolCall.result).tool_output_snippet || '' : ''
</script>

<template>
  <div class="tcc-root" :class="{ expanded }">
    <div class="tcc-header" @click="expanded = !expanded">
      <span class="tcc-arrow" :class="{ open: expanded }">▶</span>
      <span class="tcc-icon">🔧</span>
      <span class="tcc-name">{{ toolName }}</span>
      <span :class="'tv-badge ' + statusBadge(toolCall.call.status)">
        {{ toolCall.call.status === 'ok' ? 'OK' : toolCall.call.status }}
      </span>
      <span v-if="toolCall.call.duration_ms" class="tcc-dur">{{ formatMs(toolCall.call.duration_ms) }}</span>
    </div>
    <div v-if="expanded" class="tcc-body">
      <div v-if="toolInput" class="tcc-field">
        <div class="tcc-label">{{ t('trace.toolParams') }}</div>
        <pre class="tcc-value">{{ toolInput }}</pre>
      </div>
      <div v-if="toolOutput" class="tcc-field">
        <div class="tcc-label">{{ t('trace.toolResult') }}</div>
        <pre class="tcc-value">{{ toolOutput }}</pre>
      </div>
      <div v-if="toolCall.call.error_msg" class="tcc-error">
        {{ toolCall.call.error_msg }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.tcc-root {
  margin-top: 3px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  overflow: hidden;
  font-size: 11px;
}
.tcc-header {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 8px; cursor: pointer; user-select: none;
  color: var(--text-secondary); transition: background var(--transition);
}
.tcc-header:hover { background: var(--bg-hover); }
.tcc-arrow { font-size: 7px; transition: transform var(--transition); flex-shrink: 0; color: var(--text-muted); }
.tcc-arrow.open { transform: rotate(90deg); }
.tcc-icon { font-size: 10px; flex-shrink: 0; }
.tcc-name {
  font-family: monospace; font-size: 10px; font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;
}
.tcc-dur { color: var(--text-muted); font-family: monospace; font-size: 10px; }
.tcc-body { padding: 4px 8px 6px; border-top: 1px solid var(--border-light); }
.tcc-field { margin-bottom: 6px; }
.tcc-field:last-child { margin-bottom: 0; }
.tcc-label {
  font-size: 9px; color: var(--text-muted); margin-bottom: 2px;
  text-transform: uppercase; letter-spacing: 0.4px; font-weight: 600;
}
.tcc-value {
  margin: 0; font-size: 10px; line-height: 1.4;
  color: var(--text-secondary); white-space: pre-wrap; word-break: break-all;
  font-family: 'JetBrains Mono', monospace;
  max-height: 150px; overflow-y: auto;
  background: var(--bg-root); padding: 4px 8px; border-radius: 3px;
}
.tcc-error { color: var(--error-text); font-size: 10px; margin-top: 4px; }
.tv-badge {
  font-size: 9px; padding: 0px 5px; border-radius: var(--radius-full);
  font-weight: 600; white-space: nowrap;
}
.badge-ok { background: var(--success-bg); color: var(--success); }
.badge-error { background: var(--error-bg); color: var(--error); }
.badge-skip { background: rgba(255,255,255,0.05); color: var(--text-muted); }
</style>
