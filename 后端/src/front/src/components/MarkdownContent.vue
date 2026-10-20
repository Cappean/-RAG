<script lang="ts">
import MarkdownIt from 'markdown-it'
import { computed, defineComponent, h, type VNode, type VNodeChild } from 'vue'

/**
 * 只解析 Markdown，不生成 HTML 字符串：token 直接映射为 Vue 元素。
 * 模型输出属于不可信内容，因此全程不出现 v-html / innerHTML，也没有注入面。
 */
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

type Token = {
  type: string
  tag: string
  attrs: [string, string][] | null
  content: string
  children: Token[] | null
  markup: string
  info: string
  nesting: number
}

type TreeNode = { token: Token; children: TreeNode[] }

/** 按 nesting 把线性 token 组装成树，块级与行内共用同一套逻辑。 */
function toTree(tokens: Token[]): TreeNode[] {
  const root: TreeNode[] = []
  const stack: TreeNode[][] = [root]
  for (const token of tokens) {
    const current = stack[stack.length - 1]
    if (token.nesting === 1) {
      const node: TreeNode = { token, children: [] }
      current.push(node)
      stack.push(node.children)
    } else if (token.nesting === -1) {
      if (stack.length > 1) stack.pop()
    } else {
      current.push({ token, children: [] })
    }
  }
  return root
}

/** 流式期间尾部未闭合的强调/代码标记先不显示，闭合后自然出现。 */
function softenStreaming(text: string): string {
  let output = text
  const trailing = (marker: string) =>
    (output.split(marker).length - 1) % 2 === 1 && output.trimEnd().endsWith(marker)
  if (trailing('**')) output = output.slice(0, output.lastIndexOf('**'))
  if (trailing('`')) output = output.slice(0, output.lastIndexOf('`'))
  return output
}

const attrStyle = (token: Token): Record<string, string> => {
  const style = token.attrs?.find(([name]) => name === 'style')?.[1]
  return style ? { style } : {}
}

const linkHref = (token: Token): string =>
  token.attrs?.find(([name]) => name === 'href')?.[1] ?? ''

/** 文本节点里的 [n] 只有在引用范围内才变成角标，越界编号保持原样。 */
function renderText(text: string, citationCount: number, cite: (index: number) => void): (VNode | string)[] {
  const parts: (VNode | string)[] = []
  let cursor = 0
  for (const match of text.matchAll(/\[(\d{1,2})\]/g)) {
    const index = Number(match[1])
    const start = match.index ?? 0
    if (start > cursor) parts.push(text.slice(cursor, start))
    if (index >= 1 && index <= citationCount) {
      parts.push(
        h(
          'button',
          {
            class: 'cite-ref',
            type: 'button',
            title: `查看第 ${index} 条引用`,
            'aria-label': `查看第 ${index} 条引用`,
            onClick: () => cite(index),
          },
          String(index),
        ),
      )
    } else {
      parts.push(match[0])
    }
    cursor = start + match[0].length
  }
  if (cursor < text.length) parts.push(text.slice(cursor))
  return parts
}

function renderNodes(
  nodes: TreeNode[],
  citationCount: number,
  cite: (index: number) => void,
): VNodeChild[] {
  return nodes.map(node => renderNode(node, citationCount, cite))
}

function renderNode(
  node: TreeNode,
  citationCount: number,
  cite: (index: number) => void,
): VNodeChild {
  const { token } = node
  const children = () => renderNodes(node.children, citationCount, cite)

  switch (token.type) {
    case 'text':
      return renderText(token.content, citationCount, cite)
    case 'softbreak':
      return ' '
    case 'hardbreak':
      return h('br')
    case 'code_inline':
      return h('code', { class: 'md-code' }, token.content)
    case 'inline':
      return h('span', { class: 'md-inline' }, renderNodes(toTree(token.children ?? []), citationCount, cite))
    case 'strong_open':
      return h('strong', children())
    case 'em_open':
      return h('em', children())
    case 's_open':
      return h('s', children())
    case 'paragraph_open':
      return h('p', children())
    case 'heading_open':
      return h(token.tag, children())
    case 'bullet_list_open':
      return h('ul', children())
    case 'ordered_list_open': {
      const start = token.attrs?.find(([name]) => name === 'start')?.[1]
      return h('ol', start ? { start } : {}, children())
    }
    case 'list_item_open':
      return h('li', children())
    case 'blockquote_open':
      return h('blockquote', children())
    case 'fence':
    case 'code_block':
      return h('pre', { class: 'md-pre' }, h('code', token.content))
    case 'hr':
      return h('hr')
    case 'table_open':
      return h('table', children())
    case 'thead_open':
      return h('thead', children())
    case 'tbody_open':
      return h('tbody', children())
    case 'tr_open':
      return h('tr', children())
    case 'th_open':
      return h('th', attrStyle(token), children())
    case 'td_open':
      return h('td', attrStyle(token), children())
    case 'link_open':
      return h('a', { href: linkHref(token), target: '_blank', rel: 'noopener noreferrer' }, children())
    case 'image':
      // 不加载模型给出的外链图片，避免把不可信地址变成请求。
      return h('span', { class: 'md-image-note' }, `[图片：${token.content || '未命名'}]`)
    case 'html_block':
    case 'html_inline':
      return null
    default:
      return node.children.length ? h('span', children()) : null
  }
}

export default defineComponent({
  name: 'MarkdownContent',
  props: {
    content: { type: String, required: true },
    citationCount: { type: Number, default: 0 },
    streaming: { type: Boolean, default: false },
  },
  emits: ['cite'],
  setup(props, { emit }) {
    const tree = computed(() => {
      const text = props.streaming ? softenStreaming(props.content) : props.content
      return toTree(md.parse(text, {}) as Token[])
    })
    const failed = computed(() => {
      try {
        md.parse(props.content, {})
        return false
      } catch {
        return true
      }
    })
    return () => {
      // 解析异常时退回纯文本，宁可难看也不丢内容。
      if (failed.value) {
        return h('p', { class: 'message-content md-fallback' }, props.content)
      }
      return h(
        'div',
        { class: 'markdown-body' },
        renderNodes(tree.value, props.citationCount, index => emit('cite', index)),
      )
    }
  },
})
</script>
