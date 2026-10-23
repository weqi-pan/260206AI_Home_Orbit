import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { installElementPlus } from './plugins/element-plus'
// Element Plus 主题：按需引入，每个组件只带自己的样式
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/icon/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/upload/style/css'
// 命令式服务（ElMessage / ElMessageBox）不会被组件注册带入样式，需单独引入
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/loading/style/css'
// 字体：只含 woff2，且保留 unicode-range 子集（见 scripts/gen-font-css.mjs）
import './styles/noto-sans-sc.css'
import './styles/ibm-plex-mono.css'
import './styles/fonts.css'
// global.css 必须最后引入：其中的 .el-* 覆盖规则依赖排在组件主题之后
import './styles/global.css'
import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
installElementPlus(app)
app.mount('#app')
