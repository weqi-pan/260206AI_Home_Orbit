<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import type { UploadRequestOptions } from 'element-plus/es/components/upload/index.mjs'
import { Delete, RefreshRight, UploadFilled } from '@element-plus/icons-vue'
import {
  MAX_UPLOAD_SIZE,
  deleteKnowledgeFile,
  fetchFaqs,
  fetchKnowledgeFiles,
  generateFaq,
  rebuildVectorStore,
  reviewFaq,
  uploadKnowledgeFile,
  type FaqPairInfo,
  type KnowledgeFileInfo,
} from '../../api/knowledge'
import { formatDateTime } from '../../utils/format'

const activeTab = ref<'files' | 'faq'>('files')
const files = ref<KnowledgeFileInfo[]>([])
const faqs = ref<FaqPairInfo[]>([])
const faqFilter = ref<'' | 'pending' | 'approved' | 'rejected'>('pending')
const loading = ref(false)
const rebuilding = ref(false)
const generating = ref('')

async function loadFiles() {
  loading.value = true
  try {
    files.value = await fetchKnowledgeFiles()
  } catch {
  } finally {
    loading.value = false
  }
}

async function loadFaqs() {
  loading.value = true
  try {
    faqs.value = await fetchFaqs(faqFilter.value || undefined)
  } catch {
  } finally {
    loading.value = false
  }
}

async function load() {
  if (activeTab.value === 'files') await loadFiles()
  else await loadFaqs()
}

function validateFile(file: File): boolean {
  const suffix = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
  if (suffix !== '.pdf' && suffix !== '.txt') {
    ElMessage.error('仅支持 PDF/TXT 文件')
    return false
  }
  if (file.size > MAX_UPLOAD_SIZE) {
    ElMessage.error('文件不能超过20MB')
    return false
  }
  return true
}

async function handleUpload(options: UploadRequestOptions) {
  const file = options.file as File
  if (!validateFile(file)) return
  loading.value = true
  try {
    await uploadKnowledgeFile(file)
    ElMessage.success('上传成功，已写入向量库')
    await loadFiles()
  } catch {
  } finally {
    loading.value = false
  }
}

async function handleDelete(row: KnowledgeFileInfo) {
  try {
    await ElMessageBox.confirm(`删除文件「${row.filename}」将同时从向量库移除并拒绝其关联FAQ，确认删除？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteKnowledgeFile(row.id)
    ElMessage.success('文件已删除')
    await loadFiles()
  } catch {
  }
}

async function handleRebuild() {
  try {
    await ElMessageBox.confirm('将根据全部有效文件与已审FAQ重建向量库，耗时可能较长，确认执行？', '重建向量库', { type: 'warning' })
  } catch {
    return
  }
  rebuilding.value = true
  try {
    await rebuildVectorStore()
    ElMessage.success('向量库重建完成')
  } catch {
  } finally {
    rebuilding.value = false
  }
}

async function handleGenerate(row: KnowledgeFileInfo) {
  generating.value = row.id
  try {
    const created = await generateFaq(row.id)
    ElMessage.success(`已生成 ${created.length} 条待审核FAQ`)
    if (activeTab.value === 'faq') await loadFaqs()
  } catch {
  } finally {
    generating.value = ''
  }
}

async function handleReview(row: FaqPairInfo, status: 'approved' | 'rejected') {
  try {
    const updated = await reviewFaq(row.id, status)
    Object.assign(row, updated)
    ElMessage.success(status === 'approved' ? '已通过并写入向量库' : '已拒绝')
    if (faqFilter.value) await loadFaqs()
  } catch {
  }
}

const faqTitle = computed(() => (faqFilter.value === 'pending' ? '待审核FAQ' : 'FAQ列表'))

onMounted(load)
</script>

<template>
  <div class="knowledge-page">
    <el-tabs v-model="activeTab" @tab-change="load()">
      <el-tab-pane label="知识文件" name="files">
        <div class="page-toolbar">
          <el-upload :show-file-list="false" accept=".pdf,.txt" :http-request="handleUpload" :disabled="loading">
            <el-button type="primary" :icon="UploadFilled" :loading="loading">上传文件（PDF/TXT ≤ 20MB）</el-button>
          </el-upload>
          <div class="toolbar-spacer"></div>
          <el-button :icon="RefreshRight" :loading="rebuilding" @click="handleRebuild()">重建向量库</el-button>
        </div>
        <el-table v-loading="loading" :data="files" border>
          <el-table-column prop="filename" label="文件名" min-width="220" show-overflow-tooltip />
          <el-table-column prop="file_type" label="类型" width="80" align="center" />
          <el-table-column prop="file_md5" label="MD5" width="280" show-overflow-tooltip />
          <el-table-column label="上传时间" width="170">
            <template #default="{ row }">{{ formatDateTime((row as KnowledgeFileInfo).created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="240" fixed="right">
            <template #default="{ row }">
              <el-button size="small" text type="primary" :loading="generating === (row as KnowledgeFileInfo).id"
                @click="handleGenerate(row as KnowledgeFileInfo)">生成FAQ</el-button>
              <el-button size="small" text type="danger" :icon="Delete" @click="handleDelete(row as KnowledgeFileInfo)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!files.length && !loading" description="暂无知识文件" />
      </el-tab-pane>
      <el-tab-pane label="FAQ审核" name="faq">
        <div class="page-toolbar">
          <el-radio-group v-model="faqFilter" @change="loadFaqs()">
            <el-radio-button value="pending">待审核</el-radio-button>
            <el-radio-button value="approved">已通过</el-radio-button>
            <el-radio-button value="rejected">已拒绝</el-radio-button>
            <el-radio-button value="">全部</el-radio-button>
          </el-radio-group>
        </div>
        <el-table v-loading="loading" :data="faqs" border>
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="faq-answer">答案：{{ (row as FaqPairInfo).answer }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="question" label="问题" min-width="240" show-overflow-tooltip />
          <el-table-column prop="answer" label="答案" min-width="240" show-overflow-tooltip />
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="(row as FaqPairInfo).status === 'approved' ? 'success' : (row as FaqPairInfo).status === 'rejected' ? 'danger' : 'warning'">
                {{ (row as FaqPairInfo).status === 'approved' ? '已通过' : (row as FaqPairInfo).status === 'rejected' ? '已拒绝' : '待审核' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="创建时间" width="170">
            <template #default="{ row }">{{ formatDateTime((row as FaqPairInfo).created_at) }}</template>
          </el-table-column>
          <el-table-column v-if="faqFilter" label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button size="small" text type="success" :disabled="(row as FaqPairInfo).status === 'approved'"
                @click="handleReview(row as FaqPairInfo, 'approved')">通过</el-button>
              <el-button size="small" text type="danger" :disabled="(row as FaqPairInfo).status === 'rejected'"
                @click="handleReview(row as FaqPairInfo, 'rejected')">拒绝</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!faqs.length && !loading" :description="`暂无${faqTitle}`" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.knowledge-page {
  background: #fff;
  border-radius: 6px;
  padding: 16px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}

.page-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.toolbar-spacer {
  flex: 1;
}

.faq-answer {
  padding: 8px 16px;
  color: #606266;
  white-space: pre-wrap;
}
</style>
