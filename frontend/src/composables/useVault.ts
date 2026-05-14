import { ref } from 'vue'
import { useApi } from './useApi'

export function useVault() {
  const { get, post } = useApi()
  const error = ref('')

  async function init(password: string) {
    error.value = ''
    try {
      await post('/api/vault/init', { password })
      return true
    } catch (e: any) {
      error.value = e.message
      return false
    }
  }

  async function unlock(password: string) {
    error.value = ''
    try {
      await post('/api/vault/unlock', { password })
      return true
    } catch (e: any) {
      error.value = e.message
      return false
    }
  }

  async function lock() {
    try {
      await post('/api/vault/lock')
    } catch {
      // ignore
    }
  }

  async function checkStatus(): Promise<{ initialized: boolean; unlocked: boolean }> {
    return get('/api/vault/status')
  }

  return { error, init, unlock, lock, checkStatus }
}
