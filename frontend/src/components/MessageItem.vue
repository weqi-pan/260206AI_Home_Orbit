<script setup lang="ts">
import { computed } from 'vue'
import { CaretBottom, CaretTop, Service, User } from '@element-plus/icons-vue'
import { sentimentTag } from '../utils/format'
import { renderMarkdown } from '../utils/markdown'
import type { ChatUiMessage } from '../types'

const props = defineProps<{ message: ChatUiMessage }>()
const emit = defineEmits<{ feedback: [value: 'like' | 'dislike'] }>()

const sentiment = computed(() => sentimentTag(props.message.sentiment))

// 只在内容真正变化时重算一次：放在模板里直接调用会在每次父组件重渲染时重解析整段 markdown。
// 流式期间关掉语法高亮——每个 token 都会重算一次，高亮结果随即被下一个 token 作废。
const rendered = computed(() => renderMarkdown(props.message.content, { highlight: !props.message.streaming }))
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
            <div class="md-body" v-html="rendered"></div>
            <span v-if="message.streaming" class="stream-cursor"></span>
          </template>
        </template>
        <template v-else>{{ message.content }}</template>
      </div>
      <!-- 审批卡挂在气泡正下方：它是这一轮回复的一部分，脱离气泡会看不出属于哪句话 -->
      <slot name="approvals"></slot>
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
  background: var(--orbit-violet);
}

.avatar.assistant {
  background: var(--signal-green);
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
  background: #e4e1ef;
  color: var(--device-ink);
}

.bubble.failed {
  border: 1px solid #fae2e3;
  background: #fdf1f1;
  color: var(--signal-red);
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
  background: var(--orbit-violet);
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
