import type { StreamEvent } from './types'

const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
export const apiUrl = (path: string) => `${baseUrl}${path}`

/** Only display deliberately public error messages, never arbitrary response bodies. */
async function rejectResponse(response: Response): Promise<never> {
  const body = await response.json().catch(() => null)
  const error = body?.error
  throw new Error(`${error?.message ?? `请求失败 (${response.status})`}${error?.requestId ? ` · 请求 ${error.requestId}` : ''}`)
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const started = performance.now()
  console.debug('[API] request', { method: init?.method ?? 'GET', path: path.split('?')[0] })
  const response = await fetch(apiUrl(path), init)
  console.debug('[API] response', { status: response.status, elapsedMs: Math.round(performance.now() - started) })
  if (!response.ok) return rejectResponse(response)
  return response.json() as Promise<T>
}

export const jsonBody = (value: unknown, method = 'POST'): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })

/** Decode SSE across arbitrary UTF-8 and network chunk boundaries. */
export async function streamMessage(conversationId: string, content: string, receive: (event: StreamEvent) => void, signal: AbortSignal): Promise<void> {
  const response = await fetch(apiUrl(`/api/conversations/${encodeURIComponent(conversationId)}/messages`), {
    ...jsonBody({ content }), headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, signal,
  })
  console.info('[consultation] stream opened', { status: response.status })
  if (!response.ok) return rejectResponse(response)
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('服务没有返回问诊事件流')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let completed = false
  const dispatch = (block: string) => {
    const lines = block.split('\n')
    const type = lines.find(line => line.startsWith('event:'))?.slice(6).trim() ?? 'message'
    const dataLines = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart())
    if (!dataLines.length) return
    const data = JSON.parse(dataLines.join('\n')) as Record<string, unknown>
    if (type === 'done') { completed = true; console.info('[consultation] generation saved') }
    if (type === 'error') throw new Error(String(data.message ?? '问诊生成失败'))
    receive({ type, data })
  }
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      let match: RegExpExecArray | null
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        dispatch(buffer.slice(0, match.index).replace(/\r\n/g, '\n'))
        buffer = buffer.slice(match.index + match[0].length)
      }
      if (done) break
    }
    if (buffer.trim()) dispatch(buffer.replace(/\r\n/g, '\n'))
    if (!completed) throw new Error('连接已中断，回答未完成，请查看已保存的历史状态')
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
