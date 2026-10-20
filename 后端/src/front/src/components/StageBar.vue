<script setup lang="ts">
import { Check, X } from 'lucide-vue-next'
import { computed } from 'vue'
import type { StageItem } from '../stages'

/**
 * Renders one flow as a stepper: finished steps are checked, the running step is
 * animated, later steps stay pending, inapplicable steps are marked, and a failure
 * stops every animation at the step that actually failed.
 */
const props = withDefaults(
  defineProps<{
    stages: StageItem[]
    current?: string | null
    skipped?: string[]
    failed?: string | null
    finished?: boolean
    detail?: string | null
  }>(),
  { current: null, skipped: () => [], failed: null, finished: false, detail: null },
)

type ItemState = 'done' | 'active' | 'pending' | 'skipped' | 'failed'

const items = computed(() => {
  const currentIndex = props.current
    ? props.stages.findIndex(stage => stage.id === props.current)
    : -1
  return props.stages.map((stage, index) => {
    const skipped = props.skipped.includes(stage.id)
    let state: ItemState = 'pending'
    if (props.failed === stage.id) state = 'failed'
    else if (skipped) state = 'skipped'
    else if (props.finished) state = 'done'
    else if (currentIndex >= 0 && index < currentIndex) state = 'done'
    else if (currentIndex === index) state = 'active'
    return {
      ...stage,
      state,
      // 阶段内的实际计数只挂在当前步骤上，历史步骤不再重复显示。
      note: state === 'skipped' ? '不适用' : state === 'active' ? props.detail : null,
    }
  })
})
</script>

<template>
  <ol class="stage-bar" :class="{ 'has-failure': Boolean(failed) }" :aria-busy="!finished && !failed">
    <li
      v-for="(item, index) in items"
      :key="item.id"
      class="stage-item"
      :class="item.state"
      :aria-current="item.state === 'active' ? 'step' : undefined"
    >
      <span class="stage-marker" aria-hidden="true">
        <Check v-if="item.state === 'done'" :size="12" :stroke-width="3" />
        <X v-else-if="item.state === 'failed'" :size="12" :stroke-width="3" />
        <span v-else-if="item.state === 'active'" class="stage-pulse" />
        <span v-else-if="item.state === 'skipped'" class="stage-dash" />
      </span>
      <span class="stage-label">{{ item.label }}</span>
      <span v-if="item.note" class="stage-note">{{ item.note }}</span>
      <span v-if="index < items.length - 1" class="stage-line" aria-hidden="true" />
    </li>
  </ol>
</template>
