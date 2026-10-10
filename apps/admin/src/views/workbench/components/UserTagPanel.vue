<template>
  <aside class="user-tag-panel">
    <ElEmpty v-if="!userId" description="请选择会话" />
    <template v-else>
      <ElInput v-model="keyword" clearable placeholder="搜索标签或分类" />

      <div class="note-editor">
        <ElInput
          v-model="note"
          :disabled="readOnly || !profile"
          :maxlength="500"
          placeholder="客户备注"
          aria-label="客户备注"
        />
        <ElButton
          type="primary"
          :disabled="readOnly || !profile || !noteDirty || saving"
          :loading="noteSaving"
          @click="saveNote"
        >保存</ElButton>
      </div>
      <p v-if="savedNote" class="saved-note">备注：{{ savedNote }}</p>

      <div v-if="tagSyncError" class="sync-error" role="alert">
        标签同步失败，修改已保留
        <ElButton size="small" text type="danger" :loading="saving" @click="saveTags">重试</ElButton>
      </div>

      <ElAlert
        v-if="readOnly"
        title="测试身份只能查看标签"
        type="info"
        :closable="false"
        show-icon
      />

      <section v-if="serviceFacts.length" class="category-card">
        <div class="category-head"><strong>养护档案</strong></div>
        <p v-for="fact in serviceFacts" :key="fact.label">{{ fact.label }}：{{ fact.value }}</p>
      </section>

      <ElSkeleton v-if="loading || (profileLoading && !profile)" :rows="6" animated />
      <ElEmpty
        v-else-if="!visibleCategories.length"
        :description="keyword ? '没有匹配的标签' : '暂无可用标签'"
        :image-size="72"
      />
      <div v-else class="category-list">
        <section v-for="category in visibleCategories" :key="category.id" class="category-card">
          <div class="category-head">
            <strong>{{ category.name }}</strong>
            <ElTag
              size="small"
              :type="category.ai_assignable ? 'success' : 'warning'"
              effect="plain"
            >
              {{ category.ai_assignable ? 'AI 可识别' : '仅人工' }}
            </ElTag>
          </div>
          <ElSelect
            v-if="category.exclusive"
            v-model="exclusiveSelections[category.id]"
            filterable
            clearable
            :disabled="readOnly || !profile || noteSaving"
            placeholder="未选择"
            @change="handleTagChange"
          >
            <ElOption
              v-for="tag in category.tags"
              :key="tag.id"
              :label="tagValueText(tag.value)"
              :value="tag.value"
            />
          </ElSelect>

          <ElCheckboxGroup
            v-else
            v-model="multipleSelections[category.id]"
            :disabled="readOnly || !profile || noteSaving"
            class="checkbox-list"
            @change="handleTagChange"
          >
            <ElCheckbox v-for="tag in category.tags" :key="tag.id" :value="tag.value">
              {{ tagValueText(tag.value) }}
            </ElCheckbox>
          </ElCheckboxGroup>
        </section>
      </div>

    </template>
  </aside>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getTagCatalog, type TagCategory } from '@/api/admin/tags'
import { updateUserNote, updateUserTags, type UserProfile } from '@/api/user-profile'
import { isTestGate } from '@/utils/gate'
import { tagValueText } from '@/utils/tagDisplay'

const props = defineProps<{
  userId: string
  profile?: UserProfile
  profileLoading?: boolean
}>()
const emit = defineEmits<{ 'profile-changed': [profile: UserProfile] }>()

const categories = ref<TagCategory[]>([])
const keyword = ref('')
const loading = ref(false)
const saving = ref(false)
const tagSyncError = ref(false)
const noteSaving = ref(false)
const note = ref(props.profile?.customer_note || '')
const savedNote = computed(() => props.profile?.customer_note || '')
const noteDirty = computed(() => note.value.trim() !== savedNote.value)
const userId = props.userId
let tagVersion = 0
let disposed = false
const exclusiveSelections = reactive<Record<string, string>>({})
const multipleSelections = reactive<Record<string, string[]>>({})
const readOnly = isTestGate()
const serviceFacts = computed(() =>
  Object.entries({
    growing_years: '养兰年限',
    province: '所在省份',
    environment: '养护环境',
    existing_varieties: '现有品种',
    pain_points: '养护痛点',
    preference: '品种喜好',
    care_status: '养护近况'
  }).flatMap(([field, label]) => {
    const value = props.profile?.service_facts?.[field]?.value
    return value ? [{ label, value }] : []
  })
)

const profileCategories = computed(() =>
  categories.value.filter((category) => category.profile_assignable && category.tags.length)
)
const visibleCategories = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  if (!query) return profileCategories.value
  return profileCategories.value.filter((category) =>
    `${category.name} ${category.tags.map((tag) => `${tag.value} ${tagValueText(tag.value)}`).join(' ')}`
      .toLowerCase()
      .includes(query)
  )
})
const selectedTags = computed(() =>
  profileCategories.value.flatMap((category) =>
    category.exclusive
      ? exclusiveSelections[category.id] ? [exclusiveSelections[category.id]] : []
      : multipleSelections[category.id] || []
  )
)
const originalTags = computed(() => props.profile?.customer_tags?.filter(Boolean) || [])
const normalized = (values: string[]) => [...values].sort().join('\u0000')
const resetSelections = (tags = originalTags.value) => {
  const current = new Set(tags)
  for (const category of profileCategories.value) {
    const selected = category.tags.map((tag) => tag.value).filter((value) => current.has(value))
    if (category.exclusive) exclusiveSelections[category.id] = selected.at(-1) || ''
    else multipleSelections[category.id] = selected
  }
}

const loadCatalog = async () => {
  loading.value = true
  try {
    const catalog = await getTagCatalog()
    categories.value = catalog.items
    resetSelections()
  } finally {
    loading.value = false
  }
}

const saveTags = async () => {
  if (!userId || readOnly || !props.profile || saving.value || noteSaving.value) return
  saving.value = true
  tagSyncError.value = false
  try {
    let savedVersion: number
    do {
      savedVersion = tagVersion
      const result = await updateUserTags(userId, [...selectedTags.value])
      if (savedVersion === tagVersion) resetSelections(result.profile.customer_tags)
      emit('profile-changed', result.profile)
    } while (savedVersion !== tagVersion)
  } catch {
    if (!disposed) tagSyncError.value = true
  } finally {
    saving.value = false
  }
}

const handleTagChange = () => {
  ++tagVersion
  void saveTags()
}

const saveNote = async () => {
  if (!userId || readOnly || !noteDirty.value || saving.value || noteSaving.value) return
  const submittedNote = note.value
  noteSaving.value = true
  try {
    const result = await updateUserNote(userId, submittedNote.trim())
    emit('profile-changed', result.profile)
    if (!disposed) {
      if (note.value === submittedNote) note.value = result.profile.customer_note || ''
      ElMessage.success('客户备注已保存')
    }
  } catch {
    if (!disposed) ElMessage.error('客户备注保存失败，请重试')
  } finally {
    noteSaving.value = false
  }
}

watch(
  () => normalized(originalTags.value),
  () => {
    if (!saving.value && !tagSyncError.value) resetSelections()
  }
)
watch(savedNote, (value, previous) => {
  if (!noteSaving.value && note.value === previous) note.value = value
})
onMounted(loadCatalog)
onBeforeUnmount(() => { disposed = true })
</script>

<style scoped>
.user-tag-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
  padding: 16px;
  overflow: auto;
}

.category-head,
.note-editor {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.note-editor :deep(.el-input) {
  min-width: 0;
}

.saved-note,
.category-card p {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.sync-error {
  color: var(--el-color-danger);
  font-size: 13px;
}

.category-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.category-card {
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fafcfb;
}

.category-head {
  margin-bottom: 8px;
}

.category-card p {
  margin-bottom: 10px;
}

.category-card :deep(.el-select) {
  width: 100%;
}

.checkbox-list {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.checkbox-list :deep(.el-checkbox) {
  height: auto;
  margin-right: 0;
  white-space: normal;
}

</style>
