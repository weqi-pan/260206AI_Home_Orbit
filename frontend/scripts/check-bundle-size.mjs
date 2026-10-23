// 校验构建产物体积，超限时退出非零 —— CI 的 build 步骤之后执行。
//
// 阈值依据当前实测值设定（2026-10 基线）：
//   dist 总计 8.0MB，其中字体 woff2 约 5.9MB（unicode-range 子集，浏览器按需下载）
//   单文件最大 1.30MB（SmileySans-Oblique.otf.woff2 中文标题字体）
//   JS 最大 399KB（element-plus chunk），CSS 最大 221KB（index）
// 取略高于实测的整数阈值，既拦住「又把全量 Element Plus / 全量 hljs 打回来」这类回归，
// 又不至于因为几 KB 的版本浮动误报。收紧前先跑一次 `npm run build` 看实际数字。
import { readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const DIST = 'dist'
const LIMITS = {
  total: 9 * 1024 * 1024, // dist 总量
  script: 600 * 1024, // 单个 .js
  style: 300 * 1024, // 单个 .css
  asset: 2 * 1024 * 1024, // 单个其它资源（字体、图片）
}

const MB = (n) => `${(n / 1024 / 1024).toFixed(2)}MB`
const KB = (n) => `${(n / 1024).toFixed(0)}KB`

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...walk(path))
    else out.push({ path, size: statSync(path).size })
  }
  return out
}

let files
try {
  files = walk(DIST)
} catch {
  console.error(`${DIST}/ 不存在，先跑 npm run build`)
  process.exit(1)
}

const problems = []
let total = 0

for (const { path, size } of files) {
  total += size
  const kind = path.endsWith('.js') ? 'script' : path.endsWith('.css') ? 'style' : 'asset'
  const limit = LIMITS[kind]
  if (size > limit) {
    const fmt = kind === 'asset' ? MB : KB
    problems.push(`${path}  ${fmt(size)} > ${fmt(limit)}（${kind} 上限）`)
  }
}

if (total > LIMITS.total) {
  problems.push(`dist 总量 ${MB(total)} > ${MB(LIMITS.total)}`)
}

if (problems.length) {
  console.error('构建产物超出体积阈值：')
  for (const line of problems) console.error(`  - ${line}`)
  process.exit(1)
}

console.log(`体积校验通过：dist ${MB(total)}，共 ${files.length} 个文件`)
