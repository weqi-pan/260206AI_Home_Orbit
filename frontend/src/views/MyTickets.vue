<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import type { FormInstance, FormRules } from 'element-plus/es/components/form/index.mjs'
import { Plus } from '@element-plus/icons-vue'
import { createTicket, myTickets, type TicketInfo } from '../api/tickets'
import { formatDateTime, ticketPriorityTag, ticketStatusTag } from '../utils/format'
import { useRequest } from '../composables/useRequest'
import ErrorState from '../components/ErrorState.vue'

const route = useRoute()
const router = useRouter()

const detailVisible = ref(false)
const current = ref<TicketInfo | null>(null)
const createVisible = ref(false)
const creating = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({ title: '', content: '', priority: 'normal', session_id: '', emotion: '' })

const rules: FormRules = {
  title: [
    { required: true, message: '请输入标题', trigger: 'blur' },
    { max: 200, message: '标题不超过 200 字', trigger: 'blur' },
  ],
  content: [{ required: true, message: '请描述问题', trigger: 'blur' }],
}

// 原来这里的空 catch 会让接口失败静默变成「暂无工单」
const { data: tickets, loading, error, run: loadTickets } = useRequest(myTickets, { initial: [] as TicketInfo[] })

function openDetail(row: TicketInfo) {
  current.value = row
  detailVisible.value = true
}

async function handleCreate() {
  const valid = await formRef.value
    ?.validate()
    .then(() => true)
    .catch(() => false)
  if (!valid) return
  creating.value = true
  try {
    await createTicket({
      title: form.title.trim(),
      content: form.content.trim(),
      priority: form.priority,
      session_id: form.session_id || null,
      emotion: form.emotion || null,
    })
    ElMessage.success('工单已提交')
    createVisible.value = false
    form.title = ''
    form.content = ''
    form.priority = 'normal'
    await router.replace({ path: '/tickets' })
    await loadTickets()
  } catch {
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  const sessionId = route.query.session_id
  const emotion = route.query.emotion
  if (typeof sessionId === 'string' && sessionId) {
    form.session_id = sessionId
    if (typeof emotion === 'string' && emotion) form.emotion = emotion
    createVisible.value = true
  }
  void loadTickets()
})
</script>

<template>
  <div class="tickets-page">
    <div class="page-toolbar">
      <h3 class="page-title">我的工单</h3>
      <div class="toolbar-spacer"></div>
      <el-button type="primary" :icon="Plus" @click="createVisible = true">新建工单</el-button>
    </div>
    <ErrorState v-if="error" :message="error" :retrying="loading" @retry="loadTickets" />
    <el-table v-loading="loading" :data="tickets" border>
      <el-table-column prop="id" label="编号" width="80" />
      <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
      <el-table-column label="优先级" width="90" align="center">
        <template #default="{ row }">
          <el-tag :type="ticketPriorityTag(row.priority).type">{{ ticketPriorityTag(row.priority).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="ticketStatusTag(row.status).type">{{ ticketStatusTag(row.status).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="客服回复" min-width="160">
        <template #default="{ row }">
          <span :class="row.admin_reply ? '' : 'dim'">{{ row.admin_reply || '暂无回复' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="openDetail(row as TicketInfo)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!tickets.length && !loading && !error" description="暂无工单" />

    <el-dialog v-model="detailVisible" title="工单详情" width="560px">
      <el-descriptions v-if="current" :column="2" border>
        <el-descriptions-item label="编号">{{ current.id }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="ticketStatusTag(current.status).type">{{ ticketStatusTag(current.status).label }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="优先级">
          <el-tag :type="ticketPriorityTag(current.priority).type">{{
            ticketPriorityTag(current.priority).label
          }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="情绪">{{ current.emotion || '-' }}</el-descriptions-item>
        <el-descriptions-item label="标题" :span="2">{{ current.title }}</el-descriptions-item>
        <el-descriptions-item label="问题摘要" :span="2">{{ current.summary || '-' }}</el-descriptions-item>
        <el-descriptions-item label="客服回复" :span="2">{{ current.admin_reply || '暂无回复' }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="解决时间">{{ formatDateTime(current.resolved_at) }}</el-descriptions-item>
        <el-descriptions-item v-if="current.session_id" label="关联会话" :span="2">
          {{ current.session_id }}
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <el-dialog v-model="createVisible" title="新建工单" width="520px">
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <el-form-item v-if="form.session_id" label="关联会话">
          <el-tag size="small" type="info">{{ form.session_id }}</el-tag>
        </el-form-item>
        <el-form-item label="标题" prop="title">
          <el-input v-model="form.title" maxlength="200" show-word-limit placeholder="一句话概括问题" />
        </el-form-item>
        <el-form-item label="问题描述" prop="content">
          <el-input v-model="form.content" type="textarea" :rows="5" placeholder="详细描述你遇到的问题" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-radio-group v-model="form.priority">
            <el-radio-button value="low">低</el-radio-button>
            <el-radio-button value="normal">普通</el-radio-button>
            <el-radio-button value="high">高</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate()">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.tickets-page {
  background: #fff;
  border-radius: 6px;
  padding: 16px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}
</style>
