<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { fetchOrbit, fetchSetupStatus, type OrbitNode, type SetupStatus } from '../api/platform'

const auth = useAuthStore()
const nodes = ref<OrbitNode[]>([])
const setup = ref<SetupStatus | null>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    const [orbit, setupStatus] = await Promise.all([fetchOrbit(), fetchSetupStatus()])
    nodes.value = orbit.nodes
    setup.value = setupStatus
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="home-page" aria-labelledby="home-title">
    <div class="home-copy">
      <p class="eyebrow">HOME ORBIT / 家庭空间</p>
      <h1 id="home-title">{{ auth.account?.username }}，家里现在怎么样？</h1>
      <p class="home-lead">从一个家庭轨道看清需要处理的事。模型配置完成后，家庭总管会在这里接手自然语言请求。</p>

      <div v-if="!loading && setup && !setup.ready" class="setup-callout" role="status">
        <span class="status-dot" aria-hidden="true"></span>
        <div>
          <strong>AI 助手尚未启用</strong>
          <p>非 AI 的家庭、账户、设备与支持功能仍可使用。</p>
        </div>
        <router-link v-if="setup.can_configure" to="/admin/setup" class="button-link">查看初始化设置</router-link>
        <span v-else class="setup-note">请联系管理员完成模型初始化</span>
      </div>

      <form class="orbit-composer" aria-describedby="composer-help" @submit.prevent>
        <label for="home-request">交给家庭总管</label>
        <div class="composer-row">
          <input id="home-request" :disabled="!setup?.ready" placeholder="例如：安排周末大扫除" />
          <button type="submit" :disabled="!setup?.ready">发送</button>
        </div>
        <span id="composer-help" class="sr-only">模型初始化完成后可发送请求</span>
      </form>

      <section class="state-list" aria-labelledby="state-title">
        <div class="section-heading">
          <h2 id="state-title">当前轨道</h2>
          <span>{{ nodes.length }} 个状态</span>
        </div>
        <p v-if="loading">正在读取家庭状态...</p>
        <ul v-else>
          <li v-for="node in nodes" :key="node.id">
            <router-link :to="node.href">
              <span :class="['node-mark', `is-${node.status}`]" aria-hidden="true"></span>
              <span><strong>{{ node.label }}</strong><small>{{ node.detail }}</small></span>
              <span class="node-status">{{ node.status === 'attention' ? '待处理' : '正常' }}</span>
            </router-link>
          </li>
        </ul>
      </section>
    </div>

    <div class="orbit-stage" aria-hidden="true">
      <div class="orbit orbit-outer"></div>
      <div class="orbit orbit-inner"></div>
      <span v-for="(node, index) in nodes" :key="node.id" :class="['visual-node', `node-${index + 1}`, `is-${node.status}`]"></span>
      <div class="orbit-core"><span>HOME</span><strong>ORBIT</strong></div>
    </div>
  </section>
</template>

<style scoped>
.home-page { min-height: 100%; display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 46vw); overflow: hidden; }
.home-copy { position: relative; z-index: 2; align-self: center; max-width: 720px; padding: clamp(32px, 6vw, 88px); padding-right: 24px; }
.eyebrow { margin: 0 0 18px; font: 600 12px/1 var(--font-utility); letter-spacing: .16em; color: var(--orbit-violet); }
h1 { max-width: 650px; margin: 0; font-family: var(--font-display); font-size: clamp(42px, 6vw, 76px); line-height: .98; letter-spacing: -.04em; color: var(--device-ink); }
.home-lead { max-width: 570px; margin: 24px 0 30px; font-size: 17px; line-height: 1.8; color: color-mix(in srgb, var(--device-ink) 72%, white); }
.setup-callout { display: flex; align-items: center; gap: 14px; max-width: 650px; padding: 18px; border: 1px solid color-mix(in srgb, var(--charge-coral) 45%, white); border-radius: 18px; background: var(--surface-white); }
.setup-callout p { margin: 4px 0 0; color: #615d79; }
.status-dot { width: 12px; height: 12px; flex: 0 0 auto; border-radius: 50%; background: var(--charge-coral); }
.button-link { margin-left: auto; white-space: nowrap; }
.setup-note { margin-left: auto; max-width: 160px; color: #615d79; }
.orbit-composer { max-width: 650px; margin-top: 24px; }
.orbit-composer label { display: block; margin-bottom: 10px; font-weight: 700; color: var(--device-ink); }
.composer-row { display: flex; min-height: 58px; padding: 5px; border-radius: 18px; background: var(--device-ink); }
.composer-row input { flex: 1; min-width: 0; padding: 0 16px; border: 0; outline: 0; color: white; background: transparent; }
.composer-row input::placeholder { color: #b9b5d0; }
.composer-row button { min-width: 92px; border: 0; border-radius: 13px; font-weight: 700; color: var(--device-ink); background: var(--charge-coral); }
.composer-row button:disabled, .composer-row input:disabled { opacity: .55; cursor: not-allowed; }
.state-list { max-width: 650px; margin-top: 34px; }
.section-heading { display: flex; align-items: baseline; justify-content: space-between; border-bottom: 1px solid #cbc9dc; }
.section-heading h2 { margin: 0 0 10px; font: 700 18px/1.2 var(--font-display); }
.section-heading span { font: 12px var(--font-utility); }
.state-list ul { padding: 0; margin: 0; list-style: none; }
.state-list li { border-bottom: 1px solid #d8d7e4; }
.state-list a { display: grid; grid-template-columns: 12px 1fr auto; align-items: center; gap: 14px; min-height: 68px; color: inherit; text-decoration: none; }
.state-list small { display: block; margin-top: 3px; color: #6a667e; }
.node-mark { width: 10px; height: 10px; border: 2px solid var(--orbit-violet); border-radius: 50%; }
.node-mark.is-attention { border-color: var(--charge-coral); background: var(--charge-coral); }
.node-status { font: 11px var(--font-utility); }
.orbit-stage { position: relative; min-height: 720px; }
.orbit { position: absolute; top: 50%; left: 42%; aspect-ratio: 1; border: 3px solid var(--orbit-violet); border-radius: 50%; transform: translate(-50%, -50%); }
.orbit-outer { width: min(78vw, 880px); opacity: .9; }
.orbit-inner { width: min(52vw, 590px); border-width: 1px; opacity: .55; }
.orbit-core { position: absolute; top: 50%; left: 42%; display: grid; width: 190px; aspect-ratio: 1; place-content: center; border-radius: 50%; color: var(--surface-white); text-align: center; background: var(--device-ink); transform: translate(-50%, -50%); }
.orbit-core span { font: 11px var(--font-utility); letter-spacing: .25em; }
.orbit-core strong { font: 28px var(--font-display); }
.visual-node { position: absolute; z-index: 2; width: 24px; height: 24px; border: 6px solid var(--surface-white); border-radius: 50%; background: var(--orbit-violet); box-shadow: 0 0 0 2px var(--orbit-violet); }
.visual-node.is-attention { background: var(--charge-coral); box-shadow: 0 0 0 2px var(--charge-coral); }
.node-1 { top: 21%; left: 17%; }.node-2 { right: 9%; bottom: 27%; }
@media (max-width: 900px) { .home-page { display: block; }.home-copy { padding: 32px 20px 120px; }.orbit-stage { position: absolute; top: 110px; right: -280px; width: 520px; min-height: 520px; opacity: .22; }.home-copy > * { position: relative; z-index: 2; } }
@media (max-width: 600px) { h1 { font-size: 42px; }.setup-callout { align-items: flex-start; flex-wrap: wrap; }.button-link, .setup-note { width: 100%; margin-left: 26px; }.home-lead { font-size: 15px; }.composer-row { min-height: 56px; } }
</style>
