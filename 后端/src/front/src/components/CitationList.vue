<script setup lang="ts">
import { ChevronDown, ChevronRight, ExternalLink, FileText } from 'lucide-vue-next'
import { computed, nextTick, ref, watch } from 'vue'
import type { Citation } from '../api'

/**
 * 引用默认收成一行摘要，把版面留给回答本身。
 * 正文角标 ([n]) 会通过 active 触发自动展开并定位到对应片段。
 */
const props = defineProps<{ citations: Citation[]; active?: number | null }>()
const emit = defineEmits<{ open: [citation: Citation] }>()

const root = ref<HTMLElement | null>(null)
const expanded = ref(false)
const expandedItems = ref<Set<number>>(new Set())

/** 同一文档的多个切片合并成一组，文档名只出现一次。 */
const groups = computed(() => {
  const map = new Map<string, { key: string; documentName: string; items: { index: number; citation: Citation }[] }>()
  props.citations.forEach((citation, offset) => {
    const key = citation.documentId || citation.chunkId
    if (!map.has(key)) map.set(key, { key, documentName: citation.documentName, items: [] })
    map.get(key)?.items.push({ index: offset + 1, citation })
  })
  return [...map.values()]
})

/** 预览只做展示层的轻量清理，不改动原文语义；"查看原文" 打开的是未改动的原件。 */
const plain = (text: string) =>
  text
    .replace(/<[^>]*>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/^[ \t]*[-*+]\s+/gm, '· ')
    .replace(/[#*_`>]/g, '')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{2,}/g, '\n')
    .trim()

const needsExpand = (text: string) => plain(text).length > 90
const isExpanded = (index: number) => expandedItems.value.has(index)

function toggleItem(index: number) {
  const next = new Set(expandedItems.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  expandedItems.value = next
}

function open(citation: Citation) {
  emit('open', citation)
}

watch(
  () => props.active,
  async value => {
    if (!value) return
    expanded.value = true
    await nextTick()
    root.value
      ?.querySelector(`[data-citation-index="${value}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  },
)
</script>

<template>
  <section ref="root" class="citation-block">
    <button class="citation-toggle" type="button" :aria-expanded="expanded" @click="expanded = !expanded">
      <ChevronDown v-if="expanded" :size="14" />
      <ChevronRight v-else :size="14" />
      <span>参考来源 {{ citations.length }} 条（{{ groups.length }} 份文档）</span>
    </button>
    <div v-if="expanded" class="citation-groups">
      <article v-for="group in groups" :key="group.key" class="citation-group">
        <header class="citation-group-head">
          <span class="citation-doc"><FileText :size="14" />{{ group.documentName }}</span>
          <span class="citation-count">{{ group.items.length }} 段</span>
        </header>
        <div
          v-for="item in group.items"
          :key="item.citation.chunkId"
          class="citation-item"
          :class="{ active: active === item.index }"
          :data-citation-index="item.index"
        >
          <div class="citation-item-head">
            <span class="citation-index">[{{ item.index }}]</span>
            <span class="citation-path">{{ item.citation.headingPath.join(' / ') || '未标注章节' }}</span>
            <button class="citation-link" type="button" @click="open(item.citation)">查看原文<ExternalLink :size="12" /></button>
          </div>
          <p class="citation-quote" :class="{ expanded: isExpanded(item.index) }">{{ plain(item.citation.quote) }}</p>
          <button v-if="needsExpand(item.citation.quote)" class="citation-more" type="button" @click="toggleItem(item.index)">
            {{ isExpanded(item.index) ? '收起' : '展开原文' }}
          </button>
        </div>
      </article>
    </div>
  </section>
</template>
