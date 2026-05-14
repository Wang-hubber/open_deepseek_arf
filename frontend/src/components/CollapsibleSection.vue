<script setup lang="ts">
import { ref } from 'vue'

const props = withDefaults(defineProps<{
  title: string
  defaultOpen?: boolean
}>(), {
  defaultOpen: false,
})

const open = ref(props.defaultOpen)

function toggle() {
  open.value = !open.value
}
</script>

<template>
  <div class="cs-root" :class="{ open }">
    <div class="cs-header" @click="toggle">
      <span class="cs-arrow" :class="{ open }">▶</span>
      <span class="cs-title">{{ title }}</span>
    </div>
    <div class="cs-body" :class="{ open }">
      <div class="cs-body-inner">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped>
.cs-root {
  margin-top: 6px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.cs-header {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; cursor: pointer; user-select: none;
  color: var(--text-muted); font-size: 11px; font-weight: 600;
  transition: background var(--transition);
}
.cs-header:hover { background: var(--bg-hover); color: var(--text-secondary); }
.cs-arrow {
  font-size: 8px; transition: transform var(--transition);
  flex-shrink: 0;
}
.cs-arrow.open { transform: rotate(90deg); }
.cs-title { text-transform: uppercase; letter-spacing: 0.5px; }
.cs-body {
  max-height: 0; overflow: hidden;
  transition: max-height 0.3s ease;
}
.cs-body.open { max-height: 400px; overflow-y: auto; }
.cs-body-inner {
  padding: 8px 10px;
  border-top: 1px solid var(--border-light);
}
</style>
