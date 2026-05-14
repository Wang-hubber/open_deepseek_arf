import { createRouter, createWebHistory } from 'vue-router'
import { useAppStore } from '@/stores/app'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/ChatLayout.vue'),
    },
    {
      path: '/welcome',
      name: 'welcome',
      component: () => import('@/views/WelcomePage.vue'),
    },
    {
      path: '/config',
      name: 'config',
      component: () => import('@/views/ConfigPage.vue'),
    },
    {
      path: '/usage',
      name: 'usage',
      component: () => import('@/views/UsagePage.vue'),
    },
    {
      path: '/traces',
      name: 'traces',
      component: () => import('@/views/TraceView.vue'),
    },
    {
      path: '/resource-stats',
      name: 'resource-stats',
      component: () => import('@/views/ResourceStatsView.vue'),
    },
    {
      path: '/resource-stats/:name',
      name: 'resource-detail',
      component: () => import('@/views/ResourceDetailView.vue'),
    },
  ],
})

router.beforeEach(async (to) => {
  if (to.name === 'welcome') return true

  const app = useAppStore()
  if (!app.configStatus || !app.configStatus.configured) {
    if (!app.configStatus) {
      await app.checkConfigStatus()
    }
    if (!app.configStatus?.configured) {
      if (to.name === 'config') return true
      const seenWelcome = localStorage.getItem('arf_seen_welcome')
      return seenWelcome ? { name: 'config' } : { name: 'welcome' }
    }
  }

  return true
})

export default router
