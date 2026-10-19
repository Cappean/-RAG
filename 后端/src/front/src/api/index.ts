import * as mockApi from './mockApi'
import * as serverApi from './serverApi'
import type { StreamCallbacks } from './serverApi'

export type { Citation, ConsultationResult, Conversation, ConversationContent, ConversationExchange } from './mockApi'

const mode = import.meta.env.VITE_API_MODE === 'server' ? 'server' : 'mock'

export const fetchConversations = () => mode === 'server' ? serverApi.fetchConversations() : mockApi.fetchConversations()
export const createConversation = (title?: string) => mode === 'server'
  ? serverApi.createConversation(title)
  : mockApi.createConversation(title)
export const fetchConversationContent = (id: string) => mode === 'server'
  ? serverApi.fetchConversationContent(id)
  : mockApi.fetchConversationContent(id)
export const submitSupplement = (conversationId: string, text: string, file?: File | null, callbacks?: StreamCallbacks) => mode === 'server'
  ? serverApi.submitSupplement(conversationId, text, file, callbacks)
  : mockApi.submitSupplement(text, file)
export const apiMode = mode
