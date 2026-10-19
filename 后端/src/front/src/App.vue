<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  Bell, BookOpen, Camera, Check, CheckCircle2, ChevronDown,
  ClipboardList, FilePlus2, FileText, HeartPulse, History, Menu, Mic,
  Plus, Search, Send, Settings2, ShieldCheck, Stethoscope, Tag, UserRound,
  X, Zap,
} from 'lucide-vue-next'
import { createConversation, fetchConversationContent, fetchConversations, submitSupplement, type Citation, type Conversation, type ConversationContent, type ConversationExchange } from './api'

const conversations = ref<Conversation[]>([])
const activeConversationId = ref('c-1')
const activeContent = ref<ConversationContent | null>(null)
const exchanges = ref<ConversationExchange[]>([])
const inputText = ref('')
const selectedFile = ref<File | null>(null)
const busy = ref(false)
const streamedReply = ref('')
const streamStatus = ref('')
const notice = ref('')
const viewerCitation = ref<Citation | null>(null)
const showAllHistory = ref(false)
const currentPatient = ref('林晨（本人）')
const newConversation = async () => {
  try {
    const conversation = await createConversation()
    conversations.value.unshift({ ...conversation, active: true })
    activeConversationId.value = conversation.id
  } catch {
    notice.value = '创建会话失败，请稍后重试'
    window.setTimeout(() => { notice.value = '' }, 2600)
    return
  }
  activeContent.value = null
  exchanges.value = []
  conversations.value.forEach((item) => { item.active = item.id === activeConversationId.value })
  notice.value = '已创建新的问诊会话'
  window.setTimeout(() => { notice.value = '' }, 2200)
}
const selectConversation = (conversation: Conversation) => {
  activeConversationId.value = conversation.id
  void loadConversation(conversation.id)
  conversations.value.forEach((item) => { item.active = item.id === conversation.id })
}
const loadConversation = async (id: string) => {
  activeContent.value = await fetchConversationContent(id)
  exchanges.value = []
}
const visibleConversations = computed(() => showAllHistory.value ? conversations.value : conversations.value.slice(0, 3))

onMounted(async () => {
  conversations.value = await fetchConversations()
  if (!conversations.value.some((item) => item.id === activeConversationId.value)) {
    activeConversationId.value = conversations.value[0]?.id ?? ''
  }
  await loadConversation(activeConversationId.value)
})

const attachFile = (event: Event) => {
  const target = event.target as HTMLInputElement
  selectedFile.value = target.files?.[0] ?? null
}
const removeFile = () => { selectedFile.value = null }
const sendSupplement = async () => {
  if ((!inputText.value.trim() && !selectedFile.value) || busy.value) return
  busy.value = true
  streamedReply.value = ''
  streamStatus.value = ''
  const userText = inputText.value.trim()
  const file = selectedFile.value
  try {
    const result = await submitSupplement(activeConversationId.value, userText, file, {
      onToken: (token) => { streamedReply.value += token },
      onStatus: (status) => { streamStatus.value = status },
    })
    if (result.citations.length && activeContent.value) {
      activeContent.value.citations = result.citations
      activeContent.value.evidenceTitle = `RAG多源循证检索完成（命中 ${result.citations.length} 份核心指南）`
    }
    if (!activeContent.value) {
      activeContent.value = {
        question: userText || `请查看我上传的资料：${file?.name ?? ''}`,
        sentAt: `刚刚 ${result.generatedAt}`,
        entities: [],
        evidenceTitle: '本轮 mock 问诊暂未关联指南来源',
        citations: [],
        analysisTitle: '智能分析',
        analysis: result.message,
        recommendation: '请结合实际症状，必要时咨询医生',
      }
      const conversation = conversations.value.find((item) => item.id === activeConversationId.value)
      if (conversation) {
        conversation.title = (userText || `上传资料：${file?.name ?? ''}`).slice(0, 24)
        conversation.preview = result.message.slice(0, 34)
      }
    } else {
      exchanges.value.push({
        id: `exchange-${Date.now()}`,
        userText: userText || '上传了一份资料',
        fileName: file?.name,
        assistantText: result.message,
        sentAt: result.generatedAt,
      })
    }
    inputText.value = ''
    selectedFile.value = null
    notice.value = `补充已发送 · ${result.generatedAt}`
    window.setTimeout(() => { notice.value = '' }, 2600)
  } catch {
    notice.value = '发送失败，请稍后重试'
    window.setTimeout(() => { notice.value = '' }, 2600)
  } finally {
    busy.value = false
    streamedReply.value = ''
    streamStatus.value = ''
  }
}
const openCitation = (citation: Citation) => { viewerCitation.value = citation }
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand-block">
        <div class="brand-icon"><Plus :size="18" :stroke-width="3" /></div>
        <div class="brand-copy">
          <div class="brand-title">智医问康 <span>RAG临床决策</span></div>
          <div class="brand-subtitle">国家卫健委 / 中华医学会指南增强</div>
        </div>
      </div>
      <div class="top-actions">
        <div class="online-pill"><i></i> RAG知识库 · 在线</div>
        <button class="search-button" type="button"><Search :size="15" /> 搜索疾病、症状或药品...</button>
        <button class="icon-button notification" title="通知"><Bell :size="18" /><i></i></button>
        <button class="profile-button" title="个人中心"><UserRound :size="18" /></button>
      </div>
    </header>

    <div class="engine-bar">
      <span class="status-switch"><i></i></span>
      <span class="engine-dot"><Settings2 :size="14" /></span>
      <span>医学多模态向量嵌入引擎：<b>v4.2.0-MedPro</b></span>
      <div class="latency"><Zap :size="15" /> RAG检索延迟：<b>180ms</b><span></span><span></span></div>
    </div>

    <div class="workspace">
      <aside class="sidebar">
        <button class="new-consult" type="button" @click="newConversation"><Plus :size="19" /> 新建智能问诊</button>

        <section class="patient-panel">
          <div class="panel-heading"><span>当前咨询对象</span><button type="button" @click="currentPatient = currentPatient.includes('林晨') ? '林母（家属）' : '林晨（本人）'">切换档案</button></div>
          <div class="patient-info">
            <div class="patient-avatar">林</div>
            <div><strong>{{ currentPatient }}</strong><span>男 · 34岁 · 无药物过敏史</span></div>
            <ChevronDown class="patient-chevron" :size="15" />
          </div>
        </section>

        <section class="history-panel">
          <div class="history-heading"><div><History :size="17" /> <span>历史咨询记录</span></div><span class="count-pill">共 14 条</span></div>
          <div class="history-list">
            <button v-for="conversation in visibleConversations" :key="conversation.id" class="history-item" :class="{ selected: conversation.id === activeConversationId }" type="button" @click="selectConversation(conversation)">
              <div class="history-meta"><span>{{ conversation.timeLabel }}</span></div>
              <strong>{{ conversation.title }}</strong>
              <small>{{ conversation.preview }}</small>
            </button>
          </div>
          <button class="more-history" type="button" @click="showAllHistory = !showAllHistory">{{ showAllHistory ? '收起历史会话' : '查看更多历史会话 (10+)' }}</button>
        </section>

        <section class="database-panel">
          <div class="database-title"><ShieldCheck :size="17" /> 当前挂载医学证据库</div>
          <ul>
            <li>中华医学会消化病学分会指南库</li>
            <li>国家消化系统疾病临床医学中心指导</li>
            <li>国家基本药物临床应用指南 (2023)</li>
          </ul>
        </section>
      </aside>

      <main class="main-content">
        <section v-if="!activeContent" class="empty-consult surface">
          <div class="empty-icon"><Stethoscope :size="24" /></div>
          <h2>开始新的智能问诊</h2>
          <p>请在下方描述症状、持续时间和相关病史，或上传检查报告。</p>
          <div class="empty-prompts"><span>症状从什么时候开始？</span><span>什么情况会加重或缓解？</span><span>目前是否正在用药？</span></div>
        </section>
        <template v-else>
        <section class="question-card surface">
          <div class="patient-message-heading"><div class="patient-avatar small">患</div><strong>患者&nbsp; {{ currentPatient.split('（')[0] }}</strong><span>{{ activeContent.sentAt }} 发送</span></div>
          <div class="question-bubble">“{{ activeContent.question }}”</div>
          <div class="entity-row"><Tag :size="15" /><span>识别实体：</span><span v-for="entity in activeContent.entities" :key="entity" class="entity-chip">{{ entity }}</span></div>
        </section>

        <section class="evidence-section">
          <div class="section-title"><span class="search-mark"><Search :size="14" /></span><strong>{{ activeContent.evidenceTitle }}</strong></div>
          <button v-for="citation in activeContent.citations" :key="citation.id" class="evidence-card" type="button" @click="openCitation(citation)">
            <div class="evidence-head"><div><BookOpen :size="15" /><strong>{{ citation.title }}</strong></div><span class="score-pill">语义匹配度 {{ citation.score }}</span></div>
            <p>“...{{ citation.quote }}...”</p>
          </button>
        </section>

        <section class="analysis-card surface">
          <div class="analysis-header">
            <div class="analysis-icon"><Stethoscope :size="19" /></div>
            <div class="analysis-heading"><strong>智能分析</strong><span>基于循证医学指南 · 智医问康临床辅助生成</span></div>
            <span class="complete-pill"><CheckCircle2 :size="15" /> 生成完成</span>
          </div>
          <div class="analysis-title"><i></i>{{ activeContent.analysisTitle }}</div>
          <div class="analysis-body">
            <p>{{ activeContent.analysis }}</p>
          </div>
          <div class="recommendation-row"><div><HeartPulse :size="15" /><strong>建议下一步</strong></div><span>{{ activeContent.recommendation }}</span></div>
        </section>

        <section v-for="exchange in exchanges" :key="exchange.id" class="exchange-thread">
          <div class="exchange-user"><div class="patient-avatar small">患</div><div><strong>补充信息</strong><span>{{ exchange.sentAt }}</span><p>{{ exchange.userText }}<small v-if="exchange.fileName"><FileText :size="13" /> {{ exchange.fileName }}</small></p></div></div>
          <div class="exchange-assistant"><div class="analysis-icon"><Stethoscope :size="17" /></div><div><strong>智医问康</strong><span>{{ exchange.sentAt }} · mock 回复</span><p>{{ exchange.assistantText }}</p></div></div>
        </section>
        </template>

        <div v-if="busy" class="exchange-assistant streaming-reply">
          <div class="analysis-icon"><Stethoscope :size="17" /></div>
          <div><strong>智医问康</strong><span>{{ streamStatus || '正在生成回复…' }}</span><p>{{ streamedReply || streamStatus || '正在连接问诊服务…' }}</p></div>
        </div>

        <section class="followup-card surface">
          <div class="followup-heading"><div><ClipboardList :size="17" /><strong>追问补充病情或上传化验单</strong></div><span><i></i> 支持医患隐私脱敏</span></div>
          <div class="composer">
            <textarea v-model="inputText" placeholder="请输入补充症状（例如：是否排黑便、既往有无胃病史、近期是否处于高压熬夜状态等）..." @keydown.ctrl.enter.prevent="sendSupplement"></textarea>
            <div class="composer-bottom">
              <div class="attachment-actions">
                <label class="attach-button"><FilePlus2 :size="15" /> 化验单/报告<input type="file" accept=".pdf,.png,.jpg,.jpeg,.doc,.docx" @change="attachFile" /></label>
                <label class="attach-button"><Camera :size="15" /> 舌苔/体征<input type="file" accept="image/*" @change="attachFile" /></label>
                <button class="attach-icon" type="button" title="语音输入" @click="notice = '语音输入接口待接入'"><Mic :size="16" /></button>
                <span v-if="selectedFile" class="file-chip"><FileText :size="13" />{{ selectedFile.name }}<button type="button" aria-label="移除附件" @click="removeFile"><X :size="13" /></button></span>
              </div>
              <button class="send-button" type="button" :disabled="busy || (!inputText.trim() && !selectedFile)" @click="sendSupplement">{{ busy ? '生成中…' : '发送补充' }} <Send :size="15" /></button>
            </div>
          </div>
        </section>
      </main>
    </div>

    <footer class="footer-bar"><strong>免责声明与合规说明：</strong>本平台AI生成内容严格基于国家卫健委规范及临床循证指南，仅供健康健康参考与分诊建议，不可替代执业医师面诊。<span class="footer-spacer"></span><span><ShieldCheck :size="13" /> 数据同步：NMPA药监局与卫健委 2024.Q4</span><span>京ICP备20240982号-1</span></footer>

    <transition name="fade">
      <div v-if="viewerCitation" class="viewer-backdrop" @click.self="viewerCitation = null">
        <section class="document-viewer">
          <header><div><FileText :size="18" /><div><strong>{{ viewerCitation.title }}</strong><span>{{ viewerCitation.chapter }} · PDF 第 {{ viewerCitation.page }} 页</span></div></div><button type="button" title="关闭" @click="viewerCitation = null"><X :size="19" /></button></header>
          <div class="viewer-toolbar"><button type="button"><Menu :size="15" /> 目录</button><span class="page-nav">第 <b>{{ viewerCitation.page }}</b> / 48 页</span><button type="button">−</button><span>100%</span><button type="button">+</button><button class="download-link" type="button"><FileText :size="14" /> 原文</button></div>
          <div class="pdf-page"><div class="pdf-kicker">中华医学会消化病学分会</div><h2>{{ viewerCitation.chapter }}</h2><div class="pdf-rule"></div><p>消化性溃疡是指胃肠道黏膜被胃酸和胃蛋白酶消化而造成的溃疡，常见于胃和十二指肠。临床表现与溃疡部位、大小、病程及有无并发症有关。</p><p class="pdf-highlight">{{ viewerCitation.quote }}</p><p>典型的消化性溃疡具有慢性、周期性和节律性的特点。患者可表现为上腹部疼痛、反酸、嗳气等，部分患者症状不典型或无症状。</p><h3>诊断与评估</h3><p>应结合病史、体格检查及必要的辅助检查综合判断。幽门螺杆菌感染是消化性溃疡的重要病因，可根据患者情况选择尿素呼气试验、粪便抗原检测或内镜检查。</p><div class="pdf-page-number">— {{ viewerCitation.page }} —</div></div>
          <div class="viewer-source-note"><Check :size="15" /> 已定位到引用内容 <span>来源片段 {{ viewerCitation.id }}</span></div>
        </section>
      </div>
    </transition>
    <transition name="fade"><div v-if="notice" class="toast"><CheckCircle2 :size="16" />{{ notice }}</div></transition>
  </div>
</template>
