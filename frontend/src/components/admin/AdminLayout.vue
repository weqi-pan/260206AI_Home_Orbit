<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const activeMenu = computed(() => route.path)

async function handleLogout() {
  auth.logout()
  await router.push('/login')
}
</script>

<template>
  <el-container class="admin-layout">
    <el-aside width="210px" class="admin-aside">
      <div class="admin-brand">客服管理后台</div>
      <el-menu router :default-active="activeMenu" class="admin-menu" background-color="#001529" text-color="#a6adb4"
        active-text-color="#fff">
        <el-menu-item index="/admin/dashboard">仪表盘</el-menu-item>
        <el-menu-item index="/admin/users">账号管理</el-menu-item>
        <el-menu-item index="/admin/devices">设备与库存</el-menu-item>
        <el-menu-item index="/admin/knowledge">知识库</el-menu-item>
        <el-menu-item index="/admin/tickets">工单处理</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="admin-topbar">
        <span class="admin-title">智能客服管理系统</span>
        <div class="admin-user">
          <el-tag size="small" type="danger">管理员</el-tag>
          <span class="username">{{ auth.account?.username }}</span>
          <el-button size="small" text type="primary" @click="router.push('/chat')">返回用户端</el-button>
          <el-button size="small" text type="danger" @click="handleLogout()">退出</el-button>
        </div>
      </el-header>
      <el-main class="admin-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.admin-layout {
  height: 100%;
}

.admin-aside {
  background: #001529;
  overflow-x: hidden;
}

.admin-brand {
  height: 56px;
  line-height: 56px;
  text-align: center;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 1px;
}

.admin-menu {
  border-right: none;
}

.admin-menu :deep(.el-menu-item.is-active) {
  background: #409eff;
}

.admin-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}

.admin-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.admin-user {
  display: flex;
  align-items: center;
  gap: 10px;
}

.username {
  color: #606266;
}

.admin-main {
  background: #f0f2f5;
  overflow: auto;
}
</style>
