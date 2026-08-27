import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { fetchSetupStatus } from '../api/platform'

declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean
    admin?: boolean
    ai?: boolean
  }
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('../views/Login.vue'), meta: { public: true } },
    { path: '/register', name: 'register', component: () => import('../views/Register.vue'), meta: { public: true } },
    { path: '/change-password', name: 'change-password', component: () => import('../views/ChangePassword.vue') },
    {
      path: '/',
      component: () => import('../components/AppLayout.vue'),
      redirect: '/home',
      children: [
        { path: 'home', name: 'home', component: () => import('../views/Home.vue') },
        { path: 'household', name: 'household', component: () => import('../views/Household.vue') },
        { path: 'chat', name: 'chat', component: () => import('../views/Chat.vue'), meta: { ai: true } },
        { path: 'tickets', name: 'tickets', component: () => import('../views/MyTickets.vue') },
        { path: 'devices', name: 'devices', component: () => import('../views/DeviceList.vue') },
        { path: 'devices/compare', name: 'devices-compare', component: () => import('../views/DeviceCompare.vue') },
      ],
    },
    {
      path: '/admin',
      component: () => import('../components/admin/AdminLayout.vue'),
      redirect: '/admin/setup',
      meta: { admin: true },
      children: [
        { path: 'setup', name: 'admin-setup', component: () => import('../views/admin/Setup.vue') },
        { path: 'dashboard', name: 'admin-dashboard', component: () => import('../views/admin/Dashboard.vue') },
        { path: 'users', name: 'admin-users', component: () => import('../views/admin/Users.vue') },
        { path: 'devices', name: 'admin-devices', component: () => import('../views/admin/DevicesInventory.vue') },
        { path: 'knowledge', name: 'admin-knowledge', component: () => import('../views/admin/Knowledge.vue') },
        { path: 'tickets', name: 'admin-tickets', component: () => import('../views/admin/Tickets.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/home' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isLoggedIn) return { path: '/login' }
  if (to.path === '/login' && auth.isLoggedIn) return { path: '/home' }
  if (auth.isLoggedIn && auth.mustChangePassword && to.path !== '/change-password') return { path: '/change-password' }
  if (to.meta.admin && auth.account?.role !== 'admin') return { path: '/home' }
  if (to.meta.ai && auth.isLoggedIn) {
    const setup = await fetchSetupStatus()
    if (!setup.ready) return { path: '/home', query: { gate: setup.error_code ?? 'MODEL_NOT_CONFIGURED' } }
  }
})

export default router
