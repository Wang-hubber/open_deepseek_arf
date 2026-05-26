import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ActiveSession } from '@/types'

export const useSessionStore = defineStore('sessions', () => {
  const activeSession = ref<ActiveSession | null>(null)

  return { activeSession }
})
