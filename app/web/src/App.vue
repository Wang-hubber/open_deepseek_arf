<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useI18n } from '@/composables/useI18n'

const { t } = useI18n()
const router = useRouter()
const appStore = useAppStore()

onMounted(async () => {
  await appStore.init()
  navigateToPage()
})

watch(() => appStore.currentPage, () => {
  navigateToPage()
})

function navigateToPage() {
  switch (appStore.currentPage) {
    case 'welcome':
      router.replace('/welcome')
      break
    case 'chat':
      router.replace('/')
      break
  }
}
</script>

<template>
  <div v-if="appStore.loading" class="loading-screen">
    <p>{{ t('common.loading') }}</p>
  </div>
  <router-view v-else />
</template>

<style scoped>
.loading-screen {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  color: var(--text-muted);
  font-size: 14px;
}
</style>
