# 前端优化计划（Home Orbit / Vue3 + Vite）

## Context

`frontend/` 目前功能完整、58 个测试全绿，但存在四类可量化的欠账：

- **产物过大**：`dist` 24MB。实测拆解 —— JS 2.37MB、CSS 783KB、woff2 8.8MB、woff 9.3MB、otf 2MB。根因有三个：[main.ts:20](frontend/src/main.ts#L20) 全量 `app.use(ElementPlus)` + 全量 `element-plus/dist/index.css`（约 730KB CSS）；[utils/markdown.ts:1](frontend/src/utils/markdown.ts#L1) `import hljs from 'highlight.js'` 把全部语言包打进 Chat chunk（该单文件 1.1MB）；字体导入 400/500/700 三档但 **500 字重实际零使用**（全项目 `font: 500` 只作用于 IBM Plex Mono），且 [styles/fonts.css](frontend/src/styles/fonts.css) 同时声明 woff2 与 otf 两个 src 导致两套字体都被 emit。
- **加载与缓存**：[nginx.conf](frontend/nginx.conf) 只有 `gzip on`，无 `gzip_static`、带 hash 的 assets 无 immutable 缓存头；[vite.config.ts](frontend/vite.config.ts) 无任何 `build` 配置（无 manualChunks、无体积阈值）。
- **运行时**：SSE 每来一个 token 都触发 [MessageItem.vue:25](frontend/src/components/MessageItem.vue#L25) 整段 markdown 重解析 + 代码高亮，且 `scrollBottom()` 逐 token 调用；[Chat.vue:314](frontend/src/views/Chat.vue#L314) 消息列表用 index 作 key；全项目 **47 处空 `catch {}`**，页面级错误态不统一。
- **样式与工程化**：Discover / MyAgents / Creator / Household / admin 各页逐字重复定义 `.eyebrow` / `.panel` / `.panel-title` / `.dense-list` / `.row-main` 等骨架类；`#c9c6d9`(19)、`#777388`(18)、`#625f76`(8) 等硬编码色值未收敛；仓库无 ESLint / Prettier；13 个视图无测试；CI 只跑 test + build。

目标：`dist` 从 24MB 降到约 10MB（去掉全部 woff 回退 9.3MB、未使用的 500 字重 woff2 2.4MB、otf 回退 2.0MB）、首屏 CSS/JS 显著下降、流式渲染不再逐 token 重排、页面错误态可重试、样式骨架单一来源，并把 lint 与体积阈值纳入 CI。

**硬约束**：[acceptance.test.ts](frontend/src/acceptance/acceptance.test.ts) 断言了 5 个品牌色 hex 必须字面存在于 global.css、`button, a, input, select { min-height: 44px` 的精确形态、`:focus-visible` 规则、`prefers-reduced-motion` 规则、所有 `.vue` 表单项必须带可见 `label`、以及 admin 与二级页面不得出现 `orbit-outer` / `orbit-stage`。任何样式重构都必须保持这些断言成立。

---

## 分阶段实施（每阶段一个分支 + PR）

仓库现有习惯是 PR 合入 master（见 `git log`）。每阶段独立可验证、可回滚。

### PR 1：构建体积与加载性能（不改业务逻辑）

**1.1 Element Plus 按需引入**

实际用到的组件只有 22 个标签、约 15 个父组件（子组件随父组件自动注册 —— 已在 `node_modules/element-plus/es/components/{table,form,select,radio,tabs,descriptions}/index.mjs` 中确认 `withInstall(Parent, { Child })`）：

- 修改 [main.ts](frontend/src/main.ts)：移除 `import ElementPlus from 'element-plus'` 与 `element-plus/dist/index.css`，改为逐组件 `app.use(...)` 注册：`ElButton, ElInput, ElInputNumber, ElForm, ElSelect, ElRadio, ElTable, ElTabs, ElDialog, ElDrawer, ElDescriptions, ElTag, ElEmpty, ElIcon, ElAlert, ElUpload`。
- **locale 保持中文**：用 `import { provideGlobalConfig } from 'element-plus'; provideGlobalConfig({ locale: zhCn }, app, true)`（已确认该函数接受 `(config, app, global)` 三参、可在 `createApp` 之后直接调用，且会写入模块级 `globalConfig`，`ElMessageBox` / `ElMessage` 等命令式服务同样读到 zh-CN）。
- **`v-loading`**：`app.use(ElLoading)` 一步到位 —— 其 `install` 同时注册 `loading` 指令与 `$loading`（见 `element-plus/es/components/loading/index.mjs`），11 处 `v-loading` 无需改动。
- 命令式服务：`ElMessage` / `ElMessageBox` 保持从 `'element-plus'` 具名导入（现有测试的 `vi.mock('element-plus', ...)` 依赖该 specifier），并**额外**导入它们的样式入口 —— 按需注册不会自动带上这两者。
- 每个注册组件补 `import 'element-plus/es/components/<name>/style/css'`；该入口会自动带上 base / scrollbar / popper 等依赖链（已逐一核对 `select`、`table`、`input` 的 `style/css.mjs`），20 个组件的样式入口全部存在。
- **样式顺序**：`global.css` 必须最后 import，确保 [styles/global.css:110-116](frontend/src/styles/global.css#L110-L116) 的 `.el-*` 圆角/主题覆盖仍然生效。
- **测试影响**：`ModelsOps.test.ts` / `TaskOps.test.ts` 用 `plugins: [ElementPlus]` 直接挂载，不受影响；`Chat.test.ts` 用内联 stub + `directives: { loading }`，也不受影响。但若有测试依赖全局注册，需改为显式 import 组件，不要在 main.ts 里为此回退成全量注册。
- **漏注册的发现方式**：未注册的 `<el-xxx>` 会被 Vue 当作自定义元素渲染成空标签并在 console 抛 warning，走查时开 console 即可发现；再配合 `.el-` 类名是否出现在最终 DOM 上做交叉确认。

**1.2 highlight.js 按语言注册**

修改 [utils/markdown.ts](frontend/src/utils/markdown.ts)：改 `import hljs from 'highlight.js/lib/core'`，注册约 12 种常用语言（javascript / typescript / json / python / bash / sql / xml / css / yaml / markdown / java / go），保留 `highlight.js/styles/github.css`。未知语言的降级分支已存在（`:17`）。

**1.3 字体精简**

先明确一个取舍：`dist` 体积不等于真实传输量。`@fontsource/noto-sans-sc/400.css` 的 101 个 `@font-face` 都带 `unicode-range`，浏览器只下载页面实际用到的子集 —— 所以**保留 unicode-range 子集是对首屏最友好的**，不能简单换成单文件 `chinese-simplified-400.css`（那会让每次都要下载整份 1.14MB）。

因此本 PR 的做法是「保留子集、去掉冗余」：

- 删除 [main.ts](frontend/src/main.ts) 中 `@fontsource/noto-sans-sc/500.css` —— 500 字重全项目零使用（`font: 500` 仅 3 处，全作用于 IBM Plex Mono）。
- 新建 `frontend/scripts/gen-font-css.mjs`：读取 `node_modules/@fontsource/noto-sans-sc/{400,700}.css`，删掉每条 `src` 里的 `.woff` 回退、把 `url(./files/...)` 重写为 `url('../../node_modules/@fontsource/noto-sans-sc/files/...')`，输出到 `frontend/src/styles/noto-sans-sc.css`（生成物入库，脚本供后续字体升级时重跑）。`main.ts` 改为导入这个本地文件，不再导入包内 CSS。这一步直接消掉 dist 里 9.3MB 的 woff。
  - 之所以不直接换 `chinese-simplified-400.css`：那是单文件 1.14MB，浏览器无法按 unicode-range 增量加载。
  - 之所以要重写 url：CSS 被移出包目录后，`./files/` 的相对路径会失效；写法与现有 [styles/fonts.css](frontend/src/styles/fonts.css) 引用 node_modules 的方式一致。
- [styles/fonts.css](frontend/src/styles/fonts.css)：删除 `src` 里的 `.otf` 回退行，只保留 `SmileySans-Oblique.otf.woff2`，省 2MB。
- IBM Plex Mono（400/500/600，体积很小）保持不动。
- 说明：`font-weight: 600` 作用在正文字体的地方（`Chat.vue:385,445`、`admin/Users.vue:316`、`admin/ModelsOps.vue:124`、`admin/OpsOverview.vue:147`、`admin/Reviews.vue:311`）目前就不存在 600 字重（由浏览器合成），本 PR 不改变该行为，统一在 PR 4 归一到 700。

**1.4 Vite 构建配置**

在 [vite.config.ts](frontend/vite.config.ts) 增加 `build.rollupOptions.output.manualChunks`：`vue` / `vue-router` / `pinia` 合并为 `vendor`，`element-plus` + `@element-plus/icons-vue` 为 `element-plus`，`markdown-it` + `highlight.js` 为 `markdown`。加 `chunkSizeWarningLimit: 600`。

注意：`markdown` chunk 由聊天页 `import MarkdownIt` 触发，但 `utils/markdown.ts` 目前也被静态引用。实际上只有 [MessageItem.vue:5](frontend/src/components/MessageItem.vue#L5) 引入它，而 `MessageItem` 只被 `Chat.vue` 使用，因此天然只随聊天路由加载 —— 这条 chunk 命名是显式化，不是新增懒加载。

**1.5 nginx 缓存与压缩**

修改 [frontend/nginx.conf](frontend/nginx.conf)：

```nginx
# 放在 server 块内、location / 之前
location /assets/ {
    expires 1y;
    add_header Cache-Control "public, immutable";
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    try_files $uri =404;
}
location = /index.html {
    add_header Cache-Control "no-cache";
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
}
```

- **`add_header` 继承陷阱**：`server` 级已设的 4 个安全头一旦在 `location` 内出现新的 `add_header` 就**不再向下继承**。所以上面两个 location 都把这 4 个安全头重写了一遍 —— 这一步不能省，否则静态资源会丢安全头。
- `/index.html` 的 `Cache-Control` 必须为 `no-cache`，否则带 hash 的资源换版本后用户会拿到旧 HTML 引用已删除的 JS。
- 预压缩：在 [frontend/Dockerfile](frontend/Dockerfile) 构建阶段加 `RUN find dist -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.svg' \) -exec gzip -9 -k {} \;`，nginx 侧加 `gzip_static on;`。字体文件已是压缩格式，不必再 gzip。
- 保留现有 `/api/` 的 `proxy_buffering off` 与 3600s 超时，不动 SSE 相关配置。

**验收**：`npm run build` 后 `du -sh dist` 应从 24MB 降到 12MB 以内；`ls -laS dist/assets` 中最大的 JS 不应再是 1.1MB 的 Chat chunk。

> 关于「真实收益」的说明：woff 去重与 otf 删除省的是**镜像体积**，字体本身因 `unicode-range` 本就是按需下载，首屏字节数不变。真正影响首屏的是 CSS 与 JS：实测 Element Plus 全量 `dist/index.css` 为 361KB，而本项目用到的组件主题文件合计约 139KB，按需后 CSS 约从 730KB 降到 480KB；JS 侧全量注册使 `index` chunk 达 1.12MB，按需后应显著下降（以实际构建结果为准，不预设具体数字）。

---

### PR 2：聊天流式渲染与列表性能

只改 [Chat.vue](frontend/src/views/Chat.vue) 与 [MessageItem.vue](frontend/src/components/MessageItem.vue)：

- **token 批次刷新**：在 `onToken` 中累积到字符串缓冲，用 `requestAnimationFrame` 合并刷新 `assistantMessage.content`，避免每个 token 一次响应式更新。
- **markdown 只在完成时高亮**：`MessageItem` 内把 `renderMarkdown` 改为 `computed`；流式期间（`message.streaming`）走不带 highlight 的轻量渲染（或直接沿用纯文本 + `<pre>`），`done` 后切回完整 markdown。注意保持 [MessageItem.vue:25](frontend/src/components/MessageItem.vue#L25) 的 `v-html` 用法不变。
- **滚动节流 + 用户意图保留**：`scrollBottom()` 只在一帧内执行一次，且当用户已向上滚动（`scrollTop + clientHeight < scrollHeight - 80`）时不强制拉回底部。
- **稳定 key**：在 [types.ts](frontend/src/types.ts) 的 `ChatUiMessage` 增加 `uid: number`；`toUiMessage` / 乐观消息都从模块级递增计数器取号，列表 `:key` 改用 `message.uid`（`Chat.vue:314`）。SSE `done` 回填真实 `id` 时 `uid` 不变，避免流结束时整个列表重挂载。
- **卸载清理**：`onUnmounted` 中补 `abortStreaming()`（当前只重置了 `shellModalOpen`）。

**验收**：[Chat.test.ts](frontend/src/views/Chat.test.ts) 的 aria-live 段落播报断言必须继续通过（`announceCompletedParagraph` 的边界逻辑不变）。

---

### PR 3：错误态与加载态统一

- 新增 `src/composables/useRequest.ts`：导出 `useRequest<T>(fn)`，返回 `{ data, loading, error, run }`，`error` 用已有的 [extractErrorMessage](frontend/src/api/http.ts#L13) 统一取文案。
- 新增 `src/components/ErrorState.vue`（约 40 行：错误文案 + 「重试」按钮，样式落在 global.css 的 `.page-error`）。不叫 PageState，避免与 loading 概念混淆。
- 优先改造「挂载即拉数据且当前吞错」的页面：[Discover.vue:29](frontend/src/views/Discover.vue#L29)、[MyAgents.vue:30](frontend/src/views/MyAgents.vue#L30)、[admin/Reviews.vue:26](frontend/src/views/admin/Reviews.vue#L26)、[Household.vue:12](frontend/src/views/Household.vue#L12)、[admin/TaskOps.vue:22](frontend/src/views/admin/TaskOps.vue#L22)、[admin/OpsOverview.vue:9](frontend/src/views/admin/OpsOverview.vue#L9)，以及已有 loading 但 `catch {}` 为空的 [DeviceList.vue:14](frontend/src/views/DeviceList.vue#L14)、[MyTickets.vue:29](frontend/src/views/MyTickets.vue#L29)。其余页面按同一模式逐步替换，本 PR 不要求一次清完 47 处。
- 空 `catch {}` 保留的场景（用户取消 `ElMessageBox.confirm`、AbortError、JSON 解析失败）在代码里显式注明，避免 lint 规则误报。

**验收**：断网/后端 500 时页面显示错误块并可点重试，而不是空白。

---

### PR 4：样式骨架抽取与 token 收敛

- 在 [styles/global.css](frontend/src/styles/global.css) 新增共享骨架类：`.page`（`max-width: 1120px; margin: auto; padding: clamp(28px, 6vw, 72px)`）、`.eyebrow`、`.page-title`、`.page-head`、`.panel`、`.panel-title`、`.dense-list`、`.row-main`、`.meta`、`.badge`、`.state`、`.empty`、`.facts`、`.modal*`、`.risk*`。
- 新增 token：`--ink-subtle: #625f76`、`--ink-quiet: #777388`、`--hairline-strong: #c9c6d9`、`--surface-sunken: #E4E1EF`、`--risk-low-fg/bg`、`--risk-reversible-fg/bg`、`--risk-high-fg/bg`。
- 删除各视图 scoped 样式里的重复定义：`views/{Discover,MyAgents,Creator,Household}.vue`、`views/admin/{Reviews,TaskOps,AuditRelease,OpsOverview,ModelsOps,Setup}.vue` 中共 10 处 `.panel` / `.panel-title` / `.eyebrow` / `h1` 等。
- **差异用修饰类保留，不要强行统一**：现有各页的 `.panel` 并非完全相同（如 `Household` 是 `padding: 28px`，`Discover` 是 `margin-top: 26px; padding: 24px 28px`，`Creator` 用 `margin-bottom: 22px`）。共享类应取最常见形态，个别差异通过 `.panel.is-tight` / `.panel.is-stacked` 这类修饰类在本页 scoped 里覆盖，避免改变既有视觉。
- 删除后必须逐页核对：[Discover.vue:242](frontend/src/views/Discover.vue#L242)、[MyAgents.vue:183](frontend/src/views/MyAgents.vue#L183)、[Creator.vue:319](frontend/src/views/Creator.vue#L319)、[Household.vue:68](frontend/src/views/Household.vue#L68)、[admin/Reviews.vue:282](frontend/src/views/admin/Reviews.vue#L282) 等处的最终视觉不变。
- 硬编码色值替换为 token（`#c9c6d9` 19 处、`#777388` 18 处、`#625f76` 8 处等）；风险色标签已集中在 `market/types.ts` 的 `RISK_LABELS`，样式侧统一用 `data-risk` 属性选择器。
- 正文 600 字重统一改为 700（见 PR 1.3 所列 6 处），使字体需求收敛为 400/700 两档。
- **不动** [Home.vue](frontend/src/views/Home.vue) 的 `.orbit-stage` / `.orbit-outer` 与之相关的媒体查询。
- **不得改动** global.css 中 acceptance 测试精确匹配的几行（`:45-55` 的触控目标/focus-visible、`:107` 的 reduced-motion、`:9-22` 的五个品牌 hex）。

---

### PR 5：工程化补强

- 新增 ESLint 9 flat config（`eslint.config.js`）：`eslint`、`eslint-plugin-vue`、`typescript-eslint`、`@vue/eslint-config-typescript`；新增 `.prettierrc`。
- `package.json` 增 `lint` / `lint:fix` / `format` / `format:check`；**首次引入时只修 lint 自身的阻塞问题，不做全量格式化提交**，避免与 PR 4 的 diff 冲突。
- CI（[.github/workflows/ci.yml](.github/workflows/ci.yml) 的 frontend job）增加 `npm run lint` 与 `npm run format:check`。
- 新增 `scripts/check-bundle-size.mjs`：构建后校验 `dist` 总大小与单文件阈值，超限退出非零，并在 CI build 步骤后执行。阈值按 PR 1 落地后的实测值设定 —— 预期约 10MB（24MB − 9.3MB woff − 2.4MB 的 500 字重 woff2 − 2.0MB otf），建议先取 **total ≤ 12MB、单 JS ≤ 600KB**，随后按实测收紧。
- 补测试（按收益排序）：`MessageItem`（markdown/失败态/反馈按钮）、`Household`（角色鉴权渲染）、`admin/Users`、`MyTickets`、`DeviceList`。其余未覆盖视图不强求一次补齐。

---

## 风险与回滚

| 风险 | 应对 |
|---|---|
| EP 按需注册漏组件，页面渲染为空标签 | 逐页走查 9 个用户页 + 10 个 admin 页，开 console 看 Vue 的 "Unknown custom element" warning |
| 按需后样式顺序变化，`global.css` 的 `.el-*` 覆盖失效 | `global.css` 保持最后 import；在 Changelog/PR 描述里写明这条约束 |
| 字体 CSS 生成脚本把 subset 弄坏，中文变豆腐块 | 构建后目视检查中英文混排页面；`gen-font-css.mjs` 输出入库，diff 可直接 review |
| nginx `add_header` 继承导致安全头丢失 | PR 1 用 `curl -I` 逐项确认 4 个安全头仍在 |
| PR 4 样式抽取改变视觉 | 逐页对比；acceptance 套件兜底色值/触控/动效约束 |
| lint 首次接入产生大量告警淹没 PR | 首次只修 error 级规则，warning 级先 `warn`，后续逐步提升 |

`frontend/dist/` 在 `.gitignore` 里，构建产物不会进入 PR，体积验证需在本地/CI 产物上做。

---

## 关键文件清单

| 文件 | 改动 |
|---|---|
| [frontend/src/main.ts](frontend/src/main.ts) | EP 按需注册、字体导入精简、样式顺序 |
| [frontend/vite.config.ts](frontend/vite.config.ts) | manualChunks / chunkSizeWarningLimit |
| [frontend/src/utils/markdown.ts](frontend/src/utils/markdown.ts) | highlight.js core + 语言注册 |
| [frontend/src/styles/fonts.css](frontend/src/styles/fonts.css) | 删除 otf 回退 |
| `frontend/src/styles/noto-sans-sc.css`（生成物）· `frontend/scripts/gen-font-css.mjs` | 去掉 woff 回退的字体 CSS |
| [frontend/src/styles/global.css](frontend/src/styles/global.css) | 共享骨架类 + 新 token（保留 acceptance 断言行） |
| [frontend/nginx.conf](frontend/nginx.conf) · [frontend/Dockerfile](frontend/Dockerfile) | assets immutable 缓存、index.html no-cache、预压缩 |
| [frontend/src/views/Chat.vue](frontend/src/views/Chat.vue) · [components/MessageItem.vue](frontend/src/components/MessageItem.vue) · [types.ts](frontend/src/types.ts) | rAF 批量、markdown computed、稳定 key、滚动节流 |
| `frontend/src/composables/useRequest.ts` · `frontend/src/components/ErrorState.vue` | 新增：统一 loading/错误/重试 |
| 10 个视图的 `<style scoped>` | 删除重复骨架类，改用 token |
| `frontend/eslint.config.js` · `.prettierrc` · `scripts/check-bundle-size.mjs` | 新增 |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | lint + 体积校验 |

## 验证方式（每个 PR 都要跑）

```bash
cd frontend
npm test                 # 基线：16 文件 / 58 用例全绿，不得减少
npm run build            # vue-tsc 严格模式必须通过
du -sh dist && ls -laS dist/assets | head -10   # 对比体积
npm run dev              # 手工走查：登录 → 首页轨道 → 发现 → 创建 → 我的助手 → 家庭 → 聊天流式 → 工单 → 设备 → /admin 各页
docker compose up -d --build   # PR 1 后验证 nginx 缓存头：curl -I http://localhost/assets/<file>.js
```

PR 2 额外验证：在聊天页发一条长回复，DevTools Performance 面板确认流式期间不再逐 token 触发整段 markdown 解析；向上滚动时新 token 不应把你拉回底部。

PR 4 额外验证：`npm test` 中 acceptance 套件必须原样通过 —— 它同时检查了色值、触控目标、reduced-motion 与各页不得出现大轨道。
