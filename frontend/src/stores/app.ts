import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ConfigStatus } from '@/types'

export const useAppStore = defineStore('app', () => {
  const configStatus = ref<ConfigStatus | null>(null)
  const loading = ref(true)
  const language = ref(localStorage.getItem('arf_language') || 'zh')
  const usageRefreshKey = ref(0)

  const currentPage = computed(() => {
    if (loading.value) return 'loading'
    if (!configStatus.value?.configured) return 'welcome'
    return 'chat'
  })

  async function checkConfigStatus() {
    try {
      const res = await fetch('/api/config/status')
      configStatus.value = await res.json()
    } catch {
      configStatus.value = { configured: false, model_name: '', model_type: 'deep_thinking' }
    }
  }

  async function init() {
    loading.value = true
    await checkConfigStatus()
    loading.value = false
  }

  function setConfigStatus(status: ConfigStatus) {
    configStatus.value = status
  }

  function setLanguage(lang: string) {
    language.value = lang
    localStorage.setItem('arf_language', lang)
  }

  function refreshUsage() {
    usageRefreshKey.value++
  }

  return {
    configStatus,
    loading,
    language,
    usageRefreshKey,
    currentPage,
    init,
    checkConfigStatus,
    setConfigStatus,
    setLanguage,
    refreshUsage,
  }
})
