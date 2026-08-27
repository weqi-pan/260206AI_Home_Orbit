<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
const router = useRouter(); const auth = useAuthStore()
const groups = [
  { label: '初始化设置', to: '/admin/setup' }, { label: '运营概览', to: '/admin/dashboard' },
  { label: '用户与家庭', to: '/admin/users' }, { label: 'Agent / Skill 治理' }, { label: '模型与能力' },
  { label: '任务运营' }, { label: '知识与支持', to: '/admin/knowledge' }, { label: '审计与发布' },
]
async function logout() { auth.logout(); await router.push('/login') }
</script>

<template>
  <div class="admin-layout">
    <a class="skip-link" href="#admin-main">跳到主要内容</a>
    <aside class="admin-aside">
      <router-link to="/admin/setup" class="admin-brand"><span>HOME ORBIT</span><strong>控制台</strong></router-link>
      <nav aria-label="管理员导航"><template v-for="item in groups" :key="item.label"><router-link v-if="item.to" :to="item.to">{{ item.label }}</router-link><span v-else class="unavailable">{{ item.label }}<small>后续阶段</small></span></template></nav>
      <div class="admin-actions"><button @click="router.push('/home')">返回用户端</button><button @click="logout">退出 {{ auth.account?.username }}</button></div>
    </aside>
    <main id="admin-main" tabindex="-1"><router-view /></main>
  </div>
</template>

<style scoped>
.admin-layout { display: grid; grid-template-columns: 250px 1fr; min-height: 100%; background: var(--orbit-mist); }.admin-aside { display: flex; flex-direction: column; min-height: 100vh; padding: 28px 18px; color: white; background: var(--device-ink); }.admin-brand { display: flex; flex-direction: column; min-height: 54px; padding: 0 12px; color: white; text-decoration: none; }.admin-brand span { font: 9px var(--font-utility); letter-spacing: .22em; }.admin-brand strong { font: 22px var(--font-display); }.admin-aside nav { display: grid; gap: 3px; margin-top: 28px; }.admin-aside nav a, .admin-aside nav span { display: flex; align-items: center; justify-content: space-between; min-height: 46px; padding: 0 12px; border-radius: 9px; color: #d7d4e8; text-decoration: none; }.admin-aside nav a.router-link-active { color: var(--device-ink); background: var(--charge-coral); }.unavailable { opacity: .38; }.unavailable small { font: 9px var(--font-utility); }.admin-actions { display: grid; gap: 6px; margin-top: auto; }.admin-actions button { min-height: 44px; border: 1px solid #5b567d; border-radius: 9px; color: white; background: transparent; }main { min-width: 0; overflow: auto; }
@media (max-width: 760px) { .admin-layout { display: block; }.admin-aside { min-height: auto; padding: 14px 16px; }.admin-aside nav { display: flex; margin-top: 8px; overflow-x: auto; }.admin-aside nav a, .admin-aside nav span { flex: 0 0 auto; }.admin-actions { display: none; } }
</style>
