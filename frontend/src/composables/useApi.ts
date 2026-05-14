import { ref } from 'vue'
import { useAppStore } from '@/stores/app'

export function useApi() {
  const BASE = ''

  async function request<T>(url: string, options?: RequestInit): Promise<T> {
    const res = await fetch(`${BASE}${url}`, options)

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Request failed' }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  }

  function get<T>(url: string): Promise<T> {
    return request<T>(url)
  }

  function post<T>(url: string, body?: unknown): Promise<T> {
    return request<T>(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
  }

  function put<T>(url: string, body?: unknown): Promise<T> {
    return request<T>(url, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
  }

  function del<T>(url: string): Promise<T> {
    return request<T>(url, { method: 'DELETE' })
  }

  async function upload(file: File): Promise<any> {
    const formData = new FormData()
    formData.append('file', file)
    const res = await fetch(`${BASE}/api/upload`, {
      method: 'POST',
      body: formData,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Upload failed' }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.json()
  }

  return { get, post, put, del, upload }
}
