<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { fetchAdminTickets, updateTicket, type AdminTicket } from '../../api/admin'
import { formatDateTime, sentimentTag, ticketPriorityTag, ticketStatusTag } from '../../utils/format'

const tickets = ref<AdminTicket[]>([])
const loading = ref(false)
const statusFilter = ref<'' | 'open' | 'processing' | 'resolved' | 'closed'>('')

const filtered = computed(() => (statusFilter.value ? tickets.value.filter((t) => t.status === statusFilter.value) : tickets.value))
const openCount = computed(() => tickets.value.filter((t) => t.status === 'open').length)
const processingCount = computed(() => tickets.value.filter((t) => t.status === 'processing').length)

const drawerVisible = ref(false)
const current = ref<AdminTicket | null>(null)
const replyText = ref('')
const saving = ref(false)

async function load() {
  loading.value = true
  try {
    tickets.value = await fetchAdminTickets()
  } catch {
  } finally {
    loading.value = false
  }
}

function openDetail(row: AdminTicket) {
  current.value = row
  replyText.value = row.admin_reply || ''
  drawerVisible.value = true
}

function patchCurrent(updated: AdminTicket) {
  const index = tickets.value.findIndex((t) => t.id === updated.id)
  if (index >= 0) tickets.value[index] = updated
  if (current.value?.id === updated.id) current.value = updated
}

async function saveReply() {
  if (!current.value) return
  if (!replyText.value.trim()) {
    ElMessage.warning('请输入回复内容')
    return
  }
  saving.value = true
  try {
    const updated = await updateTicket(current.value.household_id, current.value.id, { admin_reply: replyText.value.trim() })
    patchCurrent(updated)
    ElMessage.success('回复已保存')
  } catch {
  } finally {
    saving.value = false
  }
}

async function changeStatus(ticket: AdminTicket, status: AdminTicket['status']) {
  saving.value = true
  try {
    const updated = await updateTicket(ticket.household_id, ticket.id, { status })
    patchCurrent(updated)
    ElMessage.success(`工单已${ticketStatusTag(status).label}`)
  } catch {
  } finally {
    saving.value = false
  }
}

async function changePriority(ticket: AdminTicket, priority: AdminTicket['priority']) {
  try {
    const updated = await updateTicket(ticket.household_id, ticket.id, { priority })
    patchCurrent(updated)
    ElMessage.success('优先级已更新')
  } catch {
  }
}

onMounted(load)
</script>

<template>
  <div class="tickets-page">
    <div class="page-toolbar">
      <h3 class="page-title">工单处理</h3>
      <el-tag type="danger" size="large">待处理 {{ openCount }}</el-tag>
      <el-tag type="warning" size="large">处理中 {{ processingCount }}</el-tag>
      <div class="toolbar-spacer"></div>
      <el-radio-group v-model="statusFilter">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="open">待处理</el-radio-button>
        <el-radio-button value="processing">处理中</el-radio-button>
        <el-radio-button value="resolved">已解决</el-radio-button>
        <el-radio-button value="closed">已关闭</el-radio-button>
      </el-radio-group>
      <el-button @click="load()">刷新</el-button>
    </div>
    <el-table v-loading="loading" :data="filtered" border>
      <el-table-column prop="id" label="编号" width="80" />
      <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
      <el-table-column label="情绪" width="90" align="center">
        <template #default="{ row }">
          <el-tag v-if="(row as AdminTicket).emotion" size="small" :type="sentimentTag((row as AdminTicket).emotion).type">
            {{ sentimentTag((row as AdminTicket).emotion).label }}
          </el-tag>
          <span v-else class="dim">-</span>
        </template>
      </el-table-column>
      <el-table-column label="优先级" width="110" align="center">
        <template #default="{ row }">
          <el-select :model-value="(row as AdminTicket).priority" size="small" style="width: 84px"
            @change="(value: string) => changePriority(row as AdminTicket, value as AdminTicket['priority'])">
            <el-option label="低" value="low" />
            <el-option label="普通" value="normal" />
            <el-option label="高" value="high" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="ticketStatusTag((row as AdminTicket).status).type">{{ ticketStatusTag((row as AdminTicket).status).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="客服回复" min-width="160">
        <template #default="{ row }">
          <span :class="(row as AdminTicket).admin_reply ? '' : 'dim'">{{ (row as AdminTicket).admin_reply || '暂无回复' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ formatDateTime((row as AdminTicket).created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="openDetail(row as AdminTicket)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!filtered.length && !loading" description="暂无工单" />

    <el-drawer v-model="drawerVisible" title="工单详情" size="480px">
      <template v-if="current">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="编号">{{ current.id }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="ticketStatusTag(current.status).type">{{ ticketStatusTag(current.status).label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="优先级">
            <el-tag :type="ticketPriorityTag(current.priority).type">{{ ticketPriorityTag(current.priority).label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="情绪">
            <el-tag v-if="current.emotion" size="small" :type="sentimentTag(current.emotion).type">{{ sentimentTag(current.emotion).label }}</el-tag>
            <span v-else>-</span>
          </el-descriptions-item>
          <el-descriptions-item label="标题" :span="2">{{ current.title }}</el-descriptions-item>
          <el-descriptions-item label="问题摘要" :span="2">{{ current.summary || '-' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="解决时间">{{ formatDateTime(current.resolved_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="current.session_id" label="关联会话" :span="2">{{ current.session_id }}</el-descriptions-item>
        </el-descriptions>

        <div class="drawer-section">
          <h4>状态流转</h4>
          <div class="flow-buttons">
            <el-button size="small" :type="current.status === 'open' ? 'primary' : 'default'" :disabled="current.status === 'open'"
              @click="changeStatus(current, 'open')">待处理</el-button>
            <el-button size="small" :type="current.status === 'processing' ? 'warning' : 'default'" :disabled="current.status === 'processing'"
              @click="changeStatus(current, 'processing')">处理中</el-button>
            <el-button size="small" :type="current.status === 'resolved' ? 'success' : 'default'" :disabled="current.status === 'resolved'"
              @click="changeStatus(current, 'resolved')">已解决</el-button>
            <el-button size="small" :type="current.status === 'closed' ? 'info' : 'default'" :disabled="current.status === 'closed'"
              @click="changeStatus(current, 'closed')">已关闭</el-button>
          </div>
        </div>

        <div class="drawer-section">
          <h4>客服回复</h4>
          <el-input v-model="replyText" type="textarea" :rows="5" placeholder="输入回复内容" aria-label="客服回复内容" />
          <el-button class="reply-save" type="primary" :loading="saving" @click="saveReply()">保存回复</el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.tickets-page {
  background: #fff;
  border-radius: 6px;
  padding: 16px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}

/* 这三个页面比全局 .page-title（18px）小一档 */
.page-title { font-size: 16px; }

.drawer-section {
  margin-top: 20px;
}

.drawer-section h4 {
  margin: 0 0 10px;
  color: #303133;
}

.flow-buttons {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.reply-save {
  margin-top: 10px;
}
</style>
