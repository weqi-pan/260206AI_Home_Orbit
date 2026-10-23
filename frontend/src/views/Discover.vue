<script setup lang="ts">
import { inject, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { useModalFocus } from '../composables/useModalFocus'
import { shellModalOpenKey } from '../composables/shellModal'
import { favoriteEntry, fetchEntry, fetchListings, installEntry, MARKET_CATEGORIES, reportEntry, unfavoriteEntry } from '../market/api'
import { RISK_LABELS, type MarketEntryDetail, type MarketListings } from '../market/types'

const shellModalOpen = inject(shellModalOpenKey, ref(false))
const listings = ref<MarketListings>({ official: [], entries: [] })
const query = ref('')
const category = ref('')
const loading = ref(false)

const detailOpen = ref(false)
const detail = ref<MarketEntryDetail | null>(null)
const detailTrigger = ref<HTMLButtonElement>()
const detailDialog = ref<HTMLElement>()
const detailFocus = useModalFocus(detailOpen, detailDialog, detailTrigger)

const installOpen = ref(false)
const installTrigger = ref<HTMLButtonElement>()
const installDialog = ref<HTMLElement>()
const installFocus = useModalFocus(installOpen, installDialog, installTrigger)
const grantedKeys = ref<string[]>([])
const installing = ref(false)
const reportReason = ref('')

async function load() {
  loading.value = true
  try {
    listings.value = await fetchListings(query.value.trim(), category.value)
  } finally {
    loading.value = false
  }
}

onMounted(load)

function selectCategory(value: string) {
  category.value = value
  void load()
}

async function openDetail(entryId: string, trigger?: EventTarget | null) {
  if (trigger) detailTrigger.value = trigger as HTMLButtonElement
  detail.value = await fetchEntry(entryId)
  reportReason.value = ''
  shellModalOpen.value = true
  await detailFocus.show()
}

async function closeDetail() {
  shellModalOpen.value = false
  await detailFocus.hide()
}

async function openInstall() {
  if (!detail.value) return
  grantedKeys.value = []
  shellModalOpen.value = true
  await installFocus.show()
}

async function closeInstall() {
  shellModalOpen.value = false
  await installFocus.hide()
}

async function confirmInstall() {
  if (!detail.value) return
  installing.value = true
  try {
    const result = await installEntry(detail.value.id, grantedKeys.value)
    ElMessage.success(`已安装固定版本 v${result.pinned_version}`)
    await closeInstall()
    await closeDetail()
    await load()
  } finally {
    installing.value = false
  }
}

async function toggleFavorite() {
  if (!detail.value) return
  if (detail.value.favorited) {
    await unfavoriteEntry(detail.value.id)
    detail.value.favorited = false
  } else {
    await favoriteEntry(detail.value.id)
    detail.value.favorited = true
  }
}

async function submitReport() {
  if (!detail.value || !reportReason.value.trim()) return
  await reportEntry(detail.value.id, reportReason.value.trim())
  ElMessage.success('举报已提交，管理员会尽快处理')
  reportReason.value = ''
}
</script>

<template>
  <section class="discover-page" aria-labelledby="discover-title">
    <header>
      <p class="eyebrow">DISCOVER / 发现</p>
      <h1 id="discover-title">发现家庭助手</h1>
      <p>搜索市场 Agent 与官方内容；安装固定审核版本并逐项授权。</p>
      <form class="search-bar" @submit.prevent="load">
        <label class="sr-only" for="market-search">搜索助手</label>
        <input id="market-search" v-model="query" data-testid="market-search" type="search"
               placeholder="按名称、描述或意图搜索" />
        <button type="submit">搜索</button>
      </form>
      <div class="category-chips" role="group" aria-label="分类过滤">
        <button type="button" :class="{ active: category === '' }" @click="selectCategory('')">全部</button>
        <button v-for="item in MARKET_CATEGORIES" :key="item" type="button"
                :class="{ active: category === item }" @click="selectCategory(item)">{{ item }}</button>
      </div>
    </header>

    <section class="panel" aria-labelledby="official-title">
      <div class="panel-title"><h2 id="official-title">官方内容</h2><span>{{ listings.official.length }}</span></div>
      <ul class="dense-list">
        <li v-for="item in listings.official" :key="item.deployment_id" class="official-row">
          <span class="badge official">官方</span>
          <span class="row-main"><strong>{{ item.name }}</strong><small>{{ item.description }}</small></span>
          <router-link :to="`/chat?deployment=${item.deployment_id}`" class="row-action">对话</router-link>
        </li>
        <li v-if="!listings.official.length" class="empty">暂无官方内容</li>
      </ul>
    </section>

    <section class="panel" aria-labelledby="market-title">
      <div class="panel-title"><h2 id="market-title">市场 Agent</h2><span>{{ listings.entries.length }}</span></div>
      <ul class="dense-list">
        <li v-for="item in listings.entries" :key="item.id">
          <button type="button" data-testid="market-entry" class="row-main entry-link"
                  @click="openDetail(item.id, $event.currentTarget)">
            <strong>{{ item.name }}<span class="badge" :data-category="item.category">{{ item.category }}</span></strong>
            <small>{{ item.description }}</small>
          </button>
          <span class="meta">v{{ item.current_version }} · {{ item.install_count }} 次安装</span>
          <span v-if="item.my_installation" class="installed-mark">已安装 v{{ item.my_installation.pinned_version }}</span>
        </li>
        <li v-if="!loading && !listings.entries.length" class="empty">没有匹配的助手，换个关键词试试</li>
      </ul>
    </section>

    <div v-if="detailOpen" class="modal-backdrop" @click="closeDetail"></div>
    <div v-if="detailOpen" ref="detailDialog" class="modal" role="dialog" aria-modal="true"
         :aria-label="detail?.name ?? '条目详情'" @keydown="detailFocus.onKeydown">
      <template v-if="detail">
        <header class="modal-head">
          <div>
            <p class="eyebrow">MARKET / 详情</p>
            <h2>{{ detail.name }}<span class="badge">{{ detail.category }}</span></h2>
          </div>
          <button type="button" class="close" aria-label="关闭详情" @click="closeDetail">关闭</button>
        </header>
        <p class="detail-desc">{{ detail.description }}</p>
        <dl class="facts">
          <div><dt>当前公共版本</dt><dd>v{{ detail.current_version.number }}</dd></div>
          <div><dt>安装量</dt><dd>{{ detail.install_count }}</dd></div>
          <div><dt>来源</dt><dd>{{ detail.official ? '官方' : '创作者提交 · 人工审核通过' }}</dd></div>
        </dl>
        <section aria-labelledby="perm-title">
          <h3 id="perm-title">权限（风险标注）</h3>
          <ul class="perm-list">
            <li v-for="tool in detail.permissions" :key="tool.tool_id">
              <span class="perm-name">{{ tool.display_name || tool.key }}</span>
              <span class="risk" :data-risk="tool.risk_level" :data-testid="`permission-risk-${tool.risk_level}`">{{ RISK_LABELS[tool.risk_level] }}</span>
              <small>{{ tool.description }}</small>
            </li>
            <li v-if="!detail.permissions.length" class="empty">该助手不使用工具</li>
          </ul>
        </section>
        <section aria-labelledby="ver-title">
          <h3 id="ver-title">版本历史</h3>
          <ol class="version-list">
            <li v-for="version in detail.versions" :key="version.submission_id">
              v{{ version.number }}<small>{{ version.published_at ?? '' }}</small>
            </li>
          </ol>
        </section>
        <div class="report-row">
          <label for="report-reason">举报</label>
          <input id="report-reason" v-model="reportReason" data-testid="report-reason" placeholder="举报原因" />
          <button type="button" data-testid="submit-report" @click="submitReport">提交举报</button>
        </div>
        <footer class="modal-actions">
          <button type="button" data-testid="toggle-favorite" @click="toggleFavorite">
            {{ detail.favorited ? '取消收藏' : '收藏' }}
          </button>
          <button v-if="!detail.my_installation" ref="detailTrigger" type="button" data-testid="open-install"
                  class="primary" @click="openInstall">安装</button>
          <span v-else class="installed-mark">已安装 v{{ detail.my_installation.pinned_version }}（在“我的助手”中管理）</span>
        </footer>
      </template>
    </div>

    <div v-if="installOpen" class="modal-backdrop" @click="closeInstall"></div>
    <div v-if="installOpen" ref="installDialog" class="modal" role="dialog" aria-modal="true"
         aria-label="逐项授权安装" @keydown="installFocus.onKeydown">
      <header class="modal-head">
        <div><p class="eyebrow">AUTHORIZE / 逐项授权</p><h2>安装 {{ detail?.name }}</h2></div>
        <button ref="installTrigger" type="button" class="close" aria-label="关闭授权" @click="closeInstall">关闭</button>
      </header>
      <p class="hint">默认拒绝：低风险只读工具按平台策略自动授权；可逆写入与高风险工具需要你逐项勾选。</p>
      <ul class="perm-list">
        <li v-for="tool in detail?.permissions ?? []" :key="tool.tool_id">
          <label class="grant-label" :class="{ auto: tool.risk_level === 'low' }">
            <input v-if="tool.risk_level !== 'low'" v-model="grantedKeys" :value="tool.key"
                   data-testid="grant-tool" type="checkbox" />
            <span v-else class="auto-mark">自动</span>
            <span class="perm-name">{{ tool.display_name || tool.key }}</span>
            <span class="risk" :data-risk="tool.risk_level">{{ RISK_LABELS[tool.risk_level] }}</span>
          </label>
          <small>{{ tool.description }}</small>
        </li>
      </ul>
      <footer class="modal-actions">
        <button type="button" @click="closeInstall">取消</button>
        <button type="button" data-testid="confirm-install" class="primary" :disabled="installing"
                @click="confirmInstall">{{ installing ? '安装中...' : '确认安装固定版本' }}</button>
      </footer>
    </div>
  </section>
</template>

<style scoped>
.discover-page { max-width: 1120px; margin: auto; padding: clamp(28px, 6vw, 72px); }
.eyebrow { font: 600 12px var(--font-utility); letter-spacing: .16em; color: var(--orbit-violet); }
h1 { margin: 12px 0; font: 700 clamp(38px, 5vw, 64px)/1 var(--font-display); color: var(--device-ink); }
header > p { color: #625f76; }
.search-bar { display: flex; gap: 10px; margin-top: 26px; max-width: 560px; }
.search-bar input { flex: 1; min-height: 48px; padding: 0 14px; border: 1px solid #c9c6d9; border-radius: 12px; }
.search-bar button, .modal-actions .primary { min-height: 48px; padding: 0 22px; border: 0; border-radius: 12px; font-weight: 700; color: var(--device-ink); background: var(--charge-coral); cursor: pointer; }
.category-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.category-chips button { min-height: 38px; padding: 0 16px; border: 1px solid #c9c6d9; border-radius: 999px; background: transparent; cursor: pointer; }
.category-chips button.active { color: white; border-color: var(--orbit-violet); background: var(--orbit-violet); }
.panel { margin-top: 26px; padding: 24px 28px; border-radius: 22px; background: var(--surface-white); box-shadow: 0 20px 60px rgba(33,29,79,.08); }
.panel-title { display: flex; justify-content: space-between; align-items: baseline; }
h2 { margin: 0 0 12px; font-family: var(--font-display); }
.dense-list { margin: 0; padding: 0; list-style: none; }
.dense-list li { display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 14px; min-height: 62px; border-top: 1px solid var(--orbit-mist); }
.dense-list .official-row { grid-template-columns: auto minmax(0, 1fr) auto; }
.dense-list li:first-child { border-top: 0; }
.row-main { display: grid; min-width: 0; gap: 3px; text-align: left; }
.row-main small, .dense-list small { color: #777388; }
.entry-link { border: 0; background: transparent; cursor: pointer; color: var(--device-ink); font: inherit; }
.entry-link:hover strong { color: var(--orbit-violet); }
.badge { display: inline-flex; align-items: center; margin-left: 10px; padding: 2px 10px; border-radius: 999px; font: 600 11px var(--font-utility); }
.badge.official, .installed-mark { color: var(--orbit-violet); background: var(--orbit-mist); }
.official-row .badge.official { align-self: center; justify-self: start; margin-left: 0; }
.meta { font: 12px var(--font-utility); color: #777388; }
.row-action { color: var(--orbit-violet); }
.empty { color: #9996a8; }
.modal-backdrop { position: fixed; z-index: 40; inset: 0; background: rgba(33,29,79,.4); }
.modal { position: fixed; z-index: 41; top: 50%; left: 50%; width: min(92vw, 640px); max-height: 84vh; overflow: auto; padding: 28px; border-radius: 22px; transform: translate(-50%, -50%); background: var(--surface-white); }
.modal-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.modal-head h2 { margin: 0; }
.close { min-height: 44px; padding: 0 14px; border: 1px solid #c9c6d9; border-radius: 10px; background: transparent; cursor: pointer; }
.detail-desc { margin-top: 16px; color: #45425c; }
.facts { display: flex; gap: 28px; margin: 18px 0; }
.facts dt { font: 600 11px var(--font-utility); color: #777388; letter-spacing: .12em; }
.facts dd { margin: 4px 0 0; font-weight: 700; }
h3 { margin: 18px 0 8px; font-family: var(--font-display); }
.perm-list { margin: 0; padding: 0; list-style: none; }
.perm-list li { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; padding: 10px 0; border-top: 1px solid var(--orbit-mist); }
.perm-list li > small { grid-column: 1 / -1; color: #777388; }
.perm-name { font-weight: 700; }
.risk { padding: 2px 10px; border-radius: 999px; font: 600 11px var(--font-utility); }
.risk[data-risk='low'] { color: #2d7a4f; background: #e3f3e9; }
.risk[data-risk='reversible'] { color: #8a6116; background: #f7edd6; }
.risk[data-risk='high'] { color: #a13c30; background: #f9e3df; }
.version-list { margin: 0; padding-left: 18px; }
.version-list li { display: flex; gap: 10px; padding: 4px 0; }
.version-list small { color: #777388; }
.report-row { display: flex; gap: 8px; align-items: center; margin-top: 18px; }
.report-row input { flex: 1; min-height: 44px; padding: 0 12px; border: 1px solid #c9c6d9; border-radius: 10px; }
.report-row button { min-height: 44px; padding: 0 14px; border: 1px solid #c9c6d9; border-radius: 10px; background: transparent; cursor: pointer; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 22px; }
.modal-actions button { min-height: 48px; padding: 0 18px; border: 1px solid #c9c6d9; border-radius: 12px; background: transparent; cursor: pointer; }
.hint { color: #625f76; }
.grant-label { display: flex; align-items: center; gap: 10px; }
.grant-label input { width: 20px; height: 20px; }
.grant-label.auto { color: #45425c; }
.auto-mark { font: 600 11px var(--font-utility); color: #2d7a4f; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media (max-width: 760px) { .discover-page { padding: 28px 18px 110px; }.dense-list li { grid-template-columns: 1fr; }.dense-list .official-row { grid-template-columns: auto minmax(0, 1fr); align-items: start; }.official-row .row-action { grid-column: 2; justify-self: start; }.facts { flex-wrap: wrap; } }
</style>
