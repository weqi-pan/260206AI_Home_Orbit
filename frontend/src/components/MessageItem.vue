<script setup lang="ts">
import { computed } from 'vue'
import { CaretBottom, CaretTop, Service, User } from '@element-plus/icons-vue'
import { sentimentTag } from '../utils/format'
import { renderMarkdown } from '../utils/markdown'
import type { ChatUiMessage } from '../types'

const props = defineProps<{ message: ChatUiMessage }>()
const emit = defineEmits<{ feedback: [value: 'like' | 'dislike'] }>()

const sentiment = computed(() => sentimentTag(props.message.sentiment))
</script>

<template>
  <div class="message" :class="message.role">
    <div class="avatar" :class="message.role">
      <el-icon v-if="message.role === 'user'"><User /></el-icon>
      <el-icon v-else><Service /></el-icon>
    </div>
    <div class="body">
      <div class="bubble" :class="{ failed: message.failed }">
        <template v-if="message.role === 'assistant'">
          <div v-if="message.streaming && !message.content" class="thinking">正在思考…</div>
          <template v-else>
            <div class="md-body" v-html="renderMarkdown(message.content)"></div>
            <span v-if="message.streaming" class="stream-cursor"></span>
          </template>
        </template>
        <template v-else>{{ message.content }}</template>
      </div>
      <div v-if="message.role === 'user' || message.id" class="meta">
        <el-tag v-if="message.role === 'user' && message.sentiment" size="small" :type="sentiment.type">
          {{ sentiment.label }}
        </el-tag>
        <template v-if="message.role === 'assistant' && message.id">
          <el-button
            size="small"
            text
            :type="message.feedback === 'like' ? 'primary' : ''"
            @click="emit('feedback', 'like')"
          >
            <el-icon><CaretTop /></el-icon>
            <span>有帮助</span>
          </el-button>
          <el-button
            size="small"
            text
            :type="message.feedback === 'dislike' ? 'danger' : ''"
            @click="emit('feedback', 'dislike')"
          >
            <el-icon><CaretBottom /></el-icon>
            <span>无帮助</span>
          </el-button>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.message {
  display: flex;
  gap: 10px;
  margin-bottom: 18px;
}

.message.user {
  flex-direction: row-reverse;
}

.avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  flex-shrink: 0;
  color: #fff;
  font-size: 16px;
}

.avatar.user {
  background: #409eff;
}

.avatar.assistant {
  background: #67c23a;
}

.body {
  max-width: 76%;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.message.user .body {
  align-items: flex-end;
}

.bubble {
  padding: 10px 14px;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
  white-space: pre-wrap;
}

.message.user .bubble {
  background: #ecf5ff;
  color: #303133;
}

.bubble.failed {
  border: 1px solid #fde2e2;
  background: #fef0f0;
  color: #f56c6c;
}

.thinking {
  color: #909399;
}

.stream-cursor {
  display: inline-block;
  width: 2px;
  height: 14px;
  margin-left: 2px;
  vertical-align: -2px;
  background: #409eff;
  animation: blink 0.8s infinite;
}

@keyframes blink {
  50% {
    opacity: 0;
  }
}

.meta {
  display: flex;
  align-items: center;
  gap: 6px;
}

.meta :deep(.el-button + .el-button) {
  margin-left: 0;
}

.meta :deep(.el-button span) {
  font-size: 12px;
}
</style>
