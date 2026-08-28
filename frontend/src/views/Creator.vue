<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  createDraft, deleteKnowledge, fetchCeilingTools, fetchDraft, fetchDrafts, fetchTrial,
  generateCard, publishDraft, runTrial, updateDraft, uploadKnowledge, validateDraft,
} from '../api/creator'
import { RISK_LABELS, advancedJson, errorText, isSelectableRisk } from '../creator/card'
import type { CeilingTool, CreatorDraftDetail, CreatorDraftSummary, TrialSummary, ValidationReport } from '../creator/types'

const drafts = ref<CreatorDraftSummary[]>([])
const tools = ref<CeilingTool[]>([])
const current = ref<CreatorDraftDetail | null>(null)
const description = ref('')
const generating = ref(false)
const modelUnready = ref(false)
const advancedText = ref('')
const advancedOpen = ref(false)
const confirmedKeys = ref<string[]>([])
const trial = ref<TrialSummary | null>(null)
const runningTrial = ref(false)
const publishing = ref(false)
const uploading = ref(false)
const newTitle = ref('')

const card = computed(() => current.value?.intent_card ?? null)
const validation = computed<ValidationReport | null>(() => current.value?.validation ?? null)
const selectedTools = computed<string[]>(() => card.value?.tools ?? [])
const reversibleTools = computed(() => tools.value.filter((tool) => tool.risk_level !== 'low' && selectedTools.value.includes(tool.key)))

async function refreshDrafts() {
  drafts.value = await fetchDrafts()
}

async function openDraft(draftId: string) {
  current.value = await fetchDraft(draftId)
  description.value = current.value.description
  advancedText.value = advancedJson(current.value.intent_card)
  confirmedKeys.value = []
  trial.value = null
}

async function submitNewDraft() {
  const detail = await createDraft(newTitle.value || '新创作', '')
  newTitle.value = ''
  await refreshDrafts()
  await openDraft(detail.id)
}

async function submitGenerate() {
  if (!current.value || !description.value.trim()) return
  generating.value = true
  modelUnready.value = false
  try {
    const result = await generateCard(current.value.id, description.value.trim())
    await openDraft(current.value.id)
    if (!result.validation.valid) ElMessage.warning('生成内容未通过平台校验，请按错误码修正')
    else ElMessage.success('意图卡已生成并通过平台校验')
  } catch (error: unknown) {
    const detail = (error as { response?: { data?: { detail?: { code?: string } } } })?.response?.data?.detail
    if (detail?.code === 'MODEL_NOT_CONFIGURED') modelUnready.value = true
  } finally {
    generating.value = false
  }
}

function toggleTool(key: string, enabled: boolean) {
  if (!card.value) return
  const next = new Set(card.value.tools)
  if (enabled) next.add(key)
  else next.delete(key)
  card.value.tools = [...next]
}

async function saveCard(next?: Partial<Parameters<typeof updateDraft>[1]>) {
  if (!current.value) return null
  try {
    current.value = await updateDraft(current.value.id, next ?? { intent_card: card.value ?? undefined })
    advancedText.value = advancedJson(current.value.intent_card)
    ElMessage.success('已保存（已通过同一校验边界）')
    return current.value
  } catch {
    return null
  }
}

async function saveAdvanced() {
  if (!current.value) return
  let parsed: unknown
  try {
    parsed = JSON.parse(advancedText.value)
  } catch {
    ElMessage.error('高级区 JSON 解析失败')
    return
  }
  await saveCard({ intent_card: parsed as never })
}

async function submitValidate() {
  if (!current.value) return
  try {
    await validateDraft(current.value.id)
    await openDraft(current.value.id)
    ElMessage.success('校验通过，草稿已进入 validated')
  } catch {
    await openDraft(current.value.id)
    ElMessage.error('校验未通过，请查看稳定错误码')
  }
}

async function onUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !current.value) return
  uploading.value = true
  try {
    await uploadKnowledge(current.value.id, file, current.value.intent_card?.skills[0]?.name ?? '')
    await openDraft(current.value.id)
    ElMessage.success('知识已上传并完成扫描')
  } catch {
    await openDraft(current.value.id)
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function removeAsset(assetId: string) {
  if (!current.value) return
  await deleteKnowledge(current.value.id, assetId)
  await openDraft(current.value.id)
}

async function submitTrial() {
  if (!current.value) return
  runningTrial.value = true
  try {
    const result = await runTrial(current.value.id, confirmedKeys.value)
    trial.value = (await fetchTrial(current.value.id, result.report_id)) as TrialSummary
  } catch {
    trial.value = null
  } finally {
    runningTrial.value = false
  }
}

async function submitPublish() {
  if (!current.value) return
  publishing.value = true
  try {
    const result = await publishDraft(current.value.id, confirmedKeys.value)
    await openDraft(current.value.id)
    ElMessage.success(`已私用发布，部署 ${result.deployment.id.slice(0, 8)}… 可立即对话`)
  } finally {
    publishing.value = false
  }
}

onMounted(async () => {
  await refreshDrafts()
  tools.value = await fetchCeilingTools()
  if (drafts.value.length) await openDraft(drafts.value[0].id)
})
</script>

<template>
  <section class="creator-page" aria-labelledby="creator-title">
    <header>
      <p class="eyebrow">CREATE / 自然语言创作器</p>
      <h1 id="creator-title">一句话，生成你的家庭助手</h1>
      <p>生成即提案：平台在你保存与发布前执行同一套校验（严格 Schema、权限天花板、提示注入扫描）。</p>
    </header>

    <div class="creator-grid">
      <aside class="panel drafts-panel" aria-labelledby="drafts-title">
        <div class="panel-title"><h2 id="drafts-title">创作草稿</h2><span>{{ drafts.length }}</span></div>
        <form class="new-draft" @submit.prevent="submitNewDraft">
          <input v-model="newTitle" placeholder="新草稿标题" aria-label="新草稿标题" />
          <button type="submit">新建</button>
        </form>
        <ul>
          <li v-for="draft in drafts" :key="draft.id">
            <button type="button" :class="{ active: current?.id === draft.id }" @click="openDraft(draft.id)">
              <strong>{{ draft.title || '未命名草稿' }}</strong>
              <small>{{ draft.status }}</small>
            </button>
          </li>
        </ul>
        <p v-if="!drafts.length" class="empty">还没有草稿，从一句话描述开始。</p>
      </aside>

      <div class="creator-main">
        <p v-if="!current" class="panel placeholder-card">选择或新建一个草稿开始创作。</p>

        <template v-else>
          <section class="panel" aria-labelledby="generate-title">
            <h2 id="generate-title">一句话描述</h2>
            <div class="composer">
              <input v-model="description" placeholder="例如：提供几道广东菜做法" aria-label="一句话描述" />
              <button type="button" :disabled="generating" @click="submitGenerate">{{ generating ? '生成中...' : '生成意图卡' }}</button>
            </div>
            <p v-if="modelUnready" class="unready" role="status">AI 模型尚未配置（MODEL_NOT_CONFIGURED）。草稿编辑、校验与知识上传仍可使用，请联系管理员完成初始化。</p>
          </section>

          <section v-if="card" class="panel" aria-labelledby="card-title">
            <div class="panel-title"><h2 id="card-title">意图卡</h2><span>{{ current.status }}</span></div>
            <div class="card-form">
              <label>名称<input v-model="card.name" /></label>
              <label>描述<input v-model="card.description" /></label>
              <label>系统提示词<textarea v-model="card.prompt" rows="3" /></label>
              <label>示例对话<input :value="card.examples[0]?.user ?? ''" placeholder="示例用户问题" @change="card.examples[0] = { user: ($event.target as HTMLInputElement).value, assistant: card.examples[0]?.assistant ?? '' }" /></label>

              <fieldset>
                <legend>工具绑定（只能选择家庭可用工具）</legend>
                <div v-for="tool in tools" :key="tool.key" class="tool-row">
                  <input :id="`tool-${tool.key}`" type="checkbox" :disabled="!isSelectableRisk(tool.risk_level)"
                         :checked="selectedTools.includes(tool.key)"
                         @change="toggleTool(tool.key, ($event.target as HTMLInputElement).checked)" />
                  <label :for="`tool-${tool.key}`">
                    <strong>{{ tool.display_name }}</strong>
                    <code>{{ tool.key }}</code>
                  </label>
                  <span :class="['risk', `risk-${tool.risk_level}`]">{{ RISK_LABELS[tool.risk_level] }}</span>
                </div>
              </fieldset>

              <label v-for="skill in card.skills" :key="skill.name">Skill 指令（{{ skill.name }}）
                <textarea v-model="skill.instructions" rows="2" />
              </label>

              <fieldset>
                <legend>预算</legend>
                <div class="budget-row">
                  <label>工具次数<input v-model.number="card.budgets.tool_calls" type="number" min="1" max="64" /></label>
                  <label>令牌<input v-model.number="card.budgets.tokens" type="number" min="1" max="100000" /></label>
                  <label>秒数<input v-model.number="card.budgets.wall_time_seconds" type="number" min="1" max="300" /></label>
                  <label>委派深度<input v-model.number="card.budgets.delegation_depth" type="number" min="0" max="16" /></label>
                </div>
              </fieldset>

              <div class="actions">
                <button type="button" @click="saveCard()">保存意图卡</button>
                <button type="button" @click="submitValidate">校验草稿</button>
              </div>
            </div>
          </section>

          <section v-if="validation && !validation.valid" class="panel error-panel" aria-labelledby="errors-title">
            <h2 id="errors-title">校验错误（稳定错误码）</h2>
            <ul>
              <li v-for="(error, index) in validation.errors" :key="index">
                <code>{{ error.code }}</code><span>{{ errorText(error) }}</span>
              </li>
            </ul>
          </section>

          <section class="panel" aria-labelledby="knowledge-title">
            <h2 id="knowledge-title">知识上传（PDF/TXT/Markdown）</h2>
            <ul class="assets">
              <li v-for="asset in current.knowledge_assets" :key="asset.id">
                <span><strong>{{ asset.filename }}</strong><small>{{ asset.scan_status }}{{ asset.scan_reason ? `：${asset.scan_reason}` : '' }}</small></span>
                <button type="button" @click="removeAsset(asset.id)">删除</button>
              </li>
            </ul>
            <input type="file" accept=".pdf,.txt,.md" aria-label="上传知识文件" :disabled="uploading" @change="onUpload" />
          </section>

          <section class="panel" aria-labelledby="trial-title">
            <h2 id="trial-title">沙盒试运行</h2>
            <fieldset v-if="reversibleTools.length">
              <legend>可撤销写工具需显式授权后才会执行</legend>
              <div v-for="tool in reversibleTools" :key="tool.key" class="tool-row">
                <input :id="`grant-${tool.key}`" type="checkbox" :value="tool.key" v-model="confirmedKeys" />
                <label :for="`grant-${tool.key}`">{{ tool.display_name }} <code>{{ tool.key }}</code></label>
              </div>
            </fieldset>
            <button type="button" :disabled="runningTrial" @click="submitTrial">{{ runningTrial ? '试运行中...' : '运行试运行' }}</button>
            <div v-if="trial" class="trial-results" data-testid="trial-results">
              <p>状态：{{ trial.status }}</p>
              <ul>
                <li v-for="item in trial.cases" :key="item.name" :class="item.status">
                  <strong>{{ item.name }}：{{ item.status === 'passed' ? '通过' : '失败' }}</strong>
                  <small v-if="item.error_code">{{ item.error_code }}</small>
                  <p>{{ item.output }}</p>
                </li>
              </ul>
            </div>
          </section>

          <section class="panel" aria-labelledby="publish-title">
            <h2 id="publish-title">私用发布</h2>
            <p>发布后立即创建不可变版本与家庭安装，可马上开始对话；可撤销写工具按上方勾选授权（opt-in）。</p>
            <button type="button" class="primary" :disabled="publishing || current.status !== 'validated'" @click="submitPublish">
              {{ publishing ? '发布中...' : '私用发布' }}
            </button>
            <p v-if="current.deployment_id">当前部署：{{ current.deployment_id }}</p>
          </section>

          <section class="panel" aria-labelledby="advanced-title">
            <button type="button" class="toggle" @click="advancedOpen = !advancedOpen">{{ advancedOpen ? '收起' : '展开' }}高级声明编辑</button>
            <div v-if="advancedOpen">
              <h2 id="advanced-title">声明式 JSON（保存前走同一校验）</h2>
              <textarea v-model="advancedText" rows="12" spellcheck="false" aria-label="声明式 JSON" />
              <button type="button" @click="saveAdvanced">保存高级声明</button>
            </div>
          </section>
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.creator-page { max-width: 1280px; margin: auto; padding: clamp(28px, 6vw, 72px); }
.eyebrow { font: 600 12px var(--font-utility); letter-spacing: .16em; color: var(--orbit-violet); }
h1 { margin: 12px 0; font: 700 clamp(38px, 5vw, 64px)/1 var(--font-display); color: var(--device-ink); }
header > p:last-child { color: #625f76; }
.creator-grid { display: grid; grid-template-columns: 300px 1fr; gap: 24px; margin-top: 42px; }
.panel { padding: 24px 28px; border-radius: 22px; background: var(--surface-white); box-shadow: 0 20px 60px rgba(33,29,79,.08); margin-bottom: 22px; }
.panel-title { display: flex; justify-content: space-between; align-items: baseline; }
h2 { margin: 0 0 18px; font-family: var(--font-display); }
.drafts-panel { align-self: start; position: sticky; top: 20px; }
.drafts-panel ul { margin: 12px 0 0; padding: 0; list-style: none; }
.drafts-panel li button { width: 100%; display: grid; gap: 2px; padding: 12px; border: 0; border-radius: 12px; background: transparent; text-align: left; cursor: pointer; }
.drafts-panel li button.active { background: var(--orbit-mist); }
.drafts-panel li small { font: 11px var(--font-utility); color: var(--orbit-violet); }
.new-draft { display: flex; gap: 8px; }
.new-draft input { flex: 1; min-height: 42px; padding: 0 10px; border: 1px solid #c9c6d9; border-radius: 10px; }
.new-draft button { padding: 0 14px; border: 0; border-radius: 10px; background: var(--orbit-violet); color: white; }
.composer { display: flex; gap: 10px; }
.composer input { flex: 1; min-height: 48px; padding: 0 14px; border: 1px solid #c9c6d9; border-radius: 12px; }
.composer button, .actions button, .card-form ~ .panel button { min-height: 44px; }
.composer button { padding: 0 18px; border: 0; border-radius: 12px; font-weight: 700; color: var(--device-ink); background: var(--charge-coral); }
.unready { margin-top: 12px; padding: 12px; border-radius: 12px; background: var(--orbit-mist); color: var(--device-ink); }
.card-form { display: grid; gap: 12px; }
.card-form label { display: grid; gap: 6px; font-weight: 700; }
.card-form input, .card-form textarea { min-height: 44px; padding: 8px 12px; border: 1px solid #c9c6d9; border-radius: 10px; font-weight: 400; }
fieldset { border: 1px solid var(--orbit-mist); border-radius: 12px; padding: 12px; }
legend { font-weight: 700; padding: 0 6px; }
.tool-row { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 10px; min-height: 44px; }
.tool-row label { display: grid; font-weight: 400; cursor: pointer; }
.tool-row code { font: 11px var(--font-utility); color: #777388; }
.risk { font: 11px var(--font-utility); }
.risk-low { color: #2c7a4b; }
.risk-reversible { color: var(--orbit-violet); }
.risk-high { color: #c0392b; }
.budget-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.budget-row label { display: grid; gap: 4px; font-weight: 700; font-size: 13px; }
.budget-row input { min-height: 40px; padding: 0 8px; border: 1px solid #c9c6d9; border-radius: 8px; }
.actions { display: flex; gap: 10px; }
.actions button { flex: 0 0 auto; padding: 0 18px; border: 0; border-radius: 12px; background: var(--orbit-violet); color: white; font-weight: 700; }
.error-panel { border: 2px solid #f0b429; }
.error-panel ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.error-panel li { display: grid; grid-template-columns: auto 1fr; gap: 10px; align-items: baseline; }
.error-panel code { font: 11px var(--font-utility); color: #c0392b; }
.assets { margin: 0 0 12px; padding: 0; list-style: none; }
.assets li { display: flex; justify-content: space-between; align-items: center; min-height: 48px; border-top: 1px solid var(--orbit-mist); }
.assets small { display: block; font: 11px var(--font-utility); color: #777388; }
.assets button { border: 0; background: transparent; color: #c0392b; cursor: pointer; min-height: 44px; }
.trial-results ul { list-style: none; padding: 0; display: grid; gap: 10px; }
.trial-results li { padding: 12px; border-radius: 12px; background: var(--orbit-mist); }
.trial-results li.passed { background: #e6f4ea; }
.trial-results li.failed { background: #fdecea; }
.trial-results p { margin: 6px 0 0; font-size: 13px; }
button.primary { padding: 0 22px; min-height: 48px; border: 0; border-radius: 12px; font-weight: 700; color: var(--device-ink); background: var(--charge-coral); }
button:disabled { opacity: .5; cursor: not-allowed; }
.toggle { border: 0; background: transparent; color: var(--orbit-violet); font-weight: 700; min-height: 44px; padding: 0; cursor: pointer; }
textarea { width: 100%; font: 12px var(--font-utility); border: 1px solid #c9c6d9; border-radius: 10px; padding: 10px; }
@media (max-width: 900px) { .creator-grid { grid-template-columns: 1fr; } .drafts-panel { position: static; } .budget-row { grid-template-columns: 1fr 1fr; } }
</style>
