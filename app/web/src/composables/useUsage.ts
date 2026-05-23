import { ref } from 'vue'
import { useApi } from './useApi'

export interface UsageByModel {
  model_name: string
  model_type: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  calls: number
}

export interface UsageSummary {
  total_tokens: number
  total_calls: number
  by_model: UsageByModel[]
}

export interface UsageDetail {
  day: string
  model_name: string
  pt: number
  ct: number
  calls: number
}

export interface ModelPricing {
  model_name: string
  input_price: number
  output_price: number
  currency: string
}

export function useUsage() {
  const { get, put } = useApi()
  const summary = ref<UsageSummary | null>(null)
  const detail = ref<UsageDetail[]>([])
  const pricing = ref<ModelPricing[]>([])
  const loading = ref(false)

  async function fetchSummary(period: string = 'month') {
    loading.value = true
    try {
      summary.value = await get<UsageSummary>(`/api/usage/summary?period=${period}`)
    } catch {
      summary.value = null
    } finally {
      loading.value = false
    }
  }

  async function fetchDetail(from: string, to: string, model?: string) {
    loading.value = true
    try {
      let url = `/api/usage/detail?from_date=${encodeURIComponent(from)}&to_date=${encodeURIComponent(to)}`
      if (model) url += `&model=${encodeURIComponent(model)}`
      detail.value = await get<UsageDetail[]>(url)
    } finally {
      loading.value = false
    }
  }

  async function fetchPricing() {
    try {
      pricing.value = await get<ModelPricing[]>('/api/usage/models/pricing')
    } catch { /* ignore */ }
  }

  async function updatePricing(modelName: string, inputPrice: number, outputPrice: number, currency: string) {
    await put(`/api/usage/models/${encodeURIComponent(modelName)}/pricing`, {
      input_price: inputPrice,
      output_price: outputPrice,
      currency,
    })
    await fetchPricing()
  }

  return { summary, detail, pricing, loading, fetchSummary, fetchDetail, fetchPricing, updatePricing }
}
