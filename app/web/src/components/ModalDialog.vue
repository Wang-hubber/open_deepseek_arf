<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  title: string
  body: string
  confirmText: string
  danger?: boolean
}>()

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()

const visible = ref(false)

function show() {
  visible.value = true
}

function hide() {
  visible.value = false
}

function onConfirm() {
  emit('confirm')
  hide()
}

function onCancel() {
  emit('cancel')
  hide()
}

function onOverlayClick(e: MouseEvent) {
  if (e.target === e.currentTarget) hide()
}

defineExpose({ show, hide })
</script>

<template>
  <div v-if="visible" class="modal-overlay" @click="onOverlayClick">
    <div class="modal-card">
      <h2>{{ title }}</h2>
      <p>{{ body }}</p>
      <div class="modal-actions">
        <button class="btn-cancel" @click="onCancel">取消</button>
        <button v-if="danger" class="btn-danger" @click="onConfirm">{{ confirmText }}</button>
        <button v-else class="btn-confirm" @click="onConfirm">{{ confirmText }}</button>
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
  width: 420px; max-width: 90vw; padding: 32px;
  animation: modalSlideIn 0.2s ease;
}
@keyframes modalSlideIn { from { opacity: 0; transform: translateY(12px) scale(0.97); } to { opacity: 1; transform: translateY(0) scale(1); } }

.modal-card h2 { font-size: 16px; margin-bottom: 10px; color: var(--text-primary); }
.modal-card p { font-size: 14px; color: var(--text-secondary); margin-bottom: 24px; line-height: 1.6; }
.modal-actions { display: flex; gap: 10px; justify-content: flex-end; }

.btn-cancel {
  padding: 8px 20px; border: 1px solid var(--border);
  background: transparent; border-radius: var(--radius-md); cursor: pointer;
  font-size: 13px; font-weight: 600; color: var(--text-secondary);
  transition: all var(--transition);
}
.btn-cancel:hover { background: var(--bg-hover); color: var(--text-primary); }

.btn-danger {
  padding: 8px 20px; border: none;
  background: rgba(239,68,68,0.15); color: var(--error-text);
  border-radius: var(--radius-md); cursor: pointer; font-size: 13px; font-weight: 600;
  transition: all var(--transition);
}
.btn-danger:hover { background: rgba(239,68,68,0.25); }

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
</style>
