<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchDashboard, type DashboardData } from '../../api/admin'
import { formatDateTime, ticketStatusTag } from '../../utils/format'

const data = ref<DashboardData | null>(null)
const loading = ref(false)

const ticketStatusOrder = ['open', 'processing', 'resolved', 'closed']

async function load() {
  loading.value = true
  try {
    data.value = await fetchDashboard()
  } catch {
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="dashboard-page">
    <el-row :gutter="16">
      <el-col :span="6"><el-card shadow="never"><el-statistic title="今日会话数" :value="data?.today_sessions ?? 0" /></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><el-statistic title="近7日消息数" :value="data?.messages_last_7_days ?? 0" /></el-card></el-col>
      <el-col :span="6"><el-card shadow="never">
        <el-statistic title="满意度率" :value="((data?.satisfaction_rate ?? 0) * 100)" :precision="1">
          <template #suffix>%</template>
        </el-statistic>
      </el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><el-statistic title="待处理工单" :value="data?.open_tickets ?? 0" /></el-card></el-col>
    </el-row>
    <el-row :gutter="16" class="row">
      <el-col v-for="status in ticketStatusOrder" :key="status" :span="6">
        <el-card shadow="never">
          <el-statistic :title="`工单·${ticketStatusTag(status).label}`" :value="data?.ticket_status_counts?.[status] ?? 0" />
        </el-card>
      </el-col>
    </el-row>
    <el-row :gutter="16" class="row">
      <el-col :span="6"><el-card shadow="never"><el-statistic title="知识文件数" :value="data?.knowledge_files ?? 0" /></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><el-statistic title="已审核FAQ数" :value="data?.approved_faq ?? 0" /></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><el-statistic title="账号数" :value="data?.accounts ?? 0" /></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><el-statistic title="设备型号数" :value="data?.devices ?? 0" /></el-card></el-col>
    </el-row>
    <el-card shadow="never" class="row">
      <template #header>低库存预警</template>
      <el-table :data="data?.low_stock_devices ?? []" border>
        <el-table-column prop="model_id" label="型号ID" width="160" />
        <el-table-column prop="model_name" label="设备名称" min-width="180" />
        <el-table-column prop="total_stock" label="总库存" width="120" align="center" />
        <el-table-column prop="stock_threshold" label="库存阈值" width="120" align="center" />
        <el-table-column label="状态" width="120" align="center">
          <template #default>
            <el-tag type="danger">需补货</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!data?.low_stock_devices?.length && !loading" description="暂无低库存设备" :image-size="60" />
    </el-card>
    <div class="updated-at">数据更新时间：{{ formatDateTime(new Date().toISOString()) }}</div>
  </div>
</template>

<style scoped>
.dashboard-page {
  min-height: 100%;
}

.row {
  margin-top: 16px;
}

.updated-at {
  margin-top: 12px;
  color: #909399;
  font-size: 12px;
  text-align: right;
}
</style>
