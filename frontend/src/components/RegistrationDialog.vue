<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useApi } from '@/composables/useApi'
import type { FormField, SlotInfo } from '@/types'

const props = defineProps<{
  slot: SlotInfo
}>()

const emit = defineEmits<{
  close: []
  configured: [name: string]
}>()

const api = useApi()
const visible = ref(false)
const submitting = ref(false)
const testing = ref(false)
const result = reactive({ ok: false, message: '', error: '' })
const formData = ref<Record<string, any>>({})

function initForm() {
  const data: Record<string, any> = {}
  if (props.slot.config_template) {
    for (const [key, field] of Object.entries(props.slot.config_template)) {
      data[key] = field.default ?? ''
    }
  }
  formData.value = data
}

function show() {
  initForm()
  result.ok = false
  result.message = ''
  result.error = ''
  visible.value = true
}

function hide() {
  visible.value = false
  emit('close')
}

function onOverlayClick(e: MouseEvent) {
  if (e.target === e.currentTarget) hide()
}

function getFieldType(field: FormField): string {
  if (field.type === 'password') return 'password'
  if (field.type === 'number') return 'number'
  return 'text'
}

async function testConnection() {
  testing.value = true
  result.ok = false
  result.message = ''
  result.error = ''
  try {
    const payload = { ...formData.value, temperature: Number(formData.value.temperature || 0.7), max_tokens: Number(formData.value.max_tokens || 4096) }
    const res: any = await api.post('/api/config/test', payload)
    if (res.ok) {
      result.ok = true
      result.message = '连接成功'
    } else {
      result.error = res.detail || '连接测试失败'
    }
  } catch (e: any) {
    result.error = e?.message || '连接测试失败'
  } finally {
    testing.value = false
  }
}

async function submit() {
  submitting.value = true
  try {
    const payload = { ...formData.value, temperature: Number(formData.value.temperature || 0.7), max_tokens: Number(formData.value.max_tokens || 4096) }
    const res: any = await api.post(`/api/resources/${props.slot.type}/${props.slot.name}/configure`, { config: payload })
    if (res.ok) {
      result.ok = true
      result.message = '配置成功'
      emit('configured', props.slot.name)
      setTimeout(() => hide(), 800)
    } else {
      result.error = res.detail || '配置失败'
    }
  } catch (e: any) {
    result.error = e?.message || '配置失败'
  } finally {
    submitting.value = false
  }
}

defineExpose({ show, hide })
</script>

<template>
  <div v-if="visible" class="modal-overlay" @click="onOverlayClick">
    <div class="modal-card">
      <h2>配置 {{ slot.name }}</h2>
      <p class="desc">{{ slot.description }}</p>
      <div v-if="slot.depends_on?.length" class="deps-info">
        依赖：
        <span v-for="dep in slot.depends_on" :key="dep.name" class="dep-tag">
          {{ dep.type }}/{{ dep.name }}
        </span>
      </div>

      <form @submit.prevent="submit" class="form-body">
        <div v-for="(field, key) in slot.config_template" :key="key" class="field">
          <label>
            {{ field.label }}
            <span v-if="field.required" class="required">*</span>
          </label>
          <select v-if="field.type === 'select'" v-model="formData[key]" class="input">
            <option v-for="opt in field.enum" :key="opt" :value="opt">{{ opt }}</option>
          </select>
          <input
            v-else
            v-model="formData[key]"
            :type="getFieldType(field)"
            :placeholder="field.placeholder"
            :required="field.required"
            class="input"
          />
        </div>
      </form>

      <div v-if="result.message" class="result-ok">{{ result.message }}</div>
      <div v-if="result.error" class="result-err">{{ result.error }}</div>

      <div class="modal-actions">
        <button class="btn-cancel" @click="hide">取消</button>
        <button
          v-if="slot.type === 'model' && slot.config_template?.base_url"
          class="btn-test"
          @click="testConnection"
          :disabled="testing"
        >
          {{ testing ? '测试中...' : '测试连接' }}
        </button>
        <button class="btn-confirm" @click="submit" :disabled="submitting">
          {{ submitting ? '保存中...' : '保存配置' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.6); z-index: 100;
  display: flex; align-items: center; justify-content: center;
  backdrop-filter: blur(4px);
  animation: fadeIn 0.15s ease;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

.modal-card {
  background: var(--bg-card); border: 1px solid var(--border-glass);
  border-radius: var(--radius-xl); box-shadow: var(--shadow-lg), var(--shadow-glow);
  width: 480px; max-width: 90vw; max-height: 85vh; overflow-y: auto;
  padding: 32px;
  animation: modalSlideIn 0.2s ease;
}
@keyframes modalSlideIn { from { opacity: 0; transform: translateY(12px) scale(0.97); } to { opacity: 1; transform: translateY(0) scale(1); } }

.modal-card h2 { font-size: 16px; margin-bottom: 6px; color: var(--text-primary); }
.desc { font-size: 13px; color: var(--text-secondary); margin-bottom: 16px; line-height: 1.5; }

.deps-info { font-size: 12px; color: var(--text-secondary); margin-bottom: 16px; }
.dep-tag {
  display: inline-block; padding: 1px 6px; margin: 0 2px;
  background: rgba(99,102,241,0.15); color: var(--accent);
  border-radius: 4px; font-size: 11px;
}

.form-body { display: flex; flex-direction: column; gap: 14px; margin-bottom: 18px; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field label { font-size: 13px; font-weight: 500; color: var(--text-primary); }
.required { color: var(--error-text); }
.input {
  padding: 8px 12px; border: 1px solid var(--border);
  background: var(--bg-input); color: var(--text-primary);
  border-radius: var(--radius-md); font-size: 13px;
  transition: border-color var(--transition);
}
.input:focus { border-color: var(--accent); outline: none; }

.result-ok { font-size: 13px; color: #22c55e; margin-bottom: 10px; }
.result-err { font-size: 13px; color: var(--error-text); margin-bottom: 10px; }

.modal-actions { display: flex; gap: 10px; justify-content: flex-end; }
.btn-cancel {
  padding: 8px 20px; border: 1px solid var(--border);
  background: transparent; border-radius: var(--radius-md); cursor: pointer;
  font-size: 13px; font-weight: 600; color: var(--text-secondary);
  transition: all var(--transition);
}
.btn-cancel:hover { background: var(--bg-hover); color: var(--text-primary); }
.btn-test {
  padding: 8px 20px; border: 1px solid var(--accent);
  background: transparent; border-radius: var(--radius-md); cursor: pointer;
  font-size: 13px; font-weight: 600; color: var(--accent);
  transition: all var(--transition);
}
.btn-test:hover { background: rgba(99,102,241,0.1); }
.btn-test:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-confirm {
  padding: 8px 20px; border: none;
  background: var(--accent-gradient); color: var(--text-on-accent);
  border-radius: var(--radius-md); cursor: pointer; font-size: 13px; font-weight: 600;
  transition: all var(--transition);
  box-shadow: 0 2px 8px rgba(99,102,241,0.25);
}
.btn-confirm:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(99,102,241,0.35);
}
.btn-confirm:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
</style>
