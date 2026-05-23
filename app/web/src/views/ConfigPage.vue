<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useApi } from '@/composables/useApi'
import { useI18n } from '@/composables/useI18n'
import OpenAIConfigForm from '@/components/OpenAIConfigForm.vue'

const router = useRouter()
const appStore = useAppStore()
const { t } = useI18n()
const api = useApi()

const activeTab = ref<'deepseek' | 'other'>('deepseek')

// --- DeepSeek tab state ---
const dsApiKey = ref('')
const dsSubmitting = ref(false)
const dsError = ref('')
const dsResult = ref<{ models: { name: string; model_name: string }[]; active_model: string } | null>(null)

async function deepseekRegister() {
  const key = dsApiKey.value.trim()
  if (!key) {
    dsError.value = '请输入 API 密钥'
    return
  }
  dsSubmitting.value = true
  dsError.value = ''
  try {
    const res: any = await api.post('/api/config/register-deepseek', { api_key: key })
    if (res.ok) {
      dsResult.value = res
    } else {
      dsError.value = res.detail || '注册失败'
    }
  } catch (e: any) {
    dsError.value = e?.message || '注册失败'
  } finally {
    dsSubmitting.value = false
  }
}

function enterSystem() {
  const res = dsResult.value
  appStore.setConfigStatus({
    configured: true,
    model_name: res?.models?.[0]?.model_name || res?.models?.[0]?.model || 'DeepSeek',
    model_type: 'deep_thinking',
  })
  router.replace('/')
}

// --- Other provider tab ---
function onOtherSaved(_name: string) {
  appStore.setConfigStatus({
    configured: true,
    model_name: '',
    model_type: 'deep_thinking',
  })
  router.replace('/')
}

</script>

<template>
  <div id="config-page">
    <div class="config-topbar">
      <span class="topbar-brand">ARF</span>
    </div>

    <div class="config-card">
      <h1>ARF <span class="badge">{{ t('config.badge') }}</span></h1>
      <p class="sub">{{ t('config.subtitle') }}</p>

      <!-- Tabs -->
      <div class="tabs">
        <button
          :class="{ active: activeTab === 'deepseek' }"
          @click="activeTab = 'deepseek'"
        >{{ t('config.deepseekTab') }}</button>
        <button
          :class="{ active: activeTab === 'other' }"
          @click="activeTab = 'other'"
        >{{ t('config.otherTab') }}</button>
      </div>

      <!-- DeepSeek Tab -->
      <div v-if="activeTab === 'deepseek'" class="tab-content">
        <!-- Pre-registration form -->
        <template v-if="!dsResult">
          <div class="field">
            <label>API 密钥 <span class="req">*</span></label>
            <p class="hint">
              在 <a href="https://platform.deepseek.com/api_keys" target="_blank">DeepSeek 平台</a> 创建 API 密钥。
              系统将自动创建三个模型：deep_thinking (v4-pro)、quick_thinking (v4-flash)、quick_no_thinking (v4-flash)。
            </p>
            <input
              v-model="dsApiKey"
              type="password"
              placeholder="sk-..."
              @keydown.enter="deepseekRegister"
            />
          </div>

          <div v-if="dsError" class="msg-err">{{ dsError }}</div>

          <button
            class="btn btn-primary"
            :disabled="dsSubmitting"
            @click="deepseekRegister"
          >
            {{ dsSubmitting ? t('config.registering') : t('config.oneClickRegister') }}
          </button>
        </template>

        <!-- Post-registration result -->
        <template v-else>
          <div class="msg-ok">{{ t('config.registerSuccess') }}：</div>
          <ul class="model-list">
            <li v-for="m in dsResult.models" :key="m.name">
              <code>{{ m.name }}</code>
              <span class="model-spec">→ {{ m.model_name }}</span>
            </li>
          </ul>
          <p class="active-hint">
            会话初始模型：<code>{{ dsResult.active_model }}</code>
          </p>

          <button class="btn btn-primary" @click="enterSystem">
            {{ t('config.enterSystem') }}
          </button>
        </template>
      </div>

      <!-- Other Provider Tab -->
      <div v-if="activeTab === 'other'" class="tab-content">
        <div class="other-hint">
          {{ t('config.otherProviderHint') }}
        </div>
        <OpenAIConfigForm
          model-name="deep_thinking"
          @saved="onOtherSaved"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.config-topbar {
  position: fixed; top: 0; left: 0; right: 0; height: 44px;
  background: rgba(7,7,16,0.92); backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-glass);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 20px; font-size: 13px; z-index: 10;
}
.topbar-brand { font-weight: 700; color: var(--text-primary); }
.topbar-user { color: var(--text-secondary); }
.topbar-logout {
  background: none; border: none; color: var(--text-muted); cursor: pointer;
  font-size: 13px; padding: 4px 12px; border-radius: var(--radius-sm);
  transition: all var(--transition);
}
.topbar-logout:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }

#config-page {
  display: flex; justify-content: center; align-items: center; min-height: 100vh;
  background: var(--bg-root);
}
.config-card {
  background: var(--bg-card); border: 1px solid var(--border-glass);
  border-radius: var(--radius-xl); box-shadow: var(--shadow-lg), var(--shadow-glow);
  width: 540px; max-width: 94vw; padding: 44px;
}
.config-card h1 { font-size: 22px; margin-bottom: 4px; color: var(--text-primary); }
.config-card .sub { color: var(--text-muted); font-size: 14px; margin-bottom: 24px; }
.config-card .badge {
  display: inline-block; background: var(--warning-bg); color: var(--warning-text);
  font-size: 12px; padding: 3px 10px; border-radius: var(--radius-full); margin-left: 8px;
  border: 1px solid var(--warning-border);
}

/* Tabs */
.tabs {
  display: flex; gap: 0; margin-bottom: 24px; border-radius: var(--radius-md);
  overflow: hidden; border: 1px solid var(--border); background: var(--bg-input);
}
.tabs button {
  flex: 1; padding: 9px; border: none; cursor: pointer; font-size: 13px;
  font-weight: 600; background: transparent; color: var(--text-secondary);
  transition: all var(--transition);
}
.tabs button:hover:not(.active) { color: var(--text-primary); background: var(--bg-hover); }
.tabs button.active { background: var(--accent); color: #fff; }

.tab-content { min-height: 200px; }

/* Fields */
.field { margin-bottom: 14px; }
.field label { display: block; font-size: 13px; font-weight: 600; color: var(--text-primary); margin-bottom: 2px; }
.req { color: var(--error-text); font-weight: 400; }
.hint {
  font-size: 11px; color: var(--text-muted); margin: 2px 0 6px; line-height: 1.5;
}
.hint a { color: var(--accent); text-decoration: none; }
.hint a:hover { text-decoration: underline; }
.field input {
  width: 100%; padding: 8px 10px; border: 1px solid var(--border);
  background: var(--bg-input); color: var(--text-primary);
  border-radius: var(--radius-md); font-size: 13px;
  transition: border-color var(--transition); box-sizing: border-box;
}
.field input:focus { border-color: var(--accent); outline: none; }

/* Messages */
.msg-ok {
  margin-top: 12px; padding: 10px 14px; border-radius: var(--radius-md);
  background: var(--success-bg); border: 1px solid var(--success-border);
  color: var(--success-text); font-size: 13px;
}
.msg-err {
  margin-top: 12px; padding: 10px 14px; border-radius: var(--radius-md);
  background: var(--error-bg); border: 1px solid var(--error-border);
  color: var(--error-text); font-size: 13px;
}

/* Result */
.model-list {
  list-style: none; padding: 0; margin: 8px 0;
}
.model-list li {
  padding: 8px 12px; margin-bottom: 4px;
  background: var(--bg-input); border-radius: var(--radius-sm);
  font-size: 13px; color: var(--text-primary);
  display: flex; align-items: center; gap: 8px;
}
.model-list li code {
  font-family: 'JetBrains Mono', monospace; font-size: 12px;
  background: var(--bg-card); padding: 2px 6px; border-radius: 3px;
  color: var(--accent);
}
.model-spec { color: var(--text-muted); }

.active-hint {
  font-size: 13px; color: var(--text-secondary); margin: 10px 0 16px;
}
.active-hint code {
  font-family: 'JetBrains Mono', monospace; font-size: 12px;
  color: var(--accent); background: var(--bg-input); padding: 2px 6px; border-radius: 3px;
}

.other-hint {
  padding: 12px 14px; margin-bottom: 20px;
  background: var(--warning-bg); border: 1px solid var(--warning-border);
  border-radius: var(--radius-md);
  font-size: 13px; color: var(--warning-text); line-height: 1.6;
}

/* Buttons */
.btn {
  display: inline-flex; align-items: center; justify-content: center;
  padding: 10px 24px; border: none; border-radius: var(--radius-md);
  font-size: 14px; font-weight: 600; cursor: pointer;
  transition: all var(--transition);
}
.btn-primary {
  width: 100%; margin-top: 8px;
  background: var(--accent-gradient); color: var(--text-on-accent);
  box-shadow: 0 2px 8px rgba(99,102,241,0.25);
}
.btn-primary:hover { transform: translateY(-1px); box-shadow: 0 4px 14px rgba(99,102,241,0.35); }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

@media (max-width: 480px) {
  .config-card { padding: 28px 22px; }
  .config-topbar { padding: 0 12px; }
}

@media (min-width: 1400px) {
  .config-card { width: 600px; }
}
</style>
