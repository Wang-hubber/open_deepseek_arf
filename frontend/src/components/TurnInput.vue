<script setup lang="ts">
import { useI18n } from '@/composables/useI18n'
import type { TurnInput as TurnInputType } from '@/types'

const { t } = useI18n()

const props = defineProps<{
  input: TurnInputType
}>()
</script>

<template>
  <div class="ti-root">
    <div class="ti-header">
      <span class="ti-icon">{{ input.type === 'user' ? '📥' : '🤖' }}</span>
      <span class="ti-label">
        {{ input.type === 'user' ? t('trace.userInput') : t('trace.agentInput') }}
      </span>
    </div>
    <div class="ti-content">{{ input.snippet }}</div>
  </div>
</template>

<style scoped>
.ti-root {
  padding: 8px 12px;
  background: var(--bg-root);
  border-bottom: 1px solid var(--border-light);
}
.ti-header {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 4px; font-size: 11px; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 0.4px; font-weight: 600;
}
.ti-icon { font-size: 13px; }
.ti-content {
  font-size: 13px; color: var(--text-primary);
  white-space: pre-wrap; word-break: break-word; line-height: 1.5;
}
</style>
