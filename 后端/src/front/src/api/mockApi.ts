export type Citation = {
  id: string
  title: string
  page: number
  chapter: string
  score: string
  quote: string
  documentId: string
}

export type Conversation = {
  id: string
  title: string
  timeLabel: string
  status: '进行中' | '已办结' | '已随访'
  preview: string
  active?: boolean
}

export type ConsultationResult = {
  message: string
  citations: Citation[]
  generatedAt: string
}

export type ConversationContent = {
  question: string
  sentAt: string
  entities: string[]
  evidenceTitle: string
  citations: Citation[]
  analysisTitle: string
  analysis: string
  recommendation: string
}

export type ConversationExchange = {
  id: string
  userText: string
  fileName?: string
  assistantText: string
  sentAt: string
}

const citations: Citation[] = [
  {
    id: 'citation-1',
    documentId: 'doc-digestive-2023',
    title: '《中国消化性溃疡病规范诊疗指南(2023)》第2章 临床表现与分型',
    chapter: '第2章 临床表现与分型',
    page: 12,
    score: '98.4%',
    quote: '十二指肠球部溃疡典型的腹痛节律为饥饿痛或夜间痛，进食碱性食物或抗酸剂后，因中和胃酸而内 pH 值暂时回升，疼痛常能迅速缓解。',
  },
  {
    id: 'citation-2',
    documentId: 'doc-hp-2024',
    title: '《幽门螺杆菌感染基层诊疗指南（2024实践版）》',
    chapter: '第3章 初诊患者评估',
    page: 8,
    score: '96.5%',
    quote: '对于初发消化不良、伴有节律性上腹痛的青年患者，推荐优先进行 13C/14C 尿素呼气试验排查 HP 感染，必要时结合内镜评估黏膜活动性损伤。',
  },
]

const defaultConversations: Conversation[] = [
  {
    id: 'c-1',
    title: '近3日胃部隐痛反酸咨询',
    timeLabel: '10:24 今天',
    status: '进行中',
    preview: '空腹痛明显，进食苏打饼干缓解…',
    active: true,
  },
  {
    id: 'c-2',
    title: '孕期叶酸补充疑问（家属）',
    timeLabel: '3天前',
    status: '已办结',
    preview: '备孕及早孕期活性叶酸与普通叶酸区…',
  },
  {
    id: 'c-3',
    title: '体检尿酸偏高复查',
    timeLabel: '2周前',
    status: '已随访',
    preview: '尿酸490μmol/L，无痛风发作史管理…',
  },
]

const conversationContent: Record<string, ConversationContent> = {
  'c-1': {
    question: '我最近3天经常胃部隐痛，空腹时明显，吃点苏打饼干会缓解，偶尔伴有轻微反酸。需要做胃镜吗？应该看什么科室？',
    sentAt: '今日 10:24:12',
    entities: ['上腹部隐痛（3天）', '饥饿痛 / 进食缓解', '反酸'],
    evidenceTitle: 'RAG多源循证检索完成（命中 2 份核心指南）',
    citations,
    analysisTitle: '病理推演与机制解释',
    analysis: '根据“上腹隐痛、空腹加重、反酸、进食碱性食物后缓解”的症状组合，需要考虑消化性溃疡等可能。进食后症状缓解可与胃酸被食物暂时缓冲有关，但仅凭症状不能确诊。偶发反酸也需要结合病史和检查评估。建议结合幽门螺杆菌检测及医生评估，必要时进行胃镜检查。',
    recommendation: '消化内科就诊 · 评估幽门螺杆菌检测及胃镜必要性',
  },
  'c-2': {
    question: '家属正在备孕，想了解叶酸应该从什么时候开始补充？活性叶酸和普通叶酸有什么区别？',
    sentAt: '3天前 15:42:06',
    entities: ['备孕', '叶酸补充', '孕早期'],
    evidenceTitle: 'RAG多源循证检索完成（命中 1 份核心指南）',
    citations: [citations[1]],
    analysisTitle: '备孕期营养补充建议',
    analysis: '备孕及孕早期的叶酸补充应结合个人健康情况和既往孕产史确定。普通叶酸是常见补充形式；是否需要其他剂型或剂量，应由产科医生根据风险因素评估，不建议仅凭“活性”宣传自行更换或加量。',
    recommendation: '建议咨询产科或妇产科医生，按个人情况确认补充剂量与时间',
  },
  'c-3': {
    question: '体检发现尿酸 490μmol/L，目前没有关节红肿或痛风发作史，需要马上用药吗？',
    sentAt: '2周前 09:16:30',
    entities: ['尿酸 490μmol/L', '无痛风发作史', '体检异常'],
    evidenceTitle: 'RAG多源循证检索完成（命中 1 份核心指南）',
    citations: [citations[0]],
    analysisTitle: '检查结果与随访建议',
    analysis: '单次尿酸结果需要结合复查结果、肾功能、合并疾病、用药和饮食情况综合判断。没有痛风发作并不意味着一定需要立即开始降尿酸药物，建议携带体检报告至全科或风湿免疫科评估，并按医生建议复查。',
    recommendation: '先复查尿酸并评估肾功能及相关危险因素，再由医生判断是否需要治疗',
  },
}

export async function fetchConversations(): Promise<Conversation[]> {
  await new Promise((resolve) => setTimeout(resolve, 120))
  return defaultConversations.map((item) => ({ ...item }))
}

export async function createConversation(title = '新的智能问诊'): Promise<Conversation> {
  await new Promise((resolve) => setTimeout(resolve, 80))
  return { id: `c-${Date.now()}`, title, timeLabel: '刚刚', status: '进行中', preview: '开始描述您的症状…' }
}

export async function fetchConversationContent(id: string): Promise<ConversationContent | null> {
  await new Promise((resolve) => setTimeout(resolve, 80))
  return conversationContent[id] ?? null
}

export async function submitSupplement(text: string, file?: File | null): Promise<ConsultationResult> {
  await new Promise((resolve) => setTimeout(resolve, 700))
  const suffix = file ? `已收到您上传的「${file.name}」。` : ''
  return {
    generatedAt: '10:25:03',
    citations,
    message: `${suffix}结合您补充的信息，目前仍建议优先排查消化性溃疡与幽门螺杆菌感染。请记录疼痛与进食的关系，近期避免酒精、咖啡和刺激性食物；如出现黑便、呕血或持续加重，请及时就医。${text ? `（已记录：${text}）` : ''}`,
  }
}

export function streamConsultation(onToken: (token: string) => void, onDone: () => void): () => void {
  const tokens = '正在结合患者信息与指南证据生成分析…'.split('')
  let index = 0
  const timer = window.setInterval(() => {
    onToken(tokens[index] ?? '')
    index += 1
    if (index >= tokens.length) {
      window.clearInterval(timer)
      onDone()
    }
  }, 35)
  return () => window.clearInterval(timer)
}

// Production contracts (replace the mock methods with these endpoints):
// POST /api/conversations, GET /api/conversations, POST /api/conversations/{id}/messages
// GET /api/knowledge/documents/{documentId}/view?pageIndex={page}&chunkId={chunkId}
// POST /api/conversations/{id}/messages/stream (text/event-stream)
