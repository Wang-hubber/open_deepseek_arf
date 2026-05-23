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

// Auto-open preview cards
const autoOpened = ref(false)
if (hasPreview.value && !autoOpened.value) {
  open.value = true
  autoOpened.value = true
}

function toggle() {
  open.value = !open.value
}

function tryFormatJson(str: string): string {
  try { return JSON.stringify(JSON.parse(str), null, 2) }
  catch { return str }
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
          <div class="tc-field-value">{{ tryFormatJson(arguments) }}</div>
        </div>
        <div v-if="result" class="tc-field">
          <div class="tc-field-label">{{ t('common.result') }}</div>
          <div class="tc-field-value">{{ tryFormatJson(result) }}</div>
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
</style>
