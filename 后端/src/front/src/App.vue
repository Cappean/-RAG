<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { BookOpen, ChevronLeft, ChevronRight, History, Plus, Send, ShieldCheck, Stethoscope, Upload } from 'lucide-vue-next'
import SourceViewer from './SourceViewer.vue'
import StageBar from './components/StageBar.vue'
import MarkdownContent from './components/MarkdownContent.vue'
import CitationList from './components/CitationList.vue'
import { ApiError, deleteConversation, jsonBody, renameConversation, requestJson, streamMessage, type Citation, type Conversation, type KnowledgeDocument, type Message, type Patient, type PatientInput, type TaskState, type UploadResult } from './api'
import { CONSULTATION_STAGES, KNOWLEDGE_STAGES, documentBar, stageLabel } from './stages'

/** 一次问诊的在途状态按会话保存，切换会话不会串内容，也不会中断生成。 */
type LiveTurn = { messageId: string; stage: string; content: string; citations: Citation[] }
/** 文件传输阶段由浏览器本地观测，此时还没有可查询的 taskId。 */
type UploadAttempt = { key: string; name: string; size: number; state: 'uploading' | 'failed'; error: string }

const TASK_STORAGE_KEY = 'medical.demo.ingest-tasks'

const patients = ref<Patient[]>([])
const conversations = ref<Conversation[]>([])
const documents = ref<KnowledgeDocument[]>([])
const messages = ref<Message[]>([])
const patientId = ref('')
const activeConversationId = ref('')
const inputText = ref('')
const error = ref('')
const notice = ref('')
const loading = ref(false)
const creatingConversation = ref(false)
const viewerCitation = ref<Citation | null>(null)
/** 正文角标点击后要定位的引用：[n] 的编号与 citations 数组下标严格对应。 */
const citationFocus = ref<{ messageId: string; index: number } | null>(null)
const showPatientForm = ref(false)
const editingPatientId = ref('')
const patientBusy = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
/** 服务端上传上限：前端据此提前拦截，避免白传一个大文件。 */
const uploadLimitBytes = ref(0)
const uploadLimitLabel = ref('')
const patientForm = reactive({ name: '', gender: '', birthDate: '', allergies: '', medicalHistory: '', medications: '' })
const taskStates = reactive<Record<string, TaskState>>({})
const taskByDocument = reactive<Record<string, string>>({})
const taskMisses = reactive<Record<string, number>>({})
const unreachableTasks = reactive<Record<string, boolean>>({})
const attempts = reactive<UploadAttempt[]>([])
const liveTurns = reactive<Record<string, LiveTurn>>({})
/** 历史列表的操作菜单：同一时刻只允许一个展开。 */
const historyMenuId = ref('')
const renamingId = ref('')
const renameDraft = ref('')
const confirmingDeleteId = ref('')
const historyBusy = ref(false)
const polling = new Set<string>()
const currentPath = ref(window.location.pathname)
const sidebarCollapsed = ref(localStorage.getItem('medical.demo.sidebar-collapsed') === 'true')
let disposed = false
let controller: AbortController | null = null
let selectionVersion = 0
let pollTimer: ReturnType<typeof setTimeout> | undefined

const currentPatient = computed(() => patients.value.find(patient => patient.id === patientId.value))
const readyDocuments = computed(() => documents.value.filter(document => document.status === 'ready'))
const selectedConversation = computed(() => conversations.value.find(conversation => conversation.id === activeConversationId.value))
const displayedTurn = computed(() => (activeConversationId.value ? liveTurns[activeConversationId.value] ?? null : null))
/** 只锁当前会话的重复发送，不锁页面其它操作。 */
const busy = computed(() => Boolean(displayedTurn.value))
const uploading = computed(() => attempts.some(attempt => attempt.state === 'uploading'))
const labelStatus = (status: string) => ({ processing: '处理中', ready: '可检索', failed: '失败', disabled: '已停用', queued: '排队中', running: '进行中', succeeded: '完成' }[status] ?? status)
const timeLabel = (value: string) => new Date(value).toLocaleString('zh-CN')
const showError = (exc: unknown) => { error.value = exc instanceof Error ? exc.message : '请求失败，请检查服务连接' }
const formatSize = (bytes: number) => bytes >= 1024 * 1024
  ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
  : `${Math.max(1, Math.round(bytes / 1024))} KB`

/** 上限由服务端给出，前端不写死数值，避免两边配置漂移。 */
async function loadUploadLimit() {
  try {
    const limits = await requestJson<{ uploadMaxBytes: number; uploadMaxLabel: string }>('/api/knowledge/limits')
    uploadLimitBytes.value = limits.uploadMaxBytes
    uploadLimitLabel.value = limits.uploadMaxLabel
  } catch {
    // 拿不到上限就不做前端预检，交给服务端拒绝，不阻断上传入口。
  }
}

/** Lists are fetched in full; UI counts always derive from real server records. */
async function refreshLists() {
  const [newPatients, newConversations, newDocuments] = await Promise.all([
    requestJson<Patient[]>('/api/patients'), requestJson<Conversation[]>('/api/conversations'), requestJson<KnowledgeDocument[]>('/api/knowledge/documents'),
  ])
  patients.value = newPatients
  conversations.value = newConversations
  documents.value = newDocuments
}

async function initialize() {
  loading.value = true
  error.value = ''
  try {
    await refreshLists()
    const savedConversation = localStorage.getItem('medical.demo.conversation')
    const previous = conversations.value.find(item => item.id === savedConversation)
    if (previous) await selectConversation(previous)
    else patientId.value = patients.value.find(item => item.id === localStorage.getItem('medical.demo.patient'))?.id ?? ''
    restoreTasks()
    schedulePoll()
  } catch (exc) { showError(exc) }
  finally { loading.value = false }
}

async function selectConversation(conversation: Conversation) {
  // 生成期间允许切换会话：原会话继续生成，历史只替换当前视图。
  const version = ++selectionVersion
  activeConversationId.value = conversation.id
  patientId.value = conversation.patientId ?? ''
  localStorage.setItem('medical.demo.conversation', conversation.id)
  messages.value = []
  loading.value = true
  error.value = ''
  try {
    const history = await requestJson<Message[]>(`/api/conversations/${conversation.id}/messages`)
    if (version !== selectionVersion) return
    messages.value = history
    // 会话仍在生成时，服务端快照可能落后于当前流；用内存中的最新片段补齐。
    applyLive(conversation.id)
  } catch (exc) { if (version === selectionVersion) showError(exc) }
  finally { if (version === selectionVersion) loading.value = false }
}

function selectPatient() {
  // Changing the consultation subject never silently reassigns an existing conversation.
  activeConversationId.value = ''
  messages.value = []
  localStorage.removeItem('medical.demo.conversation')
  localStorage.setItem('medical.demo.patient', patientId.value)
}

async function newConversation() {
  if (!patientId.value || creatingConversation.value) return
  creatingConversation.value = true
  error.value = ''
  try {
    const conversation = await requestJson<Conversation>('/api/conversations', jsonBody({ patientId: patientId.value, title: '新问诊' }))
    conversations.value.unshift(conversation)
    await selectConversation(conversation)
  } catch (exc) { showError(exc) }
  finally { creatingConversation.value = false }
}

function openPatientForm(edit = false) {
  const patient = edit ? currentPatient.value : undefined
  editingPatientId.value = patient?.id ?? ''
  Object.assign(patientForm, { name: patient?.name ?? '', gender: patient?.gender ?? '', birthDate: patient?.birthDate?.slice(0, 10) ?? '', allergies: patient?.allergies.join('\n') ?? '', medicalHistory: patient?.medicalHistory.join('\n') ?? '', medications: patient?.medications.join('\n') ?? '' })
  showPatientForm.value = true
}

async function savePatient() {
  patientBusy.value = true
  error.value = ''
  const entries = (text: string) => text.split('\n').map(item => item.trim()).filter(Boolean)
  const payload: PatientInput = { name: patientForm.name.trim(), gender: patientForm.gender || null, birthDate: patientForm.birthDate || null, allergies: entries(patientForm.allergies), medicalHistory: entries(patientForm.medicalHistory), medications: entries(patientForm.medications) }
  try {
    const patient = await requestJson<Patient>(editingPatientId.value ? `/api/patients/${editingPatientId.value}` : '/api/patients', jsonBody(payload, editingPatientId.value ? 'PUT' : 'POST'))
    patients.value = await requestJson<Patient[]>('/api/patients')
    if (!editingPatientId.value) { patientId.value = patient.id; selectPatient() }
    showPatientForm.value = false
    notice.value = '患者档案已保存'
  } catch (exc) { showError(exc) }
  finally { patientBusy.value = false }
}

/** 把在途回答的最新片段合并进当前视图；非当前会话不做任何改动。 */
function applyLive(cid: string, finalMessage?: Message) {
  const turn = liveTurns[cid]
  if (!turn || !turn.messageId || activeConversationId.value !== cid) return
  const index = messages.value.findIndex(item => item.id === turn.messageId)
  if (index < 0) return
  messages.value[index] = finalMessage
    ? finalMessage
    : { ...messages.value[index], content: turn.content, citations: [...turn.citations], status: 'generating' }
}

/** 当前正在显示的会话里，该消息对应的活动阶段；其它会话返回 null。 */
function liveStageFor(message: Message) {
  const turn = displayedTurn.value
  return turn && turn.messageId === message.id ? turn.stage : null
}

/** 点击正文 [n] 角标：展开该消息的引用区并定位到对应片段。 */
function focusCitation(message: Message, index: number) {
  citationFocus.value = { messageId: message.id, index }
}

function closeHistoryMenu() {
  historyMenuId.value = ''
  confirmingDeleteId.value = ''
}

function toggleHistoryMenu(conversation: Conversation) {
  if (historyMenuId.value === conversation.id) {
    closeHistoryMenu()
    return
  }
  historyMenuId.value = conversation.id
  confirmingDeleteId.value = ''
  renamingId.value = ''
}

function startRename(conversation: Conversation) {
  renamingId.value = conversation.id
  renameDraft.value = conversation.title
  closeHistoryMenu()
}

async function submitRename() {
  const identifier = renamingId.value
  const title = renameDraft.value.trim()
  if (!identifier || !title) return
  historyBusy.value = true
  error.value = ''
  try {
    const updated = await renameConversation(identifier, title)
    const index = conversations.value.findIndex(item => item.id === identifier)
    if (index >= 0) conversations.value[index] = { ...conversations.value[index], ...updated }
    renamingId.value = ''
    notice.value = '标题已更新'
  } catch (exc) { showError(exc) }
  finally { historyBusy.value = false }
}

async function removeConversation(conversation: Conversation) {
  historyBusy.value = true
  error.value = ''
  try {
    await deleteConversation(conversation.id)
    conversations.value = conversations.value.filter(item => item.id !== conversation.id)
    if (activeConversationId.value === conversation.id) {
      // 当前会话被删掉后不能留下一个指向已删记录的空视图。
      activeConversationId.value = ''
      messages.value = []
      localStorage.removeItem('medical.demo.conversation')
    }
    closeHistoryMenu()
    notice.value = '会话已删除'
  } catch (exc) { showError(exc) }
  finally { historyBusy.value = false }
}

function closeMenuOnOutsideClick(event: MouseEvent) {
  if (!historyMenuId.value) return
  const target = event.target as HTMLElement | null
  if (target?.closest('.history-row')) return
  closeHistoryMenu()
}

async function sendMessage() {
  if (creatingConversation.value || busy.value || !inputText.value.trim() || !patientId.value || !readyDocuments.value.length) return
  if (!activeConversationId.value) await newConversation()
  const cid = activeConversationId.value
  if (!cid || liveTurns[cid]) return
  const content = inputText.value.trim()
  const turnController = new AbortController()
  controller = turnController
  error.value = ''
  // 点击发送后立即占位，避免请求往返期间页面没有任何反馈。
  liveTurns[cid] = { messageId: '', stage: 'intake', content: '', citations: [] }
  try {
    await streamMessage(cid, content, ({ type, data }) => {
      const turn = liveTurns[cid]
      if (!turn) return
      if (type === 'assistant_created') {
        const user = data.userMessage as Message
        const assistant = data.message as Message
        turn.messageId = assistant.id
        inputText.value = ''
        if (activeConversationId.value === cid) messages.value.push(user, { ...assistant, content: '' })
      } else if (type === 'status') {
        turn.stage = String(data.stage ?? turn.stage)
      } else if (type === 'token') {
        turn.content += String(data.delta ?? '')
        applyLive(cid)
      } else if (type === 'citations') {
        turn.citations = data.citations as Citation[]
        applyLive(cid)
      } else if (type === 'done') {
        turn.stage = 'done'
        applyLive(cid, data.message as Message)
      }
    }, turnController.signal)
  } catch (exc) {
    // 断连只提示，不重发；已显示的部分回答保留，最终状态以服务端持久化结果为准。
    if (!(exc instanceof DOMException && exc.name === 'AbortError')) showError(exc)
  } finally {
    delete liveTurns[cid]
    controller = null
    if (!disposed) {
      if (activeConversationId.value === cid) {
        try { messages.value = await requestJson<Message[]>(`/api/conversations/${cid}/messages`) }
        catch (exc) { showError(exc) }
      }
      try { conversations.value = await requestJson<Conversation[]>('/api/conversations') }
      catch (exc) { showError(exc) }
    }
  }
}

function persistTasks() {
  // 只保留仍在查询的任务，刷新后可恢复；已完成的结果已写入文档状态。
  const active: Record<string, string> = {}
  for (const [documentId, taskId] of Object.entries(taskByDocument)) if (polling.has(taskId)) active[documentId] = taskId
  try { sessionStorage.setItem(TASK_STORAGE_KEY, JSON.stringify(active)) } catch { /* 存储不可用时不影响处理 */ }
}

function restoreTasks() {
  try {
    const raw = sessionStorage.getItem(TASK_STORAGE_KEY)
    if (!raw) return
    const saved = JSON.parse(raw) as Record<string, string>
    for (const [documentId, taskId] of Object.entries(saved)) {
      taskByDocument[documentId] = taskId
      polling.add(taskId)
    }
  } catch {
    sessionStorage.removeItem(TASK_STORAGE_KEY)
  }
}

function rememberTask(result: UploadResult) {
  documents.value = [result.document, ...documents.value.filter(document => document.id !== result.document.id)]
  if (result.taskId) {
    taskByDocument[result.document.id] = result.taskId
    polling.add(result.taskId)
    delete unreachableTasks[result.taskId]
    delete taskMisses[result.taskId]
  }
  persistTasks()
  notice.value = result.duplicate
    ? `文档已存在：${result.document.name}（${labelStatus(result.document.status)}）`
    : `文件已接收：${result.document.name}，正在后台处理`
  schedulePoll()
}

async function upload(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  target.value = ''
  if (!file) return
  // 前端先按服务端上限拦一道：超限文件不该先花几分钟传完再被拒。
  if (uploadLimitBytes.value && file.size > uploadLimitBytes.value) {
    error.value = `「${file.name}」约 ${formatSize(file.size)}，超过 ${uploadLimitLabel.value} 上传上限，请压缩或拆分后重试`
    return
  }
  const key = `${Date.now()}-${file.name}`
  attempts.push({ key, name: file.name, size: file.size, state: 'uploading', error: '' })
  error.value = ''
  const form = new FormData()
  form.set('file', file)
  try {
    const result = await requestJson<UploadResult>('/api/knowledge/documents', { method: 'POST', body: form })
    dropAttempt(key)
    rememberTask(result)
  } catch (exc) {
    const attempt = attempts.find(item => item.key === key)
    if (!attempt) return
    attempt.state = 'failed'
    attempt.error = exc instanceof Error ? exc.message : '文件上传失败，请重新选择文件'
  }
}

function dropAttempt(key: string) {
  const index = attempts.findIndex(item => item.key === key)
  if (index >= 0) attempts.splice(index, 1)
}

async function retry(document: KnowledgeDocument) {
  error.value = ''
  try { rememberTask(await requestJson<UploadResult>(`/api/knowledge/documents/${document.id}/retry`, { method: 'POST' })) }
  catch (exc) { showError(exc) }
}

const taskOf = (document: KnowledgeDocument) => {
  const taskId = taskByDocument[document.id]
  return taskId ? taskStates[taskId] : undefined
}
const barOf = (document: KnowledgeDocument) => documentBar(document, taskOf(document))
const isUnreachable = (document: KnowledgeDocument) => {
  const taskId = taskByDocument[document.id]
  return Boolean(taskId && unreachableTasks[taskId])
}
function ingestFailure(document: KnowledgeDocument) {
  const task = taskOf(document)
  if (!task || task.status !== 'failed') return ''
  const stage = barOf(document).failed
  return `处理在${stage ? `「${stageLabel(KNOWLEDGE_STAGES, stage)}」阶段` : ''}失败：${task.error ?? '原因未提供'}`
}

function schedulePoll() {
  if (pollTimer || disposed) return
  pollTimer = setTimeout(() => { pollTimer = undefined; void poll() }, 1500)
}

async function poll() {
  try {
    for (const taskId of [...polling]) {
      try {
        const task = await requestJson<TaskState>(`/api/tasks/${taskId}`)
        taskStates[taskId] = task
        delete taskMisses[taskId]
        delete unreachableTasks[taskId]
        if (task.status === 'succeeded' || task.status === 'failed') { polling.delete(taskId); persistTasks() }
      } catch (exc) {
        // 404 表示进程内任务已失效（服务重启）；其它错误先按连接异常重试，不直接判定失败。
        const expired = exc instanceof ApiError && exc.status === 404
        taskMisses[taskId] = (taskMisses[taskId] ?? 0) + 1
        if (expired || taskMisses[taskId] >= 3) {
          polling.delete(taskId)
          unreachableTasks[taskId] = true
          persistTasks()
          notice.value = '部分入库任务的状态已无法恢复，请刷新页面查看文档状态'
        } else {
          notice.value = '连接异常，正在重新获取状态'
        }
      }
    }
    if (polling.size || documents.value.some(document => document.status === 'processing')) {
      documents.value = await requestJson<KnowledgeDocument[]>('/api/knowledge/documents')
    }
  } catch (exc) { showError(exc) }
  if (polling.size || documents.value.some(document => document.status === 'processing')) schedulePoll()
}

onMounted(initialize)
onMounted(loadUploadLimit)
function syncPath() { currentPath.value = window.location.pathname }
/** 页内跳转必须走前端路由：整页刷新会掐断正在生成的 SSE 连接。 */
function navigate(path: string) {
  if (window.location.pathname === path) return
  window.history.pushState({}, '', path)
  currentPath.value = path
}
function navigateClick(event: MouseEvent, path: string) {
  // 保留 Ctrl/⌘ 点击开新标签页等浏览器原生行为。
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  navigate(path)
}
onMounted(() => window.addEventListener('popstate', syncPath))
// 点击菜单以外的位置收起菜单；菜单内部用 @click.stop 隔开。
onMounted(() => document.addEventListener('click', closeMenuOnOutsideClick))
onBeforeUnmount(() => { disposed = true; controller?.abort(); clearTimeout(pollTimer) })
onBeforeUnmount(() => window.removeEventListener('popstate', syncPath))
onBeforeUnmount(() => document.removeEventListener('click', closeMenuOnOutsideClick))
function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem('medical.demo.sidebar-collapsed', String(sidebarCollapsed.value))
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar"><div class="brand-block"><div class="brand-icon"><Plus :size="18" /></div><div class="brand-copy"><div class="brand-title">智医问康 <span>循证问诊 Demo</span></div><div class="brand-subtitle">真实知识检索 · 多轮问诊 · 来源追溯</div></div></div><div class="top-actions"><span class="demo-label">本地演示环境 · 暂无身份认证</span><button class="secondary-button" :disabled="loading" @click="initialize">刷新数据</button></div></header>
    <div class="engine-bar"><ShieldCheck :size="16" /><span>BGE-M3 CPU · 稠密与稀疏检索 · 暂不使用重排</span><span class="latency">知识文档可检索 {{ readyDocuments.length }} / {{ documents.length }}</span></div>
    <div v-if="error" role="alert" class="global-error">{{ error }}<button @click="error = ''">关闭</button></div>
    <div v-if="notice" role="status" class="global-notice">{{ notice }}</div>
    <nav class="page-nav"><a href="/" :aria-current="currentPath === '/' ? 'page' : undefined" @click="navigateClick($event, '/')">智能问诊</a><a href="/import" :aria-current="currentPath === '/import' ? 'page' : undefined" @click="navigateClick($event, '/import')"><Upload :size="15" />文档入库</a></nav>

    <main v-if="currentPath === '/import'" class="import-page">
      <section class="surface import-heading"><div><BookOpen :size="20" /><div><h1>知识文档入库</h1><p class="muted">仅上传作为公共知识来源的文件；单文件不超过 {{ uploadLimitLabel || '服务端上限' }}。问诊报告附件暂未启用。</p></div></div><label class="upload-button">{{ uploading ? '正在上传…' : '上传 PDF / Markdown' }}<input ref="fileInput" type="file" accept=".pdf,.md,.markdown" :disabled="uploading" @change="upload" /></label></section>
      <section class="import-list"><div class="import-list-heading"><h2>入库文档</h2><span class="muted">{{ readyDocuments.length }} / {{ documents.length }} 可检索</span></div>
        <article v-for="attempt in attempts" :key="attempt.key" class="surface import-document transfer-card">
          <div class="import-document-main"><Upload :size="18" /><div><strong>{{ attempt.name }}</strong><small v-if="attempt.state === 'uploading'">{{ formatSize(attempt.size) }} · 正在上传并保存文件</small><small v-else>{{ formatSize(attempt.size) }} · 文件上传失败</small></div></div>
          <div v-if="attempt.state === 'uploading'" class="activity-bar" role="progressbar" aria-label="正在上传并保存文件"><span /></div>
          <template v-else><p class="failure-text">{{ attempt.error }}</p><button class="text-button" @click="fileInput?.click()">重新选择文件</button><button class="text-button" @click="dropAttempt(attempt.key)">移除</button></template>
        </article>
        <div v-if="!documents.length && !attempts.length" class="import-empty surface"><BookOpen :size="22" /><p>还没有入库文档</p></div>
        <article v-for="document in documents" :key="document.id" class="surface import-document ingest-card">
          <div class="import-document-main"><BookOpen :size="18" /><div><strong>{{ document.name }}</strong><small>{{ document.sourceType.toUpperCase() }} · {{ labelStatus(document.status) }}</small></div></div>
          <span :class="`doc-status ${document.status}`">{{ labelStatus(document.status) }}</span>
          <button v-if="document.status === 'failed'" class="text-button" @click="retry(document)">重新提交入库</button>
          <button class="text-button" @click="viewerCitation = { chunkId: '', documentId: document.id, documentName: document.name, quote: '', headingPath: [], sourceLocations: [] }">查看原文</button>
          <StageBar class="ingest-stages" :stages="KNOWLEDGE_STAGES" v-bind="barOf(document)" />
          <p v-if="document.status === 'ready'" class="stage-result">入库完成，可用于问诊检索</p>
          <p v-else-if="ingestFailure(document)" class="failure-text">{{ ingestFailure(document) }}</p>
          <p v-else-if="isUnreachable(document)" class="stage-result">状态暂时无法获取，请刷新页面重试。</p>
          <p v-else-if="!taskOf(document) && document.status === 'processing'" class="stage-result">该文件不是在本浏览器提交的，只能显示服务器持久化状态。</p>
        </article>
      </section>
    </main>

    <div v-else class="workspace" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
      <aside class="sidebar">
        <button class="sidebar-toggle" :aria-label="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" :title="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" @click="toggleSidebar"><ChevronRight v-if="sidebarCollapsed" :size="18" /><ChevronLeft v-else :size="18" /></button>
        <template v-if="!sidebarCollapsed">
        <button class="new-consult" :disabled="!patientId || creatingConversation" @click="newConversation"><Plus :size="19" />新建智能问诊</button>
        <section class="patient-panel live-patient"><div class="panel-heading"><span>当前咨询对象</span><button @click="openPatientForm()">新增档案</button></div><select v-model="patientId" aria-label="选择患者" @change="selectPatient"><option value="">请选择患者</option><option v-for="patient in patients" :key="patient.id" :value="patient.id">{{ patient.name }}</option></select><template v-if="currentPatient"><div class="patient-summary"><strong>{{ currentPatient.name }}</strong><span>{{ currentPatient.gender || '性别未提供' }} · {{ currentPatient.birthDate ? `出生 ${currentPatient.birthDate.slice(0, 10)}` : '出生日期未提供' }}</span><span>过敏史：{{ currentPatient.allergies.join('、') || '未提供' }}</span><span>既往史：{{ currentPatient.medicalHistory.join('、') || '未提供' }}</span><span>当前用药：{{ currentPatient.medications.join('、') || '未提供' }}</span></div><button class="text-button" @click="openPatientForm(true)">编辑档案</button></template><p v-else class="muted">请新增或选择明确的咨询对象。</p></section>
        <section class="history-panel"><div class="history-heading"><div><History :size="17" />历史咨询</div><span class="count-pill">共 {{ conversations.length }} 条</span></div><div class="history-list"><div v-for="conversation in conversations" :key="conversation.id" class="history-row" :class="{ selected: conversation.id === activeConversationId }"><template v-if="renamingId === conversation.id"><div class="history-rename"><input v-model="renameDraft" maxlength="40" aria-label="会话标题" @keydown.enter.prevent="submitRename" @keydown.esc="renamingId = ''" /><button class="text-button" :disabled="historyBusy || !renameDraft.trim()" @click="submitRename">保存</button><button class="text-button" @click="renamingId = ''">取消</button></div></template><template v-else><button class="history-item" :class="{ selected: conversation.id === activeConversationId }" @click="selectConversation(conversation)"><span v-if="liveTurns[conversation.id]" class="live-pill">生成中</span><strong>{{ conversation.title }}</strong><small>{{ timeLabel(conversation.updatedAt) }}</small></button><button class="history-menu-trigger" type="button" aria-label="会话操作" @click.stop="toggleHistoryMenu(conversation)">⋯</button><div v-if="historyMenuId === conversation.id" class="history-menu" @click.stop><button type="button" @click="startRename(conversation)">重命名</button><template v-if="confirmingDeleteId === conversation.id"><p class="history-menu-warning">删除后无法恢复</p><button type="button" class="danger" :disabled="historyBusy" @click="removeConversation(conversation)">确定删除</button><button type="button" @click="confirmingDeleteId = ''">取消</button></template><button v-else type="button" class="danger" :disabled="Boolean(liveTurns[conversation.id])" :title="liveTurns[conversation.id] ? '生成中不能删除' : ''" @click="confirmingDeleteId = conversation.id">删除</button></div></template></div><p v-if="!conversations.length" class="muted">暂无历史会话</p></div></section>
        <section class="database-panel"><div class="database-title"><ShieldCheck :size="17" />当前可检索证据</div><ul><li v-for="document in readyDocuments" :key="document.id">{{ document.name }}</li></ul><p v-if="!readyDocuments.length" class="muted">暂无可检索文档，请先上传知识文件。</p></section>
        </template>
      </aside>
      <main class="main-content">
        <section v-if="!messages.length" class="empty-consult surface"><div class="empty-icon"><Stethoscope :size="24" /></div><h2>{{ loading ? '正在加载真实数据…' : '开始智能问诊' }}</h2><p>选择患者并确保知识文档可检索后，描述症状、持续时间和相关病史。</p><p>刷新页面会重新读取服务器保存的全部历史消息。</p></section>
        <section v-for="message in messages" :key="message.id" class="surface message-card" :class="message.role"><div class="message-heading"><strong>{{ message.role === 'user' ? '患者描述' : '智医问康 · 智能分析' }}</strong><span>{{ timeLabel(message.createdAt) }} · {{ message.status === 'generating' ? '生成中' : message.status === 'failed' ? '生成失败' : '已保存' }}</span></div>
          <StageBar v-if="liveStageFor(message)" class="message-stages" :stages="CONSULTATION_STAGES" :current="liveStageFor(message)" :finished="liveStageFor(message) === 'done'" />
          <p v-else-if="message.status === 'generating'" class="stage-offline">实时阶段不可用（页面重新加载或服务重启），回答可能仍在生成。</p>
          <MarkdownContent v-if="message.content" class="message-markdown" :content="message.content" :citation-count="message.citations.length" :streaming="message.status === 'generating'" @cite="index => focusCitation(message, index)" />
          <p v-else class="message-content">{{ message.status === 'generating' ? '正在检索与生成…' : '本轮未产生回答' }}</p>
          <p v-if="message.status === 'failed'" class="failure-text">本轮回答未完成；已显示的内容如果存在则保留，请确认服务恢复后重新发送问题。</p>
          <CitationList v-if="message.citations.length" :citations="message.citations" :active="citationFocus?.messageId === message.id ? citationFocus.index : null" @open="citation => (viewerCitation = citation)" />
        </section>
        <section class="followup-card surface"><div class="followup-heading"><div><Stethoscope :size="17" /><strong>{{ selectedConversation ? `继续问诊：${selectedConversation.title}` : '描述本次咨询问题' }}</strong></div><span v-if="busy" class="live-pill">本会话生成中，可切换会话或页面</span></div><p v-if="!readyDocuments.length" class="failure-text">知识库无可用证据，暂不发送问诊。</p><div class="composer"><textarea v-model="inputText" placeholder="请输入症状、持续时间、相关病史和目前用药（Ctrl + Enter 发送）" @keydown.ctrl.enter.prevent="sendMessage"></textarea><div class="composer-bottom"><span class="muted">附件、独立搜索、通知和语音暂未启用</span><button class="send-button" :disabled="busy || creatingConversation || !patientId || !readyDocuments.length || !inputText.trim()" @click="sendMessage">{{ busy ? '生成中…' : '发送问诊' }}<Send :size="15" /></button></div></div></section>
      </main>
    </div>
    <footer class="footer-bar">AI 内容用于健康参考与分诊建议，不能替代执业医师面诊。</footer>
    <SourceViewer v-if="viewerCitation" :key="viewerCitation.documentId + viewerCitation.chunkId" :citation="viewerCitation" @close="viewerCitation = null" />
    <div v-if="showPatientForm" class="viewer-backdrop" @click.self="showPatientForm = false"><form class="patient-form surface" @submit.prevent="savePatient"><h2>{{ editingPatientId ? '编辑患者档案' : '新增患者档案' }}</h2><label>姓名或称呼<input v-model="patientForm.name" required maxlength="100" /></label><label>性别<select v-model="patientForm.gender"><option value="">未知 / 未提供</option><option value="男">男</option><option value="女">女</option><option value="其他">其他</option></select></label><label>出生日期（未知请留空，不按年龄推算）<input v-model="patientForm.birthDate" type="date" /></label><label>过敏史（每行一项，未知留空）<textarea v-model="patientForm.allergies"></textarea></label><label>既往病史（每行一项）<textarea v-model="patientForm.medicalHistory"></textarea></label><label>当前用药（每行一项）<textarea v-model="patientForm.medications"></textarea></label><div class="form-actions"><button type="button" class="secondary-button" @click="showPatientForm = false">取消</button><button class="send-button" :disabled="patientBusy || !patientForm.name.trim()">{{ patientBusy ? '保存中…' : '保存档案' }}</button></div></form></div>
  </div>
</template>
