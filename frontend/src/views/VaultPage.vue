<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useVault } from '@/composables/useVault'

const router = useRouter()
const appStore = useAppStore()
const { error, init, unlock, checkStatus } = useVault()

const password = ref('')
const isNew = ref(false)
const submitting = ref(false)

onMounted(async () => {
  try {
    const st = await checkStatus()
    isNew.value = !st.initialized
  } catch {
    // fallback: show unlock page
  }
})

async function submit() {
  if (!password.value) {
    error.value = '请输入主密码'
    return
  }
  if (isNew.value && password.value.length < 4) {
    error.value = '密码至少需要4个字符'
    return
  }

  submitting.value = true
  error.value = ''

  let ok: boolean
  if (isNew.value) {
    ok = await init(password.value)
  } else {
    ok = await unlock(password.value)
  }

  submitting.value = false

  if (ok) {
    router.replace('/')
  }
}
</script>

<template>
  <div id="vault-page">
    <div class="vault-card">
      <h1>{{ isNew ? '设置主密码' : 'ARF Security Vault' }}</h1>
      <p class="sub">
        {{ isNew ? '创建主密码以保护你的邮箱授权码、API 密钥和个人记忆。请务必牢记此密码——丢失后将无法恢复。' : '保险库已锁定。请输入主密码解锁。' }}
      </p>
      <div class="field">
        <label for="vault-password">主密码</label>
        <input
          id="vault-password"
          v-model="password"
          type="password"
          :placeholder="isNew ? '设置主密码...' : '输入主密码...'"
          @keydown.enter="submit"
          autofocus
        />
      </div>
      <div v-if="error" class="vault-error">{{ error }}</div>
      <button class="btn btn-primary" :disabled="submitting" @click="submit">
        {{ submitting ? (isNew ? '创建中...' : '解锁中...') : (isNew ? '创建保险库' : '解锁') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
#vault-page {
  display: flex; justify-content: center; align-items: center; min-height: 100vh;
  background: var(--bg-root);
}
.vault-card {
  background: var(--bg-card); border: 1px solid var(--border-glass);
  border-radius: var(--radius-xl); box-shadow: var(--shadow-lg), var(--shadow-glow);
  width: 420px; max-width: 94vw; padding: 44px; text-align: center;
}
.vault-card h1 { font-size: 22px; margin-bottom: 4px; color: var(--text-primary); }
.vault-card .sub { color: var(--text-muted); font-size: 14px; margin-bottom: 28px; }
.vault-card .field { margin-bottom: 18px; text-align: left; }
.vault-card .field label {
  display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px;
  color: var(--text-secondary); letter-spacing: 0.01em;
}
.vault-card .field input {
  width: 100%; padding: 10px 14px; background: var(--bg-input);
  border: 1px solid var(--border); border-radius: var(--radius-md);
  font-size: 14px; color: var(--text-primary); outline: none;
  transition: all var(--transition);
}
.vault-card .field input::placeholder { color: var(--text-muted); }
.vault-card .field input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(99,102,241,0.15), var(--shadow-glow);
}
.vault-error {
  background: var(--error-bg); border: 1px solid var(--error-border);
  color: var(--error-text); padding: 10px 14px;
  border-radius: var(--radius-md); font-size: 13px; margin-bottom: 14px;
}

@media (max-width: 480px) {
  .vault-card { padding: 28px 22px; }
}
</style>
