<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Plus, Promotion, Tickets } from '@element-plus/icons-vue'
import {
  createSession,
  deleteSession,
  listMessages,
  listSessions,
  sendFeedback,
  streamChat,
  type ChatMessageInfo,
  type ChatSessionInfo,
  type ToolEventData,
} from '../api/chat'
import MessageItem from '../components/MessageItem.vue'
import type { ChatUiMessage } from '../types'

const router = useRouter()

const sessions = ref<ChatSessionInfo[]>([])
const activeId = ref('')
const messages = ref<ChatUiMessage[]>([])
const followups = ref<string[]>([])
const toolEvents = ref<ToolEventData[]>([])
const input = ref('')
const sending = ref(false)
const listLoading = ref(false)
const scrollRef = ref<HTMLElement | undefined>()
let abortController: AbortController | null = null

const activeSession = computed(() => sessions.value.find((item) => item.id === activeId.value) ?? null)

function toUiMessage(item: ChatMessageInfo): ChatUiMessage {
  return {
    id: item.id,
    role: item.role,
    content: item.content,
    sentiment: item.sentiment,
    sentimentScore: item.sentiment_score,
    feedback: item.feedback,
  }
}

function toolStatusLabel(status: string): string {
  if (status === 'start') return '运行中'
  if (status === 'success') return '完成'
  if (status === 'error') return '失败'
  return status
}

async function scrollBottom() {
  await nextTick()
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function abortStreaming() {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  sending.value = false
}

async function refreshSessions() {
  sessions.value = await listSessions()
}

async function selectSession(id: string) {
  if (id === activeId.value) return
  abortStreaming()
  activeId.value = id
  followups.value = []
  toolEvents.value = []
  messages.value = (await listMessages(id)).map(toUiMessage)
  await scrollBottom()
}

async function handleNewSession() {
  abortStreaming()
  try {
    const created = await createSession()
    sessions.value.unshift(created)
    await selectSession(created.id)
  } catch {
  }
}

async function handleDeleteSession(id: string) {
  try {
    await ElMessageBox.confirm('删除后该会话的消息将不可恢复，确定删除？', '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  if (id === activeId.value) abortStreaming()
  try {
    await deleteSession(id)
    ElMessage.success('会话已删除')
    sessions.value = sessions.value.filter((item) => item.id !== id)
    if (id === activeId.value) {
      if (sessions.value.length) await selectSession(sessions.value[0].id)
      else await handleNewSession()
    }
  } catch {
  }
}

async function loadSessions() {
  listLoading.value = true
  try {
    sessions.value = await listSessions()
    if (sessions.value.length) await selectSession(sessions.value[0].id)
    else await handleNewSession()
  } catch {
  } finally {
    listLoading.value = false
  }
}

function onEnterKey(evt: Event) {
  const event = evt as KeyboardEvent
  if (event.isComposing) return
  void sendMessage()
}

async function sendMessage(preset?: string) {
  const content = (preset ?? input.value).trim()
  if (!content || sending.value || !activeId.value) return
  if (preset === undefined) input.value = ''
  abortController = new AbortController()
  const signal = abortController.signal
  sending.value = true
  followups.value = []
  toolEvents.value = []
  const userMessage = reactive<ChatUiMessage>({
    id: null,
    role: 'user',
    content,
    sentiment: null,
    sentimentScore: null,
    feedback: null,
  })
  const assistantMessage = reactive<ChatUiMessage>({
    id: null,
    role: 'assistant',
    content: '',
    sentiment: null,
    sentimentScore: null,
    feedback: null,
    streaming: true,
  })
  messages.value.push(userMessage, assistantMessage)
  await scrollBottom()
  try {
    await streamChat(
      activeId.value,
      content,
      {
        onToken: (token) => {
          assistantMessage.content += token
          void scrollBottom()
        },
        onTool: (data) => {
          toolEvents.value.push(data)
        },
        onSentiment: (data) => {
          userMessage.sentiment = data.label
          userMessage.sentimentScore = data.score
        },
        onFollowup: (questions) => {
          followups.value = questions.slice(0, 3)
        },
        onDone: (data) => {
          assistantMessage.id = data.message_id
          refreshSessions().catch(() => undefined)
        },
        onError: (message) => {
          assistantMessage.failed = true
          ElMessage.error(message)
        },
      },
      signal,
    )
  } catch (error) {
    if ((error as Error).name !== 'AbortError') {
      assistantMessage.failed = true
      ElMessage.error(error instanceof Error ? error.message : '发送失败')
    }
  } finally {
    assistantMessage.streaming = false
    abortController = null
    sending.value = false
    await scrollBottom()
  }
}

async function handleFeedback(message: ChatUiMessage, value: 'like' | 'dislike') {
  if (!message.id || message.feedback === value) return
  const previous = message.feedback
  message.feedback = value
  try {
    await sendFeedback(message.id, value)
  } catch {
    message.feedback = previous
  }
}

function goCreateTicket() {
  const emotion = [...messages.value].reverse().find((item) => item.role === 'user' && item.sentiment)?.sentiment
  void router.push({ path: '/tickets', query: { session_id: activeId.value, ...(emotion ? { emotion } : {}) } })
}

onMounted(() => {
  void loadSessions()
})
</script>

<template>
  <div class="chat-page">
    <aside v-loading="listLoading" class="sessions-panel">
      <div class="sessions-head">
        <span class="sessions-title">会话列表</span>
        <el-button type="primary" size="small" :icon="Plus" @click="handleNewSession()">新建</el-button>
      </div>
      <div class="session-list">
        <div
          v-for="item in sessions"
          :key="item.id"
          class="session-item"
          :class="{ active: item.id === activeId }"
          @click="selectSession(item.id)"
        >
          <span class="session-name" :title="item.title">{{ item.title }}</span>
          <el-icon class="session-delete" title="删除会话" @click.stop="handleDeleteSession(item.id)"><Delete /></el-icon>
        </div>
        <el-empty v-if="!sessions.length && !listLoading" description="暂无会话" :image-size="60" />
      </div>
    </aside>
    <section class="chat-main">
      <header class="chat-header">
        <span class="chat-session-title" :title="activeSession?.title">{{ activeSession?.title ?? '新对话' }}</span>
        <el-button size="small" :icon="Tickets" @click="goCreateTicket()">提交工单</el-button>
      </header>
      <div ref="scrollRef" class="message-list">
        <MessageItem
          v-for="(message, index) in messages"
          :key="index"
          :message="message"
          @feedback="(value) => handleFeedback(message, value)"
        />
        <div v-if="toolEvents.length" class="tool-events">
          <el-tag
            v-for="(event, index) in toolEvents.slice(-4)"
            :key="`${event.name}-${index}`"
            size="small"
            :type="event.status === 'error' ? 'danger' : event.status === 'start' ? 'warning' : 'info'"
          >
            {{ event.name }} · {{ toolStatusLabel(event.status) }}
          </el-tag>
        </div>
        <div v-if="followups.length && !sending" class="followups">
          <span class="followups-label">猜你想问：</span>
          <el-button v-for="question in followups" :key="question" size="small" round @click="sendMessage(question)">
            {{ question }}
          </el-button>
        </div>
        <el-empty v-if="!messages.length && !listLoading" description="开始你的第一句提问吧" :image-size="80" />
      </div>
      <footer class="composer">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          resize="none"
          :disabled="sending"
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          @keydown.enter.exact.prevent="onEnterKey"
        />
        <el-button class="send-button" type="primary" :icon="Promotion" :loading="sending" @click="sendMessage()">
          发送
        </el-button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  height: 100%;
  background: #fff;
  border-radius: 6px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}

.sessions-panel {
  display: flex;
  flex-direction: column;
  width: 240px;
  flex-shrink: 0;
  border-right: 1px solid #ebeef5;
}

.sessions-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  border-bottom: 1px solid #ebeef5;
}

.sessions-title {
  font-weight: 600;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 10px;
  margin-bottom: 4px;
  border-radius: 6px;
  cursor: pointer;
}

.session-item:hover {
  background: #f5f7fa;
}

.session-item.active {
  background: #ecf5ff;
}

.session-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.session-delete {
  color: #c0c4cc;
  flex-shrink: 0;
}

.session-delete:hover {
  color: #f56c6c;
}

.chat-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid #ebeef5;
}

.chat-session-title {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 18px 16px;
  background: #f5f7fa;
}

.tool-events {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: -6px;
  margin-bottom: 14px;
}

.followups {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}

.followups-label {
  font-size: 13px;
  color: #909399;
}

.composer {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  padding: 12px 16px;
  border-top: 1px solid #ebeef5;
}

.send-button {
  flex-shrink: 0;
  height: 54px;
}
</style>
