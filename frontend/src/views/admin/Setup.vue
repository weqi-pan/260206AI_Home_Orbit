<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchSetupStatus, type SetupStatus } from '../../api/platform'

const setup = ref<SetupStatus | null>(null)
onMounted(async () => { setup.value = await fetchSetupStatus() })
</script>

<template>
  <section class="setup-page" aria-labelledby="setup-title">
    <p class="eyebrow">INITIALIZATION / 初始化设置</p>
    <h1 id="setup-title">模型运行准备</h1>
    <p class="lead">Phase 1 建立稳定门禁合同。Endpoint、密钥加密、模型测试与用途绑定将在下一阶段提供。</p>
    <div class="readiness" role="status">
      <span :class="['readiness-mark', { ready: setup?.ready }]" aria-hidden="true"></span>
      <div><strong>{{ setup?.ready ? '平台已就绪' : '等待模型配置' }}</strong><p>{{ setup?.error_code ?? '所有必需用途均已绑定' }}</p></div>
    </div>
    <section class="requirements" aria-labelledby="requirements-title">
      <h2 id="requirements-title">必需用途</h2>
      <ul><li v-for="purpose in setup?.required_purposes" :key="purpose"><code>{{ purpose }}</code><span>{{ setup?.configured_purposes.includes(purpose) ? '已配置' : '未配置' }}</span></li></ul>
    </section>
  </section>
</template>

<style scoped>
.setup-page { max-width: 840px; margin: auto; padding: 64px 32px; }
.eyebrow { font: 600 12px var(--font-utility); letter-spacing: .15em; color: var(--orbit-violet); }
h1 { margin: 12px 0; font: 700 52px/1 var(--font-display); color: var(--device-ink); }.lead { max-width: 700px; line-height: 1.8; color: #625f76; }
.readiness { display: flex; align-items: center; gap: 18px; margin-top: 38px; padding: 24px; border: 1px solid #d9d7e5; border-radius: 16px; background: white; }.readiness p { margin: 5px 0 0; font-family: var(--font-utility); }
.readiness-mark { width: 18px; height: 18px; border-radius: 50%; background: var(--charge-coral); }.readiness-mark.ready { background: var(--orbit-violet); }
.requirements { margin-top: 24px; padding: 24px; background: white; border-radius: 16px; }.requirements h2 { margin-top: 0; }.requirements ul { padding: 0; list-style: none; }.requirements li { display: flex; justify-content: space-between; min-height: 52px; align-items: center; border-top: 1px solid var(--orbit-mist); }.requirements code { font-family: var(--font-utility); }
</style>
