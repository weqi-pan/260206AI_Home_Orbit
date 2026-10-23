import { describe, expect, it } from 'vitest'
import { renderMarkdown } from './markdown'

const CODE_BLOCK = '```js\nconst a = 1\n```'

describe('renderMarkdown', () => {
  it('高亮已知语言的代码块', () => {
    const html = renderMarkdown(CODE_BLOCK)
    expect(html).toContain('<pre class="hljs">')
    expect(html).toContain('hljs-keyword') // const 被高亮成 span
  })

  it('highlight: false 时不跑语法高亮，但仍转义代码内容', () => {
    const html = renderMarkdown(CODE_BLOCK, { highlight: false })
    expect(html).toContain('<pre class="hljs">')
    expect(html).not.toContain('hljs-keyword')
    expect(renderMarkdown('```js\nif (a < b) {}\n```', { highlight: false })).toContain('&lt;')
  })

  it('未知语言降级为转义后的纯文本代码块', () => {
    const html = renderMarkdown('```brainfuck\n+<>&\n```')
    expect(html).toContain('&lt;&gt;&amp;')
  })

  it('空内容返回空串', () => {
    expect(renderMarkdown('')).toBe('')
  })
})
