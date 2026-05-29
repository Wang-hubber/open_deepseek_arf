<script setup lang="ts">
import { ref, onMounted, onUnmounted, shallowRef } from 'vue'
import { useApi } from '@/composables/useApi'
import { useI18n } from '@/composables/useI18n'
import { useAppStore } from '@/stores/app'
import type { ResourceMap, SlotInfo, ResourceItem } from '@/types'
import DeepSeekConfigForm from '@/components/DeepSeekConfigForm.vue'

const { t } = useI18n()
const appStore = useAppStore()

defineProps<{
  overlay?: boolean
}>()

defineEmits<{
  (e: 'close-overlay'): void
}>()

const data = ref<ResourceMap | null>(null)
const unconfigured = ref<SlotInfo[]>([])
const error = ref(false)
const collapsed = ref(false)
const activeTab = ref<'user' | 'system' | 'pending'>('user')

let timer: ReturnType<typeof setInterval> | null = null

// Config dialog state
const configDialogVisible = ref(false)
const configModelTarget = ref<string>('')
const configPageName = ref<string>('')
const configPageMissing = ref(false)

async function load() {
  try {
    const { get } = useApi()
    data.value = await get<ResourceMap>('/api/resources')
    error.value = false
  } catch {
    error.value = true
  }
}

async function loadUnconfigured() {
  try {
    const { get } = useApi()
    unconfigured.value = await get('/api/resources/unconfigured')
  } catch { /* ignore */ }
}

function toggle() {
  collapsed.value = !collapsed.value
}

function setTab(tab: 'user' | 'system' | 'pending') {
  activeTab.value = tab
  if (tab === 'pending') loadUnconfigured()
}

function findModel(name: string): ResourceItem | null {
  if (!data.value) return null
  return data.value.models.find(m => m.name === name) || null
}

function openConfigForModel(name: string) {
  configModelTarget.value = name
  const model = findModel(name)
  const page = model?.config_page || ''
  if (!page) {
    configPageMissing.value = true
    configPageName.value = ''
  } else {
    configPageMissing.value = false
    configPageName.value = page
  }
  configDialogVisible.value = true
}

function onConfigSaved() {
  configDialogVisible.value = false
  load()
}

function onConfigClose() {
  configDialogVisible.value = false
}

onMounted(() => {
  load()
  timer = setInterval(load, 15000)
})

onUnmounted(() => {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
})

defineExpose({ load })
</script>

<template>
  <aside id="resource-panel-right" :key="appStore.language" :class="{ overlay: overlay }">
    <div v-if="overlay" class="overlay-close-bar">
      <span class="overlay-title">{{ t('common.resources') }}</span>
      <button class="overlay-close-btn" @click="$emit('close-overlay')" :title="t('common.closeMenu')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div id="rp-header">
      <span class="rp-title">{{ t('resource.title') }}</span>
      <button id="btn-rp-collapse" type="button" @click="toggle"
        :title="collapsed ? t('resource.expand') : t('resource.collapse')">
        {{ collapsed ? '◀' : '▶' }}
      </button>
    </div>

    <div id="rp-tabs" v-show="!collapsed">
      <button :class="{ active: activeTab === 'user' }" @click="setTab('user')">{{ t('resource.userTab') }}</button>
      <button :class="{ active: activeTab === 'system' }" @click="setTab('system')">{{ t('resource.systemTab') }}</button>
      <button :class="{ active: activeTab === 'pending' }" @click="setTab('pending')">
        {{ t('resource.pendingTab') }}
        <span v-if="unconfigured.length" class="pending-count">{{ unconfigured.length }}</span>
      </button>
    </div>

    <div id="rp-content" :class="{ collapsed }">
      <template v-if="error">
        <div class="rp-empty">{{ t('resource.error') }}</div>
      </template>
      <template v-else-if="!data">
        <div class="rp-loading">{{ t('resource.loading') }}</div>
      </template>

      <!-- ═══ User tab ═══ -->
      <template v-else-if="activeTab === 'user'">
        <div class="rp-section">
          <h3 class="rp-section-title">{{ t('resource.models') }}</h3>
          <template v-if="!data.models.filter(m => m.source !== 'system').length">
            <div class="rp-empty">{{ t('resource.noUserModels') }}</div>
          </template>
          <div v-for="m in data.models.filter(m => m.source !== 'system')" :key="m.name"
               class="rp-item clickable" @click="openConfigForModel(m.name)">
            <span v-if="appStore.configStatus?.config_name === m.name" class="dot-active" title="当前激活的模型"></span>
            <span class="rp-item-name">{{ m.name }}</span>
            <span v-if="m.model_name" class="rp-item-extra">{{ m.model_name }}</span>
            <span class="rp-action-hint">⚙</span>
            <div v-if="m.description" class="rp-item-desc">{{ m.description }}</div>
          </div>
        </div>
        <div class="rp-section">
          <h3 class="rp-section-title">{{ t('resource.tools') }}</h3>
          <template v-if="!data.tools.filter(t => t.source !== 'system').length">
            <div class="rp-empty">{{ t('resource.noUserTools') }}</div>
          </template>
          <div v-for="t in data.tools.filter(t => t.source !== 'system')" :key="t.name" class="rp-item">
            <span class="rp-item-name">{{ t.name }}</span>
            <div v-if="t.description" class="rp-item-desc">{{ t.description }}</div>
          </div>
        </div>
        <div class="rp-section">
          <h3 class="rp-section-title">{{ t('resource.skills') }}</h3>
          <template v-if="!data.skills.filter(s => s.source !== 'system').length">
            <div class="rp-empty">{{ t('resource.noUserSkills') }}</div>
          </template>
          <div v-for="s in data.skills.filter(s => s.source !== 'system')" :key="s.name" class="rp-item">
            <span class="rp-item-name">{{ s.name }}</span>
            <div v-if="s.description" class="rp-item-desc">{{ s.description }}</div>
          </div>
        </div>
      </template>

      <!-- ═══ System tab ═══ -->
      <template v-else-if="activeTab === 'system'">
        <div class="rp-section">
          <h3 class="rp-section-title">{{ t('resource.models') }}</h3>
          <div v-for="m in data.models" :key="'sys-'+m.name"
               class="rp-item" :class="{ clickable: m.configured }"
               @click="m.configured && openConfigForModel(m.name)">
            <span v-if="appStore.configStatus?.config_name === m.name" class="dot-active" title="当前激活的模型"></span>
            <span class="rp-item-name">{{ m.name }}</span>
            <span class="rp-badge-sys">{{ t('resource.sys') }}</span>
            <span v-if="m.configured" class="rp-action-hint">⚙</span>
            <span v-else class="rp-action-hint" style="color:var(--warning-text)" title="点击待配置标签页进行配置">⚠</span>
            <div v-if="m.description" class="rp-item-desc">{{ m.description }}</div>
          </div>
          <div v-if="!data.models.length" class="rp-empty">{{ t('resource.noSystemModels') }}</div>
        </div>
        <div class="rp-section">
          <h3 class="rp-section-title">{{ t('resource.tools') }}</h3>
          <div v-for="tool in data.tools" :key="'sys-'+tool.name" class="rp-item">
            <span class="rp-item-name">{{ tool.name }}</span>
            <span class="rp-badge-sys">{{ t('resource.sys') }}</span>
            <div v-if="tool.description" class="rp-item-desc">{{ tool.description }}</div>
          </div>
          <div v-if="!data.tools.length" class="rp-empty">{{ t('resource.noSystemTools') }}</div>
        </div>
        <div class="rp-section">
          <h3 class="rp-section-title">{{ t('resource.skills') }}</h3>
          <div v-for="s in data.skills" :key="'sys-'+s.name" class="rp-item">
            <span class="rp-item-name">{{ s.name }}</span>
            <span class="rp-badge-sys">{{ t('resource.sys') }}</span>
            <div v-if="s.description" class="rp-item-desc">{{ s.description }}</div>
          </div>
          <div v-if="!data.skills.length" class="rp-empty">{{ t('resource.noSystemSkills') }}</div>
        </div>
      </template>

      <!-- ═══ Pending tab ═══ -->
      <template v-else-if="activeTab === 'pending'">
        <div class="rp-section">
          <h3 class="rp-section-title">{{ t('resource.unconfiguredTitle') }}</h3>
          <template v-if="!unconfigured.length">
            <div class="rp-empty">{{ t('resource.allConfigured') }}</div>
          </template>
          <div v-for="slot in unconfigured" :key="`${slot.type}/${slot.name}`" class="rp-item slot-item">
            <div class="slot-main">
              <span class="rp-item-name">{{ slot.name }}</span>
              <span class="rp-badge-pending">{{ slot.type }}</span>
              <span v-if="slot.required" class="rp-badge-required">{{ t('resource.required') }}</span>
              <button class="btn-configure" @click="openConfigForModel(slot.name)">{{ t('resource.configure') }}</button>
            </div>
            <div v-if="slot.description" class="rp-item-desc">{{ slot.description }}</div>
            <div v-if="slot.depends_on?.length" class="rp-item-desc slot-deps">
              依赖: {{ slot.depends_on.map(d => `${d.type}/${d.name}`).join(', ') }}
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- ═══ Config dialog ═══ -->
    <div v-if="configDialogVisible" class="config-dialog-overlay" @click.self="onConfigClose">
      <div class="config-dialog-card">
        <div class="config-dialog-header">
          <h3>模型配置: {{ configModelTarget }}</h3>
          <button class="btn-close" @click="onConfigClose">✕</button>
        </div>
        <template v-if="configPageMissing">
          <div class="config-page-missing">
            <p><strong>页面缺失</strong></p>
            <p>该资源 ({{ configModelTarget }}) 的配置页面未注册。</p>
            <p>请联系系统管理员完善相关表单 page。</p>
          </div>
        </template>
        <template v-else>
          <DeepSeekConfigForm
            v-if="configPageName === 'DeepSeekConfigForm'"
            :model-name="configModelTarget"
            @saved="onConfigSaved"
            @close="onConfigClose"
          />
          <div v-else class="config-page-missing">
            <p><strong>页面未找到</strong></p>
            <p>配置页 "{{ configPageName }}" 不存在。</p>
            <p>请联系系统管理员完善相关表单 page。</p>
          </div>
        </template>
      </div>
    </div>
  </aside>
</template>

<style scoped>
#resource-panel-right {
  grid-column: 2; grid-row: 2;
  background: var(--bg-panel); border-left: 1px solid var(--border);
  display: flex; flex-direction: column; overflow: hidden;
}
#rp-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 14px; border-bottom: 1px solid var(--border);
}
#rp-header .rp-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
#btn-rp-collapse {
  background: none; border: 1px solid var(--border); color: var(--text-muted);
  cursor: pointer; font-size: 11px; padding: 3px 8px; border-radius: var(--radius-sm);
  transition: all var(--transition);
}
#btn-rp-collapse:hover { background: var(--bg-hover); color: var(--text-primary); border-color: var(--text-muted); }

#rp-tabs {
  display: flex; margin: 10px 12px 0; border-radius: var(--radius-md);
  overflow: hidden; border: 1px solid var(--border); background: var(--bg-input);
}
#rp-tabs button {
  flex: 1; padding: 6px 8px; border: none; cursor: pointer; font-size: 12px;
  font-weight: 600; background: transparent; color: var(--text-secondary);
  transition: all var(--transition);
}
#rp-tabs button:hover:not(.active) { color: var(--text-primary); background: var(--bg-hover); }
#rp-tabs button.active {
  background: var(--accent); color: #fff;
  box-shadow: 0 1px 4px rgba(99,102,241,0.3);
}

#rp-content {
  flex: 1; overflow-y: auto; padding: 12px 14px;
  transition: max-height 0.3s ease, opacity 0.25s ease, padding 0.3s ease;
}
#rp-content.collapsed { max-height: 0; overflow: hidden; padding: 0 14px; opacity: 0; }

.rp-section { margin-bottom: 20px; }
.rp-section-title {
  font-size: 10px; text-transform: uppercase; color: var(--text-muted);
  margin-bottom: 8px; letter-spacing: 1px; font-weight: 700;
}
.rp-item {
  font-size: 13px; padding: 7px 10px; border-radius: var(--radius-sm);
  color: var(--text-primary); display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  transition: background var(--transition);
}
.rp-item:hover { background: var(--bg-hover); }
.rp-item.clickable { cursor: pointer; }
.rp-item-name { font-weight: 500; }
.rp-action-hint { margin-left: auto; font-size: 12px; color: var(--text-muted); opacity: 0; transition: opacity var(--transition); }
.rp-item:hover .rp-action-hint { opacity: 1; }

.dot-active {
  display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: var(--success); flex-shrink: 0;
  box-shadow: 0 0 6px rgba(34,197,94,0.5);
  animation: dotPulse 3s ease infinite;
}
@keyframes dotPulse {
  0%, 100% { box-shadow: 0 0 6px rgba(34,197,94,0.5); }
  50% { box-shadow: 0 0 12px rgba(34,197,94,0.8); }
}
.rp-item-extra {
  font-size: 11px; color: var(--accent); font-family: 'JetBrains Mono', 'Fira Code', monospace;
  background: var(--accent-light); padding: 1px 7px; border-radius: var(--radius-full);
}
.rp-item .rp-item-desc { font-size: 11px; color: var(--text-muted); width: 100%; }
.rp-badge-sys {
  font-size: 10px; color: var(--text-muted); border: 1px solid var(--border);
  padding: 0 6px; border-radius: var(--radius-full); font-weight: 400;
}
.rp-empty {
  font-size: 12px; color: var(--text-muted); font-style: italic;
  padding: 6px 10px; line-height: 1.5;
}
.rp-loading {
  padding: 24px; text-align: center; color: var(--text-muted); font-size: 12px;
}

.pending-count {
  font-size: 10px; background: var(--error-text); color: #fff;
  border-radius: 50%; min-width: 16px; height: 16px; display: inline-flex;
  align-items: center; justify-content: center; margin-left: 4px;
}
.slot-main { display: flex; align-items: center; gap: 6px; width: 100%; }
.rp-badge-pending {
  font-size: 9px; color: var(--accent); border: 1px solid rgba(99,102,241,0.3);
  padding: 0 5px; border-radius: var(--radius-full); font-weight: 400;
}
.rp-badge-required {
  font-size: 9px; color: var(--error-text); border: 1px solid rgba(239,68,68,0.3);
  padding: 0 5px; border-radius: var(--radius-full); font-weight: 400;
}
.btn-configure {
  margin-left: auto; padding: 2px 8px; font-size: 11px;
  border: 1px solid var(--accent); background: transparent;
  color: var(--accent); border-radius: var(--radius-sm); cursor: pointer;
  transition: all var(--transition);
}
.btn-configure:hover { background: rgba(99,102,241,0.15); }
.slot-deps { color: var(--text-muted); }

/* Config dialog overlay */
.config-dialog-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.6);
  z-index: 100; display: flex; align-items: center; justify-content: center;
  backdrop-filter: blur(4px);
  animation: fadeIn 0.15s ease;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.config-dialog-card {
  background: var(--bg-card); border: 1px solid var(--border-glass);
  border-radius: var(--radius-xl); box-shadow: var(--shadow-lg), var(--shadow-glow);
  width: 580px; max-width: 90vw; max-height: 85vh; overflow-y: auto; padding: 32px;
  animation: modalSlideIn 0.2s ease;
}
@keyframes modalSlideIn { from { opacity: 0; transform: translateY(12px) scale(0.97); } to { opacity: 1; transform: translateY(0) scale(1); } }
.config-dialog-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.config-dialog-header h3 { font-size: 15px; color: var(--text-primary); }
.btn-close {
  background: none; border: 1px solid var(--border); color: var(--text-muted);
  cursor: pointer; font-size: 14px; padding: 4px 10px; border-radius: var(--radius-sm);
  transition: all var(--transition);
}
.btn-close:hover { background: var(--bg-hover); color: var(--text-primary); }

.config-page-missing {
  padding: 40px 20px; text-align: center; color: var(--text-secondary);
}
.config-page-missing p { margin-bottom: 8px; line-height: 1.6; }
.config-page-missing strong { color: var(--error-text); font-size: 16px; }

/* ── Mobile overlay mode ──────────────────────── */
#resource-panel-right.overlay {
  display: flex !important;
  position: fixed; top: 44px; right: 0; bottom: 0; width: 300px; z-index: 50;
  animation: slideInRight 0.25s ease;
}
@keyframes slideInRight {
  from { transform: translateX(100%); }
  to   { transform: translateX(0); }
}
.overlay-close-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; border-bottom: 1px solid var(--border);
}
.overlay-title {
  font-size: 14px; font-weight: 600; color: var(--text-primary);
}
.overlay-close-btn {
  display: flex; align-items: center; justify-content: center;
  width: 30px; height: 30px; border-radius: var(--radius-sm);
  border: none; background: transparent; color: var(--text-muted);
  cursor: pointer; transition: all var(--transition);
}
.overlay-close-btn:hover { background: rgba(255,255,255,0.08); color: var(--text-primary); }
</style>
