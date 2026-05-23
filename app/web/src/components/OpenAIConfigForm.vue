<script setup lang="ts">
/**
 * OpenAI 兼容 API 模型配置表单
 *
 * 适用于所有兼容 OpenAI API 的 LLM 提供商（OpenAI / Groq / Together / vLLM / Ollama 等）。
 * 必填: base_url, api_key, model_name
 * 选填: 折叠显示，使用 OpenAI 标准默认值。
 */
import { ref, reactive, onMounted } from 'vue'
import { useApi } from '@/composables/useApi'

const props = defineProps<{
  modelName?: string
}>()

const emit = defineEmits<{
  close: []
  saved: [name: string]
}>()

const api = useApi()

// ─── 必填 ───────────────────────────────────────
const base_url = ref('https://api.openai.com/v1')
const api_key = ref('')
const model_name = ref('')

// ─── 选填 ───────────────────────────────────────
const showAdvanced = ref(false)
const temperature = ref(1.0)
const top_p = ref(1.0)
const max_tokens = ref(4096)
const response_format = ref<'text' | 'json_object'>('text')
const stop = ref('')
const stream = ref(true)
const frequency_penalty = ref(0.0)
const presence_penalty = ref(0.0)

// ─── 常用预设 ───────────────────────────────────
const presets = [
  { label: '自定义', base_url: '', model: '' },
  { label: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-4o' },
  { label: 'Groq', base_url: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile' },
  { label: 'Together', base_url: 'https://api.together.xyz/v1', model: 'meta-llama/Llama-3.3-70B-Instruct-Turbo' },
  { label: 'DeepSeek', base_url: 'https://api.deepseek.com', model: 'deepseek-v4-pro' },
  { label: 'vLLM', base_url: 'http://localhost:8000/v1', model: '' },
  { label: 'Ollama', base_url: 'http://localhost:11434/v1', model: 'llama3' },
]

const selectedPreset = ref('')

function applyPreset(label: string) {
  const p = presets.find(x => x.label === label)
  if (!p || p.label === '自定义') return
  selectedPreset.value = label
  base_url.value = p.base_url
  model_name.value = p.model
}

// ─── 状态 ───────────────────────────────────────
const submitting = ref(false)
const testing = ref(false)
const testOk = ref(false)
const testMsg = ref('')
const testErr = ref('')
const saveMsg = ref('')

onMounted(async () => {
  if (props.modelName) {
    try {
      const res: any = await api.get(`/api/resources/models/${props.modelName}`)
      const c = res?.config
      if (!c) return
      if (c.base_url) base_url.value = c.base_url
      if (c.api_key) api_key.value = c.api_key
      if (c.model_name) model_name.value = c.model_name
      if (c.temperature != null) { temperature.value = c.temperature; showAdvanced.value = true }
      if (c.top_p != null) top_p.value = c.top_p
      if (c.max_tokens != null) max_tokens.value = c.max_tokens
      if (c.response_format) response_format.value = c.response_format
      if (c.stop) stop.value = Array.isArray(c.stop) ? c.stop.join(', ') : c.stop
      if (c.stream != null) stream.value = c.stream
      if (c.frequency_penalty != null) frequency_penalty.value = c.frequency_penalty
      if (c.presence_penalty != null) presence_penalty.value = c.presence_penalty
    } catch { /* 新建 */ }
  }
})

function getPayload() {
  const payload: Record<string, any> = {
    model_name: model_name.value.trim(),
    base_url: base_url.value.trim(),
    api_key: api_key.value.trim(),
  }
  if (showAdvanced.value) {
    payload.temperature = Number(temperature.value)
    payload.top_p = Number(top_p.value)
    payload.max_tokens = Number(max_tokens.value)
    payload.response_format = response_format.value
    payload.stream = stream.value
    payload.frequency_penalty = Number(frequency_penalty.value)
    payload.presence_penalty = Number(presence_penalty.value)
    const stops = stop.value.split(',').map(s => s.trim()).filter(Boolean)
    if (stops.length) payload.stop = stops
  }
  return payload
}

async function testConnection() {
  testing.value = true; testOk.value = false; testMsg.value = ''; testErr.value = ''
  try {
    const res: any = await api.post('/api/config/test', {
      base_url: base_url.value.trim(),
      api_key: api_key.value.trim(),
      model_name: model_name.value.trim(),
      temperature: Number(temperature.value),
      max_tokens: 64,
    })
    if (res.ok) { testOk.value = true; testMsg.value = `连接成功 — ${res.response}` }
    else testErr.value = res.detail || '连接失败'
  } catch (e: any) { testErr.value = e?.message || '连接失败' }
  finally { testing.value = false }
}

async function onSubmit() {
  submitting.value = true; saveMsg.value = ''
  try {
    const name = props.modelName || 'default'
    const res: any = await api.post(`/api/resources/model/${name}/configure`, { config: getPayload() })
    if (res.ok) { saveMsg.value = '已保存'; emit('saved', name); setTimeout(() => emit('close'), 600) }
    else testErr.value = res.detail || '保存失败'
  } catch (e: any) { testErr.value = e?.message || '保存失败' }
  finally { submitting.value = false }
}
</script>

<template>
  <div class="oa-form">
    <!-- ═══ 预设快速填充 ═══ -->
    <div class="field">
      <label>快速预设</label>
      <p class="hint">选择常用提供商，自动填充 API 地址和模型名。</p>
      <select v-model="selectedPreset" @change="applyPreset(($event.target as HTMLSelectElement).value)">
        <option value="">自定义</option>
        <option v-for="p in presets.filter(x => x.label !== '自定义')" :key="p.label" :value="p.label">
          {{ p.label }} — {{ p.model || '(需手动填写模型名)' }}
        </option>
      </select>
    </div>

    <!-- ═══ 必填 ═══ -->
    <div class="field">
      <label>API 地址 (base_url) <span class="req">*</span></label>
      <p class="hint">OpenAI 兼容的 API 端点，一般以 <code>/v1</code> 结尾。</p>
      <input v-model="base_url" type="text" placeholder="https://api.openai.com/v1" required />
    </div>

    <div class="field">
      <label>API 密钥 <span class="req">*</span></label>
      <p class="hint">在对应平台生成。密钥仅保存在本地工作区。</p>
      <input v-model="api_key" type="password" placeholder="sk-..." required />
    </div>

    <div class="field">
      <label>模型名称 (model) <span class="req">*</span></label>
      <p class="hint">服务商支持的模型标识符，如 <code>gpt-4o</code>、<code>claude-sonnet-4-6</code>、<code>llama-3.3-70b</code>。</p>
      <input v-model="model_name" type="text" placeholder="gpt-4o" required />
    </div>

    <!-- ═══ 选填折叠 ═══ -->
    <button type="button" class="toggle-advanced" @click="showAdvanced = !showAdvanced">
      {{ showAdvanced ? '收起高级选项 ▲' : '高级选项 ▶' }}
    </button>

    <div v-show="showAdvanced" class="advanced">
      <div class="field-row">
        <div class="field">
          <label>Temperature</label>
          <p class="hint">采样温度 0~2。越高越随机，越低越确定。</p>
          <input v-model.number="temperature" type="number" min="0" max="2" step="0.1" />
        </div>
        <div class="field">
          <label>Top P</label>
          <p class="hint">核采样 0~1。0.9 表示累积概率 90%。</p>
          <input v-model.number="top_p" type="number" min="0" max="1" step="0.05" />
        </div>
      </div>

      <div class="field">
        <label>Max Tokens</label>
        <p class="hint">最大输出 token 数。根据模型上下文窗口设置。</p>
        <input v-model.number="max_tokens" type="number" min="1" max="131072" />
      </div>

      <div class="field-row">
        <div class="field">
          <label>Frequency Penalty</label>
          <p class="hint">降低重复词频率。-2.0 ~ 2.0。</p>
          <input v-model.number="frequency_penalty" type="number" min="-2" max="2" step="0.1" />
        </div>
        <div class="field">
          <label>Presence Penalty</label>
          <p class="hint">鼓励新话题。-2.0 ~ 2.0。</p>
          <input v-model.number="presence_penalty" type="number" min="-2" max="2" step="0.1" />
        </div>
      </div>

      <div class="field-row">
        <div class="field">
          <label>输出格式</label>
          <p class="hint"><code>text</code> 普通文本；<code>json_object</code> 强制 JSON。</p>
          <select v-model="response_format">
            <option value="text">text</option>
            <option value="json_object">json_object</option>
          </select>
        </div>
        <div class="field">
          <label>Stop 词</label>
          <p class="hint">停止词，逗号分隔。</p>
          <input v-model="stop" type="text" placeholder="END, STOP" />
        </div>
      </div>

      <div class="field checkbox-field">
        <label>
          <input type="checkbox" v-model="stream" />
          流式输出 (stream)
        </label>
        <p class="hint">逐 token 返回。默认开启。</p>
      </div>
    </div>

    <!-- ═══ 操作 ═══ -->
    <div class="actions">
      <button class="btn btn-test" :disabled="testing" @click="testConnection">
        {{ testing ? '测试中...' : '测试连接' }}
      </button>
      <button class="btn btn-save" :disabled="submitting" @click="onSubmit">
        {{ submitting ? '保存中...' : testOk ? '保存并继续' : '保存配置' }}
      </button>
    </div>

    <div v-if="testMsg" class="msg-ok">{{ testMsg }}</div>
    <div v-if="testErr" class="msg-err">{{ testErr }}</div>
    <div v-if="saveMsg" class="msg-ok">{{ saveMsg }}</div>
  </div>
</template>

<style scoped>
.oa-form { max-height: 70vh; overflow-y: auto; padding-right: 4px; }

.field { margin-bottom: 14px; }
.field label { display: block; font-size: 13px; font-weight: 600; color: var(--text-primary); margin-bottom: 2px; }
.req { color: var(--error-text); font-weight: 400; }
.hint {
  font-size: 11px; color: var(--text-muted); margin: 2px 0 6px; line-height: 1.5;
}
.hint code {
  font-family: 'JetBrains Mono', monospace; font-size: 10px;
  background: var(--bg-input); padding: 1px 4px; border-radius: 3px;
}
.field input, .field select {
  width: 100%; padding: 8px 10px; border: 1px solid var(--border);
  background: var(--bg-input); color: var(--text-primary);
  border-radius: var(--radius-md); font-size: 13px;
  transition: border-color var(--transition); box-sizing: border-box;
}
.field input:focus, .field select:focus { border-color: var(--accent); outline: none; }
.field select {
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='5'%3E%3Cpath d='M0 0l4 5 4-5z' fill='%23999'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 10px center; padding-right: 28px;
}
.checkbox-field { margin: 14px 0; }
.checkbox-field label {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 13px; font-weight: 500; color: var(--text-primary);
}
.checkbox-field .hint { margin-top: 2px; margin-left: 24px; }
.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }

.toggle-advanced {
  width: 100%; padding: 8px 0; margin-bottom: 16px;
  background: none; border: none; border-top: 1px solid var(--border);
  color: var(--text-muted); font-size: 12px; cursor: pointer;
  transition: color var(--transition);
}
.toggle-advanced:hover { color: var(--accent); }

.advanced {
  padding: 16px; margin-bottom: 16px;
  background: var(--bg-input); border-radius: var(--radius-md);
  border: 1px solid var(--border);
}

.actions { display: flex; gap: 10px; margin-top: 16px; }
.btn {
  flex: 1; padding: 10px 16px; border: none; border-radius: var(--radius-md);
  cursor: pointer; font-size: 13px; font-weight: 600; transition: all var(--transition);
}
.btn-test { background: transparent; border: 1px solid var(--accent); color: var(--accent); }
.btn-test:hover { background: rgba(99,102,241,0.1); }
.btn-test:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-save {
  background: var(--accent-gradient); color: var(--text-on-accent);
  box-shadow: 0 2px 8px rgba(99,102,241,0.25);
}
.btn-save:hover { transform: translateY(-1px); box-shadow: 0 4px 14px rgba(99,102,241,0.35); }
.btn-save:disabled { opacity: 0.5; cursor: not-allowed; }

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
</style>
