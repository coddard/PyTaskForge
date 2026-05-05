import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  { path: '/login', name: 'Login',     component: () => import('@/views/LoginView.vue'),     meta: { public: true } },
  { path: '/',      name: 'Dashboard', component: () => import('@/views/DashboardView.vue'), meta: { public: false } },
  { path: '/jobs/new',      name: 'JobNew',    component: () => import('@/views/JobFormView.vue'),   meta: { public: false } },
  { path: '/jobs/:id/edit', name: 'JobEdit',   component: () => import('@/views/JobFormView.vue'),   meta: { public: false } },
  { path: '/jobs/:id/logs', name: 'JobLogs',   component: () => import('@/views/JobLogsView.vue'),   meta: { public: false } },
  { path: '/:pathMatch(.*)*', redirect: '/' }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isAuthenticated) {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
})

export default router

