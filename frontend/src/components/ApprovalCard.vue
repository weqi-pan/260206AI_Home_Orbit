<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { RISK_LABELS, type ToolRiskLevel } from '../market/types'
import type { ChatApproval } from '../types'

const props = defineProps<{ approval: ChatApproval }>()
const emit = defineEmits<{ approve: []; reject: []; dismiss: [] }>()
// 倒计时每秒刷新，但**不进 aria-live**：读屏会被数字刷屏，真正要播报的是
// 「待确认 / 已批准 / 已拒绝」这种状态变化（由 Chat.vue 的 streamAnnouncement 负责）
const now = ref(Date.now())
let timer: number | undefined
function startTimer() {
  if (timer) return
  timer = window.setInterval(() => {
    now.value = Date.now()
  }, 1000)
}
function stopTimer() {
  if (timer) {
    window.clearInterval(timer)
    timer = undefined
  }
}
watch(
  () => props.approval.expiresAt,
  (value) => {
    if (value) startTimer()
    else stopTimer()
  },
  { immediate: true },
)
onUnmounted(stopTimer)

const remaining = computed(() => {
  const expiresAt = props.approval.expiresAt
  if (!expiresAt) return null
  return Math.max(0, Math.floor((expiresAt - now.value) / 1000))
})

const countdown = computed(() => {
  const seconds = remaining.value
  if (seconds === null) return ''
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`
})

const risk = computed(() => props.approval.info.risk_level as ToolRiskLevel | null)
const riskLabel = computed(() => (risk.value ? RISK_LABELS[risk.value] : '高风险操作'))
const toolName = computed(() => props.approval.info.tool_display_name || props.approval.info.tool_key || '高风险工具')

// 参数写入时已被后端 redact，这里只做展示；空对象不占版面
const argumentRows = computed(() =>
  Object.entries(props.approval.info.arguments ?? {}).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  })),
)

const busy = computed(() => props.approval.state === 'busy')
const decided = computed(() => ['approved', 'rejected'].includes(props.approval.state))
const statusText = computed(() => {
  switch (props.approval.state) {
    case 'busy':
      return '处理中…'
    case 'approved':
      return remaining.value === 0 ? '授权已过期' : `已授权，剩余 ${countdown.value}`
    case 'rejected':
      return '已拒绝'
    case 'failed':
      return '操作失败，请重试'
    default:
      return '等待你的确认'
  }
})
</script>

<template>
  <section class="approval-card" role="region" :aria-label="`高风险操作审批：${toolName}`">
    <header class="approval-head">
      <span class="approval-tool">{{ toolName }}</span>
      <span class="approval-risk" :data-risk="risk ?? 'high'">{{ riskLabel }}</span>
    </header>
    <p v-if="approval.info.arguments && argumentRows.length" class="approval-args">
      <span v-for="row in argumentRows" :key="row.key" class="approval-arg">
        <span class="approval-arg-key">{{ row.key }}</span>
        <span class="approval-arg-value">{{ row.value }}</span>
      </span>
    </p>
    <p class="approval-status" data-testid="approval-status">{{ statusText }}</p>
    <div class="approval-actions">
      <template v-if="!decided">
        <el-button type="primary" size="small" :loading="busy" :disabled="busy" @click="emit('approve')">
          批准这一次
        </el-button>
        <el-button size="small" :disabled="busy" @click="emit('reject')">拒绝</el-button>
        <el-button size="small" text :disabled="busy" @click="emit('dismiss')">忽略</el-button>
      </template>
      <el-button v-else size="small" text @click="emit('dismiss')">收起</el-button>
    </div>
  </section>
</template>

<style scoped>
.approval-card {
  margin-top: 8px;
  padding: 12px 14px;
  border: 1px solid #f0d9cf;
  border-radius: 10px;
  background: #fdf6f3;
}
.approval-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.approval-tool {
  font-weight: 700;
  color: var(--device-ink);
}
.approval-risk {
  padding: 2px 10px;
  border-radius: 999px;
  font: 600 11px var(--font-utility);
}
.approval-risk[data-risk='low'] {
  color: var(--risk-low-fg);
  background: var(--risk-low-bg);
}
.approval-risk[data-risk='reversible'] {
  color: var(--risk-reversible-fg);
  background: var(--risk-reversible-bg);
}
.approval-risk[data-risk='high'] {
  color: var(--risk-high-fg);
  background: var(--risk-high-bg);
}
.approval-args {
  display: grid;
  gap: 4px;
  margin: 10px 0 0;
  font-size: 13px;
}
.approval-arg {
  display: flex;
  gap: 8px;
}
.approval-arg-key {
  flex-shrink: 0;
  min-width: 72px;
  color: var(--ink-quiet);
}
.approval-arg-value {
  color: var(--ink-body);
  word-break: break-word;
}
.approval-status {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--ink-quiet);
}
.approval-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
/* 触控目标 ≥44px：聊天页在手机上用得最多 */
.approval-actions :deep(.el-button) {
  min-height: 44px;
  margin-left: 0;
}
</style>
