<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  fetchAdminActions, fetchDeploymentPointers, fetchModelConfigRevisions,
  type AdminActionItem, type DeploymentPointers, type ModelConfigRevision,
} from '../../api/audit'
import { formatDateTime } from '../../utils/format'

const actions = ref<AdminActionItem[]>([])
const actionTotal = ref(0)
const actionPage = ref(1)
const actionPageSize = 20
const actionFilter = ref('')
const resourceFilter = ref('')
const pointers = ref<DeploymentPointers | null>(null)
const revisions = ref<ModelConfigRevision[]>([])

async function loadActions() {
  const result = await fetchAdminActions({
    page: actionPage.value, page_size: actionPageSize,
    action: actionFilter.value || undefined,
    resource_type: resourceFilter.value || undefined,
  })
  actions.value = result.items
  actionTotal.value = result.total
}

onMounted(async () => {
  await loadActions()
  pointers.value = await fetchDeploymentPointers()
  revisions.value = await fetchModelConfigRevisions()
})
</script>

<template>
  <section class="audit-page" aria-labelledby="audit-title">
    <header class="page-head">
      <div>
        <p class="eyebrow">AUDIT &amp; RELEASE / 审计与发布</p>
        <h1 id="audit-title">审计与发布治理</h1>
      </div>
    </header>
    <p class="lead">管理动作日志、市场/官方 Agent 发布指针历史与模型配置修订证据；读取本身留痕，审计体永不包含密钥或异常原文。</p>

    <section class="panel" aria-labelledby="actions-title">
      <div class="panel-title">
        <h2 id="actions-title">管理动作日志</h2>
        <div class="filters">
          <label>动作<input v-model="actionFilter" data-testid="action-filter" placeholder="review.approve" @change="actionPage = 1; loadActions()" /></label>
          <label>资源类型<input v-model="resourceFilter" placeholder="review_submission" @change="actionPage = 1; loadActions()" /></label>
        </div>
      </div>
      <table>
        <thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>资源</th><th>家庭（租户）</th><th>详情（脱敏）</th></tr></thead>
        <tbody>
          <tr v-for="item in actions" :key="item.id">
            <td class="mono">{{ formatDateTime(item.created_at) }}</td>
            <td>{{ item.actor_username ?? item.principal_id }}</td>
            <td class="mono">{{ item.action }}</td>
            <td class="mono">{{ item.resource_type }}/{{ item.resource_id }}</td>
            <td class="mono clip" :title="item.household_id ?? ''">{{ item.household_id ?? '平台' }}</td>
            <td class="mono clip" :title="JSON.stringify(item.detail)">{{ JSON.stringify(item.detail) }}</td>
          </tr>
          <tr v-if="!actions.length"><td colspan="6" class="empty">没有匹配的审计记录</td></tr>
        </tbody>
      </table>
      <div class="pager">
        <button type="button" :disabled="actionPage <= 1" @click="actionPage -= 1; loadActions()">上一页</button>
        <span>第 {{ actionPage }} 页 / 共 {{ Math.max(1, Math.ceil(actionTotal / actionPageSize)) }} 页（{{ actionTotal }} 条）</span>
        <button type="button" :disabled="actionPage >= Math.ceil(actionTotal / actionPageSize)" @click="actionPage += 1; loadActions()">下一页</button>
      </div>
    </section>

    <section class="panel" aria-labelledby="pointers-title">
      <h2 id="pointers-title">发布指针历史（市场条目 / 官方 Agent）</h2>
      <div v-if="pointers" class="pointer-grid">
        <table>
          <thead><tr><th>市场条目</th><th>状态</th><th>当前版本</th><th>版本轨迹（版本 · 决定者 · 时间）</th></tr></thead>
          <tbody>
            <tr v-for="entry in pointers.market_entries" :key="entry.entry_id">
              <td>{{ entry.name }}<small class="mono block">{{ entry.entry_id }}</small></td>
              <td>{{ entry.status }}<small v-if="entry.official" class="chip">官方</small></td>
              <td>v{{ entry.current_version_number }}</td>
              <td>
                <span v-for="step in entry.history" :key="step.submission_id" class="chip">
                  v{{ step.version }} · {{ step.changed_by_account_id ?? '—' }} · {{ formatDateTime(step.changed_at) }}
                </span>
                <span v-if="!entry.history.length" class="dim">无已通过版本</span>
              </td>
            </tr>
            <tr v-if="!pointers.market_entries.length"><td colspan="4" class="empty">暂无市场条目</td></tr>
          </tbody>
        </table>
        <table>
          <thead><tr><th>官方部署</th><th>家庭</th><th>修订</th><th>版本轨迹</th></tr></thead>
          <tbody>
            <tr v-for="deployment in pointers.official" :key="deployment.deployment_id">
              <td class="mono clip" :title="deployment.deployment_id">{{ deployment.deployment_id }}</td>
              <td class="mono clip" :title="deployment.household_id">{{ deployment.household_id }}</td>
              <td>r{{ deployment.revision }}</td>
              <td>
                <span v-for="step in deployment.history" :key="step.version_id" class="chip">
                  v{{ step.version }} · {{ step.published_at ? formatDateTime(step.published_at) : '—' }}
                </span>
              </td>
            </tr>
            <tr v-if="!pointers.official.length"><td colspan="4" class="empty">暂无官方部署</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" aria-labelledby="model-revisions-title">
      <h2 id="model-revisions-title">模型配置修订与健康证据</h2>
      <table>
        <thead><tr><th>模型</th><th>类型</th><th>启用</th><th>修订</th><th>最近健康</th><th>检查轨迹（状态 · 端点修订 · 模型修订 · 时间）</th></tr></thead>
        <tbody>
          <tr v-for="model in revisions" :key="model.model_id">
            <td>{{ model.model_name }}<small class="mono block">{{ model.model_id }}</small></td>
            <td>{{ model.model_type }}</td>
            <td>{{ model.enabled ? '是' : '否' }}</td>
            <td>r{{ model.revision }}</td>
            <td>
              <template v-if="model.latest_health">
                <span :class="['state', model.latest_health.status]">{{ model.latest_health.status }}</span>
                <small v-if="model.latest_health.error_code" class="mono block">{{ model.latest_health.error_code }}</small>
              </template>
              <span v-else class="dim">无</span>
            </td>
            <td>
              <span v-for="(check, index) in model.history.slice(0, 6)" :key="index" class="chip">
                {{ check.status }} · e{{ check.endpoint_revision }}/m{{ check.model_revision }} · {{ formatDateTime(check.checked_at) }}
              </span>
              <span v-if="!model.history.length" class="dim">无证据</span>
            </td>
          </tr>
          <tr v-if="!revisions.length"><td colspan="6" class="empty">暂无模型档案</td></tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<style scoped>
.audit-page { display: grid; gap: 18px; }
.page-head .eyebrow { margin: 0; font: 10px var(--font-utility, monospace); letter-spacing: .2em; opacity: .6; }
h1 { margin: 4px 0 0; font-size: 24px; }
.lead { margin: 0; opacity: .74; font-size: 13px; }
.panel { padding: 14px; border: 1px solid #e3e1ee; border-radius: 10px; background: #fff; display: grid; gap: 10px; }
.panel-title { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.panel h2 { margin: 0; font-size: 15px; }
.filters { display: flex; gap: 10px; font-size: 12px; }
.filters label { display: inline-flex; align-items: center; gap: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { text-align: left; padding: 4px 6px; border-bottom: 1px solid #eee; opacity: .6; }
td { padding: 5px 6px; border-bottom: 1px solid #f3f2f8; vertical-align: top; }
.mono { font-family: ui-monospace, monospace; }
.clip { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.block { display: block; opacity: .55; }
.empty { opacity: .5; text-align: center; }
.dim { opacity: .5; }
.chip { display: inline-block; margin: 1px 0 1px 6px; padding: 1px 7px; border-radius: 8px; background: #f4f2fb; font-family: ui-monospace, monospace; font-size: 11px; }
.state { padding: 1px 8px; border-radius: 8px; font-size: 11px; }
.state.passed { background: #e7f6ec; color: #1d7a3a; }
.state.failed { background: #fdeaea; color: #b03030; }
.pager { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.pointer-grid { display: grid; gap: 12px; }
</style>
