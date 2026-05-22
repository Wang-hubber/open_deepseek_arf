<script setup lang="ts">
import { useI18n } from '@/composables/useI18n'
import type { TraceEvent } from '@/types'
import HookGroup from './HookGroup.vue'

const { t } = useI18n()

defineProps<{
  postModelHooks: TraceEvent[]
  sessionEndHooks?: TraceEvent[]
}>()
</script>

<template>
  <div v-if="postModelHooks.length > 0 || sessionEndHooks?.length" class="teh-root">
    <HookGroup
      v-if="postModelHooks.length > 0"
      :hooks="postModelHooks"
      :title="t('trace.postModelHooks')"
    />
    <HookGroup
      v-if="sessionEndHooks?.length"
      :hooks="sessionEndHooks"
      :title="t('trace.sessionEndHooks')"
    />
  </div>
</template>

<style scoped>
.teh-root {
  padding: 4px 12px 6px;
  border-top: 1px solid var(--border-light);
}
</style>
