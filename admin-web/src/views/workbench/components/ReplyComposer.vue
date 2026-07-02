<template>
  <div class="composer">
    <ElAlert
      v-if="status === 'ai_active' || status === 'ai_waiting'"
      title="当前由 AI 自动回复，人工仅可监控"
      type="info"
      :closable="false"
    />
    <ElButton v-else-if="status === 'handoff_pending'" type="primary" @click="$emit('claim')">
      领取接管
    </ElButton>
    <template v-else-if="status === 'human_active'">
      <ElInput v-model="content" type="textarea" :rows="4" placeholder="输入人工回复" />
      <ElButton type="primary" :disabled="!content.trim()" @click="send">发送</ElButton>
    </template>
    <ElAlert v-else title="会话已结束，仅可查看" type="warning" :closable="false" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ status: string }>()
const emit = defineEmits<{ claim: []; send: [content: string] }>()
const content = ref('')

const send = () => {
  const value = content.value.trim()
  if (!value) return
  emit('send', value)
  content.value = ''
}
</script>

<style scoped>
.composer {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
</style>
