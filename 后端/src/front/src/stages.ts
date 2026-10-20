import type { KnowledgeDocument, TaskState } from './api'

/**
 * Progress contracts mirror the API stage identifiers exactly. Labels live here so
 * wording changes never rewrite the wire format, and a stage the backend invents
 * later degrades to its raw id instead of disappearing silently.
 */
export type StageItem = { id: string; label: string }

export const CONSULTATION_STAGES: StageItem[] = [
  { id: 'intake', label: '整理问诊信息' },
  { id: 'retrieval', label: '检索指南' },
  { id: 'citation', label: '整理引用' },
  { id: 'generation', label: '生成回答' },
  { id: 'done', label: '完成' },
]

export const KNOWLEDGE_STAGES: StageItem[] = [
  { id: 'queued', label: '等待处理' },
  { id: 'parse', label: '解析文档' },
  { id: 'split', label: '切分内容' },
  { id: 'vectorize', label: '生成向量并入库' },
  { id: 'done', label: '完成' },
]

export const stageLabel = (stages: StageItem[], id: string | null | undefined) =>
  stages.find(stage => stage.id === id)?.label ?? id ?? ''

/** Everything a stepper needs to render one flow; no percentages by design. */
export type BarState = {
  current: string | null
  skipped: string[]
  failed: string | null
  finished: boolean
  detail: string | null
}

/**
 * Task stage and document status are different facts: the task is process-local and
 * dies with a restart, while document status is persisted. Never invent a stage for
 * a task we cannot query — fall back to the persisted status only.
 */
export function documentBar(document: KnowledgeDocument, task: TaskState | undefined): BarState {
  const skipped = task?.skippedStages?.length
    ? task.skippedStages
    : document.sourceType === 'md'
      ? ['parse']
      : []
  if (!task) {
    return {
      current: null,
      skipped,
      failed: null,
      finished: document.status === 'ready',
      detail: null,
    }
  }
  if (task.status === 'failed') {
    return {
      current: task.stage,
      skipped,
      failed: task.failedStage ?? task.stage,
      finished: false,
      detail: task.stageDetail,
    }
  }
  if (task.status === 'succeeded') {
    return { current: 'done', skipped, failed: null, finished: true, detail: null }
  }
  return {
    current: task.stage ?? 'queued',
    skipped,
    failed: null,
    finished: false,
    detail: task.stageDetail,
  }
}
