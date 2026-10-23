import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // 单文件阈值：超出说明有库被整体打入某个路由 chunk
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('highlight.js') || id.includes('markdown-it')) return 'markdown'
          if (id.includes('element-plus') || id.includes('@element-plus')) return 'element-plus'
          if (id.includes('/vue/') || id.includes('vue-router') || id.includes('/pinia/') || id.includes('@vue/')) return 'vendor'
          return undefined
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    css: true,
  },
})
