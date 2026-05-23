<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from '@/composables/useI18n'
import type { TraceEvent } from '@/types'

const { t } = useI18n()

const props = defineProps<{
  hooks: TraceEvent[]
  title: string
}>()

const expanded = ref(false)

function parseMeta(evt: TraceEvent): Record<string, any> {
  if (!evt.metadata) return {}
  try { return JSON.parse(evt.metadata) } catch { return {} }
}

function exitBadge(code: number): string {
  if (code === 0) return 'badge-ok'
  if (code === 1) return 'badge-error'
  if (code === 2) return 'badge-warn'
  return 'badge-skip'
}

function exitLabel(code: number): string {
  if (code === 0) return 'OK'
  if (code === 1) return 'BLOCK'
  if (code === 2) return 'INJECT'
  return String(code)
}

function formatMs(ms: number | undefined): string {
  if (!ms) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}
</script>

<template>
  <div class="hg-root">
    <div class="hg-header" @click="expanded = !expanded">
      <span class="hg-arrow" :class="{ open: expanded }">▶</span>
      <span class="hg-icon">🪝</span>
      <span class="hg-title">{{ title }}</span>
      <span class="hg-count">({{ hooks.length }})</span>
    </div>
    <div v-if="expanded" class="hg-body">
      <div v-if="hooks.length === 0" class="hg-empty">—</div>
      <div v-for="(h, i) in hooks" :key="h.id ?? i" class="hg-item">
        <div class="hg-item-head">
          <span class="hg-item-name">{{ parseMeta(h).hook_name || parseMeta(h).command || 'hook' }}</span>
          <span :class="'tv-badge ' + exitBadge(parseMeta(h).exit_code ?? -1)">
            {{ exitLabel(parseMeta(h).exit_code ?? -1) }}
          </span>
          <span v-if="h.duration_ms" class="hg-item-dur">{{ formatMs(h.duration_ms) }}</span>
        </div>
        <div v-if="parseMeta(h).stdout" class="hg-stdout">
          <pre>{{ parseMeta(h).stdout }}</pre>
        </div>
        <div v-if="parseMeta(h).stderr" class="hg-stderr">
          <pre>{{ parseMeta(h).stderr }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hg-root {
  margin-top: 4px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  overflow: hidden;
  font-size: 11px;
}
.hg-header {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 8px; cursor: pointer; user-select: none;
  color: var(--text-muted); transition: background var(--transition);
}
.hg-header:hover { background: var(--bg-hover); color: var(--text-secondary); }
.hg-arrow { font-size: 7px; transition: transform var(--transition); flex-shrink: 0; }
.hg-arrow.open { transform: rotate(90deg); }
.hg-icon { font-size: 10px; flex-shrink: 0; }
.hg-title { font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px; }
.hg-count { font-family: monospace; }
.hg-body { padding: 4px 8px 6px; border-top: 1px solid var(--border-light); }
.hg-empty { color: var(--text-muted); padding: 2px 0; text-align: center; }
.hg-item { margin-bottom: 4px; }
.hg-item:last-child { margin-bottom: 0; }
.hg-item-head {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--text-secondary);
}
.hg-item-name {
  font-family: monospace; font-size: 10px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;
}
.hg-item-dur { color: var(--text-muted); font-family: monospace; font-size: 10px; }
.hg-stdout pre, .hg-stderr pre {
  margin: 2px 0 0; font-size: 10px; line-height: 1.4;
  color: var(--text-muted); white-space: pre-wrap; word-break: break-all;
  font-family: 'JetBrains Mono', monospace;
  max-height: 120px; overflow-y: auto;
  background: var(--bg-root); padding: 4px 8px; border-radius: 3px;
}
.hg-stderr pre { color: var(--error-text); }
.tv-badge {
  font-size: 9px; padding: 0px 5px; border-radius: var(--radius-full);
  font-weight: 600; white-space: nowrap;
}
.badge-ok { background: var(--success-bg); color: var(--success); }
.badge-error { background: var(--error-bg); color: var(--error); }
.badge-warn { background: var(--warning-bg); color: var(--warning); }
.badge-skip { background: rgba(255,255,255,0.05); color: var(--text-muted); }
</style>
