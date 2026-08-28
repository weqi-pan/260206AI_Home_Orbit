<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchAccounts, fetchAdminHouseholds, resetAccountPassword, updateAccount, type AdminAccount, type AdminHousehold } from '../../api/admin'
import { adjustHouseholdQuota, fetchHouseholdQuota, type HouseholdQuotaView } from '../../api/ops'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime } from '../../utils/format'

const auth = useAuthStore()
const accounts = ref<AdminAccount[]>([])
const households = ref<AdminHousehold[]>([])
const loading = ref(false)
const passwordResult = ref('')
const passwordVisible = ref(false)
const quotaView = ref<HouseholdQuotaView | null>(null)
const quotaVisible = ref(false)
const quotaSaving = ref(false)
const quotaForm = reactive({ monthly_limit: 0, requests_per_minute: 10, agent_token_budget: 0 })

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
    ;[accounts.value, households.value] = await Promise.all([fetchAccounts(), fetchAdminHouseholds()])
  } catch {
  } finally {
    loading.value = false
  }
}

async function openQuota(household: AdminHousehold) {
  quotaView.value = await fetchHouseholdQuota(household.id)
  const quota = quotaView.value.quota
  quotaForm.monthly_limit = quota?.monthly_limit ?? 0
  quotaForm.requests_per_minute = quota?.requests_per_minute ?? 10
  quotaForm.agent_token_budget = quota?.agent_token_budget ?? 0
  quotaVisible.value = true
}

async function saveQuota() {
  if (!quotaView.value) return
  quotaSaving.value = true
  try {
    await adjustHouseholdQuota(quotaView.value.household_id, {
      monthly_limit: quotaForm.monthly_limit,
      requests_per_minute: quotaForm.requests_per_minute,
      agent_token_budget: quotaForm.agent_token_budget || null,
    })
    ElMessage.success('配额已调整（仅对新预留生效，已审计）')
    quotaView.value = await fetchHouseholdQuota(quotaView.value.household_id)
  } finally {
    quotaSaving.value = false
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

    <div class="page-toolbar household-toolbar">
      <h3 class="page-title">家庭与配额</h3>
      <div class="toolbar-spacer"></div>
      <el-button @click="load()">刷新</el-button>
    </div>
    <el-table v-loading="loading" :data="households" border>
      <el-table-column prop="name" label="家庭" min-width="140" />
      <el-table-column label="成员" min-width="220">
        <template #default="{ row }">
          <span v-for="member in (row as AdminHousehold).members" :key="member.account_id" class="member-chip">
            {{ member.username }}（{{ member.role }}）
          </span>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ formatDateTime((row as AdminHousehold).created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" data-testid="open-quota" @click="openQuota(row as AdminHousehold)">配额</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!households.length && !loading" description="暂无家庭" />

    <el-dialog v-model="quotaVisible" title="家庭配额与用量" width="640px">
      <div v-if="quotaView">
        <dl class="quota-facts">
          <div><dt>当月用量（{{ quotaView.usage.month }}）</dt>
            <dd>{{ quotaView.usage.tokens }} 令牌 / {{ quotaView.usage.requests }} 次请求</dd></div>
          <div><dt>家庭预算（agent budget）</dt>
            <dd>{{ quotaView.quota?.agent_token_budget ?? '未设置' }}</dd></div>
        </dl>
        <el-form label-width="140px" class="quota-form">
          <el-form-item label="月度令牌上限">
            <el-input-number v-model="quotaForm.monthly_limit" :min="0" data-testid="quota-monthly" />
          </el-form-item>
          <el-form-item label="每分钟请求数">
            <el-input-number v-model="quotaForm.requests_per_minute" :min="1" data-testid="quota-rpm" />
          </el-form-item>
          <el-form-item label="单请求令牌预算">
            <el-input-number v-model="quotaForm.agent_token_budget" :min="0" data-testid="quota-budget" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" data-testid="save-quota" :loading="quotaSaving" @click="saveQuota">保存调整（审计）</el-button>
          </el-form-item>
        </el-form>
        <h4 class="quota-section">近 14 天趋势</h4>
        <table class="quota-table">
          <thead><tr><th>日期</th><th>请求</th><th>令牌</th></tr></thead>
          <tbody>
            <tr v-for="day in quotaView.trend" :key="day.day"><td>{{ day.day }}</td><td>{{ day.requests }}</td><td>{{ day.tokens }}</td></tr>
            <tr v-if="!quotaView.trend.length"><td colspan="3" class="dim">暂无用量</td></tr>
          </tbody>
        </table>
        <h4 class="quota-section">超限事故史（稳定码）</h4>
        <table class="quota-table">
          <thead><tr><th>时间</th><th>码</th><th>档案</th></tr></thead>
          <tbody>
            <tr v-for="incident in quotaView.incidents" :key="incident.id">
              <td>{{ formatDateTime(incident.created_at) }}</td>
              <td>{{ incident.code }}</td>
              <td>{{ incident.model_name }}</td>
            </tr>
            <tr v-if="!quotaView.incidents.length"><td colspan="3" class="dim">无事故</td></tr>
          </tbody>
        </table>
      </div>
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

.household-toolbar {
  margin-top: 22px;
}

.member-chip {
  display: inline-block;
  margin: 0 6px 4px 0;
  padding: 1px 8px;
  border-radius: 8px;
  background: #f4f2fb;
  font-size: 12px;
}

.quota-facts {
  display: grid;
  gap: 6px;
  margin: 0 0 12px;
}

.quota-facts > div {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
}

.quota-facts dt {
  opacity: .66;
}

.quota-facts dd {
  margin: 0;
  font-weight: 600;
}

.quota-form {
  max-width: 420px;
}

.quota-section {
  margin: 14px 0 6px;
  font-size: 13px;
}

.quota-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.quota-table th {
  text-align: left;
  padding: 4px 6px;
  border-bottom: 1px solid #eee;
  opacity: .6;
}

.quota-table td {
  padding: 5px 6px;
  border-bottom: 1px solid #f3f2f8;
}
</style>
