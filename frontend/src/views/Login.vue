<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import type { FormInstance, FormRules } from 'element-plus/es/components/form/index.mjs'
import { Lock, User } from '@element-plus/icons-vue'
import { login } from '../api/auth'
import AuthShell from '../components/AuthShell.vue'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: '', password: '' })

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleSubmit() {
  const valid = await formRef.value?.validate().then(() => true).catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    const pair = await login(form.username.trim(), form.password)
    auth.setSession(pair.access_token, pair.refresh_token, pair.account)
    ElMessage.success('登录成功')
    await router.push(pair.account.must_change_password ? '/change-password' : '/chat')
  } catch {
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AuthShell>
    <h2 class="auth-heading">欢迎回家</h2>
    <p class="auth-hint">登录 Home Orbit，回到你的家庭轨道。</p>
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @keyup.enter="handleSubmit()">
      <el-form-item label="用户名" prop="username">
        <el-input v-model="form.username" :prefix-icon="User" placeholder="请输入用户名" />
      </el-form-item>
      <el-form-item label="密码" prop="password">
        <el-input v-model="form.password" type="password" show-password :prefix-icon="Lock" placeholder="请输入密码" />
      </el-form-item>
      <el-button class="auth-submit" type="primary" :loading="loading" @click="handleSubmit()">登 录</el-button>
    </el-form>
    <div class="auth-links">
      <router-link to="/register">没有账号？立即注册</router-link>
    </div>
  </AuthShell>
</template>

<style scoped>
</style>
