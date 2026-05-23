<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '@/composables/useI18n'
import type { TraceEvent } from '@/types'

const { t } = useI18n()

const props = defineProps<{
  event: TraceEvent
}>()

function parseMeta(evt: TraceEvent): Record<string, any> {
  if (!evt.metadata) return {}
  try { return JSON.parse(evt.metadata) } catch { return {} }
}

const meta = parseMeta(props.event)
const outputSnippet = meta.model_output_snippet || ''
const thinkingContent = outputSnippet || ''

const TRUNCATE_AT = 500
const isLong = computed(() => thinkingContent.length > TRUNCATE_AT)
const showToggle = computed(() => isLong.value)
</script>

<template>
  <div v-if="thinkingContent" class="rb-root">
    <div class="rb-label">{{ t('trace.reasoning') }}</div>
    <div class="rb-content" :class="{ truncated: showToggle }">
      <template v-if="showToggle">
        <details>
          <summary class="rb-summary">
            {{ thinkingContent.slice(0, TRUNCATE_AT) }}...
          </summary>
          <pre class="rb-full">{{ thinkingContent }}</pre>
        </details>
      </template>
      <pre v-else class="rb-full">{{ thinkingContent }}</pre>
    </div>
  </div>
</template>

<style scoped>
.rb-root {
  margin-bottom: 6px;
}
.rb-label {
  font-size: 10px; color: var(--text-muted); margin-bottom: 3px;
  text-transform: uppercase; letter-spacing: 0.4px; font-weight: 600;
}
.rb-content {
  background: var(--bg-root); border: 1px solid var(--border-light);
  border-radius: var(--radius-sm); padding: 6px 10px;
  font-size: 12px; line-height: 1.5; color: var(--text-secondary);
  max-height: 300px; overflow-y: auto;
}
.rb-summary {
  cursor: pointer; color: var(--text-muted);
}
.rb-full {
  margin: 0; white-space: pre-wrap; word-break: break-word;
  font-family: 'JetBrains Mono', monospace; font-size: 11px;
}
</style>
