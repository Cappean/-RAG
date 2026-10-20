<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { BookOpen, ChevronLeft, ChevronRight, History, Plus, Send, ShieldCheck, Stethoscope, Upload } from 'lucide-vue-next'
import SourceViewer from './SourceViewer.vue'
import { jsonBody, requestJson, streamMessage, type Citation, type Conversation, type KnowledgeDocument, type Message, type Patient, type PatientInput, type TaskState, type UploadResult } from './api'

const patients = ref<Patient[]>([])
const conversations = ref<Conversation[]>([])
const documents = ref<KnowledgeDocument[]>([])
const messages = ref<Message[]>([])
const patientId = ref('')
const activeConversationId = ref('')
const inputText = ref('')
const error = ref('')
const notice = ref('')
const busy = ref(false)
const loading = ref(false)
const creatingConversation = ref(false)
const streamStatus = ref('')
const uploadBusy = ref(false)
const viewerCitation = ref<Citation | null>(null)
const showPatientForm = ref(false)
const editingPatientId = ref('')
const patientBusy = ref(false)
const patientForm = reactive({ name: '', gender: '', birthDate: '', allergies: '', medicalHistory: '', medications: '' })
const taskStates = reactive<Record<string, TaskState>>({})
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
const labelStatus = (status: string) => ({ processing: '处理中', ready: '可检索', failed: '失败', disabled: '已停用', queued: '排队中', running: '运行中', succeeded: '完成' }[status] ?? status)
const timeLabel = (value: string) => new Date(value).toLocaleString('zh-CN')
const showError = (exc: unknown) => { error.value = exc instanceof Error ? exc.message : '请求失败，请检查服务连接' }

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
    schedulePoll()
  } catch (exc) { showError(exc) }
  finally { loading.value = false }
}

async function selectConversation(conversation: Conversation) {
  if (busy.value) return
  const version = ++selectionVersion
  activeConversationId.value = conversation.id
  patientId.value = conversation.patientId ?? ''
  localStorage.setItem('medical.demo.conversation', conversation.id)
  messages.value = []
  loading.value = true
  error.value = ''
  try {
    const history = await requestJson<Message[]>(`/api/conversations/${conversation.id}/messages`)
    if (version === selectionVersion) messages.value = history
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
  if (!patientId.value || busy.value || creatingConversation.value) return
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

async function sendMessage() {
  if (busy.value || creatingConversation.value || !inputText.value.trim() || !patientId.value || !readyDocuments.value.length) return
  if (!activeConversationId.value) await newConversation()
  if (!activeConversationId.value) return
  busy.value = true
  error.value = ''
  streamStatus.value = '正在连接问诊服务…'
  controller = new AbortController()
  const content = inputText.value.trim()
  try {
    await streamMessage(activeConversationId.value, content, ({ type, data }) => {
      if (type === 'assistant_created') {
        messages.value.push(data.userMessage as Message, data.message as Message)
        inputText.value = ''
      } else if (type === 'status') streamStatus.value = String(data.stage ?? '正在处理')
      else if (type === 'token') {
        const message = messages.value.find(item => item.id === data.messageId)
        if (message) message.content += String(data.delta ?? '')
      } else if (type === 'citations') {
        const message = messages.value.find(item => item.id === data.messageId)
        if (message) message.citations = data.citations as Citation[]
      } else if (type === 'done') {
        const completed = data.message as Message
        const index = messages.value.findIndex(item => item.id === completed.id)
        if (index >= 0) messages.value[index] = completed
        notice.value = data.retrievalMs != null ? `本轮检索 ${Number(data.retrievalMs).toFixed(0)} ms` : '回答已保存'
      }
    }, controller.signal)
  } catch (exc) { showError(exc) }
  finally {
    busy.value = false
    streamStatus.value = ''
    controller = null
    // Reconcile persisted state after completion, disconnect or server-side failure.
    if (!disposed) {
      try { messages.value = await requestJson<Message[]>(`/api/conversations/${activeConversationId.value}/messages`) }
      catch (exc) { showError(exc) }
    }
    if (!disposed) {
      try { conversations.value = await requestJson<Conversation[]>('/api/conversations') }
      catch (exc) { showError(exc) }
    }
  }
}

function rememberTask(result: UploadResult) {
  documents.value = [result.document, ...documents.value.filter(document => document.id !== result.document.id)]
  if (result.taskId) polling.add(result.taskId)
  notice.value = result.duplicate ? '文件已存在，已返回真实文档状态' : '文件已接收，等待入库完成'
  schedulePoll()
}

async function upload(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  uploadBusy.value = true
  error.value = ''
  const form = new FormData()
  form.set('file', file)
  try { rememberTask(await requestJson<UploadResult>('/api/knowledge/documents', { method: 'POST', body: form })) }
  catch (exc) { showError(exc) }
  finally { uploadBusy.value = false; target.value = '' }
}
async function retry(document: KnowledgeDocument) {
  error.value = ''
  try { rememberTask(await requestJson<UploadResult>(`/api/knowledge/documents/${document.id}/retry`, { method: 'POST' })) }
  catch (exc) { showError(exc) }
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
        if (task.status === 'succeeded' || task.status === 'failed') polling.delete(taskId)
      } catch (exc) { polling.delete(taskId); showError(exc) }
    }
    if (documents.value.some(document => document.status === 'processing') || Object.keys(taskStates).length) documents.value = await requestJson<KnowledgeDocument[]>('/api/knowledge/documents')
  } catch (exc) { showError(exc) }
  if (polling.size || documents.value.some(document => document.status === 'processing')) schedulePoll()
}

onMounted(initialize)
function syncPath() { currentPath.value = window.location.pathname }
onMounted(() => window.addEventListener('popstate', syncPath))
onBeforeUnmount(() => { disposed = true; controller?.abort(); clearTimeout(pollTimer) })
onBeforeUnmount(() => window.removeEventListener('popstate', syncPath))
function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem('medical.demo.sidebar-collapsed', String(sidebarCollapsed.value))
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar"><div class="brand-block"><div class="brand-icon"><Plus :size="18" /></div><div class="brand-copy"><div class="brand-title">智医问康 <span>循证问诊 Demo</span></div><div class="brand-subtitle">真实知识检索 · 多轮问诊 · 来源追溯</div></div></div><div class="top-actions"><span class="demo-label">本地演示环境 · 暂无身份认证</span><button class="secondary-button" :disabled="busy || loading" @click="initialize">刷新数据</button></div></header>
    <div class="engine-bar"><ShieldCheck :size="16" /><span>BGE-M3 CPU · 稠密与稀疏检索 · 暂不使用重排</span><span class="latency">知识文档可检索 {{ readyDocuments.length }} / {{ documents.length }}</span></div>
    <div v-if="error" role="alert" class="global-error">{{ error }}<button @click="error = ''">关闭</button></div>
    <div v-if="notice" role="status" class="global-notice">{{ notice }}</div>
    <nav class="page-nav"><a href="/" :aria-current="currentPath === '/' ? 'page' : undefined">智能问诊</a><a href="/import" :aria-current="currentPath === '/import' ? 'page' : undefined"><Upload :size="15" />文档入库</a></nav>
    <main v-if="currentPath === '/import'" class="import-page">
      <section class="surface import-heading"><div><BookOpen :size="20" /><div><h1>知识文档入库</h1><p class="muted">仅上传作为公共知识来源的文件；问诊报告附件暂未启用。</p></div></div><label class="upload-button">{{ uploadBusy ? '正在上传…' : '上传 PDF / Markdown' }}<input type="file" accept=".pdf,.md,.markdown" :disabled="uploadBusy" @change="upload" /></label></section>
      <section class="import-list"><div class="import-list-heading"><h2>入库文档</h2><span class="muted">{{ readyDocuments.length }} / {{ documents.length }} 可检索</span></div><div v-if="!documents.length" class="import-empty surface"><BookOpen :size="22" /><p>还没有入库文档</p></div><article v-for="document in documents" :key="document.id" class="surface import-document"><div class="import-document-main"><BookOpen :size="18" /><div><strong>{{ document.name }}</strong><small>{{ document.sourceType.toUpperCase() }} · {{ labelStatus(document.status) }}</small></div></div><span :class="`doc-status ${document.status}`">{{ labelStatus(document.status) }}</span><button v-if="document.status === 'failed'" class="text-button" @click="retry(document)">重新提交入库</button><button class="text-button" @click="viewerCitation = { chunkId: '', documentId: document.id, documentName: document.name, quote: '', headingPath: [], sourceLocations: [] }">查看原文</button></article><p v-for="task in taskStates" :key="task.taskId" class="task-line">任务 {{ task.taskId.slice(0, 8) }}：{{ labelStatus(task.status) }} {{ task.progress || '' }}<span v-if="task.error" class="failure-text"> · {{ task.error }}</span></p></section>
    </main>
    <div v-else class="workspace" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
      <aside class="sidebar">
        <button class="sidebar-toggle" :aria-label="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" :title="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" @click="toggleSidebar"><ChevronRight v-if="sidebarCollapsed" :size="18" /><ChevronLeft v-else :size="18" /></button>
        <template v-if="!sidebarCollapsed">
        <button class="new-consult" :disabled="!patientId || busy || creatingConversation" @click="newConversation"><Plus :size="19" />新建智能问诊</button>
        <section class="patient-panel live-patient"><div class="panel-heading"><span>当前咨询对象</span><button :disabled="busy" @click="openPatientForm()">新增档案</button></div><select v-model="patientId" aria-label="选择患者" :disabled="busy" @change="selectPatient"><option value="">请选择患者</option><option v-for="patient in patients" :key="patient.id" :value="patient.id">{{ patient.name }}</option></select><template v-if="currentPatient"><div class="patient-summary"><strong>{{ currentPatient.name }}</strong><span>{{ currentPatient.gender || '性别未提供' }} · {{ currentPatient.birthDate ? `出生 ${currentPatient.birthDate.slice(0, 10)}` : '出生日期未提供' }}</span><span>过敏史：{{ currentPatient.allergies.join('、') || '未提供' }}</span><span>既往史：{{ currentPatient.medicalHistory.join('、') || '未提供' }}</span><span>当前用药：{{ currentPatient.medications.join('、') || '未提供' }}</span></div><button class="text-button" :disabled="busy" @click="openPatientForm(true)">编辑档案</button></template><p v-else class="muted">请新增或选择明确的咨询对象。</p></section>
        <section class="history-panel"><div class="history-heading"><div><History :size="17" />历史咨询</div><span class="count-pill">共 {{ conversations.length }} 条</span></div><div class="history-list"><button v-for="conversation in conversations" :key="conversation.id" class="history-item" :class="{ selected: conversation.id === activeConversationId }" :disabled="busy" @click="selectConversation(conversation)"><strong>{{ conversation.title }}</strong><small>{{ timeLabel(conversation.updatedAt) }}</small></button><p v-if="!conversations.length" class="muted">暂无历史会话</p></div></section>
        <section class="database-panel"><div class="database-title"><ShieldCheck :size="17" />当前可检索证据</div><ul><li v-for="document in readyDocuments" :key="document.id">{{ document.name }}</li></ul><p v-if="!readyDocuments.length" class="muted">暂无可检索文档，请先上传知识文件。</p></section>
        </template>
      </aside>
      <main class="main-content">
        <section v-if="!messages.length" class="empty-consult surface"><div class="empty-icon"><Stethoscope :size="24" /></div><h2>{{ loading ? '正在加载真实数据…' : '开始智能问诊' }}</h2><p>选择患者并确保知识文档可检索后，描述症状、持续时间和相关病史。</p><p>刷新页面会重新读取服务器保存的全部历史消息。</p></section>
        <section v-for="message in messages" :key="message.id" class="surface message-card" :class="message.role"><div class="message-heading"><strong>{{ message.role === 'user' ? '患者描述' : '智医问康 · 智能分析' }}</strong><span>{{ timeLabel(message.createdAt) }} · {{ message.status === 'generating' ? '生成中' : message.status === 'failed' ? '生成失败' : '已保存' }}</span></div><p class="message-content">{{ message.content || (message.status === 'generating' ? '正在检索与生成…' : '本轮未产生回答') }}</p><p v-if="message.status === 'failed'" class="failure-text">本轮回答未完成；请查看错误提示，确认服务恢复后重新发送问题。</p><button v-for="citation in message.citations" :key="citation.chunkId" class="evidence-card" @click="viewerCitation = citation"><div class="evidence-head"><div><BookOpen :size="15" /><strong>{{ citation.documentName }}</strong></div><span>查看引用原文</span></div><p>{{ citation.quote }}</p><small>{{ citation.headingPath.join(' / ') }}</small></button></section>
        <div v-if="busy" role="status" class="stream-status">{{ streamStatus }}</div>
        <section class="followup-card surface"><div class="followup-heading"><div><Stethoscope :size="17" /><strong>{{ selectedConversation ? `继续问诊：${selectedConversation.title}` : '描述本次咨询问题' }}</strong></div></div><p v-if="!readyDocuments.length" class="failure-text">知识库无可用证据，暂不发送问诊。</p><div class="composer"><textarea v-model="inputText" :disabled="busy" placeholder="请输入症状、持续时间、相关病史和目前用药（Ctrl + Enter 发送）" @keydown.ctrl.enter.prevent="sendMessage"></textarea><div class="composer-bottom"><span class="muted">附件、独立搜索、通知和语音暂未启用</span><button class="send-button" :disabled="busy || loading || !patientId || !readyDocuments.length || !inputText.trim()" @click="sendMessage">{{ busy ? '生成中…' : '发送问诊' }}<Send :size="15" /></button></div></div></section>
      </main>
    </div>
    <footer class="footer-bar">AI 内容用于健康参考与分诊建议，不能替代执业医师面诊。</footer>
    <SourceViewer v-if="viewerCitation" :key="viewerCitation.documentId + viewerCitation.chunkId" :citation="viewerCitation" @close="viewerCitation = null" />
    <div v-if="showPatientForm" class="viewer-backdrop" @click.self="showPatientForm = false"><form class="patient-form surface" @submit.prevent="savePatient"><h2>{{ editingPatientId ? '编辑患者档案' : '新增患者档案' }}</h2><label>姓名或称呼<input v-model="patientForm.name" required maxlength="100" /></label><label>性别<select v-model="patientForm.gender"><option value="">未知 / 未提供</option><option value="男">男</option><option value="女">女</option><option value="其他">其他</option></select></label><label>出生日期（未知请留空，不按年龄推算）<input v-model="patientForm.birthDate" type="date" /></label><label>过敏史（每行一项，未知留空）<textarea v-model="patientForm.allergies"></textarea></label><label>既往病史（每行一项）<textarea v-model="patientForm.medicalHistory"></textarea></label><label>当前用药（每行一项）<textarea v-model="patientForm.medications"></textarea></label><div class="form-actions"><button type="button" class="secondary-button" @click="showPatientForm = false">取消</button><button class="send-button" :disabled="patientBusy || !patientForm.name.trim()">{{ patientBusy ? '保存中…' : '保存档案' }}</button></div></form></div>
  </div>
</template>
