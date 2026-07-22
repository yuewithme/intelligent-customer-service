<template>
  <ElDialog
    :model-value="modelValue"
    title="存为活动"
    width="520px"
    destroy-on-close
    @close="close"
  >
    <ElForm label-position="top">
      <ElFormItem label="活动名称" required>
        <ElInput v-model="form.title" maxlength="256" show-word-limit placeholder="例如：七月兰花活动" />
      </ElFormItem>
      <ElFormItem label="活动说明">
        <ElInput
          v-model="form.summary"
          type="textarea"
          :rows="3"
          placeholder="说明本活动适用场景"
        />
      </ElFormItem>
      <ElFormItem label="有效时间">
        <div class="time-fields">
          <ElDatePicker
            v-model="form.valid_from"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ssZ"
            placeholder="开始时间（可选）"
          />
          <ElDatePicker
            v-model="form.valid_until"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ssZ"
            placeholder="结束时间（留空为长期）"
          />
        </div>
      </ElFormItem>
      <ElAlert
        :title="`已选择 ${messageIds.length} 条消息，后端会按原会话时间排序保存`"
        type="info"
        :closable="false"
      />
    </ElForm>
    <template #footer>
      <ElButton @click="close">取消</ElButton>
      <ElButton type="primary" :loading="saving" :disabled="!form.title.trim()" @click="save">
        保存为草稿
      </ElButton>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { createActivityFromMessages } from '@/api/admin/activities'
import { useUserStore } from '@/store/modules/user'

const props = defineProps<{
  modelValue: boolean
  conversationId: string
  messageIds: number[]
}>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: []
}>()

const userStore = useUserStore()
const saving = ref(false)
const form = reactive({ title: '', summary: '', valid_from: '', valid_until: '' })

const close = () => emit('update:modelValue', false)

const save = async () => {
  const title = form.title.trim()
  if (!title || !props.conversationId || !props.messageIds.length) return
  saving.value = true
  try {
    await createActivityFromMessages({
      conversation_id: props.conversationId,
      message_ids: props.messageIds,
      title,
      summary: form.summary.trim() || undefined,
      operator_id: userStore.user.nickname || 'admin',
      valid_from: form.valid_from || undefined,
      valid_until: form.valid_until || undefined
    })
    ElMessage.success('已保存为活动草稿')
    emit('saved')
    close()
  } finally {
    saving.value = false
  }
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      form.title = ''
      form.summary = ''
      form.valid_from = ''
      form.valid_until = ''
    }
  }
)
</script>

<style scoped>
.time-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  width: 100%;
}

.time-fields :deep(.el-date-editor) {
  width: 100%;
}
</style>
