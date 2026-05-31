<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from '@/composables/useI18n'

const { t } = useI18n()

const props = defineProps<{
  name: string
  arguments: string
  status: 'executing' | 'completed' | 'failed'
  result?: string
  error?: string
  turn?: number
}>()

const open = ref(false)

const parsedResult = computed(() => {
  if (!props.result) return null
  try { return JSON.parse(props.result) } catch { return null }
})

const isFileWriter = computed(() => props.name === 'file_writer')
const hasPreview = computed(() => isFileWriter.value && parsedResult.value?.preview)
const previewFilename = computed(() => parsedResult.value?.filename || '')
const previewContent = computed(() => parsedResult.value?.preview || '')
const previewPath = computed(() => parsedResult.value?.path || '')

const autoOpened = ref(false)
if (hasPreview.value && !autoOpened.value) {
  open.value = true
  autoOpened.value = true
}

function toggle() {
  open.value = !open.value
}

const MAX_VAL = 200

function formatArgs(argsStr: string): { label: string; value: string }[] {
  let obj: any = null
  if (typeof argsStr === 'object' && argsStr !== null) {
    obj = argsStr
  } else if (typeof argsStr === 'string') {
    try { obj = JSON.parse(argsStr) } catch {}
  }
  if (obj && typeof obj === 'object') {
    const isArray = Array.isArray(obj) || Object.prototype.toString.call(obj) === '[object Array]'
    if (isArray) {
      return (obj as any[]).map((item, i) => {
        const val = typeof item === 'string' ? item : JSON.stringify(item)
        return { label: String(i), value: val.length > MAX_VAL ? val.slice(0, MAX_VAL) + '…' : val }
      })
    }
    return Object.entries(obj).map(([k, v]) => {
      let val: string
      if (typeof v === 'string') {
        if (v.length > MAX_VAL) {
          val = (k === 'path' || k.endsWith('_path'))
            ? '…' + v.slice(-MAX_VAL)
            : v.slice(0, MAX_VAL) + '…'
        } else {
          val = v
        }
      } else {
        val = JSON.stringify(v)
        if (val.length > MAX_VAL) val = val.slice(0, MAX_VAL) + '…'
      }
      return { label: k, value: val }
    })
  }
  const s = typeof argsStr === 'string' ? argsStr : JSON.stringify(argsStr)
  return [{ label: '', value: s.length > MAX_VAL ? s.slice(0, MAX_VAL) + '…' : s }]
}

function langFromPath(p: string): string {
  if (p.endsWith('.py')) return 'python'
  if (p.endsWith('.yaml') || p.endsWith('.yml')) return 'yaml'
  if (p.endsWith('.json')) return 'json'
  if (p.endsWith('.md')) return 'markdown'
  if (p.endsWith('.ts') || p.endsWith('.tsx')) return 'typescript'
  if (p.endsWith('.js') || p.endsWith('.jsx')) return 'javascript'
  if (p.endsWith('.css')) return 'css'
  if (p.endsWith('.html')) return 'html'
  return ''
}
</script>

<template>
  <div class="tool-card" :class="status">
    <div class="tc-header" @click="toggle">
      <span class="tc-icon" :class="{ spin: status === 'executing' }">
        {{ status === 'executing' ? '…' : status === 'completed' ? '✓' : '✗' }}
      </span>
      <span class="tc-name">{{ name }}</span>
      <span v-if="turn" class="tc-turn">T{{ turn }}</span>
      <span class="tc-status" :class="status">
        {{ status === 'executing' ? t('common.executing') : status === 'completed' ? t('common.completed') : t('common.failed') }}
      </span>
      <span class="tc-arrow" :class="{ open }">▶</span>
    </div>

    <div class="tc-body" :class="{ open }">
      <div class="tc-body-inner">
        <!-- File preview card for file_writer -->
        <div v-if="hasPreview" class="file-preview-card">
          <div class="fpc-header">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span class="fpc-filename">{{ previewFilename }}</span>
            <span class="fpc-lang">{{ langFromPath(previewPath) || 'text' }}</span>
          </div>
          <pre class="fpc-content" :class="langFromPath(previewPath) ? `lang-${langFromPath(previewPath)}` : ''"><code>{{ previewContent }}</code></pre>
        </div>

        <div class="tc-field">
          <div class="tc-field-label">{{ t('common.parameters') }}</div>
          <div class="tc-params-grid">
            <div v-for="(p, pi) in formatArgs(arguments)" :key="pi" class="tc-param-row">
              <span v-if="p.label" class="tc-param-key">{{ p.label }}</span>
              <code class="tc-param-val">{{ p.value }}</code>
            </div>
          </div>
        </div>
        <div v-if="result" class="tc-field">
          <div class="tc-field-label">{{ t('common.result') }}</div>
          <div class="tc-field-value">{{ result }}</div>
        </div>
        <div v-if="error" class="tc-field">
          <div class="tc-field-label">{{ t('common.error') }}</div>
          <div class="tc-error-msg">{{ error }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.file-preview-card {
  border: 1px solid var(--border); border-radius: var(--radius-md);
  overflow: hidden; margin-bottom: 12px;
  background: var(--bg-root);
}
.fpc-header {
  display: flex; align-items: center; gap: 6px;
  padding: 7px 12px; background: var(--bg-input);
  border-bottom: 1px solid var(--border);
  font-size: 11px; color: var(--text-secondary);
}
.fpc-filename {
  font-weight: 600; color: var(--text-primary);
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
}
.fpc-lang {
  margin-left: auto; font-size: 10px; color: var(--text-muted);
  background: var(--bg-card); padding: 1px 7px; border-radius: var(--radius-sm);
  text-transform: uppercase;
}
.fpc-content {
  margin: 0; padding: 12px 14px;
  font-size: 12px; line-height: 1.5;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  color: var(--text-primary);
  white-space: pre-wrap; word-break: break-word;
  max-height: 360px; overflow-y: auto;
  background: var(--bg-root);
}
.fpc-content code {
  font-family: inherit; color: inherit;
}
.tc-params-grid {
  display: flex; flex-direction: column; gap: 4px;
}
.tc-param-row {
  display: flex; align-items: baseline; gap: 6px;
  font-size: 12px;
}
.tc-param-key {
  color: var(--accent);
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 11px;
  flex-shrink: 0;
  min-width: 60px;
}
.tc-param-key::after { content: ':'; }
.tc-param-val {
  color: var(--text-primary);
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 11px;
  background: var(--bg-input);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  word-break: break-all;
  max-width: 100%;
}
</style>
