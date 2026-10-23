<script setup lang="ts">
defineProps<{
  /** 错误文案；由 useRequest 的 error 透传 */
  message: string
  retrying?: boolean
}>()
const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <!-- role="alert" 让读屏器在数据加载失败时立即播报，而不是停在空白页 -->
  <div class="page-error" role="alert" :data-testid="'page-error'">
    <p class="page-error-message">{{ message }}</p>
    <button type="button" class="page-error-retry" :disabled="retrying" @click="emit('retry')">
      {{ retrying ? '重试中…' : '重试' }}
    </button>
  </div>
</template>
