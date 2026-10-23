<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { fetchUsageAccounting, type UsageAccounting } from '../../api/audit'
import { releaseExpiredReservations } from '../../api/ops'
import { formatDateTime } from '../../utils/format'

const accounting = ref<UsageAccounting | null>(null)
const days = ref(7)
const releasing = ref(false)

async function load() {
  accounting.value = await fetchUsageAccounting(days.value)
}

async function release() {
  releasing.value = true
  try {
    const result = await releaseExpiredReservations()
    ElMessage.success(`已释放 ${result.released} 条过期预留（令牌仍保守计入用量）`)
    await load()
  } finally {
    releasing.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="modelsops-page" aria-labelledby="modelsops-title">
    <header class="page-head">
      <div>
        <p class="eyebrow">MODEL OPERATIONS / 模型与能力</p>
        <h1 id="modelsops-title">模型用量与对账</h1>
      </div>
      <div class="head-actions">
        <label>窗口
          <select v-model="days" data-testid="usage-window" @change="load">
            <option :value="1">近 1 天</option>
            <option :value="7">近 7 天</option>
            <option :value="30">近 30 天</option>
          </select>
        </label>
        <button type="button" data-testid="release-expired" :disabled="releasing" @click="release">释放过期预留</button>
      </div>
    </header>
    <p class="lead">预留-完成对账与按档案/用途/日的用量聚合来自真实 ModelUsage 行；配置入口在“初始化设置”，此处为用量、成本与治理视图。</p>

    <div v-if="accounting" class="panel-grid">
      <section class="panel" aria-labelledby="reconcile-title">
        <h2 id="reconcile-title">预留 / 完成对账</h2>
        <dl class="facts">
          <div><dt>窗口内请求</dt><dd>{{ accounting.totals.requests }}</dd></div>
          <div><dt>窗口内令牌</dt><dd>{{ accounting.totals.tokens }}</dd></div>
          <div v-for="(count, status) in accounting.by_status" :key="status"><dt>{{ status }}</dt><dd>{{ count }}</dd></div>
        </dl>
        <h3>未对账预留（超过 {{ accounting.unreconciled.window_minutes }} 分钟）</h3>
        <table>
          <thead><tr><th>家庭</th><th>档案</th><th>预留令牌</th><th>预留时间</th></tr></thead>
          <tbody>
            <tr v-for="row in accounting.unreconciled.items" :key="row.id">
              <td class="mono clip" :title="row.household_id">{{ row.household_id }}</td>
              <td class="mono clip" :title="row.model_id">{{ row.model_id }}</td>
              <td>{{ row.reserved_tokens }}</td>
              <td class="mono">{{ formatDateTime(row.created_at) }}</td>
            </tr>
            <tr v-if="!accounting.unreconciled.items.length"><td colspan="4" class="empty">无未对账预留</td></tr>
          </tbody>
        </table>
      </section>

      <section class="panel" aria-labelledby="by-model-title">
        <h2 id="by-model-title">按档案 / 用途</h2>
        <table>
          <thead><tr><th>档案</th><th>请求</th><th>令牌</th></tr></thead>
          <tbody>
            <tr v-for="row in accounting.by_model" :key="row.model_id"><td>{{ row.model_name }}</td><td>{{ row.requests }}</td><td>{{ row.tokens }}</td></tr>
            <tr v-if="!accounting.by_model.length"><td colspan="3" class="empty">窗口内无用量</td></tr>
          </tbody>
        </table>
        <table>
          <thead><tr><th>用途</th><th>请求</th><th>令牌</th></tr></thead>
          <tbody>
            <tr v-for="row in accounting.by_purpose" :key="row.purpose"><td class="mono">{{ row.purpose }}</td><td>{{ row.requests }}</td><td>{{ row.tokens }}</td></tr>
          </tbody>
        </table>
      </section>

      <section class="panel" aria-labelledby="by-day-title">
        <h2 id="by-day-title">按日趋势</h2>
        <table>
          <thead><tr><th>日期</th><th>请求</th><th>令牌</th></tr></thead>
          <tbody>
            <tr v-for="row in accounting.by_day" :key="row.day"><td class="mono">{{ row.day }}</td><td>{{ row.requests }}</td><td>{{ row.tokens }}</td></tr>
            <tr v-if="!accounting.by_day.length"><td colspan="3" class="empty">窗口内无用量</td></tr>
          </tbody>
        </table>
      </section>
    </div>
  </section>
</template>

<style scoped>
.modelsops-page { display: grid; gap: 18px; }
.page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
.head-actions { display: flex; gap: 10px; align-items: center; }
.eyebrow { margin: 0; font: 10px var(--font-utility, monospace); letter-spacing: .2em; opacity: .6; }
h1 { margin: 4px 0 0; font-size: 24px; }
h3 { margin: 8px 0 0; font-size: 13px; }
.lead { margin: 0; opacity: .74; font-size: 13px; }
.panel-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
.panel { padding: 14px; border: 1px solid #e3e1ee; border-radius: 10px; background: #fff; display: grid; gap: 10px; align-content: start; }
.panel h2 { margin: 0; font-size: 15px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { text-align: left; padding: 4px 6px; border-bottom: 1px solid #eee; opacity: .6; }
td { padding: 5px 6px; border-bottom: 1px solid #f3f2f8; }
.mono { font-family: ui-monospace, monospace; }
.clip { max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty { opacity: .5; text-align: center; }
.facts { display: grid; gap: 6px; margin: 0; }
.facts > div { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; }
.facts dt { opacity: .66; }
.facts dd { margin: 0; font-weight: 600; }
label { font-size: 13px; display: inline-flex; gap: 8px; align-items: center; }
</style>
