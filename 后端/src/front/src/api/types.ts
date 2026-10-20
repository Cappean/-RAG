/** Public contracts mirror the API; clinical content stays out of console logs. */
export type PatientInput = { name: string; gender: string | null; birthDate: string | null; allergies: string[]; medicalHistory: string[]; medications: string[] }
export type Patient = PatientInput & { id: string }
export type Conversation = { id: string; patientId: string | null; title: string; updatedAt: string }
export type SourceLocation = { pageNumber?: number | null; bbox?: number[] | null; startLine?: number | null; endLine?: number | null }
export type Citation = { chunkId: string; documentId: string; documentName: string; quote: string; headingPath: string[]; sourceLocations: SourceLocation[] }
export type Message = { id: string; role: 'user' | 'assistant'; content: string; status: 'generating' | 'completed' | 'failed'; citations: Citation[]; createdAt: string }
export type KnowledgeDocument = { id: string; name: string; fileName: string; sourceType: 'pdf' | 'md'; status: 'processing' | 'ready' | 'failed' | 'disabled' }
export type UploadResult = { document: KnowledgeDocument; taskId: string | null; duplicate: boolean }
export type TaskState = { taskId: string; status: 'queued' | 'running' | 'succeeded' | 'failed'; progress: string | null; error: string | null }
export type StreamEvent = { type: string; data: Record<string, unknown> }
