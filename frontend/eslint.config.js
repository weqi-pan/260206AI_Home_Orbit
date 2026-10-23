import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

// 首次接入的取舍：只启用确定性问题（语法错误、未定义变量、Vue 模板正确性），
// 样式类规则全部留空 —— 这样不会一上来产生成百上千条格式化告警淹没 PR，
// 也不会与刚合并的样式重构 PR 产生大范围 diff。
export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', 'src/styles/noto-sans-sc.css', 'src/styles/ibm-plex-mono.css'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: { parser: tseslint.parser },
    },
  },
  {
    languageOptions: {
      globals: {
        // 浏览器环境（项目没有单独引入 globals 包）
        window: 'readonly',
        document: 'readonly',
        navigator: 'readonly',
        localStorage: 'readonly',
        sessionStorage: 'readonly',
        fetch: 'readonly',
        AbortController: 'readonly',
        EventSource: 'readonly',
        EventTarget: 'readonly',
        HTMLElement: 'readonly',
        HTMLButtonElement: 'readonly',
        HTMLInputElement: 'readonly',
        HTMLTextAreaElement: 'readonly',
        HTMLSelectElement: 'readonly',
        RequestInit: 'readonly',
        Response: 'readonly',
        Headers: 'readonly',
        FormData: 'readonly',
        URLSearchParams: 'readonly',
        URL: 'readonly',
        Blob: 'readonly',
        File: 'readonly',
        FileReader: 'readonly',
        ResizeObserver: 'readonly',
        IntersectionObserver: 'readonly',
        MutationObserver: 'readonly',
        requestAnimationFrame: 'readonly',
        cancelAnimationFrame: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        queueMicrotask: 'readonly',
        console: 'readonly',
        performance: 'readonly',
        crypto: 'readonly',
        structuredClone: 'readonly',
        TextEncoder: 'readonly',
        TextDecoder: 'readonly',
        process: 'readonly',
        __dirname: 'readonly',
      },
    },
    rules: {
      // 空 catch 是本项目刻意保留的模式（用户取消、AbortError、拦截器已提示），
      // 代码里用注释说明了原因，不强制补变量绑定。
      'no-empty': ['error', { allowEmptyCatch: true }],
      // v-html 用在 markdown 渲染上，输入侧已由 markdown-it 转义（utils/markdown.ts）。
      'vue/no-v-html': 'off',
      // 组件名多为页面级单词名（Chat、Home），与文件一一对应，不强制多词。
      'vue/multi-word-component-names': 'off',
      // 模板里的属性顺序/换行交给 Prettier，ESLint 只管正确性。
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-indent': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/first-attribute-linebreak': 'off',
      'vue/attributes-order': 'off',
      'vue/html-quotes': 'off',
      '@typescript-eslint/no-explicit-any': 'warn',
    },
  },
  {
    files: ['**/*.test.ts', 'src/acceptance/**'],
    rules: { '@typescript-eslint/no-explicit-any': 'off' },
  },
)
