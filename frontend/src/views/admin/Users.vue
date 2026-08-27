<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchAccounts, resetAccountPassword, updateAccount, type AdminAccount } from '../../api/admin'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime } from '../../utils/format'

const auth = useAuthStore()
const accounts = ref<AdminAccount[]>([])
const loading = ref(false)
const passwordResult = ref('')
const passwordVisible = ref(false)

function isSelf(row: AdminAccount) {
  return row.id === auth.account?.id
}

const keyword = ref('')
const filtered = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  if (!k) return accounts.value
  return accounts.value.filter((item) => item.username.toLowerCase().includes(k))
})

async function load() {
  loading.value = true
  try {
    accounts.value = await fetchAccounts()
  } catch {
  } finally {
    loading.value = false
  }
}

function generatePassword(): string {
  const chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
  let result = 'Kf@'
  for (let i = 0; i < 10; i++) result += chars[Math.floor(Math.random() * chars.length)]
  return result
}

async function toggleStatus(row: AdminAccount) {
  const next = row.status === 'active' ? 'disabled' : 'active'
  try {
    const updated = await updateAccount(row.id, { status: next })
    Object.assign(row, updated)
    ElMessage.success(next === 'disabled' ? '账号已禁用' : '账号已启用')
  } catch {
  }
}

async function toggleRole(row: AdminAccount) {
  const next = row.role === 'admin' ? 'user' : 'admin'
  try {
    const updated = await updateAccount(row.id, { role: next })
    Object.assign(row, updated)
    ElMessage.success(next === 'admin' ? '已设为管理员' : '已设为普通用户')
  } catch {
  }
}

async function handleReset(row: AdminAccount) {
  const password = generatePassword()
  try {
    await resetAccountPassword(row.id, password)
    passwordResult.value = password
    passwordVisible.value = true
    row.must_change_password = true
  } catch {
  }
}

async function copyPassword() {
  try {
    await navigator.clipboard.writeText(passwordResult.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动复制')
  }
}

onMounted(load)
</script>

<template>
  <div class="users-page">
    <div class="page-toolbar">
      <h3 class="page-title">账号管理</h3>
      <div class="toolbar-spacer"></div>
      <el-input v-model="keyword" placeholder="按用户名搜索" clearable style="width: 220px" />
      <el-button @click="load()">刷新</el-button>
    </div>
    <el-table v-loading="loading" :data="filtered" border>
      <el-table-column prop="username" label="用户名" min-width="140" />
      <el-table-column label="角色" width="110" align="center">
        <template #default="{ row }">
          <el-tag :type="(row as AdminAccount).role === 'admin' ? 'danger' : 'info'">
            {{ (row as AdminAccount).role === 'admin' ? '管理员' : '普通用户' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="(row as AdminAccount).status === 'active' ? 'success' : 'info'">
            {{ (row as AdminAccount).status === 'active' ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="需改密" width="90" align="center">
        <template #default="{ row }">
          <el-tag v-if="(row as AdminAccount).must_change_password" type="warning" size="small">是</el-tag>
          <span v-else class="dim">否</span>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ formatDateTime((row as AdminAccount).created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :type="(row as AdminAccount).status === 'active' ? 'danger' : 'success'" :disabled="isSelf(row as AdminAccount)"
            @click="toggleStatus(row as AdminAccount)">
            {{ (row as AdminAccount).status === 'active' ? '禁用' : '启用' }}
          </el-button>
          <el-button size="small" :type="(row as AdminAccount).role === 'admin' ? 'info' : 'warning'" :disabled="isSelf(row as AdminAccount)"
            @click="toggleRole(row as AdminAccount)">
            {{ (row as AdminAccount).role === 'admin' ? '降为用户' : '升为管理员' }}
          </el-button>
          <el-button size="small" type="primary" :disabled="isSelf(row as AdminAccount)" @click="handleReset(row as AdminAccount)">
            重置密码
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!filtered.length && !loading" description="暂无账号" />

    <el-dialog v-model="passwordVisible" title="重置密码成功" width="440px">
      <p>新密码（一次性，仅此一次展示，请立即复制并告知用户）：</p>
      <el-input :model-value="passwordResult" readonly class="password-box">
        <template #append>
          <el-button @click="copyPassword">复制</el-button>
        </template>
      </el-input>
      <p class="dim tip">用户首次登录后将被要求修改密码。</p>
    </el-dialog>
  </div>
</template>

<style scoped>
.users-page {
  background: #fff;
  border-radius: 6px;
  padding: 16px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}

.page-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.page-title {
  margin: 0;
  font-size: 16px;
}

.toolbar-spacer {
  flex: 1;
}

.dim {
  color: #c0c4cc;
}

.password-box {
  margin: 8px 0;
}

.tip {
  font-size: 12px;
}
</style>
