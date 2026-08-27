<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const primary = [
  { label: '首页', to: '/home' },
  { label: '发现', unavailable: true },
  { label: '创建', unavailable: true },
  { label: '我的助手', unavailable: true },
  { label: '家庭', to: '/household' },
]
async function handleLogout() { auth.logout(); await router.push('/login') }
</script>

<template>
  <div class="app-layout">
    <a class="skip-link" href="#main-content">跳到主要内容</a>
    <header class="app-topbar">
      <router-link to="/home" class="brand" aria-label="Home Orbit 首页"><span>HOME</span><strong>ORBIT</strong></router-link>
      <nav class="primary-nav" aria-label="主要导航">
        <template v-for="item in primary" :key="item.label">
          <router-link v-if="item.to" :to="item.to">{{ item.label }}</router-link>
          <span v-else class="unavailable" :title="`${item.label}将在后续阶段开放`">{{ item.label }}<small>稍后</small></span>
        </template>
      </nav>
      <div class="utility-nav">
        <router-link to="/devices">设备</router-link><router-link to="/tickets">支持</router-link>
        <router-link v-if="auth.account?.role === 'admin'" to="/admin/setup">管理</router-link>
        <button type="button" @click="handleLogout">退出</button>
      </div>
    </header>
    <main id="main-content" class="app-content" tabindex="-1"><router-view /></main>
    <nav class="bottom-nav" aria-label="移动端主要导航">
      <template v-for="item in primary" :key="item.label">
        <router-link v-if="item.to" :to="item.to">{{ item.label }}</router-link>
        <span v-else class="unavailable">{{ item.label }}</span>
      </template>
    </nav>
  </div>
</template>

<style scoped>
.app-layout { min-height: 100%; background: var(--orbit-mist); }.app-topbar { position: relative; z-index: 10; display: grid; grid-template-columns: 150px 1fr auto; align-items: center; min-height: 72px; padding: 0 28px; border-bottom: 1px solid rgba(75,66,151,.18); background: color-mix(in srgb, var(--orbit-mist) 92%, white); }
.brand { display: flex; flex-direction: column; color: var(--device-ink); text-decoration: none; line-height: .9; }.brand span { font: 9px var(--font-utility); letter-spacing: .26em; }.brand strong { font: 20px var(--font-display); }
.primary-nav { display: flex; justify-content: center; gap: clamp(4px, 2vw, 26px); }.primary-nav a, .primary-nav .unavailable, .utility-nav a, .utility-nav button { display: inline-flex; align-items: center; min-height: 44px; padding: 0 10px; border: 0; color: var(--device-ink); text-decoration: none; background: transparent; }.primary-nav a.router-link-active { color: var(--orbit-violet); box-shadow: inset 0 -3px var(--charge-coral); }.unavailable { cursor: not-allowed; opacity: .42; }.unavailable small { margin-left: 4px; font: 9px var(--font-utility); }
.utility-nav { display: flex; gap: 2px; align-items: center; font-size: 13px; }.utility-nav button { cursor: pointer; }.app-content { min-height: calc(100vh - 72px); }.bottom-nav { display: none; }
@media (max-width: 860px) { .app-topbar { grid-template-columns: 1fr auto; min-height: 62px; padding: 0 18px; }.primary-nav { display: none; }.utility-nav a:not(:last-of-type) { display: none; }.app-content { min-height: calc(100vh - 126px); }.bottom-nav { position: fixed; z-index: 20; right: 0; bottom: 0; left: 0; display: grid; grid-template-columns: repeat(5, 1fr); min-height: 64px; border-top: 1px solid #cbc8dc; background: var(--surface-white); }.bottom-nav a, .bottom-nav span { display: grid; min-height: 64px; place-items: center; color: var(--device-ink); font-size: 12px; text-decoration: none; }.bottom-nav a.router-link-active { color: var(--orbit-violet); box-shadow: inset 0 3px var(--charge-coral); } }
</style>
