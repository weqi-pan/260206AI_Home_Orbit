<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { restockNotify, searchDevices, type DeviceInfo } from '../api/devices'

const router = useRouter()
const keyword = ref('')
const devices = ref<DeviceInfo[]>([])
const selected = ref<DeviceInfo[]>([])
const loading = ref(false)

async function loadDevices() {
  loading.value = true
  try {
    devices.value = await searchDevices(keyword.value.trim())
  } catch {
  } finally {
    loading.value = false
  }
}

function handleSelectionChange(rows: DeviceInfo[]) {
  selected.value = rows
}

function stockType(stock: number): 'danger' | 'warning' | 'success' {
  if (stock <= 0) return 'danger'
  if (stock <= 5) return 'warning'
  return 'success'
}

async function handleRestock(row: DeviceInfo) {
  try {
    await restockNotify(row.model_id)
    ElMessage.success('已登记到货通知，到货后可在工单中查看处理结果')
  } catch {
  }
}

function goCompare() {
  if (selected.value.length < 2) {
    ElMessage.warning('请至少勾选 2 个设备')
    return
  }
  if (selected.value.length > 4) {
    ElMessage.warning('最多支持 4 个设备对比')
    return
  }
  const ids = selected.value.map((item) => item.model_id).join(',')
  void router.push({ path: '/devices/compare', query: { ids } })
}

onMounted(() => {
  void loadDevices()
})
</script>

<template>
  <div class="device-page">
    <div class="page-toolbar">
      <el-input
        v-model="keyword"
        class="search-input"
        placeholder="搜索型号 / 名称 / 类别 / 功能"
        clearable
        @keyup.enter="loadDevices()"
        @clear="loadDevices()"
      />
      <el-button type="primary" :icon="Search" :loading="loading" @click="loadDevices()">搜索</el-button>
      <div class="toolbar-spacer"></div>
      <el-button type="primary" plain :disabled="selected.length < 2" @click="goCompare">
        对比所选（{{ selected.length }}/4）
      </el-button>
    </div>
    <el-table v-loading="loading" :data="devices" row-key="model_id" border @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="45" reserve-selection />
      <el-table-column prop="model_id" label="型号" width="110" />
      <el-table-column prop="model_name" label="名称" min-width="200" show-overflow-tooltip />
      <el-table-column prop="category" label="类别" width="130" />
      <el-table-column label="价格" width="100">
        <template #default="{ row }">￥{{ row.price.toFixed(2) }}</template>
      </el-table-column>
      <el-table-column label="库存" width="90" align="center">
        <template #default="{ row }">
          <el-tag :type="stockType(row.total_stock)">{{ row.total_stock > 0 ? row.total_stock : '缺货' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="features" label="功能特性" min-width="220" show-overflow-tooltip />
      <el-table-column label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.total_stock <= 0" size="small" type="warning" @click="handleRestock(row as DeviceInfo)">
            到货通知
          </el-button>
          <span v-else class="dim">-</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!devices.length && !loading" description="未找到匹配设备" />
  </div>
</template>

<style scoped>
.device-page {
  background: #fff;
  border-radius: 6px;
  padding: 16px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}

.search-input {
  width: 300px;
}

.dim {
  color: #c0c4cc;
}
</style>
