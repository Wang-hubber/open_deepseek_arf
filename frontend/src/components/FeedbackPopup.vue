<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  submit: [text: string]
  close: []
}>()

const feedback = ref('')
const submitted = ref(false)

function handleSubmit() {
  if (feedback.value.trim()) {
    submitted.value = true
    emit('submit', feedback.value.trim())
  }
}
</script>

<template>
  <div class="fb-popup">
    <div v-if="!submitted" class="fb-body">
      <p class="fb-title">这条回复有什么问题？</p>
      <textarea
        v-model="feedback"
        class="fb-input"
        placeholder="请描述问题..."
        rows="2"
        @keyup.enter.exact="handleSubmit"
      ></textarea>
      <div class="fb-actions">
        <button class="fb-cancel" @click="emit('close')">取消</button>
        <button class="fb-submit" :disabled="!feedback.trim()" @click="handleSubmit">提交</button>
      </div>
    </div>
    <div v-else class="fb-done">
      感谢反馈！
    </div>
  </div>
</template>

<style scoped>
.fb-popup {
  margin-top: 8px;
  padding: 10px 14px;
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 8px;
  max-width: 360px;
}
.fb-title {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--text-secondary);
}
.fb-input {
  width: 100%;
  background: rgba(0,0,0,0.3);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 6px;
  padding: 8px 10px;
  color: var(--text-primary);
  font-size: 13px;
  resize: vertical;
  outline: none;
  box-sizing: border-box;
}
.fb-input:focus { border-color: var(--accent); }
.fb-actions {
  display: flex; gap: 8px; justify-content: flex-end;
  margin-top: 8px;
}
.fb-cancel, .fb-submit {
  font-size: 12px; padding: 4px 14px; border-radius: 4px;
  border: none; cursor: pointer;
}
.fb-cancel {
  background: transparent; color: var(--text-secondary);
}
.fb-cancel:hover { color: var(--text-primary); }
.fb-submit {
  background: var(--accent); color: #fff; font-weight: 600;
}
.fb-submit:disabled { opacity: 0.4; cursor: not-allowed; }
.fb-done {
  font-size: 13px; color: var(--success);
}
</style>
