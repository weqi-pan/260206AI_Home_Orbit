<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { fetchCeleryTaskDetail, fetchCeleryTasks, type CeleryTaskDetail, type CeleryTaskItem } from '../../api/audit'
import { fetchTaskTypes, runAdminTask, type TaskTypeSpec } from '../../api/ops'
import { formatDateTime } from '../../utils/format'
import { useRequest } from '../../composables/useRequest'
import ErrorState from '../../components/ErrorState.vue'

const total = ref(0)
const page = ref(1)
const pageSize = 20
const statusFilter = ref('')
const typeFilter = ref('')
const errorFilter = ref('')
const types = ref<TaskTypeSpec[]>([])
const detail = ref<CeleryTaskDetail | null>(null)
const detailOpen = ref(false)

const trigger = reactive({ taskType: 'household.reminders.dispatch', householdId: '', payloadJson: '{}' })
const triggering = ref(false)

// 任务列表失败时不能继续显示上一页的旧数据当成本页结果，
// 因此这里把错误交给 ErrorState，列表区在 error 时整体让位。
const {
  data: taskPage,
  loading,
  error,
  run: load,
} = useRequest(
  async () => {
    const result = await fetchCeleryTasks({
      page: page.value,
      page_size: pageSize,
      status: statusFilter.value || undefined,
      task_type: typeFilter.value || undefined,
      error_code: errorFilter.value || undefined,
    })
    total.value = result.total
    return result.items
  },
  { initial: [] as CeleryTaskItem[] },
)

const items = computed(() => taskPage.value)

// 筛选变更与翻页共用；不用「page = 1; load()」这类多语句内联表达式，
// Prettier 会把模板里的内联语句拆成多行，Vue 编译器无法解析（会直接编译报错）
function resetAndLoad() {
  page.value = 1
  load()
}

function shiftPage(delta: number) {
  page.value += delta
  load()
}

async function openDetail(item: CeleryTaskItem) {
  detail.value = await fetchCeleryTaskDetail(item.id)
  detailOpen.value = true
}

async function submitTrigger() {
  let payload: Record<string, unknown>
  try {
    payload = JSON.parse(trigger.payloadJson || '{}')
  } catch {
    ElMessage.error('负载不是合法 JSON')
    return
  }
  triggering.value = true
  try {
    const result = await runAdminTask(trigger.taskType, payload, trigger.householdId || null)
    ElMessage.success(`已触发：${result.status}${result.error_code ? `（${result.error_code}）` : ''}`)
    await load()
  } finally {
    triggering.value = false
  }
}

onMounted(async () => {
  types.value = await fetchTaskTypes()
  await load()
})
</script>

<template>
  <section class="ops-page taskops-page" aria-labelledby="taskops-title">
    <header class="page-head">
      <div>
        <p class="eyebrow">TASK OPERATIONS / 任务运营</p>
        <h1 id="taskops-title">后台任务运营</h1>
      </div>
    </header>
    <p class="lead">幂等键、尝试次数、退避与死信全部来自真实任务记录；手动触发是既有管理边界，全部动作留痕审计。</p>

    <ErrorState v-if="error" :message="error" :retrying="loading" @retry="load" />

    <section class="panel" aria-labelledby="task-list-title">
      <div class="panel-title">
        <h2 id="task-list-title">任务列表</h2>
        <div class="filters">
          <label
            >状态
            <select v-model="statusFilter" data-testid="task-status" @change="resetAndLoad">
              <option value="">全部</option>
              <option
                v-for="option in ['pending', 'running', 'succeeded', 'failed', 'retry_scheduled', 'dead']"
                :key="option"
                :value="option"
              >
                {{ option }}
              </option>
            </select>
          </label>
          <label
            >类型
            <select v-model="typeFilter" data-testid="task-type" @change="resetAndLoad">
              <option value="">全部</option>
              <option v-for="option in types" :key="option.task_type" :value="option.task_type">
                {{ option.task_type }}
              </option>
            </select>
          </label>
          <label
            >错误码<input
              v-model="errorFilter"
              data-testid="task-error"
              placeholder="UPSTREAM_UNAVAILABLE"
              @change="resetAndLoad"
          /></label>
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>类型</th>
            <th>状态</th>
            <th>尝试</th>
            <th>幂等键</th>
            <th>家庭</th>
            <th>错误码</th>
            <th>下次重试</th>
            <th>更新时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td class="mono">{{ item.task_type }}</td>
            <td>
              <span class="state" :data-status="item.status">{{ item.status }}</span>
            </td>
            <td>{{ item.attempt }}/{{ item.max_attempts }}</td>
            <td class="mono clip">{{ item.idempotency_key }}</td>
            <td class="mono clip" :title="item.household_id ?? ''">{{ item.household_id ?? '平台' }}</td>
            <td>
              <span v-if="item.error_code" class="chip danger">{{ item.error_code }}</span
              ><span v-else class="dim">—</span>
            </td>
            <td class="mono">{{ item.next_retry_at ? formatDateTime(item.next_retry_at) : '—' }}</td>
            <td class="mono">{{ formatDateTime(item.updated_at) }}</td>
            <td><button type="button" data-testid="open-task" @click="openDetail(item)">详情</button></td>
          </tr>
          <tr v-if="!items.length && !error">
            <td colspan="9" class="empty">没有匹配的任务</td>
          </tr>
        </tbody>
      </table>
      <div class="pager">
        <button type="button" :disabled="page <= 1" @click="shiftPage(-1)">上一页</button>
        <span>第 {{ page }} 页 / 共 {{ Math.max(1, Math.ceil(total / pageSize)) }} 页（{{ total }} 条）</span>
        <button type="button" :disabled="page >= Math.ceil(total / pageSize)" @click="shiftPage(1)">下一页</button>
      </div>
    </section>

    <section class="panel" aria-labelledby="trigger-title">
      <h2 id="trigger-title">手动触发（审计边界）</h2>
      <form class="trigger-form" @submit.prevent="submitTrigger">
        <label
          >任务类型
          <select v-model="trigger.taskType" data-testid="trigger-type">
            <option v-for="option in types" :key="option.task_type" :value="option.task_type">
              {{ option.task_type }}
            </option>
          </select>
        </label>
        <label
          >家庭 ID（可空）<input
            v-model="trigger.householdId"
            data-testid="trigger-household"
            placeholder="全局任务留空"
        /></label>
        <label
          >负载 JSON<textarea v-model="trigger.payloadJson" data-testid="trigger-payload" rows="3"></textarea>
        </label>
        <button type="submit" data-testid="trigger-submit" :disabled="triggering">触发并运行一次</button>
      </form>
    </section>

    <el-dialog v-model="detailOpen" title="任务详情" width="720px">
      <div v-if="detail" class="detail-grid">
        <dl class="facts">
          <div>
            <dt>任务类型</dt>
            <dd class="mono">{{ detail.task_type }}</dd>
          </div>
          <div>
            <dt>幂等键</dt>
            <dd class="mono">{{ detail.idempotency_key }}</dd>
          </div>
          <div>
            <dt>状态</dt>
            <dd>
              <span class="state" :data-status="detail.status">{{ detail.status }}</span
              >（{{ detail.attempt }}/{{ detail.max_attempts }}）
            </dd>
          </div>
          <div>
            <dt>错误码</dt>
            <dd class="mono" data-testid="task-error-code">{{ detail.error_code ?? '—' }}</dd>
          </div>
          <div v-if="detail.error_detail">
            <dt>诊断（已消毒）</dt>
            <dd class="mono secondary" data-testid="sanitized-error-detail">{{ detail.error_detail }}</dd>
          </div>
          <div>
            <dt>下次重试</dt>
            <dd class="mono">{{ detail.next_retry_at ? formatDateTime(detail.next_retry_at) : '—' }}</dd>
          </div>
        </dl>
        <h3>负载（脱敏）</h3>
        <pre class="json">{{ JSON.stringify(detail.payload, null, 2) }}</pre>
        <h3>结果</h3>
        <pre class="json">{{ JSON.stringify(detail.result ?? {}, null, 2) }}</pre>
        <h3>审计轨迹</h3>
        <pre class="json">{{ JSON.stringify(detail.audit, null, 2) }}</pre>
        <h3>失败通知</h3>
        <table v-if="detail.notifications.length">
          <thead>
            <tr>
              <th>标题</th>
              <th>类型</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="notice in detail.notifications" :key="notice.id">
              <td>{{ notice.title }}</td>
              <td class="mono">{{ notice.kind }}</td>
              <td class="mono">{{ formatDateTime(notice.created_at) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="dim">无失败通知</p>
      </div>
    </el-dialog>
  </section>
</template>

<style scoped>
/* 这一族的骨架（页面网格、页头、.panel、表格、.facts、状态色、.empty/.dim、.chip）
   见 styles/global.css 的 .ops-page 段；这里只留本页差异。 */
.filters {
  flex-wrap: wrap;
}
/* 本页状态胶囊是等宽字体的代码味标签；其余三页不是 */
.state {
  background: var(--surface-sunken);
  font-family: ui-monospace, monospace;
}
/* 全局 .chip 是 inline-block 且带 1px 上下外边距；本页危险标签原本是零外边距的行内元素 */
.chip.danger {
  display: inline;
  margin: 0;
}
.clip {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.trigger-form {
  display: grid;
  gap: 10px;
  max-width: 560px;
  font-size: 13px;
}
.trigger-form label {
  display: grid;
  gap: 4px;
}
.detail-grid {
  display: grid;
  gap: 10px;
}
.detail-grid h3 {
  margin: 8px 0 0;
  font-size: 13px;
}
.facts dd.secondary {
  opacity: 0.78;
  font-size: 12px;
}
.json {
  margin: 0;
  padding: 10px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  font-size: 11.5px;
  overflow: auto;
  max-height: 220px;
}
</style>
