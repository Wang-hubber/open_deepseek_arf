<script setup lang="ts">
import { onMounted } from 'vue'
import { useSessionStore } from '@/stores/sessions'
import { useChatStore } from '@/stores/chat'
import { useI18n } from '@/composables/useI18n'
import type { ChatMessage } from '@/types'

defineProps<{
  overlay?: boolean
}>()

defineEmits<{
  (e: 'close-overlay'): void
}>()

const { t } = useI18n()

const sessionStore = useSessionStore()
const chatStore = useChatStore()

onMounted(() => {
  sessionStore.loadSessions()
})

function relativeTime(isoStr: string | null | undefined): string {
  if (!isoStr) return ''
  const then = new Date(isoStr)
  const now = new Date()
  const diffDays = Math.floor((now.getTime() - then.getTime()) / 86400000)
  if (diffDays === 0) return t('session.today')
  if (diffDays === 1) return t('session.yesterday')
  if (diffDays <= 7) return t('session.last7days')
  if (diffDays <= 30) return t('session.last30days')
  return `${then.getFullYear()}-${String(then.getMonth() + 1).padStart(2, '0')}-${String(then.getDate()).padStart(2, '0')}`
}

function combinedItems() {
  const items: {
    id: string; title: string; timeLabel: string; messageCount: number; turnCount: number; jsonSizeMb: number; isActive: boolean; isPending?: boolean
  }[] = []

  // Pending new session placeholder (frontend-only, not yet on backend)
  if (sessionStore.pendingNewSession) {
    items.push({
      id: '__pending__', title: sessionStore.pendingPlaceholder, timeLabel: t('session.current'),
      messageCount: 0, turnCount: 0, jsonSizeMb: 0, isActive: true, isPending: true,
    })
  } else if (sessionStore.activeSession) {
    const a = sessionStore.activeSession
    items.push({
      id: a.id, title: a.title, timeLabel: t('session.current'),
      messageCount: a.message_count, turnCount: 0, jsonSizeMb: 0, isActive: true,
    })
  }

  for (const s of sessionStore.sessions) {
    if (sessionStore.activeSession && s.id === sessionStore.activeSession.id) continue
    items.push({
      id: s.id, title: s.title,
      timeLabel: relativeTime(s.ended_at || s.created_at),
      messageCount: s.message_count,
      turnCount: s.turn_count || 0,
      jsonSizeMb: s.json_size_mb || 0,
      isActive: false,
    })
  }

  items.sort((a, b) => {
    if (a.isActive) return -1
    if (b.isActive) return 1
    return 0
  })

  return items
}

async function handleClick(id: string, isActive: boolean) {
  if (id === '__pending__') return
  if (isActive) {
    try {
      const api = (await import('@/composables/useApi')).useApi()
      const messages = await api.get<ChatMessage[]>('/api/sessions/active/messages')
      chatStore.renderFromHistory(messages || [])
    } catch {
      chatStore.renderFromHistory([])
    }
  } else {
    try {
      const data = await sessionStore.fetchArchive(id)
      chatStore.renderFromHistory(data.messages || [])
    } catch (e: any) {
      alert(t('session.loadFailed', { msg: e.message }))
    }
  }
}

function confirmDelete(id: string, title: string, isActive: boolean) {
  if (!confirm(`${t('session.deleteConfirm', { title })}`)) return
  sessionStore.deleteSession(id, isActive).then(firstArchive => {
    chatStore.clearMessages()
    if (isActive) {
      if (firstArchive) {
        handleClick(firstArchive.id, false)
      } else {
        sessionStore.createSession().then(() => {
          chatStore.clearMessages()
        })
      }
    }
  }).catch(e => {
    alert(t('common.error', { msg: e.message }))
  })
}

let lastCreateTime = 0
const DEBOUNCE_MS = 5000

function createNewSession() {
  const now = Date.now()
  if (now - lastCreateTime < DEBOUNCE_MS) return
  lastCreateTime = now

  const total = sessionStore.totalCount()
  if (total >= 10 && sessionStore.sessions.length > 0) {
    const oldest = sessionStore.sessions[sessionStore.sessions.length - 1]
    if (!confirm(`${t('session.sessionLimit', { title: oldest.title })}`)) return
  }

  // Frontend-only placeholder — backend session is created lazily on first message
  chatStore.clearMessages()
  const hhmm = `${String(new Date().getHours()).padStart(2, '0')}:${String(new Date().getMinutes()).padStart(2, '0')}`
  sessionStore.startNewSession(`新会话 · ${hhmm}`)
}
</script>

<template>
  <aside id="session-panel" :class="{ overlay: overlay }">
    <div v-if="overlay" class="overlay-close-bar">
      <span class="overlay-title">{{ t('common.sessions') }}</span>
      <button class="overlay-close-btn" @click="$emit('close-overlay')" :title="t('common.closeMenu')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div id="session-header">
      <button id="btn-new-session" @click="createNewSession">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
        {{ t('nav.newSession') }}
      </button>
    </div>

    <div v-if="sessionStore.loading" class="session-skeleton">
      <div class="sk-item"></div>
      <div class="sk-item"></div>
      <div class="sk-item"></div>
    </div>

    <div v-else-if="sessionStore.error" id="session-error">
      <span class="err-text">{{ t('session.loading') }}</span>
      <button class="btn-retry" @click="sessionStore.loadSessions()">{{ t('session.retry') }}</button>
    </div>

    <div v-else-if="combinedItems().length === 0" id="session-empty">
      {{ t('session.empty') }}
    </div>

    <div v-else id="session-list">
      <div
        v-for="item in combinedItems()"
        :key="item.id"
        class="session-item"
        :class="{
          active: item.isActive,
        }"
        @click="handleClick(item.id, item.isActive)"
      >
        <div class="si-indicator"></div>
        <div class="si-content">
          <div class="si-title">{{ item.title }}</div>
          <div class="si-time">{{ item.timeLabel }}<template v-if="item.turnCount"> · {{ t('session.turns', { n: item.turnCount }) }}</template><template v-if="item.jsonSizeMb"> · {{ item.jsonSizeMb }} MB</template></div>
        </div>
        <button v-if="!item.isPending" class="si-delete" :title="t('session.deleteTitle')" @click.stop="confirmDelete(item.id, item.title, item.isActive)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 11v6M14 11v6"/></svg>
        </button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
#session-panel {
  grid-column: 1; grid-row: 2;
  background: var(--bg-panel); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; overflow: hidden;
}
#session-header {
  padding: 14px 12px; border-bottom: 1px solid var(--border);
}
#btn-new-session {
  display: flex; align-items: center; justify-content: center; gap: 6px;
  width: 100%; padding: 10px 14px;
  background: var(--accent-gradient); color: var(--text-on-accent);
  border: none; border-radius: var(--radius-md); font-size: 14px;
  font-weight: 600; cursor: pointer;
  transition: all var(--transition);
  box-shadow: 0 2px 8px rgba(99,102,241,0.25);
}
#btn-new-session:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(99,102,241,0.35);
}
#btn-new-session:active { transform: translateY(0); }

#session-list { flex: 1; overflow-y: auto; padding: 6px 10px; }
#session-empty {
  flex: 1; display: flex; align-items: center; justify-content: center;
  padding: 24px; color: var(--text-muted); font-size: 13px; text-align: center;
}
#session-error {
  flex: 1; display: flex; flex-direction: column; align-items: center;
  justify-content: center; padding: 24px; gap: 12px;
}
#session-error .err-text { color: var(--error-text); font-size: 13px; }
.btn-retry {
  padding: 6px 20px; background: var(--accent); color: #fff; border: none;
  border-radius: var(--radius-md); cursor: pointer; font-size: 13px; font-weight: 500;
}

.session-item {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px;
  border-radius: var(--radius-md); cursor: pointer; margin-bottom: 2px;
  transition: all var(--transition); position: relative;
}
.session-item:hover { background: var(--bg-hover); }
.session-item.active {
  background: var(--bg-active);
}
.session-item.active .si-indicator {
  background: var(--accent);
  box-shadow: 0 0 8px rgba(99,102,241,0.4);
}
.session-item.viewing {
  background: var(--warning-bg);
}
.session-item.viewing .si-indicator {
  background: var(--warning);
}

.si-indicator {
  width: 3px; height: 32px; border-radius: 2px;
  background: transparent; flex-shrink: 0;
  transition: all var(--transition);
}

.session-item .si-content { flex: 1; min-width: 0; }
.session-item .si-title {
  font-size: 13px; font-weight: 600; color: var(--text-primary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.session-item .si-time {
  font-size: 11px; color: var(--text-muted); margin-top: 2px;
}
.session-item .si-delete {
  flex-shrink: 0; width: 28px; height: 28px; display: flex; align-items: center;
  justify-content: center; border-radius: var(--radius-sm); border: none;
  background: transparent; color: var(--text-muted); cursor: pointer;
  transition: all var(--transition); opacity: 0;
}
.session-item:hover .si-delete { opacity: 1; }
.session-item .si-delete:hover { color: var(--error-text); background: var(--error-bg); }

/* ── Mobile overlay mode ──────────────────────── */
#session-panel.overlay {
  display: flex !important;
  position: fixed; top: 44px; left: 0; bottom: 0; width: 280px; z-index: 50;
  animation: slideInLeft 0.25s ease;
}
@keyframes slideInLeft {
  from { transform: translateX(-100%); }
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
