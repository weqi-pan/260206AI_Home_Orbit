<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { compareDevices, type DeviceInfo } from '../api/devices'

const route = useRoute()
const router = useRouter()
const devices = ref<DeviceInfo[]>([])
const loading = ref(false)
const errorMessage = ref('')

const ids = computed(() => {
  const raw = route.query.ids
  return (typeof raw === 'string' ? raw : '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
})

function toLines(value: string): string {
  return value
    .split(';')
    .map((item) => item.trim())
    .filter(Boolean)
    .join('\n')
}

const rows = computed(() => {
  if (!devices.value.length) return []
  return [
    { attr: '型号', values: devices.value.map((d) => d.model_id) },
    { attr: '名称', values: devices.value.map((d) => d.model_name) },
    { attr: '类别', values: devices.value.map((d) => d.category) },
    { attr: '价格', values: devices.value.map((d) => `￥${d.price.toFixed(2)}`) },
    { attr: '总库存', values: devices.value.map((d) => (d.total_stock > 0 ? String(d.total_stock) : '缺货')) },
    { attr: '规格参数', values: devices.value.map((d) => toLines(d.specs)) },
    { attr: '功能特性', values: devices.value.map((d) => toLines(d.features)) },
    { attr: '描述', values: devices.value.map((d) => d.description) },
  ]
})

async function load() {
  if (ids.value.length < 2) {
    errorMessage.value = '请先从设备列表勾选至少 2 个设备'
    return
  }
  if (ids.value.length > 4) {
    errorMessage.value = '最多支持 4 个设备对比'
    return
  }
  loading.value = true
  try {
    devices.value = await compareDevices(ids.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '加载对比数据失败'
  } finally {
    loading.value = false
  }
}

function goBack() {
  void router.push('/devices')
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="compare-page">
    <div class="page-toolbar">
      <h3 class="page-title">设备对比</h3>
      <div class="toolbar-spacer"></div>
      <el-button @click="goBack()">返回设备列表</el-button>
    </div>
    <el-alert v-if="errorMessage" :title="errorMessage" type="warning" :closable="false" show-icon />
    <el-table v-else v-loading="loading" :data="rows" border>
      <el-table-column prop="attr" label="属性" width="120" fixed />
      <el-table-column
        v-for="(device, index) in devices"
        :key="device.model_id"
        :label="device.model_name"
        min-width="200"
      >
        <template #default="{ row }">
          <span class="pre-line">{{ row.values[index] }}</span>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.compare-page {
  background: #fff;
  border-radius: 6px;
  padding: 16px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}

.pre-line {
  white-space: pre-line;
}
</style>
