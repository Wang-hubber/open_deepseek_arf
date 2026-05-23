import { ref } from 'vue'
import { useApi } from './useApi'
import type { ResourceStat, ResourceDailyStat } from '@/types'

export function useResourceStats() {
  const api = useApi()
  const stats = ref<ResourceStat[]>([])
  const dailyStats = ref<ResourceDailyStat[]>([])
  const loading = ref(false)

  async function fetchStats(period = 'all') {
    loading.value = true
    try {
      const res: any = await api.get(`/api/traces/resource-stats?period=${period}`)
      stats.value = res.resources || []
    } catch {
      stats.value = []
    } finally {
      loading.value = false
    }
  }

  async function fetchDetail(name: string, fromDate = '', toDate = '') {
    loading.value = true
    try {
      const params = new URLSearchParams()
      if (fromDate) params.set('from_date', fromDate)
      if (toDate) params.set('to_date', toDate)
      const qs = params.toString()
      const url = `/api/traces/resource-stats/${encodeURIComponent(name)}${qs ? '?' + qs : ''}`
      const res: any = await api.get(url)
      dailyStats.value = res.daily || []
    } catch {
      dailyStats.value = []
    } finally {
      loading.value = false
    }
  }

  function downloadCSV(blob: Blob, filename: string) {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = filename
    a.click()
    URL.revokeObjectURL(a.href)
  }

  function exportCSV(period: string) {
    const token = localStorage.getItem('arf_token')
    fetch(`/api/traces/resource-stats/export?period=${period}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(r => {
        if (!r.ok) throw new Error('Export failed')
        return r.blob()
      })
      .then(blob => downloadCSV(blob, `resource-stats-${period}.csv`))
      .catch(err => console.error('Export failed:', err))
  }

  function exportDetailCSV(name: string, fromDate: string, toDate: string) {
    const token = localStorage.getItem('arf_token')
    const params = new URLSearchParams()
    if (fromDate) params.set('from_date', fromDate)
    if (toDate) params.set('to_date', toDate)
    const qs = params.toString()
    fetch(
      `/api/traces/resource-stats/${encodeURIComponent(name)}/export${qs ? '?' + qs : ''}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} },
    )
      .then(r => {
        if (!r.ok) throw new Error('Export failed')
        return r.blob()
      })
      .then(blob => downloadCSV(blob, `resource-${name}-detail.csv`))
      .catch(err => console.error('Export failed:', err))
  }

  return { stats, dailyStats, loading, fetchStats, fetchDetail, exportCSV, exportDetailCSV }
}
