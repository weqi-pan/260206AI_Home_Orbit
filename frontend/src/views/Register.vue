<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import type { FormInstance, FormRules } from 'element-plus/es/components/form/index.mjs'
import { Iphone, Location, Lock, User } from '@element-plus/icons-vue'
import { register } from '../api/auth'
import AuthShell from '../components/AuthShell.vue'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: '', password: '', confirm: '', city: '上海', phone: '' })

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 64, message: '长度需为 3-64 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, max: 72, message: '长度需为 8-72 个字符', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule: unknown, value: string, callback: (error?: Error) => void) => {
        if (value !== form.password) callback(new Error('两次输入的密码不一致'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
}

async function handleSubmit() {
  const valid = await formRef.value?.validate().then(() => true).catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    const pair = await register({
      username: form.username.trim(),
      password: form.password,
      city: form.city.trim() || undefined,
      phone: form.phone.trim() || undefined,
    })
    auth.setSession(pair.access_token, pair.refresh_token, pair.account)
    ElMessage.success('注册成功')
    await router.push(pair.account.must_change_password ? '/change-password' : '/chat')
  } catch {
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AuthShell>
    <h2 class="auth-heading">注册账号</h2>
    <p class="auth-hint">注册即创建你的默认家庭空间，之后可邀请家人加入。</p>
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @keyup.enter="handleSubmit()">
      <el-form-item label="用户名" prop="username">
        <el-input v-model="form.username" :prefix-icon="User" placeholder="3-64 个字符" />
      </el-form-item>
      <el-form-item label="密码" prop="password">
        <el-input v-model="form.password" type="password" show-password :prefix-icon="Lock" placeholder="至少 8 位" />
      </el-form-item>
      <el-form-item label="确认密码" prop="confirm">
        <el-input v-model="form.confirm" type="password" show-password :prefix-icon="Lock" placeholder="再次输入密码" />
      </el-form-item>
      <el-form-item label="所在城市">
        <el-input v-model="form.city" :prefix-icon="Location" placeholder="默认：上海" />
      </el-form-item>
      <el-form-item label="手机号（选填）">
        <el-input v-model="form.phone" :prefix-icon="Iphone" placeholder="选填" />
      </el-form-item>
      <el-button class="auth-submit" type="primary" :loading="loading" @click="handleSubmit()">注 册</el-button>
    </el-form>
    <div class="auth-links">
      <router-link to="/login">已有账号？返回登录</router-link>
    </div>
  </AuthShell>
</template>

<style scoped>
</style>
