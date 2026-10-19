import type { Citation, ConsultationResult, Conversation, ConversationContent } from './mockApi'

const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

type StreamEvent = {
  type: string
  data: Record<string, unknown>
}

export type StreamCallbacks = {
  onStatus?: (status: string) => void
  onToken?: (token: string) => void
  onCitation?: (citation: Citation) => void
}

function apiUrl(path: string): string {
  return `${baseUrl}${path}`
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init)
  if (!response.ok) throw new Error(`API ${response.status}: ${await response.text()}`)
  return response.json() as Promise<T>
}

function unwrapList(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value as Record<string, unknown>[]
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    const items = record.items ?? record.conversations ?? record.data
    if (Array.isArray(items)) return items as Record<string, unknown>[]
  }
  return []
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function mapConversation(item: Record<string, unknown>): Conversation {
  const id = asString(item.id ?? item._id)
  const status = asString(item.status, 'active')
  const statusLabel: Conversation['status'] = status === 'closed' || status === 'completed' || status === '已办结'
    ? '已办结'
    : status === 'followed_up' || status === '已随访' ? '已随访' : '进行中'
  const updatedAt = asString(item.updatedAt ?? item.createdAt)
  return {
    id,
    title: asString(item.title, '智能问诊'),
    timeLabel: updatedAt ? new Date(updatedAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '',
    status: statusLabel,
    preview: asString(item.preview ?? item.lastMessage),
  }
}

function mapCitation(value: unknown): Citation | null {
  if (!value || typeof value !== 'object') return null
  const item = value as Record<string, unknown>
  const source = item.source && typeof item.source === 'object' ? item.source as Record<string, unknown> : {}
  const id = asString(item.id ?? item.chunkId ?? item.chunk_id)
  const documentId = asString(item.documentId ?? item.document_id ?? source.documentId)
  if (!id || !documentId) return null
  return {
    id,
    documentId,
    title: asString(item.title ?? item.documentName ?? item.document_name, '指南来源'),
    chapter: asString(item.chapter ?? item.chapterPath ?? item.chapter_path ?? source.chapterPath),
    page: Number(item.page ?? item.pageIndex ?? item.page_index ?? source.pageIndex ?? 1),
    score: typeof item.score === 'number' ? `${(item.score * 100).toFixed(1)}%` : asString(item.score, ''),
    quote: asString(item.quote ?? item.text ?? item.content),
  }
}

function listCitations(value: unknown): Citation[] {
  if (!Array.isArray(value)) return []
  return value.map(mapCitation).filter((item): item is Citation => item !== null)
}

function mapMessagesToContent(value: unknown): ConversationContent | null {
  const messages = unwrapList(value)
  if (!messages.length) return null
  const userMessage = [...messages].reverse().find((message) => message.role === 'user')
  const assistantMessage = [...messages].reverse().find((message) => message.role === 'assistant')
  if (!userMessage && !assistantMessage) return null
  const citations = listCitations(assistantMessage?.citations)
  const question = asString(userMessage?.content ?? userMessage?.text)
  return {
    question,
    sentAt: asString(userMessage?.createdAt ?? userMessage?.sentAt),
    entities: Array.isArray(userMessage?.entities) ? userMessage.entities.map(String) : [],
    evidenceTitle: citations.length ? `RAG多源循证检索完成（命中 ${citations.length} 份核心指南）` : '本轮暂未关联指南来源',
    citations,
    analysisTitle: '智能分析',
    analysis: asString(assistantMessage?.content ?? assistantMessage?.text),
    recommendation: asString(assistantMessage?.recommendation),
  }
}

export async function fetchConversations(): Promise<Conversation[]> {
  const response = await requestJson<unknown>('/api/conversations')
  return unwrapList(response).map(mapConversation)
}

export async function createConversation(title = '新的智能问诊'): Promise<Conversation> {
  const item = await requestJson<Record<string, unknown>>('/api/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  return mapConversation(item)
}

export async function fetchConversationContent(id: string): Promise<ConversationContent | null> {
  const response = await requestJson<unknown>(`/api/conversations/${encodeURIComponent(id)}/messages`)
  return mapMessagesToContent(response)
}

function parseSseBlock(block: string): StreamEvent | null {
  let eventType = ''
  const dataLines: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) eventType = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (!dataLines.length) return null
  const rawData = dataLines.join('\n')
  try {
    const parsed = JSON.parse(rawData) as Record<string, unknown>
    return { type: eventType || asString(parsed.type ?? parsed.event), data: parsed }
  } catch {
    return { type: eventType || 'token', data: { token: rawData } }
  }
}

export async function submitSupplement(
  conversationId: string,
  text: string,
  file?: File | null,
  callbacks: StreamCallbacks = {},
): Promise<ConsultationResult> {
  const headers = new Headers({ Accept: 'text/event-stream' })
  let body: BodyInit
  if (file) {
    const form = new FormData()
    form.set('content', text)
    form.set('file', file)
    body = form
  } else {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify({ content: text })
  }

  const response = await fetch(apiUrl(`/api/conversations/${encodeURIComponent(conversationId)}/messages?stream=true`), {
    method: 'POST',
    headers,
    body,
  })
  if (!response.ok) throw new Error(`API ${response.status}: ${await response.text()}`)
  if (!response.body) throw new Error('SSE 响应没有可读数据流')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let message = ''
  let citations: Citation[] = []
  let generatedAt = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  let completed = false
  const handleEvent = (event: StreamEvent) => {
    const { type, data } = event
    if (type === 'status') callbacks.onStatus?.(asString(data.message ?? data.status))
    else if (type === 'token') {
      const token = asString(data.token ?? data.content ?? data.text)
      message += token
      callbacks.onToken?.(token)
    } else if (type === 'citation') {
      const citation = mapCitation(data.citation ?? data)
      if (citation) {
        citations.push(citation)
        callbacks.onCitation?.(citation)
      }
    } else if (type === 'message.completed') {
      const finalMessage = asString(data.content ?? data.message ?? data.text)
      if (finalMessage) message = finalMessage
      citations = listCitations(data.citations).length ? listCitations(data.citations) : citations
      generatedAt = asString(data.createdAt ?? data.generatedAt, generatedAt)
      completed = true
    } else if (type === 'done') {
      completed = true
      generatedAt = asString(data.generatedAt, generatedAt)
    } else if (type === 'error') {
      throw new Error(asString(data.message ?? data.error, 'SSE 请求失败'))
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const blocks = buffer.split(/\r?\n\r?\n/)
    buffer = blocks.pop() ?? ''
    for (const block of blocks) {
      const event = parseSseBlock(block)
      if (event) handleEvent(event)
    }
    if (done) break
  }
  if (buffer.trim()) {
    const event = parseSseBlock(buffer)
    if (event) handleEvent(event)
  }
  if (!completed && !message) throw new Error('SSE 流结束，但没有收到回答内容')
  return { message, citations, generatedAt }
}
