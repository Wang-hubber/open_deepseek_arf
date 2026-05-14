<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useVault } from '@/composables/useVault'
import { useI18n } from '@/composables/useI18n'

const props = defineProps<{
  showMobileSessions: boolean
  showMobileResources: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle-sessions'): void
  (e: 'toggle-resources'): void
}>()

const { t } = useI18n()
const router = useRouter()
const appStore = useAppStore()
const { lock: doLock } = useVault()

async function lockVault() {
  await doLock()
}

function onLanguageChange() {
  const lang = appStore.language
  appStore.setLanguage(lang)
}

// Idle timer for auto-lock
const IDLE_TIMEOUT = 10 * 60 * 1000
let idleTimer: ReturnType<typeof setTimeout> | null = null

function resetIdleTimer() {
  if (idleTimer) clearTimeout(idleTimer)
  idleTimer = setTimeout(() => lockVault(), IDLE_TIMEOUT)
}

const events = ['mousedown', 'mousemove', 'keydown', 'scroll', 'touchstart', 'click']
for (const evt of events) {
  document.addEventListener(evt, resetIdleTimer)
}
resetIdleTimer()
</script>

<template>
  <div id="status-bar">
    <span class="status-left">
      <button class="sb-btn sb-btn-icon mobile-only" :title="t('common.menu')" @click="emit('toggle-sessions')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
        </svg>
      </button>

      <span class="dot dot-ok" title="当前会话激活模型"></span>
      <span class="model-name">{{ appStore.configStatus?.model_name || 'ARF Agent' }}</span>
    </span>

    <span class="status-right">
      <button class="sb-btn sb-btn-icon" @click="router.push('/usage')" :title="t('status.usage')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 3v18h18"/><path d="M7 16l4-8 4 4 4-6"/></svg>
      </button>

      <button class="sb-btn sb-btn-icon" @click="router.push('/traces')" title="Trace 追踪">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
      </button>

      <button class="sb-btn sb-btn-icon" @click="router.push('/resource-stats')" title="资源统计">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/><path d="M15 21V9"/></svg>
      </button>

      <select
        class="lang-select"
        v-model="appStore.language"
        @change="onLanguageChange"
        :title="t('status.language')"
      >
        <option value="zh">中文</option>
        <option value="en">EN</option>
      </select>

      <button class="sb-btn sb-btn-icon mobile-only" :title="t('common.resources')" @click="emit('toggle-resources')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
      </button>
    </span>
  </div>
</template>

<style scoped>
#status-bar {
  grid-column: 1 / -1; grid-row: 1;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 16px; font-size: 13px; z-index: 10;
  background: rgba(7,7,16,0.92);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-glass);
}

#status-bar .dot {
  display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  margin-right: 8px; position: relative;
}
.dot-ok {
  background: var(--success);
  box-shadow: 0 0 6px rgba(34,197,94,0.5);
  animation: dotPulse 3s ease infinite;
}
@keyframes dotPulse {
  0%, 100% { box-shadow: 0 0 6px rgba(34,197,94,0.5); }
  50% { box-shadow: 0 0 12px rgba(34,197,94,0.8); }
}

.status-left, .status-right {
  display: flex; align-items: center; gap: 8px;
}
.model-name {
  color: var(--text-secondary); font-size: 12px;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
}
.status-user { color: var(--text-primary); font-weight: 600; font-size: 13px; }

/* ── Language selector ────────────────────────── */
.lang-select {
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: 13px; font-weight: 500;
  padding: 5px 10px;
  cursor: pointer; outline: none;
  transition: border-color var(--transition), background var(--transition);
}
.lang-select:hover { background: rgba(255,255,255,0.12); }
.lang-select:focus { border-color: var(--accent); }
.lang-select option { background: var(--bg-card); color: var(--text-primary); }

/* ── Buttons ──────────────────────────────────── */
.sb-btn {
  background: none; border: none; color: var(--text-secondary);
  cursor: pointer; font-size: 13px; padding: 4px 10px;
  border-radius: var(--radius-sm); transition: all var(--transition);
  display: flex; align-items: center; gap: 4px;
}
.sb-btn:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }
.sb-btn-icon {
  padding: 5px 8px;
}

.sb-btn-logout {
  background: none; border: 1px solid rgba(255,255,255,0.08);
  color: var(--text-secondary); cursor: pointer;
  font-size: 13px; padding: 5px 12px; border-radius: var(--radius-sm);
  transition: all var(--transition); display: flex; align-items: center; gap: 5px;
}
.sb-btn-logout:hover { color: var(--text-primary); border-color: rgba(255,255,255,0.15); background: rgba(255,255,255,0.04); }

/* ── Mobile-only controls ─────────────────────── */
.mobile-only { display: none; }

@media (max-width: 640px) {
  .mobile-only { display: flex; }
  .logout-text { display: none; }
  .model-name { display: none; }
  #status-bar .dot { margin-right: 0; }
  .status-left { gap: 4px; }
  .status-right { gap: 4px; }
  .lang-select { padding: 4px 7px; font-size: 12px; }
  .sb-btn-logout { padding: 5px 8px; }
}

@media (max-width: 480px) {
  #status-bar { padding: 0 10px; }
  .status-user { display: none; }
}
</style>
