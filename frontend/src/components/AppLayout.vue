<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()

async function handleLogout() {
  auth.logout()
  await router.push('/login')
}
</script>

<template>
  <div class="app-layout">
    <header class="app-topbar">
      <span class="brand">智能客服</span>
      <nav class="nav">
        <router-link to="/chat">聊天</router-link>
        <router-link to="/devices">设备</router-link>
        <router-link to="/tickets">我的工单</router-link>
      </nav>
      <div class="user-area">
        <span class="username">{{ auth.account?.username }}</span>
        <el-button size="small" text type="danger" @click="handleLogout()">退出</el-button>
      </div>
    </header>
    <main class="app-content">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.app-topbar {
  display: flex;
  align-items: center;
  gap: 32px;
  height: 52px;
  padding: 0 24px;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}

.brand {
  font-size: 17px;
  font-weight: 600;
  color: #409eff;
}

.nav {
  display: flex;
  gap: 8px;
  flex: 1;
}

.nav a {
  padding: 4px 12px;
  border-radius: 4px;
  color: #606266;
  text-decoration: none;
}

.nav a:hover {
  color: #409eff;
}

.nav a.router-link-active {
  color: #409eff;
  background: rgba(64, 158, 255, 0.1);
}

.user-area {
  display: flex;
  align-items: center;
  gap: 8px;
}

.username {
  color: #606266;
}

.app-content {
  flex: 1;
  min-height: 0;
  padding: 16px;
}
</style>
