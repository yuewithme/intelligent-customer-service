<template>
  <aside class="user-tag-panel">
    <ElEmpty v-if="!userId" description="请选择会话" />
    <template v-else>
      <div class="panel-head">
        <div>
          <h3>客户标签</h3>
          <p>人工可修改 AI 标签，保存后立即用于后端策略。</p>
        </div>
        <ElTag size="small" effect="plain">{{ selectedTagCount }} 个</ElTag>
      </div>

      <ElAlert
        v-if="readOnly"
        title="测试身份只能查看标签"
        type="info"
        :closable="false"
        show-icon
      />

      <ElInput v-model="keyword" clearable placeholder="搜索标签或分类" />

      <ElSkeleton v-if="loading || profileLoading" :rows="6" animated />
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
          <p v-if="category.prompt_rule">{{ category.prompt_rule }}</p>

          <ElSelect
            v-if="category.exclusive"
            v-model="exclusiveSelections[category.id]"
            filterable
            clearable
            :disabled="readOnly"
            placeholder="未选择"
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
            :disabled="readOnly"
            class="checkbox-list"
          >
            <ElCheckbox v-for="tag in category.tags" :key="tag.id" :value="tag.value">
              {{ tagValueText(tag.value) }}
            </ElCheckbox>
          </ElCheckboxGroup>
        </section>
      </div>

      <div class="panel-actions">
        <span v-if="dirty">有未保存的修改</span>
        <span v-else>已与后端同步</span>
        <ElButton
          type="primary"
          :disabled="readOnly || !dirty"
          :loading="saving"
          @click="save"
        >
          保存标签
        </ElButton>
      </div>
    </template>
  </aside>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getTagCatalog, type TagCategory } from '@/api/admin/tags'
import { updateUserTags, type UserProfile } from '@/api/user-profile'
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
const exclusiveSelections = reactive<Record<string, string>>({})
const multipleSelections = reactive<Record<string, string[]>>({})
const readOnly = isTestGate()

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
const dirty = computed(() => normalized(selectedTags.value) !== normalized(originalTags.value))
const selectedTagCount = computed(() => selectedTags.value.length)

const resetSelections = () => {
  const current = new Set(originalTags.value)
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

const save = async () => {
  if (!props.userId || readOnly || !dirty.value) return
  saving.value = true
  try {
    const result = await updateUserTags(props.userId, selectedTags.value)
    emit('profile-changed', result.profile)
    ElMessage.success('客户标签已保存并同步到后端')
  } finally {
    saving.value = false
  }
}

watch(
  () => [props.userId, ...(props.profile?.customer_tags || [])],
  resetSelections
)
onMounted(loadCatalog)
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

.panel-head,
.category-head,
.panel-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.panel-head h3 {
  margin: 0 0 5px;
  font-size: 16px;
}

.panel-head p,
.category-card p {
  margin: 0;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.55;
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

.panel-actions {
  position: sticky;
  bottom: -16px;
  z-index: 1;
  margin: 0 -16px -16px;
  padding: 12px 16px;
  border-top: 1px solid #e5e7eb;
  background: rgb(255 255 255 / 96%);
}

.panel-actions span {
  color: #6b7280;
  font-size: 12px;
}
</style>
