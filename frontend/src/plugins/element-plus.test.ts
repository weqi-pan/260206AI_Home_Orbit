import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createApp } from 'vue'
import { describe, expect, it } from 'vitest'
import { installElementPlus } from './element-plus'

const srcRoot = resolve(__dirname, '..')

function collectVueFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(dir, entry.name)
    if (entry.isDirectory()) return collectVueFiles(path)
    return entry.name.endsWith('.vue') ? [path] : []
  })
}

// 从所有 .vue 模板中提取用到的 <el-xxx> 标签，转成注册名
function usedElementComponents(): Set<string> {
  const names = new Set<string>()
  for (const file of collectVueFiles(srcRoot)) {
    const source = readFileSync(file, 'utf-8')
    for (const match of source.matchAll(/<(el-[a-z-]+)[\s>]/g)) {
      const pascal = match[1]
        .split('-')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join('')
      names.add(pascal)
    }
  }
  return names
}

// 用一个真实 Vue app 捕获 install 过程注册了哪些组件/指令
function registeredByPlugin(): { components: Set<string>; directives: Set<string> } {
  const components = new Set<string>()
  const directives = new Set<string>()
  const app = createApp({ render: () => null })
  const originalComponent = app.component.bind(app)
  const originalDirective = app.directive.bind(app)
  // 只拦截注册调用，其余行为仍走真实 app（installer 会写 globalProperties / provide）
  app.component = ((name: string, definition?: unknown) => {
    if (definition === undefined) return originalComponent(name)
    components.add(name)
    return originalComponent(name, definition as never)
  }) as typeof app.component
  app.directive = ((name: string, definition?: unknown) => {
    if (definition === undefined) return originalDirective(name)
    directives.add(name)
    return originalDirective(name, definition as never)
  }) as typeof app.directive
  installElementPlus(app)
  return { components, directives }
}

describe('Element Plus 按需注册', () => {
  it('注册了所有模板中实际使用的 <el-*> 组件', () => {
    const used = usedElementComponents()
    const { components } = registeredByPlugin()
    // 少注册会在浏览器里渲染成空标签并抛 "Failed to resolve component"
    const missing = [...used].filter((name) => !components.has(name)).sort()
    expect(missing).toEqual([])
    expect(used.size).toBeGreaterThan(15)
  })

  it('注册了 v-loading 指令', () => {
    expect(registeredByPlugin().directives.has('loading')).toBe(true)
  })

  it('不再从 element-plus 根入口导入组件（否则整个组件库无法 tree-shake）', () => {
    const offenders = collectVueFiles(srcRoot)
      .filter((file) => !file.endsWith('.test.ts'))
      .filter((file) => /import\s*\{[^}]*(ElButton|ElTable|ElInput|ElForm)[^}]*\}\s*from\s*'element-plus'/.test(readFileSync(file, 'utf-8')))
    expect(offenders).toEqual([])
  })
})
