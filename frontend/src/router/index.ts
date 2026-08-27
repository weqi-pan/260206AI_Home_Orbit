import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean
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
      redirect: '/chat',
      children: [
        { path: 'chat', name: 'chat', component: () => import('../views/Chat.vue') },
        { path: 'tickets', name: 'tickets', component: () => import('../views/MyTickets.vue') },
        { path: 'devices', name: 'devices', component: () => import('../views/DeviceList.vue') },
        { path: 'devices/compare', name: 'devices-compare', component: () => import('../views/DeviceCompare.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isLoggedIn) return { path: '/login' }
  if (to.path === '/login' && auth.isLoggedIn) return { path: '/chat' }
  if (auth.isLoggedIn && auth.mustChangePassword && to.path !== '/change-password') return { path: '/change-password' }
})

export default router
