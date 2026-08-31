<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { bindPurpose, createEndpoint, createModel, fetchEndpoints, fetchModels, runCapabilityTest, type CapabilityResult, type ModelEndpoint, type ModelProfile, type ModelProtocol, type ModelType } from '../../api/models'
import { fetchSetupStatus, type SetupStatus } from '../../api/setup'

const setup = ref<SetupStatus | null>(null)
const endpoints = ref<ModelEndpoint[]>([])
const models = ref<ModelProfile[]>([])
const endpoint = reactive({ protocol: 'openai_compatible' as ModelProtocol, display_name: '', base_url: '', api_key: '', timeout_seconds: 30, max_retries: 2, enabled: true })
const profile = reactive({ endpoint_id: '', model_name: '', model_type: 'chat' as ModelType, temperature: 0.2, max_tokens: 1024 as number | null, embedding_dimensions: null as number | null, use_responses_api: false, requests_per_minute: 60, agent_token_budget: null as number | null, enabled: true })
const latestEndpoint = ref<ModelEndpoint | null>(null)
const capability = ref<CapabilityResult | null>(null)
const busy = ref(false)
const message = ref('')
const selectedPurpose = ref<'primary_chat' | 'embedding'>('primary_chat')

const activeModel = computed(() => models.value.find((item) => item.id === (capability.value as CapabilityResult & { model_id?: string } | null)?.model_id))

async function refresh() {
  ;[setup.value, endpoints.value, models.value] = await Promise.all([fetchSetupStatus(), fetchEndpoints(), fetchModels()])
  if (!profile.endpoint_id && endpoints.value[0]) profile.endpoint_id = endpoints.value[0].id
}

async function saveEndpoint() {
  busy.value = true
  try {
    latestEndpoint.value = await createEndpoint({ ...endpoint, api_key: endpoint.api_key || undefined })
    endpoint.api_key = ''
    profile.endpoint_id = latestEndpoint.value.id
    message.value = 'Endpoint 已保存，凭据只显示末四位。'
    await refresh()
  } finally { busy.value = false }
}

async function saveProfile() {
  busy.value = true
  try {
    const created = await createModel({ ...profile })
    models.value.push(created)
    capability.value = null
    message.value = '模型档案已创建，请运行全部能力测试。'
  } finally { busy.value = false }
}

async function testProfile(model: ModelProfile) {
  busy.value = true
  try {
    const result = await runCapabilityTest(model.id)
    capability.value = Object.assign(result, { model_id: model.id })
    message.value = result.passed ? '全部必需能力已通过。' : `能力测试失败：${result.error_code}`
  } finally { busy.value = false }
}

async function bindTestedModel() {
  const model = activeModel.value
  if (!model || !capability.value?.passed) return
  await bindPurpose(selectedPurpose.value, model.id)
  message.value = `${selectedPurpose.value} 已绑定。`
  await refresh()
}

onMounted(refresh)
</script>

<template>
  <section class="setup-page" aria-labelledby="setup-title">
    <header class="page-head">
      <div><p class="eyebrow">INITIALIZATION / 初始化设置</p><h1 id="setup-title">模型控制台</h1></div>
      <div class="readiness" role="status"><span :class="['status-dot', { ready: setup?.ai_ready }]" aria-hidden="true"></span><div><strong>{{ setup?.ai_ready ? '核心 AI 已就绪' : '核心 AI 等待配置' }}</strong><p>{{ setup?.master_key_valid ? (setup?.embedding_ready ? '知识增强已启用' : '知识增强为可选能力，当前未启用') : '主密钥缺失或格式无效' }}</p></div></div>
    </header>
    <p class="lead">按连接、档案、证据、绑定四道检查完成初始化。每一步都留下可审查的运行证据，不回显凭据。</p>
    <p v-if="message" class="notice" role="status">{{ message }}</p>

    <div class="wizard-grid">
      <form data-step="endpoint" class="work-card" @submit.prevent="saveEndpoint">
        <div class="step-head"><span>连接</span><h2>配置 Endpoint</h2></div>
        <label>协议<select v-model="endpoint.protocol"><option value="openai_compatible">OpenAI-compatible</option><option value="anthropic">Anthropic</option></select></label>
        <label>显示名称<input v-model="endpoint.display_name" name="display_name" required></label>
        <label>Base URL<input v-model="endpoint.base_url" name="base_url" type="url" required></label>
        <label>API Key<input v-model="endpoint.api_key" name="api_key" type="password" autocomplete="new-password"></label>
        <div class="split"><label>超时 / 秒<input v-model.number="endpoint.timeout_seconds" type="number" min="1"></label><label>重试<input v-model.number="endpoint.max_retries" type="number" min="0"></label></div>
        <button :disabled="busy">保存加密连接</button>
        <p v-if="latestEndpoint" class="key-proof">已保存凭据 <code>{{ latestEndpoint.masked_api_key }}</code></p>
      </form>

      <form class="work-card" @submit.prevent="saveProfile">
        <div class="step-head"><span>档案</span><h2>创建模型档案</h2></div>
        <label>Endpoint<select v-model="profile.endpoint_id" required><option v-for="item in endpoints" :key="item.id" :value="item.id">{{ item.display_name }}</option></select></label>
        <label>模型名<input v-model="profile.model_name" required></label>
        <label>类型<select v-model="profile.model_type"><option value="chat">Chat</option><option value="embedding" :disabled="endpoints.find(e => e.id === profile.endpoint_id)?.protocol === 'anthropic'">Embedding</option></select></label>
        <label v-if="profile.model_type === 'embedding'">向量维度<input v-model.number="profile.embedding_dimensions" type="number" min="1" required></label>
        <div class="split"><label>温度<input v-model.number="profile.temperature" type="number" min="0" max="2" step="0.1"></label><label>每分钟请求<input v-model.number="profile.requests_per_minute" type="number" min="1"></label></div>
        <button :disabled="busy || !profile.endpoint_id">创建模型档案</button>
      </form>
    </div>

    <section class="evidence-panel">
      <div class="step-head"><span>证据</span><h2>能力测试与用途绑定</h2></div>
      <div v-if="!models.length" class="empty">创建档案后才能运行能力测试。</div>
      <article v-for="model in models" :key="model.id" class="model-row">
        <div><strong>{{ model.model_name }}</strong><p>{{ model.model_type }} · {{ model.enabled ? '已启用' : '未启用' }}</p></div>
        <button class="secondary" :disabled="busy" @click="testProfile(model)">运行全部能力测试</button>
      </article>
      <div v-if="capability" class="proof" :class="{ failed: !capability.passed }">
        <strong>{{ capability.passed ? '能力证据通过' : '能力证据未通过' }}</strong>
        <ul><li v-for="(status, name) in capability.capabilities" :key="name"><code>{{ name }}</code><span>{{ status }}</span></li></ul>
        <div class="binding"><select v-model="selectedPurpose"><option value="primary_chat">主对话</option><option value="embedding">Embedding</option></select><button :disabled="!capability.passed" @click="bindTestedModel">绑定用途</button></div>
      </div>
    </section>

    <section class="requirements">
      <h2>核心 AI 就绪检查</h2>
      <ul><li v-for="purpose in setup?.required_purposes" :key="purpose"><code>{{ purpose }}</code><span>{{ setup?.configured_purposes.includes(purpose) ? '已验证并绑定' : '等待配置' }}</span></li></ul>
      <h2 class="optional-title">可选能力</h2>
      <ul><li v-for="purpose in setup?.optional_purposes" :key="purpose"><code>{{ purpose }}</code><span>{{ setup?.configured_purposes.includes(purpose) ? '已验证并绑定' : '等待配置' }}</span></li></ul>
    </section>
  </section>
</template>

<style scoped>
.setup-page { max-width: 1120px; margin: auto; padding: 48px 32px 80px; color: var(--device-ink); }
.page-head { display: flex; justify-content: space-between; gap: 32px; align-items: end; border-bottom: 1px solid #cbc8db; padding-bottom: 24px; }
.eyebrow { margin: 0; font: 600 12px var(--font-utility); letter-spacing: .15em; color: var(--orbit-violet); }
h1 { margin: 10px 0 0; font: 700 52px/1 var(--font-display); } h2 { margin: 4px 0 0; font: 700 24px var(--font-display); }
.lead { max-width: 760px; margin: 22px 0 30px; line-height: 1.8; color: #625f76; }
.readiness { min-width: 250px; display: flex; align-items: center; gap: 13px; padding: 16px 18px; background: white; border: 1px solid #d9d7e5; border-radius: 14px; }.readiness p { margin: 4px 0 0; font: 12px var(--font-utility); }.status-dot { width: 14px; height: 14px; border-radius: 50%; background: var(--charge-coral); }.status-dot.ready { background: #2f8f65; }
.notice { padding: 12px 16px; border-left: 4px solid var(--charge-coral); background: #fff5f2; }
.wizard-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }.work-card,.evidence-panel,.requirements { background: white; border: 1px solid #d9d7e5; border-radius: 16px; padding: 24px; box-shadow: 0 12px 32px rgba(33,29,79,.05); }.step-head { display: flex; gap: 12px; align-items: baseline; margin-bottom: 20px; }.step-head>span { font: 600 11px var(--font-utility); text-transform: uppercase; color: var(--charge-coral); }
label { display: grid; gap: 7px; margin: 14px 0; font: 600 12px var(--font-utility); } input,select { min-height: 44px; border: 1px solid #bbb8cd; border-radius: 9px; padding: 0 12px; color: var(--device-ink); background: var(--surface-white); } input:focus-visible,select:focus-visible,button:focus-visible { outline: 3px solid #bbb5ed; outline-offset: 2px; }.split { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
button { min-height: 44px; border: 0; border-radius: 9px; padding: 0 18px; color: white; background: var(--device-ink); font-weight: 700; cursor: pointer; }button:disabled { opacity: .48; cursor: not-allowed; }.secondary { background: var(--orbit-violet); }.key-proof { font: 13px var(--font-utility); }.evidence-panel,.requirements { margin-top: 20px; }.model-row { display: flex; justify-content: space-between; gap: 20px; align-items: center; border-top: 1px solid var(--orbit-mist); padding: 16px 0; }.model-row p { margin: 4px 0 0; color: #77728c; }.proof { margin-top: 16px; padding: 18px; border: 1px solid #91c8ad; background: #f3fbf6; border-radius: 12px; }.proof.failed { border-color: #e2a89f; background: #fff5f2; }.proof ul,.requirements ul { list-style: none; padding: 0; }.proof li,.requirements li { display: flex; justify-content: space-between; padding: 10px 0; border-top: 1px solid #d9d7e5; }.binding { display: flex; justify-content: flex-end; gap: 10px; }.empty { color: #77728c; padding: 18px 0; }
.optional-title { margin-top: 24px; }
@media (max-width: 760px) { .setup-page { padding: 28px 18px 72px; }.page-head { align-items: stretch; flex-direction: column; }.readiness { min-width: 0; } h1 { font-size: 40px; }.wizard-grid { grid-template-columns: 1fr; }.split { grid-template-columns: 1fr; }.model-row { align-items: stretch; flex-direction: column; }.binding { flex-direction: column; } }
</style>
