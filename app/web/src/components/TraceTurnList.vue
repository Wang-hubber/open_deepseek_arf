<script setup lang="ts">
import type { TurnStart, Turn } from '@/types'
import TurnStartCard from './TurnStartCard.vue'
import TurnCard from './TurnCard.vue'

defineProps<{
  turnStart: TurnStart
  turns: Turn[]
}>()
</script>

<template>
  <div class="ttl-root">
    <TurnStartCard :turn-start="turnStart" />
    <TurnCard v-for="turn in turns" :key="turn.turnIndex" :turn="turn" />
  </div>
</template>

<style scoped>
.ttl-root {
  flex: 1;
  overflow-y: auto;
}
</style>
