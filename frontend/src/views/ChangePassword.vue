<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Key, Lock } from '@element-plus/icons-vue'
import { changePassword } from '../api/auth'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ currentPassword: '', newPassword: '', confirm: '' })

const rules: FormRules = {
  currentPassword: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  newPassword: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, max: 72, message: '长度需为 8-72 个字符', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_rule: unknown, value: string, callback: (error?: Error) => void) => {
        if (value !== form.newPassword) callback(new Error('两次输入的密码不一致'))
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
    await changePassword(form.currentPassword, form.newPassword)
    auth.setPasswordChanged()
    ElMessage.success('密码修改成功')
    await router.push('/chat')
  } catch {
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <el-card class="auth-card">
      <h2 class="auth-title">修改密码</h2>
      <p class="auth-hint">首次登录或管理员重置密码后，需要修改密码才能继续使用</p>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @keyup.enter="handleSubmit()">
        <el-form-item label="当前密码" prop="currentPassword">
          <el-input v-model="form.currentPassword" type="password" show-password :prefix-icon="Key" placeholder="请输入当前密码" />
        </el-form-item>
        <el-form-item label="新密码" prop="newPassword">
          <el-input v-model="form.newPassword" type="password" show-password :prefix-icon="Lock" placeholder="至少 8 位" />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm">
          <el-input v-model="form.confirm" type="password" show-password :prefix-icon="Lock" placeholder="再次输入新密码" />
        </el-form-item>
        <el-button class="auth-submit" type="primary" :loading="loading" @click="handleSubmit()">保存并继续</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.auth-page {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  background: linear-gradient(180deg, #fdf6ec 0%, #f5f7fa 100%);
}

.auth-card {
  width: 380px;
}

.auth-title {
  margin: 0 0 8px;
  text-align: center;
  color: #e6a23c;
}

.auth-hint {
  margin: 0 0 20px;
  font-size: 13px;
  color: #909399;
  text-align: center;
}

.auth-submit {
  width: 100%;
}
</style>
