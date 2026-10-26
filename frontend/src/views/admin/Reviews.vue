<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import {
  decideReviewSubmission,
  fetchReviewQueue,
  fetchReviewReports,
  fetchReviewSubmission,
  handleReviewReport,
  takedownEntry,
} from '../../review/api'
import {
  REVIEW_STATUS_LABELS,
  type ReviewAction,
  type ReviewQueueItem,
  type ReviewReportItem,
  type ReviewSubmissionDetail,
} from '../../review/types'
import { RISK_LABELS } from '../../market/types'
import { useRequest } from '../../composables/useRequest'
import ErrorState from '../../components/ErrorState.vue'

const statusFilter = ref('review_pending')
const reportStatusFilter = ref('open')

const detail = ref<ReviewSubmissionDetail | null>(null)
const detailOpen = ref(false)
const decisionAction = ref<ReviewAction>('approve')
const decisionReason = ref('')
const deciding = ref(false)
const takedownReason = ref('')
const reportNotes = ref<Record<string, string>>({})

const STATUS_OPTIONS = ['', 'precheck_pending', 'precheck_failed', 'review_pending', 'returned', 'rejected', 'approved']

// 队列与举报一次拉取：任一失败都算页面数据不完整
const {
  data: pageData,
  loading,
  error,
  run: load,
} = useRequest(
  async () => {
    const [queueResult, reportsResult] = await Promise.all([
      fetchReviewQueue(statusFilter.value),
      fetchReviewReports(reportStatusFilter.value),
    ])
    return { queue: queueResult.items, reports: reportsResult.items }
  },
  { initial: { queue: [] as ReviewQueueItem[], reports: [] as ReviewReportItem[] }, immediate: true },
)

const queue = computed(() => pageData.value.queue)
const reports = computed(() => pageData.value.reports)

async function openDetail(item: ReviewQueueItem) {
  detail.value = await fetchReviewSubmission(item.submission_id)
  decisionAction.value = 'approve'
  decisionReason.value = ''
  takedownReason.value = ''
  detailOpen.value = true
}

async function closeDetail() {
  detailOpen.value = false
  detail.value = null
}

async function submitDecision() {
  if (!detail.value) return
  deciding.value = true
  try {
    const result = await decideReviewSubmission(detail.value.submission.id, decisionAction.value, decisionReason.value)
    ElMessage.success(
      `已记录决定：${REVIEW_STATUS_LABELS[result.status as ReviewQueueItem['status']] ?? result.status}`,
    )
    await closeDetail()
    await load()
  } finally {
    deciding.value = false
  }
}

async function submitTakedown() {
  if (!detail.value?.submission || !detail.value.submission.id || !takedownReason.value.trim()) return
  const entryId = queue.value.find((item) => item.submission_id === detail.value?.submission.id)?.entry_id
  if (!entryId) {
    ElMessage.warning('该提交尚未关联市场条目（未通过首发审核）')
    return
  }
  await takedownEntry(entryId, takedownReason.value.trim())
  ElMessage.success('已下架：市场隐藏、阻止新安装；既有安装固定版本继续运行')
  await closeDetail()
  await load()
}

async function handleReport(item: ReviewReportItem, action: 'resolve' | 'dismiss') {
  await handleReviewReport(item.id, action, reportNotes.value[item.id] ?? '')
  ElMessage.success(action === 'resolve' ? '举报已处理' : '举报已驳回')
  await load()
}
</script>

<template>
  <section class="page reviews-page" aria-labelledby="reviews-title">
    <header>
      <p class="eyebrow">GOVERNANCE / Agent 审核</p>
      <h1 id="reviews-title">审核工作台</h1>
      <p>自动预检证据 + 审核模型风险摘要仅供参考；通过与否只能由管理员人工决定，全部动作留痕审计。</p>
    </header>

    <ErrorState v-if="error" :message="error" :retrying="loading" @retry="load" />

    <section class="panel" aria-labelledby="queue-title">
      <div class="panel-title">
        <h2 id="queue-title">审核队列</h2>
        <label
          >状态
          <select v-model="statusFilter" data-testid="queue-status" @change="load">
            <option v-for="option in STATUS_OPTIONS" :key="option" :value="option">{{ option || '全部' }}</option>
          </select>
        </label>
      </div>
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>状态</th>
            <th>分类</th>
            <th>版本</th>
            <th>预检</th>
            <th>风险摘要</th>
            <th>提交时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in queue" :key="item.submission_id">
            <td>{{ item.name }}</td>
            <td>
              <span class="state" :data-status="item.status">{{ REVIEW_STATUS_LABELS[item.status] }}</span>
            </td>
            <td>{{ item.category }}</td>
            <td>
              v{{ item.version
              }}<small v-if="item.prior_public_version">（公共 v{{ item.prior_public_version }}）</small>
            </td>
            <td :data-precheck="item.precheck_status">{{ item.precheck_status }}</td>
            <td :data-risk-summary="item.risk_summary_status">{{ item.risk_summary_status }}</td>
            <td class="mono">{{ item.submitted_at?.slice(0, 19).replace('T', ' ') }}</td>
            <td><button type="button" data-testid="open-submission" @click="openDetail(item)">证据详情</button></td>
          </tr>
          <tr v-if="!queue.length && !error">
            <td colspan="8" class="empty">队列为空</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel" aria-labelledby="reports-title">
      <div class="panel-title">
        <h2 id="reports-title">举报处理</h2>
        <label
          >状态
          <select v-model="reportStatusFilter" @change="load">
            <option value="open">open</option>
            <option value="resolved">resolved</option>
            <option value="dismissed">dismissed</option>
            <option value="">全部</option>
          </select>
        </label>
      </div>
      <table>
        <thead>
          <tr>
            <th>条目</th>
            <th>原因</th>
            <th>状态</th>
            <th>处理备注</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in reports" :key="item.id">
            <td>{{ item.entry_name ?? item.entry_id }}</td>
            <td>{{ item.reason }}</td>
            <td>{{ item.status }}</td>
            <td>
              <input
                v-if="item.status === 'open'"
                v-model="reportNotes[item.id]"
                data-testid="report-note"
                placeholder="处理备注（审计）"
              />
              <span v-else>{{ item.resolution_note }}</span>
            </td>
            <td v-if="item.status === 'open'" class="actions">
              <button type="button" @click="handleReport(item, 'resolve')">处理完成</button>
              <button type="button" data-testid="dismiss-report" @click="handleReport(item, 'dismiss')">驳回</button>
            </td>
            <td v-else></td>
          </tr>
          <tr v-if="!reports.length && !error">
            <td colspan="5" class="empty">暂无举报</td>
          </tr>
        </tbody>
      </table>
    </section>

    <div v-if="detailOpen" class="modal-backdrop" @click="closeDetail"></div>
    <div
      v-if="detailOpen"
      class="modal"
      role="dialog"
      aria-modal="true"
      :aria-label="`审核证据 ${detail?.listing_preview.name ?? ''}`"
      @keydown.esc="closeDetail"
    >
      <template v-if="detail">
        <header class="modal-head">
          <div>
            <p class="eyebrow">EVIDENCE / 审核证据</p>
            <h2>{{ detail.listing_preview.name }} · v{{ detail.agent_version.version }}</h2>
          </div>
          <button type="button" class="close" aria-label="关闭证据" @click="closeDetail">关闭</button>
        </header>

        <section aria-labelledby="preview-title">
          <h3 id="preview-title">商店展示预览</h3>
          <dl class="facts">
            <div>
              <dt>名称</dt>
              <dd>{{ detail.listing_preview.name }}</dd>
            </div>
            <div>
              <dt>分类</dt>
              <dd>{{ detail.listing_preview.category }}</dd>
            </div>
            <div>
              <dt>状态</dt>
              <dd>{{ REVIEW_STATUS_LABELS[detail.submission.status] }}</dd>
            </div>
          </dl>
          <p class="detail-desc">{{ detail.listing_preview.description }}</p>
        </section>

        <section aria-labelledby="diff-title">
          <h3 id="diff-title">版本差异（对比上一公共版本 v{{ detail.prior_public_version?.version ?? '—' }}）</h3>
          <ul class="evidence-list" v-if="detail.diff">
            <li :data-changed="detail.diff.prompt_changed">
              提示词{{ detail.diff.prompt_changed ? '已变更' : '未变' }}
            </li>
            <li :data-changed="detail.diff.workflow_changed">
              受限流程{{ detail.diff.workflow_changed ? '已变更' : '未变' }}
            </li>
            <li v-if="detail.diff.tools_added.length" data-changed="true">
              新增权限：{{ detail.diff.tools_added.join('、') }}
            </li>
            <li v-if="detail.diff.tools_removed.length" data-changed="true">
              移除权限：{{ detail.diff.tools_removed.join('、') }}
            </li>
            <li v-if="detail.diff.budgets_changed.length" data-changed="true">
              预算变更：{{ detail.diff.budgets_changed.join('、') }}
            </li>
          </ul>
          <p v-else class="empty">首发版本，无上一公共版本。</p>
        </section>

        <section aria-labelledby="perm-title">
          <h3 id="perm-title">权限（风险标注）</h3>
          <ul class="perm-list">
            <li v-for="tool in detail.permissions" :key="tool.tool_id">
              <span class="perm-name">{{ tool.display_name || tool.key }}</span>
              <span class="risk" :data-risk="tool.risk_level">{{ RISK_LABELS[tool.risk_level] }}</span>
              <small>{{ tool.description }}</small>
            </li>
            <li v-if="!detail.permissions.length" class="empty">无工具权限</li>
          </ul>
        </section>

        <section aria-labelledby="sandbox-title">
          <h3 id="sandbox-title">沙盒试运行轨迹</h3>
          <ul class="evidence-list" v-if="detail.sandbox">
            <li>
              状态：{{ detail.sandbox.status }}{{ detail.sandbox.error_code ? `（${detail.sandbox.error_code}）` : '' }}
            </li>
            <li
              v-for="caseItem in detail.sandbox.cases"
              :key="caseItem.name"
              :data-passed="caseItem.status === 'passed'"
            >
              {{ caseItem.name }}：{{ caseItem.status }}<small>{{ caseItem.output }}</small>
            </li>
          </ul>
          <p v-else class="empty">无试运行报告</p>
        </section>

        <section aria-labelledby="precheck-title">
          <h3 id="precheck-title">自动预检</h3>
          <ul class="evidence-list" v-if="detail.precheck">
            <li v-for="check in detail.precheck.checks" :key="check.code" :data-passed="check.passed">
              {{ check.code }}：{{ check.passed ? '通过' : `未过 ${check.detail}` }}
            </li>
          </ul>
          <p v-else class="empty">预检尚未完成</p>
        </section>

        <section aria-labelledby="knowledge-title">
          <h3 id="knowledge-title">知识来源与扫描</h3>
          <ul class="evidence-list">
            <li v-for="asset in detail.knowledge" :key="asset.asset_id" :data-passed="asset.scan_status === 'passed'">
              {{ asset.filename }} · 扫描{{ asset.scan_status }}
            </li>
            <li v-if="!detail.knowledge.length" class="empty">无知识附件</li>
          </ul>
        </section>

        <section aria-labelledby="risk-title">
          <h3 id="risk-title">审核模型风险摘要</h3>
          <p v-if="detail.risk_summary.status === 'done'" data-testid="risk-advisory" class="risk-summary">
            <span class="advisory">仅参考 · 不构成通过决定</span>
            {{ detail.risk_summary.summary }}
            <small>平台风险分级：{{ detail.risk_summary.risk_level }}</small>
          </p>
          <p v-else-if="detail.risk_summary.status === 'unavailable'" class="empty">
            审核模型不可用（{{ detail.risk_summary.code }}）：提交仍在队列，等待人工审核。
          </p>
          <p v-else class="empty">摘要生成中</p>
        </section>

        <section aria-labelledby="decision-title" class="decision-block">
          <h3 id="decision-title">人工决定（审计）</h3>
          <label
            >动作
            <select v-model="decisionAction" data-testid="decision-action">
              <option value="approve">通过（发布公共版本）</option>
              <option value="reject">拒绝</option>
              <option value="return">退回修改</option>
              <option value="re-review">复审（重跑预检与摘要）</option>
            </select>
          </label>
          <label
            >理由（通过/拒绝必填）
            <input v-model="decisionReason" data-testid="decision-reason" />
          </label>
          <button type="button" data-testid="submit-decision" :disabled="deciding" @click="submitDecision">
            {{ deciding ? '提交中...' : '提交决定' }}
          </button>
        </section>

        <section aria-labelledby="takedown-title" class="decision-block danger-block">
          <h3 id="takedown-title">下架（既有安装继续运行）</h3>
          <label
            >下架理由
            <input v-model="takedownReason" data-testid="takedown-reason" />
          </label>
          <button type="button" data-testid="submit-takedown" @click="submitTakedown">下架公共条目</button>
        </section>
      </template>
    </div>
  </section>
</template>

<style scoped>
/* 骨架（页宽、页头说明、.panel、.panel-title、.empty、.facts、.risk、.modal*、.close）
   见 styles/global.css；这里只留本页差异。 */
/* 审核页的 h1 比用户端小一档 */
h1 {
  font: 700 clamp(34px, 4vw, 52px)/1 var(--font-display);
}
.panel {
  margin-top: 26px;
}
.panel h2 {
  margin-bottom: 14px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
th {
  padding: 8px 10px;
  text-align: left;
  font: 600 11px var(--font-utility);
  letter-spacing: 0.1em;
  color: var(--ink-quiet);
  border-bottom: 2px solid var(--orbit-mist);
}
td {
  padding: 10px;
  border-bottom: 1px solid var(--orbit-mist);
  vertical-align: top;
}
td small {
  display: block;
  color: var(--ink-quiet);
}
.mono {
  font: 12px var(--font-utility);
}
.state {
  background: var(--orbit-mist);
  color: var(--orbit-violet);
}
.state[data-status='approved'] {
  color: var(--risk-low-fg);
  background: var(--risk-low-bg);
}
.state[data-status='rejected'],
.state[data-status='precheck_failed'] {
  color: var(--risk-high-fg);
  background: var(--risk-high-bg);
}
td[data-precheck='failed'],
td[data-risk-summary='unavailable'] {
  color: var(--risk-high-fg);
}
td[data-precheck='passed'],
td[data-risk-summary='done'] {
  color: var(--risk-low-fg);
}
/* 弹窗关闭按钮的 44px 触控高度由全局 .close 提供，别被本页 42px 的按钮度量压掉 */
button:not(.close) {
  min-height: 42px;
  padding: 0 14px;
  border: 1px solid var(--hairline-strong);
  border-radius: 10px;
  background: transparent;
  cursor: pointer;
}
.actions {
  display: flex;
  gap: 8px;
}
.modal {
  inset: 6vh 50% auto 50%;
  width: min(92vw, 720px);
  transform: translateX(-50%);
}
h3 {
  margin: 20px 0 8px;
  font-family: var(--font-display);
}
.detail-desc {
  color: var(--ink-body);
}
/* 证据弹窗的权限行比用户端更紧凑 */
.perm-list li {
  padding: 8px 0;
}
.evidence-list {
  margin: 0;
  padding-left: 18px;
}
.evidence-list li[data-passed='false'],
.evidence-list li[data-changed='false'] {
  color: var(--ink-quiet);
}
.evidence-list li[data-passed='true'] {
  color: var(--risk-low-fg);
}
.evidence-list li[data-changed='true'] {
  color: var(--risk-high-fg);
  font-weight: 700;
}
.evidence-list small {
  display: block;
  color: var(--ink-quiet);
}
.risk-summary {
  padding: 12px 14px;
  border-left: 4px solid var(--orbit-violet);
  background: var(--orbit-mist);
}
.risk-summary small {
  display: block;
  margin-top: 6px;
  color: var(--ink-quiet);
}
.advisory {
  display: inline-flex;
  margin-right: 8px;
  padding: 2px 10px;
  border-radius: 999px;
  font: 600 11px var(--font-utility);
  color: var(--orbit-violet);
  background: var(--surface-white);
}
.decision-block {
  display: grid;
  gap: 10px;
  margin-top: 18px;
  padding: 16px;
  border: 1px solid var(--hairline-strong);
  border-radius: 14px;
}
.decision-block label {
  display: grid;
  gap: 4px;
  font-weight: 700;
}
.decision-block select,
.decision-block input {
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--hairline-strong);
  border-radius: 10px;
}
.decision-block button {
  justify-self: start;
  border: 0;
  font-weight: 700;
  color: var(--device-ink);
  background: var(--charge-coral);
}
.danger-block button {
  color: var(--risk-high-fg);
  background: var(--risk-high-bg);
}
</style>
