<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { fetchHousehold, inviteMember, type Household } from '../api/platform'

const household = ref<Household | null>(null)
const username = ref('')
const role = ref('member')
const inviting = ref(false)
const canInvite = computed(() => ['owner', 'admin'].includes(household.value?.role ?? ''))

onMounted(async () => { household.value = await fetchHousehold() })

async function submitInvitation() {
  if (!household.value || !username.value.trim()) return
  inviting.value = true
  try {
    await inviteMember(household.value.id, username.value.trim(), role.value)
    ElMessage.success('邀请已创建，对方可在登录后接受')
    username.value = ''
  } finally {
    inviting.value = false
  }
}
</script>

<template>
  <section class="household-page" aria-labelledby="household-title">
    <header>
      <p class="eyebrow">HOUSEHOLD / 家庭边界</p>
      <h1 id="household-title">{{ household?.name ?? '家庭空间' }}</h1>
      <p>首版每个账号只属于一个家庭。家庭内资源遵循成员角色隔离。</p>
    </header>
    <div class="household-grid">
      <section class="panel" aria-labelledby="members-title">
        <div class="panel-title"><h2 id="members-title">家庭成员</h2><span>{{ household?.members.length ?? 0 }}</span></div>
        <ul>
          <li v-for="member in household?.members" :key="member.account_id">
            <span class="avatar">{{ member.username.slice(0, 1).toUpperCase() }}</span>
            <span><strong>{{ member.username }}</strong><small>{{ member.account_id }}</small></span>
            <span class="role">{{ member.role }}</span>
          </li>
        </ul>
      </section>
      <section class="panel invite-panel" aria-labelledby="invite-title">
        <h2 id="invite-title">邀请成员</h2>
        <p v-if="!canInvite">只有家庭 owner 或 admin 可以创建邀请。</p>
        <form v-else @submit.prevent="submitInvitation">
          <label for="invite-username">已注册用户名</label>
          <input id="invite-username" v-model="username" required minlength="3" />
          <label for="invite-role">家庭角色</label>
          <select id="invite-role" v-model="role">
            <option value="admin">admin</option><option value="member">member</option><option value="viewer">viewer</option>
          </select>
          <button type="submit" :disabled="inviting">{{ inviting ? '创建中...' : '创建邀请' }}</button>
        </form>
      </section>
    </div>
  </section>
</template>

<style scoped>
.household-page { max-width: 1120px; margin: auto; padding: clamp(28px, 6vw, 72px); }
.eyebrow { font: 600 12px var(--font-utility); letter-spacing: .16em; color: var(--orbit-violet); }
h1 { margin: 12px 0; font: 700 clamp(38px, 5vw, 64px)/1 var(--font-display); color: var(--device-ink); }
header > p:last-child { color: #625f76; }
.household-grid { display: grid; grid-template-columns: 1.4fr .8fr; gap: 24px; margin-top: 42px; }
.panel { padding: 28px; border-radius: 22px; background: var(--surface-white); box-shadow: 0 20px 60px rgba(33,29,79,.08); }
.panel-title { display: flex; justify-content: space-between; align-items: baseline; }
h2 { margin: 0 0 22px; font-family: var(--font-display); }
.panel ul { margin: 0; padding: 0; list-style: none; }
.panel li { display: grid; grid-template-columns: 46px 1fr auto; align-items: center; gap: 12px; min-height: 68px; border-top: 1px solid var(--orbit-mist); }
.avatar { display: grid; width: 38px; height: 38px; place-items: center; border-radius: 50%; color: white; background: var(--orbit-violet); }
.panel small { display: block; margin-top: 3px; font: 11px var(--font-utility); color: #777388; }
.role { font: 12px var(--font-utility); color: var(--orbit-violet); }
.invite-panel form { display: grid; gap: 10px; }
.invite-panel label { margin-top: 8px; font-weight: 700; }
.invite-panel input, .invite-panel select { min-height: 46px; padding: 0 12px; border: 1px solid #c9c6d9; border-radius: 10px; background: white; }
.invite-panel button { min-height: 48px; margin-top: 14px; border: 0; border-radius: 12px; font-weight: 700; color: var(--device-ink); background: var(--charge-coral); }
@media (max-width: 760px) { .household-page { padding: 28px 18px 110px; }.household-grid { grid-template-columns: 1fr; } }
</style>
