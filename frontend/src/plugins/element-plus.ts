// Element Plus 按需注册入口。
// 注意：不要从 'element-plus' 根入口导入组件 —— 根入口的 default 是
// makeInstaller([...所有组件]) 的模块级副作用，会阻止 tree-shaking，
// 使整个组件库（约 950KB）进入产物。这里一律走组件的深层路径。
import { ElAlert } from 'element-plus/es/components/alert/index.mjs'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { provideGlobalConfig } from 'element-plus/es/components/config-provider/index.mjs'
import { ElDescriptions } from 'element-plus/es/components/descriptions/index.mjs'
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs'
import { ElDrawer } from 'element-plus/es/components/drawer/index.mjs'
import { ElEmpty } from 'element-plus/es/components/empty/index.mjs'
import { ElForm } from 'element-plus/es/components/form/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElInputNumber } from 'element-plus/es/components/input-number/index.mjs'
import { ElLoading } from 'element-plus/es/components/loading/index.mjs'
import { ElRadio } from 'element-plus/es/components/radio/index.mjs'
import { ElSelect } from 'element-plus/es/components/select/index.mjs'
import { ElTable } from 'element-plus/es/components/table/index.mjs'
import { ElTabs } from 'element-plus/es/components/tabs/index.mjs'
import { ElTag } from 'element-plus/es/components/tag/index.mjs'
import { ElUpload } from 'element-plus/es/components/upload/index.mjs'
import zhCn from 'element-plus/es/locale/lang/zh-cn.mjs'
import type { App } from 'vue'

// 子组件（ElFormItem / ElTableColumn / ElOption / ElRadioGroup / ElTabPane /
// ElDescriptionsItem）由父组件的 install 一并注册，无需单独 app.use。
const components = [
  ElAlert,
  ElButton,
  ElDescriptions,
  ElDialog,
  ElDrawer,
  ElEmpty,
  ElForm,
  ElIcon,
  ElInput,
  ElInputNumber,
  ElRadio,
  ElSelect,
  ElTable,
  ElTabs,
  ElTag,
  ElUpload,
]

export function installElementPlus(app: App): void {
  for (const component of components) app.use(component)
  // 同时注册 v-loading 指令与 $loading
  app.use(ElLoading)
  // 全局中文 locale：ElMessageBox / ElMessage 等命令式服务同样读取该配置
  provideGlobalConfig({ locale: zhCn }, app, true)
}
