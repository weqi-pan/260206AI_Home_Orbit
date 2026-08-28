<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { useModalFocus } from '../../composables/useModalFocus'

const router = useRouter()
const auth = useAuthStore()
const drawerOpen = ref(false)
const drawerTrigger = ref<HTMLButtonElement>()
const drawer = ref<HTMLElement>()
const { show: openDrawer, hide: closeDrawer, onKeydown: onDrawerKeydown } = useModalFocus(drawerOpen, drawer, drawerTrigger)
const groups = [
  { label: '初始化设置', to: '/admin/setup' }, { label: '运营概览', to: '/admin/dashboard' },
  { label: '用户与家庭', to: '/admin/users' }, { label: 'Agent / Skill 治理', to: '/admin/reviews' },
  { label: '模型与能力' },
  { label: '任务运营' }, { label: '知识与支持', to: '/admin/knowledge' }, { label: '审计与发布' },
]

async function goUser() { await closeDrawer(); await router.push('/home') }
async function logout() { auth.logout(); await router.push('/login') }
</script>

<template>
  <div class="admin-layout">
    <div class="admin-shell-background" :inert="drawerOpen || undefined" :aria-hidden="drawerOpen ? 'true' : undefined">
      <a class="skip-link" href="#admin-main">跳到主要内容</a>
      <button ref="drawerTrigger" class="admin-drawer-trigger" type="button" aria-label="打开管理员导航" @click="openDrawer">菜单</button>
      <main id="admin-main" tabindex="-1"><router-view /></main>
    </div>
    <div v-if="drawerOpen" class="admin-backdrop" @click="closeDrawer"></div>
    <aside ref="drawer" :class="['admin-aside', { 'drawer-open': drawerOpen }]" :role="drawerOpen ? 'dialog' : undefined" :aria-label="drawerOpen ? '管理员导航' : undefined" :aria-modal="drawerOpen ? 'true' : undefined" @keydown="onDrawerKeydown">
      <button v-if="drawerOpen" class="admin-drawer-close" type="button" aria-label="关闭管理员导航" @click="closeDrawer">关闭</button>
      <router-link to="/admin/setup" class="admin-brand" @click="closeDrawer"><span>HOME ORBIT</span><strong>控制台</strong></router-link>
      <nav aria-label="管理员导航"><template v-for="item in groups" :key="item.label"><router-link v-if="item.to" :to="item.to" @click="closeDrawer">{{ item.label }}</router-link><span v-else class="unavailable">{{ item.label }}<small>后续阶段</small></span></template></nav>
      <div class="admin-actions"><button @click="goUser">返回用户端</button><button @click="logout">退出 {{ auth.account?.username }}</button></div>
    </aside>
  </div>
</template>

<style scoped>
.admin-layout { display: grid; grid-template-columns: 250px 1fr; min-height: 100%; background: var(--orbit-mist); }.admin-shell-background { grid-column: 2; min-width: 0; }.admin-aside { display: flex; flex-direction: column; min-height: 100vh; padding: 28px 18px; color: white; background: var(--device-ink); }.admin-brand { display: flex; flex-direction: column; min-height: 54px; padding: 0 12px; color: white; text-decoration: none; }.admin-brand span { font: 9px var(--font-utility); letter-spacing: .22em; }.admin-brand strong { font: 22px var(--font-display); }.admin-aside nav { display: grid; gap: 3px; margin-top: 28px; }.admin-aside nav a, .admin-aside nav span { display: flex; align-items: center; justify-content: space-between; min-height: 46px; padding: 0 12px; border-radius: 9px; color: #d7d4e8; text-decoration: none; }.admin-aside nav a.router-link-active { color: var(--device-ink); background: var(--charge-coral); }.unavailable { opacity: .38; }.unavailable small { font: 9px var(--font-utility); }.admin-actions { display: grid; gap: 6px; margin-top: auto; }.admin-actions button { min-height: 44px; border: 1px solid #5b567d; border-radius: 9px; color: white; background: transparent; }main { min-width: 0; overflow: auto; }.admin-drawer-trigger, .admin-drawer-close, .admin-backdrop { display: none; }
@media (max-width: 760px) { .admin-layout { display: block; }.admin-shell-background { min-height: 100vh; }.admin-drawer-trigger { position: fixed; z-index: 42; top: 12px; left: 12px; display: inline-flex; align-items: center; min-height: 44px; padding: 0 14px; border: 1px solid #cbc8dc; border-radius: 10px; color: var(--device-ink); background: var(--surface-white); }.admin-aside { position: fixed; z-index: 50; top: 0; bottom: 0; left: 0; width: min(86vw, 320px); padding: 20px 18px; transform: translateX(-105%); transition: transform .18s ease; }.admin-aside.drawer-open { transform: translateX(0); }.admin-drawer-close { display: inline-flex; align-self: flex-end; align-items: center; min-height: 44px; border: 1px solid #5b567d; border-radius: 9px; color: white; background: transparent; }.admin-backdrop { position: fixed; z-index: 49; inset: 0; display: block; background: rgba(33,29,79,.42); }.admin-aside nav { display: grid; margin-top: 18px; overflow-y: auto; }.admin-actions { display: grid; } }
</style>
