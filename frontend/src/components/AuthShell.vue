<template>
  <div class="auth-shell">
    <aside class="auth-brand">
      <div class="auth-orbit" aria-hidden="true">
        <div class="ring r1"></div>
        <div class="ring r2"></div>
        <div class="ring r3"></div>
        <div class="spin s1"><span class="satellite coral"></span></div>
        <div class="spin s2"><span class="satellite violet"></span></div>
        <div class="spin s3"><span class="satellite mist"></span></div>
      </div>
      <div class="auth-brand-copy">
        <p class="eyebrow">HOME ORBIT / 家庭空间</p>
        <h2 class="auth-brand-headline">家里的事，<br />交给一条轨道。</h2>
        <p>设备管家、家庭厨师、家庭计划员、生活百科，与负责调度的家庭总管，围绕你的日常运转。</p>
      </div>
      <div class="auth-brand-foot" aria-hidden="true">
        <span>HOME</span>
        <strong>ORBIT</strong>
      </div>
    </aside>
    <main class="auth-form-panel">
      <div class="auth-form-card">
        <div class="auth-compact-brand" aria-hidden="true"><span>HOME</span><strong>ORBIT</strong></div>
        <slot />
      </div>
    </main>
  </div>
</template>
