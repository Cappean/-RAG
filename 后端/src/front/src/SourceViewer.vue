<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as pdfjs from 'pdfjs-dist'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { apiUrl, requestJson, type Citation, type SourceLocation } from './api'

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker
const props = defineProps<{ citation: Citation }>()
defineEmits<{ close: [] }>()
const canvas = ref<HTMLCanvasElement | null>(null)
const reader = ref<HTMLElement | null>(null)
const lines = ref<string[]>([])
const sourceType = ref<'pdf' | 'md'>('pdf')
const page = ref(1)
const pages = ref(0)
const scale = ref(1.25)
const loading = ref(true)
const error = ref('')
const originalUrl = ref('')
const overlays = ref<SourceLocation[]>([])
const locationNote = ref('')
let pdf: pdfjs.PDFDocumentProxy | null = null
let renderTask: pdfjs.RenderTask | null = null
let closed = false
let rendering = false

const highlightedLine = (index: number) => props.citation.sourceLocations.some(location =>
  location.startLine != null && location.endLine != null && index + 1 >= location.startLine && index + 1 <= location.endLine)

/** Canvas and overlays share CSS dimensions; normalized boxes remain valid at any zoom. */
async function renderPage() {
  if (!pdf || !canvas.value || rendering || closed) return false
  rendering = true
  error.value = ''
  try {
    const pdfPage = await pdf.getPage(page.value)
    const viewport = pdfPage.getViewport({ scale: scale.value })
    const target = canvas.value
    const pixelRatio = window.devicePixelRatio || 1
    target.width = Math.ceil(viewport.width * pixelRatio)
    target.height = Math.ceil(viewport.height * pixelRatio)
    target.style.width = `${viewport.width}px`
    target.style.height = `${viewport.height}px`
    const context = target.getContext('2d')
    if (!context) throw new Error('浏览器无法创建 PDF 画布')
    renderTask = pdfPage.render({ canvasContext: context, viewport, transform: [pixelRatio, 0, 0, pixelRatio, 0, 0] })
    await renderTask.promise
    overlays.value = props.citation.sourceLocations.filter(location => location.pageNumber === page.value &&
      location.bbox?.length === 4 && location.bbox.every(value => Number.isFinite(value) && value >= 0 && value <= 1) &&
      location.bbox[2]! > location.bbox[0]! && location.bbox[3]! > location.bbox[1]!)
    await nextTick()
    // The first source region may be near the bottom of a long physical page.
    // Bring that real region into view instead of leaving it below the viewport.
    reader.value?.querySelector('.source-bbox')?.scrollIntoView({ block: 'center', inline: 'center' })
    return true
  } catch (exc) {
    if (!closed) error.value = exc instanceof Error ? exc.message : 'PDF 渲染失败'
    return false
  } finally { rendering = false }
}

async function navigate(delta: number) {
  if (rendering) return
  page.value = Math.min(pages.value, Math.max(1, page.value + delta))
  await renderPage()
}
async function zoom(delta: number) {
  if (rendering) return
  scale.value = Math.max(0.5, Math.min(2.5, scale.value + delta))
  await renderPage()
}
const boxStyle = (location: SourceLocation) => {
  const box = location.bbox!
  return { left: `${box[0]! * 100}%`, top: `${box[1]! * 100}%`, width: `${(box[2]! - box[0]!) * 100}%`, height: `${(box[3]! - box[1]!) * 100}%` }
}

onMounted(async () => {
  try {
    const access = await requestJson<{ sourceType: 'pdf' | 'md'; url: string }>(`/api/knowledge/documents/${props.citation.documentId}/access-url`)
    if (closed) return
    sourceType.value = access.sourceType
    originalUrl.value = access.url.startsWith('/') ? apiUrl(access.url) : access.url
    const response = await fetch(originalUrl.value)
    if (!response.ok) throw new Error('原文访问失败，短链接可能已过期，请关闭后重新打开')
    if (sourceType.value === 'md') {
      // Exact original text, one DOM node per physical line. HTML/images are never executed.
      lines.value = (await response.text()).split(/\r\n|\n|\r/)
      loading.value = false
      await nextTick()
      const target = reader.value?.querySelector('.line-highlight')
      if (target) {
        target.scrollIntoView({ block: 'center' })
        locationNote.value = '已定位并高亮原文行号；按原始 Markdown 显示'
      } else locationNote.value = '该引用没有可用行号，仅打开原文件'
    } else {
      pdf = await pdfjs.getDocument({
        data: await response.arrayBuffer(), isEvalSupported: false,
        cMapUrl: '/pdfjs/cmaps/', cMapPacked: true,
        standardFontDataUrl: '/pdfjs/standard_fonts/',
      }).promise
      if (closed) { await pdf.destroy(); return }
      pages.value = pdf.numPages
      const targetPage = props.citation.sourceLocations.find(location => location.pageNumber != null)?.pageNumber
      const reliablePage = targetPage != null && Number.isInteger(targetPage) && targetPage >= 1 && targetPage <= pdf.numPages
      page.value = reliablePage ? targetPage : 1
      loading.value = false
      await nextTick()
      const rendered = await renderPage()
      locationNote.value = rendered ? (reliablePage ? (overlays.value.length ? '已定位物理页与来源区域；区域不代表精确句子' : '已定位物理页；该引用无可靠区域') : '该引用没有可靠页码，仅打开原文件') : '原文未成功渲染，请查看错误提示或打开原文件'
    }
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '原文读取失败'
    loading.value = false
  }
})
onBeforeUnmount(() => { closed = true; renderTask?.cancel(); void pdf?.destroy() })
</script>

<template>
  <div class="viewer-backdrop" @click.self="$emit('close')">
    <section class="document-viewer real-viewer" role="dialog" aria-modal="true" aria-label="引用原文">
      <header><div><strong>{{ citation.documentName }}</strong><span>{{ citation.headingPath.join(' / ') }}</span></div><button aria-label="关闭原文" @click="$emit('close')">✕</button></header>
      <div class="viewer-toolbar">
        <template v-if="sourceType === 'pdf' && pages"><button :disabled="page <= 1" @click="navigate(-1)">上一页</button><span>{{ page }} / {{ pages }} 页</span><button :disabled="page >= pages" @click="navigate(1)">下一页</button><button @click="zoom(-0.25)">−</button><span>{{ Math.round(scale * 100) }}%</span><button @click="zoom(0.25)">＋</button></template>
        <span v-else>Markdown 原文 · 保留实际行号</span>
        <a v-if="originalUrl" :href="originalUrl" target="_blank" rel="noopener noreferrer" class="download-link">打开原文件</a>
      </div>
      <p v-if="loading" class="reader-message">正在读取原文件…</p><p v-if="error" role="alert" class="reader-error">{{ error }}</p>
      <div ref="reader" class="source-scroll">
        <div v-if="sourceType === 'pdf' && !loading" class="pdf-canvas-wrap"><canvas ref="canvas"></canvas><div v-for="(location, index) in overlays" :key="index" class="source-bbox" :style="boxStyle(location)"></div></div>
        <div v-if="sourceType === 'md'" class="markdown-original"><div v-for="(line, index) in lines" :key="index" :data-line="index + 1" :class="{ 'line-highlight': highlightedLine(index) }"><span class="line-number">{{ index + 1 }}</span><code>{{ line }}</code></div></div>
      </div>
      <div class="viewer-source-note">{{ locationNote }}</div>
    </section>
  </div>
</template>
