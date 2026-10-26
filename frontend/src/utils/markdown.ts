import hljs from 'highlight.js/lib/core'
import MarkdownIt from 'markdown-it'
import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
import go from 'highlight.js/lib/languages/go'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'
import 'highlight.js/styles/github.css'

// 只注册常用语言，避免把 highlight.js 全量语言包打进聊天 chunk。
// 未知语言走下方 escapeHtml 降级分支，仍可正常展示。
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('css', css)
hljs.registerLanguage('go', go)
hljs.registerLanguage('java', java)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('python', python)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('yaml', yaml)

const BASE_OPTIONS = { html: false, linkify: true, breaks: true } as const

const md = new MarkdownIt({
  ...BASE_OPTIONS,
  highlight(code: string, lang: string): string {
    const language = lang && hljs.getLanguage(lang) ? lang : ''
    if (language) {
      try {
        return `<pre class="hljs"><code>${hljs.highlight(code, { language }).value}</code></pre>`
      } catch {}
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(code)}</code></pre>`
  },
})

// 流式期间用的实例：不做语法高亮。高亮是这里最贵的一步，
// 而每来一个 token 都要重渲染一次整段内容，流式期间高亮的输出随即作废。
const mdPlain = new MarkdownIt({
  ...BASE_OPTIONS,
  highlight(code: string): string {
    return `<pre class="hljs"><code>${mdPlain.utils.escapeHtml(code)}</code></pre>`
  },
})

export interface RenderMarkdownOptions {
  /** 流式输出中传 false：跳过语法高亮，完成后再用完整渲染替换 */
  highlight?: boolean
}

export function renderMarkdown(text: string, options: RenderMarkdownOptions = {}): string {
  if (!text) return ''
  return options.highlight === false ? mdPlain.render(text) : md.render(text)
}
