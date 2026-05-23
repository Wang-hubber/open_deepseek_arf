import { computed } from 'vue'
import { useAppStore } from '@/stores/app'
import zh from '@/locales/zh-CN.json'
import en from '@/locales/en-US.json'

const locales: Record<string, Record<string, any>> = { zh, en }

export function useI18n() {
  const appStore = useAppStore()

  const currentLanguage = computed(() => appStore.language || 'zh')

  function t(key: string, params?: Record<string, string | number>): string {
    const lang = currentLanguage.value
    const locale = locales[lang] || locales.zh
    const keys = key.split('.')
    let result: any = locale
    for (const k of keys) {
      result = result?.[k]
      if (result === undefined) break
    }
    if (typeof result !== 'string') {
      // Try fallback to zh
      let fallback: any = locales.zh
      for (const k of keys) {
        fallback = fallback?.[k]
        if (fallback === undefined) break
      }
      if (typeof fallback === 'string') result = fallback
      else return key
    }
    if (params) {
      return result.replace(/\{(\w+)\}/g, (_: string, p: string) =>
        String(params[p] ?? `{${p}}`))
    }
    return result
  }

  return { t, currentLanguage }
}
