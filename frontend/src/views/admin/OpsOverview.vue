<script setup lang="ts">
import { ref } from 'vue'
import { fetchOpsOverview, type OpsOverview } from '../../api/ops'
import { useRequest } from '../../composables/useRequest'
import ErrorState from '../../components/ErrorState.vue'

const windowDays = ref(7)

const { data: overview, loading, error, run: load } = useRequest(
  () => fetchOpsOverview(windowDays.value),
  { immediate: true },
)

function pct(value: number) {
  return `${Math.round(value * 100)}%`
}

function maxCount(rows: { count: number }[]) {
  return Math.max(1, ...rows.map((row) => row.count))
}

function bar(count: number, max: number) {
  return `${Math.max(2, Math.round((count / max) * 100))}%`
}

const cards = [
  { key: 'households', label: '家庭总数', hint: '窗口内新增', metric: (o: OpsOverview) => String(o.households.total), sub: (o: OpsOverview) => `+${o.households.new_in_window}` },
  { key: 'installs', label: '活跃安装', hint: '覆盖家庭', metric: (o: OpsOverview) => String(o.installations.active), sub: (o: OpsOverview) => `${o.installations.households_with_active} 户` },
  { key: 'runs', label: 'Agent 运行', hint: '窗口内运行数', metric: (o: OpsOverview) => String(o.agent_runs.total_in_window), sub: (o: OpsOverview) => `失败 ${o.agent_runs.by_status.failed ?? 0}` },
  { key: 'tasks', label: '任务失败率', hint: 'Celery 窗口内', metric: (o: OpsOverview) => pct(o.celery_tasks.failure_rate), sub: (o: OpsOverview) => `${o.celery_tasks.total_in_window} 个任务` },
  { key: 'review', label: '审核待办', hint: '平均周转', metric: (o: OpsOverview) => String(o.review.queue_depth), sub: (o: OpsOverview) => o.review.avg_turnaround_seconds != null ? `${Math.round(o.review.avg_turnaround_seconds / 60)} 分钟` : '—' },
  { key: 'tokens', label: '模型令牌消耗', hint: '窗口内请求数', metric: (o: OpsOverview) => String(o.model_usage.tokens), sub: (o: OpsOverview) => `${o.model_usage.requests} 次请求` },
  { key: 'incidents', label: '配额超限', hint: '窗口内事故', metric: (o: OpsOverview) => String(o.quota_incidents.count), sub: (o: OpsOverview) => Object.keys(o.quota_incidents.by_code).slice(0, 2).join(' / ') || '无' },
  { key: 'risk', label: '高风险通过', hint: '权限拒绝', metric: (o: OpsOverview) => String(o.risk.high_risk_approvals), sub: (o: OpsOverview) => `拒绝 ${o.risk.permission_denials}` },
] as const
</script>

<template>
  <section class="ops-page overview-page" aria-labelledby="ops-title">
    <header class="page-head">
      <div>
        <p class="eyebrow">OPERATIONS / 运营治理</p>
        <h1 id="ops-title">运营概览</h1>
      </div>
      <label>时间窗口
        <select v-model="windowDays" data-testid="ops-window" @change="load">
          <option :value="1">近 1 天</option>
          <option :value="7">近 7 天</option>
          <option :value="30">近 30 天</option>
        </select>
      </label>
    </header>
    <p class="lead">所有指标来自真实记录（运行、工具执行、任务、审核、用量、事故）并按所选窗口聚合，读取本身留痕审计。</p>

    <ErrorState v-if="error" :message="error" :retrying="loading" @retry="load" />

    <div v-if="overview" class="metric-grid" data-testid="ops-cards">
      <article v-for="card in cards" :key="card.key" class="metric-card">
        <p class="metric-label">{{ card.label }}</p>
        <p class="metric-value" :data-testid="`metric-${card.key}`">{{ card.metric(overview) }}</p>
        <p class="metric-sub">{{ card.hint }}：{{ card.sub(overview) }}</p>
      </article>
    </div>

    <div v-if="overview" class="panel-grid">
      <section class="panel" aria-labelledby="trend-runs">
        <h2 id="trend-runs">运行 / 任务吞吐</h2>
        <table>
          <thead><tr><th>日期</th><th>Agent 运行</th><th>成功任务</th></tr></thead>
          <tbody>
            <tr v-for="row in overview.agent_runs.by_day" :key="row.day">
              <td class="mono">{{ row.day }}</td>
              <td><span class="bar-track"><span class="bar" :style="{ width: bar(row.count, maxCount(overview.agent_runs.by_day)) }"></span></span> {{ row.count }}</td>
              <td>{{ overview.celery_tasks.throughput_by_day.find((item) => item.day === row.day)?.count ?? 0 }}</td>
            </tr>
            <tr v-for="row in overview.celery_tasks.throughput_by_day.filter((item) => !overview!.agent_runs.by_day.some((r) => r.day === item.day))" :key="`task-${row.day}`">
              <td class="mono">{{ row.day }}</td>
              <td>0</td>
              <td>{{ row.count }}</td>
            </tr>
            <tr v-if="!overview.agent_runs.by_day.length && !overview.celery_tasks.throughput_by_day.length"><td colspan="3" class="empty">窗口内无数据</td></tr>
          </tbody>
        </table>
      </section>

      <section class="panel" aria-labelledby="usage-models">
        <h2 id="usage-models">模型用量 / 令牌（按档案 · 用途）</h2>
        <table>
          <thead><tr><th>档案</th><th>请求</th><th>令牌</th></tr></thead>
          <tbody>
            <tr v-for="row in overview.model_usage.by_model" :key="row.model_id">
              <td>{{ row.model_name }}</td><td>{{ row.requests }}</td><td>{{ row.tokens }}</td>
            </tr>
            <tr v-if="!overview.model_usage.by_model.length"><td colspan="3" class="empty">窗口内无用量</td></tr>
          </tbody>
        </table>
        <table>
          <thead><tr><th>用途</th><th>请求</th><th>令牌</th></tr></thead>
          <tbody>
            <tr v-for="row in overview.model_usage.by_purpose" :key="row.purpose">
              <td>{{ row.purpose }}</td><td>{{ row.requests }}</td><td>{{ row.tokens }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="panel" aria-labelledby="risk-panel">
        <h2 id="risk-panel">风险与治理</h2>
        <dl class="facts">
          <div><dt>权限拒绝</dt><dd>{{ overview.tool_executions.permission_denials }}</dd></div>
          <div><dt>工具执行失败</dt><dd>{{ overview.tool_executions.failed }}</dd></div>
          <div><dt>高风险工具审核通过</dt><dd>{{ overview.risk.high_risk_approvals }}</dd></div>
          <div><dt>配额事故（按码）</dt><dd>
            <span v-for="(count, code) in overview.quota_incidents.by_code" :key="code" class="chip" :data-code="code">{{ code }} ×{{ count }}</span>
            <span v-if="!overview.quota_incidents.count" class="dim">无</span>
          </dd></div>
          <div><dt>审核决定（窗口内）</dt><dd>
            <span v-for="(count, action) in overview.review.decided_by_action" :key="action" class="chip">{{ action }} ×{{ count }}</span>
            <span v-if="!overview.review.decided_in_window" class="dim">无</span>
          </dd></div>
        </dl>
      </section>
    </div>
    <p v-else-if="!loading && !error" class="dim">暂无数据</p>
  </section>
</template>

<style scoped>
/* 这一族的骨架（页面网格、页头、.panel、表格、.facts、.empty/.dim、.chip）
   见 styles/global.css 的 .ops-page 段；这里只留本页差异。 */
/* 正文度量统一收到 400/700 两档，表头用 700 */
th { font-weight: 700; }
/* 页头横排只有本页与 ModelsOps 需要，不进 .ops-page 共享段 */
.page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; }
.page-head label { display: inline-flex; gap: 8px; align-items: center; font-size: 13px; }
.metric-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 10px; }
.metric-card { padding: 12px 14px; border: 1px solid var(--hairline-soft); border-radius: 10px; background: #fff; }
.metric-label { margin: 0; font-size: 12px; opacity: .66; }
.metric-value { margin: 4px 0 2px; font-size: 26px; font-weight: 700; }
.metric-sub { margin: 0; font-size: 11px; opacity: .6; }
.bar-track { display: inline-block; width: 72px; height: 8px; margin-right: 6px; border-radius: 4px; background: var(--el-fill-color); vertical-align: middle; overflow: hidden; }
.bar { display: block; height: 100%; background: var(--device-ink); border-radius: 4px; }
.panel { align-content: start; }
.facts { gap: 8px; }
.facts dd { text-align: right; }
</style>
