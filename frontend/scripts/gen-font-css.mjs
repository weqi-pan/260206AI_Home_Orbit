// 生成只含 woff2 的字体 CSS。
// @fontsource 包内的 CSS 每条 @font-face 同时声明 woff2 与 woff 回退，
// Vite 会把两者都 emit 到 dist —— 其中 woff 约 9.3MB 且永远不会被现代浏览器下载。
// 本脚本读取包内 CSS，去掉 woff 回退并把 url 改写为可跨目录解析的相对路径。
//
// 用法：node scripts/gen-font-css.mjs        重跑会覆盖生成物
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')

const TARGETS = [
  { pkg: '@fontsource/noto-sans-sc', weights: [400, 700], out: 'src/styles/noto-sans-sc.css', family: 'Noto Sans SC' },
  { pkg: '@fontsource/ibm-plex-mono', weights: [400, 500, 600], out: 'src/styles/ibm-plex-mono.css', family: 'IBM Plex Mono' },
]

for (const target of TARGETS) {
  const pkgDir = `node_modules/${target.pkg}`
  const blocks = []

  for (const weight of target.weights) {
    const css = readFileSync(resolve(root, pkgDir, `${weight}.css`), 'utf-8')
    const faces = css.match(/@font-face\s*\{[^}]*\}/g) ?? []
    for (const face of faces) {
      // 只保留 woff2 的 src，丢弃 woff 回退；url 改写为相对生成文件的 node_modules 路径
      const stripped = face
        .replace(/src:\s*([^;]*);/, (_match, src) => {
          const woff2 = String(src)
            .split(',')
            .map((part) => part.trim())
            .find((part) => part.includes('format(\'woff2\')'))
          if (!woff2) throw new Error(`${target.pkg} ${weight}: @font-face 中没有 woff2`)
          return `src: ${woff2.replace(/url\(\.\/files\//g, `url('../../${pkgDir}/files/`).replace(/\.woff2\)/, ".woff2')")};`
        })
      blocks.push(stripped)
    }
  }

  const banner = `/* 由 scripts/gen-font-css.mjs 生成，请勿手工编辑。\n   ${target.family}：仅 woff2，保留 unicode-range 子集。 */\n`
  writeFileSync(resolve(root, target.out), `${banner}${blocks.join('\n\n')}\n`, 'utf-8')
  console.log(`${target.out}: ${blocks.length} 个 @font-face`)
}
