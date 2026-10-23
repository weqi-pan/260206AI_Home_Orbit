<script setup lang="ts">
import { inject, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { fetchMyAgents } from '../api/creator'
import { fetchMyInstalls, uninstallMarketInstall, upgradeMarketInstall } from '../market/api'
import { RISK_LABELS, type MyMarketInstall } from '../market/types'
import type { PublishedAgent } from '../creator/types'
import { useModalFocus } from '../composables/useModalFocus'
import { shellModalOpenKey } from '../composables/shellModal'

const shellModalOpen = inject(shellModalOpenKey, ref(false))
const selfBuilt = ref<PublishedAgent[]>([])
const installed = ref<MyMarketInstall[]>([])

const upgradeTarget = ref<MyMarketInstall | null>(null)
const upgradeOpen = ref(false)
const upgradeDialog = ref<HTMLElement>()
const upgradeTrigger = ref<HTMLButtonElement>()
const upgradeFocus = useModalFocus(upgradeOpen, upgradeDialog, upgradeTrigger)
const regrantKeys = ref<string[]>([])
const upgrading = ref(false)

const uninstallTarget = ref<MyMarketInstall | null>(null)
const uninstallOpen = ref(false)
const uninstallDialog = ref<HTMLElement>()
const uninstallTrigger = ref<HTMLButtonElement>()
const uninstallFocus = useModalFocus(uninstallOpen, uninstallDialog, uninstallTrigger)
const revokePermissions = ref(false)

async function load() {
  const [agents, installs] = await Promise.all([fetchMyAgents(), fetchMyInstalls()])
  selfBuilt.value = agents
  installed.value = installs
}

onMounted(load)

async function openUpgrade(item: MyMarketInstall, trigger?: EventTarget | null) {
  if (!item.update) return
  upgradeTarget.value = item
  regrantKeys.value = []
  if (trigger) upgradeTrigger.value = trigger as HTMLButtonElement
  shellModalOpen.value = true
  await upgradeFocus.show()
}

async function closeUpgrade() {
  shellModalOpen.value = false
  await upgradeFocus.hide()
  upgradeTarget.value = null
}

async function confirmUpgrade() {
  if (!upgradeTarget.value) return
  upgrading.value = true
  try {
    const result = await upgradeMarketInstall(upgradeTarget.value.installation_id, regrantKeys.value)
    ElMessage.success(`已升级到固定版本 v${result.pinned_version}`)
    await closeUpgrade()
    await load()
  } finally {
    upgrading.value = false
  }
}

async function openUninstall(item: MyMarketInstall, trigger?: EventTarget | null) {
  uninstallTarget.value = item
  revokePermissions.value = false
  if (trigger) uninstallTrigger.value = trigger as HTMLButtonElement
  shellModalOpen.value = true
  await uninstallFocus.show()
}

async function closeUninstall() {
  shellModalOpen.value = false
  await uninstallFocus.hide()
  uninstallTarget.value = null
}

async function confirmUninstall() {
  if (!uninstallTarget.value) return
  await uninstallMarketInstall(uninstallTarget.value.installation_id, revokePermissions.value)
  ElMessage.success('已卸载')
  await closeUninstall()
  await load()
}
</script>

<template>
  <section class="agents-page" aria-labelledby="agents-title">
    <header>
      <p class="eyebrow">MY AGENTS / 我的助手</p>
      <h1 id="agents-title">我的助手</h1>
      <p>自建助手与市场安装的助手；安装固定在审核版本，升级需要你确认并重新授权新增权限。</p>
    </header>

    <section class="panel" aria-labelledby="selfbuilt-title">
      <div class="panel-title"><h2 id="selfbuilt-title">自建</h2><span>{{ selfBuilt.length }}</span></div>
      <ul class="dense-list">
        <li v-for="agent in selfBuilt" :key="agent.draft_id">
          <span class="row-main"><strong>{{ agent.name }}</strong><small>{{ agent.description }}</small></span>
          <span class="meta">v{{ agent.versions[agent.versions.length - 1]?.version ?? 1 }}</span>
          <router-link v-if="agent.deployment_id" :to="`/chat?deployment=${agent.deployment_id}`" class="row-action">对话</router-link>
        </li>
        <li v-if="!selfBuilt.length" class="empty">还没有自建助手，去「创建」开始一句话创作。</li>
      </ul>
    </section>

    <section class="panel" aria-labelledby="installed-title">
      <div class="panel-title"><h2 id="installed-title">市场安装</h2><span>{{ installed.length }}</span></div>
      <ul class="dense-list">
        <li v-for="item in installed" :key="item.installation_id" class="install-row">
          <span class="row-main">
            <strong>{{ item.entry.name }}
              <span v-if="item.deprecated" class="state deprecated">已下架 · 旧版本继续可用</span>
              <span v-else-if="item.update_available" data-testid="update-badge" class="state update">可更新</span>
            </strong>
            <small>{{ item.entry.description }}</small>
          </span>
          <span class="meta" data-testid="pinned-version">固定 v{{ item.pinned_version }} · 最新 v{{ item.latest_version }}</span>
          <span class="actions">
            <router-link :to="`/chat?deployment=${item.deployment_id}`" class="row-action">对话</router-link>
            <button v-if="item.update_available && item.update" type="button" data-testid="start-upgrade"
                    @click="openUpgrade(item, $event.currentTarget)">升级</button>
            <button type="button" data-testid="start-uninstall" @click="openUninstall(item, $event.currentTarget)">卸载</button>
          </span>
        </li>
        <li v-if="!installed.length" class="empty">还没有安装市场助手，去「发现」看看。</li>
      </ul>
    </section>

    <div v-if="upgradeOpen" class="modal-backdrop" @click="closeUpgrade"></div>
    <div v-if="upgradeOpen" ref="upgradeDialog" class="modal" role="dialog" aria-modal="true"
         aria-label="确认升级" @keydown="upgradeFocus.onKeydown">
      <header class="modal-head">
        <div><p class="eyebrow">UPGRADE / 确认升级</p>
          <h2>{{ upgradeTarget?.entry.name }}：v{{ upgradeTarget?.update?.from_version }} → v{{ upgradeTarget?.update?.to_version }}</h2></div>
        <button ref="upgradeTrigger" type="button" class="close" aria-label="关闭升级" @click="closeUpgrade">关闭</button>
      </header>
      <p class="hint">已授权权限会延续；新增权限必须重新授权，默认不勾选。不升级则继续使用当前固定版本。</p>
      <ul class="perm-list">
        <li v-for="tool in upgradeTarget?.update?.new_permissions ?? []" :key="tool.tool_id">
          <label class="grant-label">
            <input v-model="regrantKeys" :value="tool.key" data-testid="regrant-tool" type="checkbox" />
            <span class="perm-name">{{ tool.display_name || tool.key }}</span>
            <span class="risk" :data-risk="tool.risk_level">{{ RISK_LABELS[tool.risk_level] }}</span>
          </label>
          <small>{{ tool.description }}</small>
        </li>
        <li v-if="!(upgradeTarget?.update?.new_permissions ?? []).length" class="empty">本版本没有新增权限</li>
      </ul>
      <footer class="modal-actions">
        <button type="button" @click="closeUpgrade">暂不升级</button>
        <button type="button" data-testid="confirm-upgrade" class="primary" :disabled="upgrading"
                @click="confirmUpgrade">{{ upgrading ? '升级中...' : '确认升级' }}</button>
      </footer>
    </div>

    <div v-if="uninstallOpen" class="modal-backdrop" @click="closeUninstall"></div>
    <div v-if="uninstallOpen" ref="uninstallDialog" class="modal" role="dialog" aria-modal="true"
         aria-label="卸载确认" @keydown="uninstallFocus.onKeydown">
      <header class="modal-head">
        <div><p class="eyebrow">UNINSTALL / 卸载</p><h2>卸载 {{ uninstallTarget?.entry.name }}</h2></div>
        <button ref="uninstallTrigger" type="button" class="close" aria-label="关闭卸载" @click="closeUninstall">关闭</button>
      </header>
      <label class="revoke-label">
        <input v-model="revokePermissions" data-testid="revoke-permissions" type="checkbox" />
        同时撤销该安装的逐项工具授权
      </label>
      <footer class="modal-actions">
        <button type="button" @click="closeUninstall">取消</button>
        <button type="button" data-testid="confirm-uninstall" class="danger" @click="confirmUninstall">确认卸载</button>
      </footer>
    </div>
  </section>
</template>

<style scoped>
.agents-page { max-width: 1120px; margin: auto; padding: clamp(28px, 6vw, 72px); }
.eyebrow { font: 600 12px var(--font-utility); letter-spacing: .16em; color: var(--orbit-violet); }
h1 { margin: 12px 0; font: 700 clamp(38px, 5vw, 64px)/1 var(--font-display); color: var(--device-ink); }
header > p { color: #625f76; }
.panel { margin-top: 26px; padding: 24px 28px; border-radius: 22px; background: var(--surface-white); box-shadow: 0 20px 60px rgba(33,29,79,.08); }
.panel-title { display: flex; justify-content: space-between; align-items: baseline; }
h2 { margin: 0 0 12px; font-family: var(--font-display); }
.dense-list { margin: 0; padding: 0; list-style: none; }
.dense-list li { display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 14px; min-height: 64px; border-top: 1px solid var(--orbit-mist); }
.dense-list li:first-child { border-top: 0; }
.row-main { display: grid; gap: 3px; }
.row-main small, .dense-list small { color: #777388; }
.meta { font: 12px var(--font-utility); color: #777388; }
.row-action { color: var(--orbit-violet); }
.state { display: inline-flex; margin-left: 10px; padding: 2px 10px; border-radius: 999px; font: 600 11px var(--font-utility); }
.state.update { color: var(--orbit-violet); background: var(--orbit-mist); }
.state.deprecated { color: #a13c30; background: #f9e3df; }
.actions { display: flex; gap: 8px; }
.actions button, .actions .row-action { min-height: 40px; padding: 0 14px; border: 1px solid #c9c6d9; border-radius: 10px; background: transparent; cursor: pointer; }
.empty { color: #9996a8; }
.modal-backdrop { position: fixed; z-index: 40; inset: 0; background: rgba(33,29,79,.4); }
.modal { position: fixed; z-index: 41; top: 50%; left: 50%; width: min(92vw, 620px); max-height: 84vh; overflow: auto; padding: 28px; border-radius: 22px; transform: translate(-50%, -50%); background: var(--surface-white); }
.modal-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.modal-head h2 { margin: 0; }
.close { min-height: 44px; padding: 0 14px; border: 1px solid #c9c6d9; border-radius: 10px; background: transparent; cursor: pointer; }
.hint { color: #625f76; }
.perm-list { margin: 14px 0 0; padding: 0; list-style: none; }
.perm-list li { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; padding: 10px 0; border-top: 1px solid var(--orbit-mist); }
.perm-list li > small { grid-column: 1 / -1; color: #777388; }
.perm-name { font-weight: 700; }
.risk { padding: 2px 10px; border-radius: 999px; font: 600 11px var(--font-utility); }
.risk[data-risk='low'] { color: #2d7a4f; background: #e3f3e9; }
.risk[data-risk='reversible'] { color: #8a6116; background: #f7edd6; }
.risk[data-risk='high'] { color: #a13c30; background: #f9e3df; }
.grant-label { display: flex; align-items: center; gap: 10px; }
.grant-label input { width: 20px; height: 20px; }
.revoke-label { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
.revoke-label input { width: 20px; height: 20px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 22px; }
.modal-actions button { min-height: 48px; padding: 0 18px; border: 1px solid #c9c6d9; border-radius: 12px; background: transparent; cursor: pointer; }
.modal-actions .primary { border: 0; font-weight: 700; color: var(--device-ink); background: var(--charge-coral); }
.modal-actions .danger { color: #a13c30; border-color: #a13c30; }
@media (max-width: 760px) { .agents-page { padding: 28px 18px 110px; }.dense-list li { grid-template-columns: 1fr; } }
</style>
