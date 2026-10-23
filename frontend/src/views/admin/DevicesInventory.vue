<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import type { FormInstance, FormRules } from 'element-plus/es/components/form/index.mjs'
import { Plus } from '@element-plus/icons-vue'
import {
  createDevice,
  deleteDevice,
  fetchAdminDevices,
  fetchAdminHouseholds,
  fetchInventory,
  updateDevice,
  upsertInventory,
  type AdminDevice,
  type AdminHousehold,
  type DevicePayload,
  type InventoryRow,
} from '../../api/admin'

const devices = ref<AdminDevice[]>([])
const inventoryRows = ref<InventoryRow[]>([])
const households = ref<AdminHousehold[]>([])
const householdFilter = ref('')
const loading = ref(false)

const householdName = computed(() => {
  const map = new Map(households.value.map((item) => [item.id, item.name]))
  return (id: string) => map.get(id) ?? id.slice(0, 8)
})

const deviceDialogVisible = ref(false)
const editing = ref(false)
const editingHousehold = ref('')
const saving = ref(false)
const deviceFormRef = ref<FormInstance>()
const deviceForm = reactive<DevicePayload & { household_id: string }>({
  household_id: '',
  model_id: '',
  model_name: '',
  category: '',
  specs: '',
  description: '',
  price: 0,
  features: '',
})

const deviceRules: FormRules = {
  household_id: [{ required: true, message: '请选择所属家庭', trigger: 'change' }],
  model_id: [
    { required: true, message: '请输入型号ID', trigger: 'blur' },
    { pattern: /^\S+$/, message: '型号ID不能包含空格', trigger: 'blur' },
  ],
  model_name: [{ required: true, message: '请输入设备名称', trigger: 'blur' }],
  category: [{ required: true, message: '请输入类别', trigger: 'blur' }],
  specs: [{ required: true, message: '请输入规格', trigger: 'blur' }],
  description: [{ required: true, message: '请输入描述', trigger: 'blur' }],
  price: [{ required: true, message: '请输入价格', trigger: 'blur' }],
  features: [{ required: true, message: '请输入特性', trigger: 'blur' }],
}

async function load() {
  loading.value = true
  try {
    if (!households.value.length) households.value = await fetchAdminHouseholds()
    const scope = householdFilter.value || undefined
    const [deviceList, inventoryList] = await Promise.all([fetchAdminDevices(scope), fetchInventory(scope)])
    devices.value = deviceList
    inventoryRows.value = inventoryList
  } catch {
  } finally {
    loading.value = false
  }
}

function formatTime(value: string) {
  return value ? new Date(value).toLocaleString() : '-'
}

function openCreate() {
  editing.value = false
  editingHousehold.value = ''
  deviceFormRef.value?.resetFields()
  Object.assign(deviceForm, {
    household_id: householdFilter.value,
    model_id: '',
    model_name: '',
    category: '',
    specs: '',
    description: '',
    price: 0,
    features: '',
  })
  deviceDialogVisible.value = true
}

function openEdit(row: AdminDevice) {
  editing.value = true
  editingHousehold.value = row.household_id
  deviceFormRef.value?.resetFields()
  Object.assign(deviceForm, row)
  deviceDialogVisible.value = true
}

async function saveDevice() {
  const valid = await deviceFormRef.value?.validate().then(() => true).catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    if (editing.value) {
      await updateDevice(editingHousehold.value, deviceForm.model_id, deviceForm)
      ElMessage.success('设备已更新')
    } else {
      await createDevice(deviceForm.household_id, deviceForm)
      ElMessage.success('设备已创建')
    }
    deviceDialogVisible.value = false
    await load()
  } catch {
  } finally {
    saving.value = false
  }
}

async function removeDevice(row: AdminDevice) {
  try {
    await ElMessageBox.confirm(`删除设备「${row.model_name}」将同时删除其库存与补货通知，确认删除？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteDevice(row.household_id, row.model_id)
    ElMessage.success('设备已删除')
    await load()
  } catch {
  }
}

const inventoryDialogVisible = ref(false)
const inventorySaving = ref(false)
const inventoryFormRef = ref<FormInstance>()
const inventoryForm = reactive({ household_id: '', model_id: '', model_name: '', warehouse: '', stock: 0, min_stock: 0 })

const inventoryRules: FormRules = {
  model_id: [{ required: true, message: '请选择型号', trigger: 'change' }],
  warehouse: [{ required: true, message: '请输入仓库名称', trigger: 'blur' }],
  stock: [{ required: true, message: '请输入库存数量', trigger: 'blur' }],
  min_stock: [{ required: true, message: '请输入库存阈值', trigger: 'blur' }],
}

function openInventory(row: AdminDevice) {
  inventoryFormRef.value?.resetFields()
  Object.assign(inventoryForm, {
    household_id: row.household_id,
    model_id: row.model_id,
    model_name: row.model_name,
    warehouse: '',
    stock: 0,
    min_stock: 0,
  })
  inventoryDialogVisible.value = true
}

function openInventoryRow(row: InventoryRow) {
  inventoryFormRef.value?.resetFields()
  const device = devices.value.find((item) => item.model_id === row.model_id && item.household_id === row.household_id)
  Object.assign(inventoryForm, {
    household_id: row.household_id,
    model_id: row.model_id,
    model_name: device?.model_name ?? row.model_id,
    warehouse: row.warehouse,
    stock: row.stock,
    min_stock: row.min_stock,
  })
  inventoryDialogVisible.value = true
}

async function saveInventory() {
  const valid = await inventoryFormRef.value?.validate().then(() => true).catch(() => false)
  if (!valid) return
  inventorySaving.value = true
  try {
    await upsertInventory(inventoryForm.household_id, {
      model_id: inventoryForm.model_id,
      warehouse: inventoryForm.warehouse.trim(),
      stock: inventoryForm.stock,
      min_stock: inventoryForm.min_stock,
    })
    ElMessage.success('库存已保存')
    inventoryDialogVisible.value = false
    await load()
  } catch {
  } finally {
    inventorySaving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="devices-page">
    <div class="page-toolbar">
      <h3 class="page-title">设备管理</h3>
      <div class="toolbar-spacer"></div>
      <el-select v-model="householdFilter" clearable placeholder="全部家庭" style="width: 180px" @change="load()">
        <el-option v-for="home in households" :key="home.id" :label="home.name" :value="home.id" />
      </el-select>
      <el-button @click="load()">刷新</el-button>
      <el-button type="primary" :icon="Plus" @click="openCreate()">新增设备</el-button>
    </div>
    <el-table v-loading="loading" :data="devices" border>
      <el-table-column prop="model_id" label="型号ID" width="140" />
      <el-table-column label="所属家庭" width="150">
        <template #default="{ row }">
          {{ householdName((row as AdminDevice).household_id) }}
        </template>
      </el-table-column>
      <el-table-column prop="model_name" label="设备名称" min-width="150" />
      <el-table-column prop="category" label="类别" width="130" />
      <el-table-column prop="specs" label="规格" min-width="140" show-overflow-tooltip />
      <el-table-column prop="price" label="价格(¥)" width="110" align="right" />
      <el-table-column prop="features" label="特性" min-width="140" show-overflow-tooltip />
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="openEdit(row as AdminDevice)">编辑</el-button>
          <el-button size="small" text type="warning" @click="openInventory(row as AdminDevice)">库存设置</el-button>
          <el-button size="small" text type="danger" @click="removeDevice(row as AdminDevice)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!devices.length && !loading" description="暂无设备" />

    <h3 class="page-title section">型号库存</h3>
    <el-table v-loading="loading" :data="inventoryRows" border>
      <el-table-column prop="model_id" label="型号ID" width="140" />
      <el-table-column label="所属家庭" width="150">
        <template #default="{ row }">
          {{ householdName((row as InventoryRow).household_id) }}
        </template>
      </el-table-column>
      <el-table-column prop="warehouse" label="仓库" width="140" />
      <el-table-column label="库存数量" width="120" align="center">
        <template #default="{ row }">
          <el-tag :type="(row as InventoryRow).stock > (row as InventoryRow).min_stock ? 'success' : 'danger'">
            {{ (row as InventoryRow).stock }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="min_stock" label="库存阈值" width="110" align="center" />
      <el-table-column label="更新时间" min-width="170">
        <template #default="{ row }">
          {{ formatTime((row as InventoryRow).updated_at) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="warning" @click="openInventoryRow(row as InventoryRow)">
            编辑库存/阈值
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!inventoryRows.length && !loading" description="暂无库存数据" />

    <el-dialog v-model="deviceDialogVisible" :title="editing ? '编辑设备' : '新增设备'" width="560px">
      <el-form ref="deviceFormRef" :model="deviceForm" :rules="deviceRules" label-width="90px">
        <el-form-item v-if="!editing" label="所属家庭" prop="household_id">
          <el-select v-model="deviceForm.household_id" placeholder="选择设备所属家庭" style="width: 100%">
            <el-option v-for="home in households" :key="home.id" :label="home.name" :value="home.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="型号ID" prop="model_id">
          <el-input v-model="deviceForm.model_id" :disabled="editing" placeholder="唯一标识，如 R1-Lite" />
        </el-form-item>
        <el-form-item label="设备名称" prop="model_name">
          <el-input v-model="deviceForm.model_name" />
        </el-form-item>
        <el-form-item label="类别" prop="category">
          <el-input v-model="deviceForm.category" />
        </el-form-item>
        <el-form-item label="规格" prop="specs">
          <el-input v-model="deviceForm.specs" />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input v-model="deviceForm.description" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="价格" prop="price">
          <el-input-number v-model="deviceForm.price" :min="0" :precision="2" />
        </el-form-item>
        <el-form-item label="特性" prop="features">
          <el-input v-model="deviceForm.features" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deviceDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveDevice()">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="inventoryDialogVisible" :title="`库存设置 · ${inventoryForm.model_name}`" width="480px">
      <el-form ref="inventoryFormRef" :model="inventoryForm" :rules="inventoryRules" label-width="90px">
        <el-form-item label="所属家庭">
          <el-input :model-value="householdName(inventoryForm.household_id)" disabled />
        </el-form-item>
        <el-form-item label="型号" prop="model_id">
          <el-input v-model="inventoryForm.model_id" disabled />
        </el-form-item>
        <el-form-item label="仓库" prop="warehouse">
          <el-input v-model="inventoryForm.warehouse" placeholder="如：北京仓 / 上海仓，输入新仓库即为新增" />
        </el-form-item>
        <el-form-item label="库存数量" prop="stock">
          <el-input-number v-model="inventoryForm.stock" :min="0" />
        </el-form-item>
        <el-form-item label="库存阈值" prop="min_stock">
          <el-input-number v-model="inventoryForm.min_stock" :min="0" />
        </el-form-item>
      </el-form>
      <p class="dim tip">按「家庭+型号+仓库」保存，已存在则覆盖更新；输入新仓库名称即可新增仓库行。</p>
      <template #footer>
        <el-button @click="inventoryDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="inventorySaving" @click="saveInventory()">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.devices-page {
  background: #fff;
  border-radius: 6px;
  padding: 16px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}

/* 这三个页面比全局 .page-title（18px）小一档 */
.page-title { font-size: 16px; }

.section {
  margin: 24px 0 16px;
}

.tip {
  font-size: 12px;
  margin: 0;
}
</style>
