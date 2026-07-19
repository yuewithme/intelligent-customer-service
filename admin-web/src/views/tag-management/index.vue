<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>标签管理</h1>
        <p>统一维护客户画像、销售阶段、对话意图、情绪、风险及策略标签；AI 只能使用此处已配置的标签。</p>
      </div>
      <ElButton type="primary" @click="openCategoryCreate">新增分类</ElButton>
    </div>

    <div class="metrics">
      <div><span>标签分类</span><strong>{{ catalog.total_categories }}</strong></div>
      <div><span>标签总数</span><strong>{{ catalog.total_tags }}</strong></div>
      <div><span>提示词配置</span><strong>{{ catalog.total_prompts }}</strong></div>
    </div>

    <div class="toolbar">
      <ElInput v-model="keyword" clearable placeholder="搜索分类、标签或提示词" />
      <span>删除标签会立即停止后端继续使用，并同步清除画像或销售状态中的对应值。</span>
    </div>

    <div v-loading="loading" class="category-list">
      <section v-for="category in filteredCategories" :key="category.id" class="category-card">
        <div class="category-head">
          <button type="button" class="category-toggle" @click="toggleCategory(category.id)">
            <span class="fold-icon" :class="{ expanded: shouldShowCategoryBody(category.id) }">›</span>
            <span class="category-overview">
            <div class="category-title">
              <h2>{{ category.name }}</h2>
              <code>{{ category.id }}</code>
              <ElTag v-if="isSystemCategory(category.id)" size="small" type="warning" effect="plain">
                系统标签
              </ElTag>
              <ElTag size="small" :type="category.ai_assignable ? 'success' : 'info'">
                {{ category.ai_assignable ? 'AI 可分配' : '仅业务分配' }}
              </ElTag>
              <ElTag size="small" effect="plain">
                {{ category.exclusive ? '单选互斥' : '允许多选' }}
              </ElTag>
            </div>
            <span class="category-stats">
              <b>{{ category.tags.length }}</b> 个标签
              <i></i>
              <b>{{ categoryPromptCount(category) }}</b> 条提示词配置
            </span>
            </span>
          </button>
          <div class="category-actions">
            <ElButton @click="openTagCreate(category)">新增标签</ElButton>
            <ElButton @click="openCategoryEdit(category)">编辑分类</ElButton>
            <ElButton type="danger" plain @click="removeCategory(category)">删除分类</ElButton>
          </div>
        </div>

        <div v-show="shouldShowCategoryBody(category.id)" class="category-body">
          <p class="category-rule">{{ category.prompt_rule || '暂无分类使用说明' }}</p>
          <div v-if="category.tags.length" class="tag-board">
            <button
              v-for="tag in category.tags"
              :key="tag.id"
              type="button"
              class="tag-card"
              @click="openTagDetail(category, tag)"
            >
              <span class="tag-card-head">
                <strong>{{ displayTagValue(tag.value) }}</strong>
                <em :class="{ empty: !tag.prompts.length }">
                  {{ tag.prompts.length ? `${tag.prompts.length} 条` : '未配置' }}
                </em>
              </span>
              <code v-if="displayTagValue(tag.value) !== tag.value" class="tag-token">{{ tag.value }}</code>
              <span v-if="tag.prompts.length" class="prompt-name-list">
                <span v-for="prompt in tag.prompts" :key="prompt.block_id">{{ prompt.title }}</span>
              </span>
              <span v-else class="empty-prompt">暂无关联提示词</span>
              <span class="view-detail">查看完整内容 <b>→</b></span>
            </button>
          </div>
          <ElEmpty v-else description="该分类暂无标签" :image-size="58" />
        </div>
      </section>
      <ElEmpty v-if="!loading && !filteredCategories.length" description="没有匹配的标签" />
    </div>

    <ElDialog
      v-model="detailDialog.visible"
      :title="selectedTag ? `标签详情 · ${displayTagValue(selectedTag.value)}` : '标签详情'"
      width="760px"
    >
      <template v-if="selectedTag && selectedCategory">
        <div class="detail-summary">
          <div>
            <span>所属分类</span>
            <strong>{{ selectedCategory.name }}</strong>
          </div>
          <div>
            <span>标签名称</span>
            <strong>{{ displayTagValue(selectedTag.value) }}</strong>
          </div>
          <div>
            <span>提示词</span>
            <strong>{{ selectedTag.prompts.length }} 条</strong>
          </div>
        </div>
        <div v-if="selectedTag.prompts.length" class="detail-prompts">
          <article v-for="prompt in selectedTag.prompts" :key="prompt.block_id">
            <div class="detail-prompt-head">
              <div>
                <strong>{{ prompt.title }}</strong>
                <code>{{ prompt.block_id }}</code>
              </div>
              <ElTag v-if="prompt.shared_count > 1" size="small" type="warning" effect="plain">
                {{ prompt.shared_count }} 个标签共用
              </ElTag>
            </div>
            <p>{{ prompt.content }}</p>
          </article>
        </div>
        <ElEmpty v-else description="该标签暂无关联提示词" :image-size="68" />
      </template>
      <template #footer>
        <ElButton @click="detailDialog.visible = false">关闭</ElButton>
        <ElButton v-if="selectedTag" type="danger" plain @click="removeSelectedTag">删除标签</ElButton>
        <ElButton v-if="selectedTag" type="primary" @click="editSelectedTag">编辑标签与提示词</ElButton>
      </template>
    </ElDialog>

    <ElDialog v-model="categoryDialog.visible" :title="categoryDialog.editingId ? '编辑分类' : '新增分类'" width="560px">
      <ElForm label-position="top">
        <ElFormItem v-if="!categoryDialog.editingId" label="分类标识" required>
          <ElInput v-model="categoryForm.id" placeholder="例如 customer_preference" />
          <small>仅支持小写字母、数字和下划线，创建后不可修改。</small>
        </ElFormItem>
        <ElFormItem label="分类名称" required><ElInput v-model="categoryForm.name" /></ElFormItem>
        <ElFormItem label="分类使用说明">
          <ElInput v-model="categoryForm.prompt_rule" type="textarea" :rows="3" placeholder="说明此类标签应如何影响识别或回复" />
        </ElFormItem>
        <div class="switch-row">
          <ElFormItem label="允许 AI 自动分配"><ElSwitch v-model="categoryForm.ai_assignable" /></ElFormItem>
          <ElFormItem label="分类内标签互斥"><ElSwitch v-model="categoryForm.exclusive" /></ElFormItem>
        </div>
      </ElForm>
      <template #footer>
        <ElButton @click="categoryDialog.visible = false">取消</ElButton>
        <ElButton type="primary" :loading="saving" @click="saveCategory">保存</ElButton>
      </template>
    </ElDialog>

    <ElDialog v-model="tagDialog.visible" :title="tagDialog.editingId ? '编辑标签' : '新增标签'" width="720px" destroy-on-close>
      <ElForm label-position="top">
        <ElFormItem label="所属分类"><ElInput :model-value="tagDialog.categoryName" disabled /></ElFormItem>
        <ElFormItem label="标签名称" required><ElInput v-model="tagForm.value" maxlength="256" /></ElFormItem>
        <div class="prompt-editor-head">
          <div><strong>关联提示词</strong><small>命中该标签时，提示词会加入模型回复上下文。</small></div>
          <ElButton @click="addPrompt">新增提示词</ElButton>
        </div>
        <div v-if="tagForm.prompts.length" class="prompt-editors">
          <section v-for="(prompt, index) in tagForm.prompts" :key="prompt.localKey">
            <div class="prompt-editor-title">
              <strong>提示词 {{ index + 1 }}</strong>
              <span>
                <ElTag v-if="prompt.shared_count > 1" size="small" type="warning">共用配置</ElTag>
                <ElButton link type="danger" @click="tagForm.prompts.splice(index, 1)">移除</ElButton>
              </span>
            </div>
            <ElFormItem label="标题" required><ElInput v-model="prompt.title" /></ElFormItem>
            <ElFormItem label="内容" required>
              <ElInput v-model="prompt.content" type="textarea" :rows="4" maxlength="12000" show-word-limit />
            </ElFormItem>
            <small v-if="prompt.shared_count > 1">此提示词由多个标签共用，修改内容会同步影响这些标签；移除只解除当前标签的关联。</small>
          </section>
        </div>
        <ElEmpty v-else description="暂未配置提示词，可直接保存标签" :image-size="58" />
      </ElForm>
      <template #footer>
        <ElButton @click="tagDialog.visible = false">取消</ElButton>
        <ElButton type="primary" :loading="saving" @click="saveTag">保存并生效</ElButton>
      </template>
    </ElDialog>
  </ContentWrap>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createTag,
  createTagCategory,
  deleteTag as deleteTagRequest,
  deleteTagCategory,
  getTagCatalog,
  updateTag,
  updateTagCategory,
  type ManagedTag,
  type TagCatalog,
  type TagCategory,
  type TagPrompt
} from '@/api/admin/tags'

interface EditablePrompt {
  localKey: string
  block_id?: string
  title: string
  content: string
  shared_count: number
}

const emptyCatalog = (): TagCatalog => ({ items: [], total_categories: 0, total_tags: 0, total_prompts: 0 })
const catalog = reactive<TagCatalog>(emptyCatalog())
const keyword = ref('')
const loading = ref(false)
const saving = ref(false)
const expandedCategoryIds = ref<string[]>([])
const expansionInitialized = ref(false)
const categoryDialog = reactive({ visible: false, editingId: '' })
const tagDialog = reactive({ visible: false, editingId: 0, categoryId: '', categoryName: '' })
const detailDialog = reactive({ visible: false, categoryId: '', tagId: 0 })
const categoryForm = reactive({ id: '', name: '', prompt_rule: '', ai_assignable: true, exclusive: true })
const tagForm = reactive<{ value: string; prompts: EditablePrompt[] }>({ value: '', prompts: [] })
const systemCategoryIds = new Set([
  'intent', 'sales_stage', 'customer_segment', 'customer_sentiment',
  'risk_level', 'pain_point', 'product_interest'
])

const isSystemCategory = (categoryId: string) => systemCategoryIds.has(categoryId)
const displayTagValue = (value: string) => value.includes(':') ? value.split(':', 2)[1] : value

const selectedCategory = computed(() =>
  catalog.items.find((category) => category.id === detailDialog.categoryId)
)
const selectedTag = computed(() =>
  selectedCategory.value?.tags.find((tag) => tag.id === detailDialog.tagId)
)

const filteredCategories = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  if (!query) return catalog.items
  return catalog.items.flatMap((category) => {
    const categoryMatch = `${category.name} ${category.id} ${category.prompt_rule}`.toLowerCase().includes(query)
    const tags = categoryMatch ? category.tags : category.tags.filter((tag) =>
      `${tag.value} ${tag.prompts.map((prompt) => `${prompt.title} ${prompt.content}`).join(' ')}`.toLowerCase().includes(query)
    )
    return categoryMatch || tags.length ? [{ ...category, tags }] : []
  })
})

const loadCatalog = async () => {
  loading.value = true
  try {
    Object.assign(catalog, await getTagCatalog())
    if (!expansionInitialized.value) {
      expandedCategoryIds.value = catalog.items.map((category) => category.id)
      expansionInitialized.value = true
    } else {
      const validIds = new Set(catalog.items.map((category) => category.id))
      expandedCategoryIds.value = expandedCategoryIds.value.filter((id) => validIds.has(id))
    }
  } finally { loading.value = false }
}

const categoryPromptCount = (category: TagCategory) =>
  category.tags.reduce((total, tag) => total + tag.prompts.length, 0)

const shouldShowCategoryBody = (categoryId: string) =>
  Boolean(keyword.value.trim()) || expandedCategoryIds.value.includes(categoryId)

const toggleCategory = (categoryId: string) => {
  if (expandedCategoryIds.value.includes(categoryId)) {
    expandedCategoryIds.value = expandedCategoryIds.value.filter((id) => id !== categoryId)
  } else {
    expandedCategoryIds.value = [...expandedCategoryIds.value, categoryId]
  }
}

const openCategoryCreate = () => {
  Object.assign(categoryForm, { id: '', name: '', prompt_rule: '', ai_assignable: true, exclusive: true })
  Object.assign(categoryDialog, { visible: true, editingId: '' })
}

const openCategoryEdit = (category: TagCategory) => {
  Object.assign(categoryForm, category)
  Object.assign(categoryDialog, { visible: true, editingId: category.id })
}

const saveCategory = async () => {
  if (!categoryForm.name.trim() || (!categoryDialog.editingId && !/^[a-z][a-z0-9_]*$/.test(categoryForm.id))) {
    ElMessage.warning('请填写有效的分类标识和名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: categoryForm.name.trim(), prompt_rule: categoryForm.prompt_rule.trim(),
      ai_assignable: categoryForm.ai_assignable, exclusive: categoryForm.exclusive
    }
    if (categoryDialog.editingId) await updateTagCategory(categoryDialog.editingId, payload)
    else await createTagCategory({ id: categoryForm.id, ...payload })
    categoryDialog.visible = false
    ElMessage.success('分类已保存')
    await loadCatalog()
  } finally { saving.value = false }
}

const removeCategory = async (category: TagCategory) => {
  await ElMessageBox.confirm(
    `确定删除“${category.name}”及其中 ${category.tags.length} 个标签吗？画像、销售状态及提示词绑定中的对应内容也会清除。`,
    '删除标签分类', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
  )
  await deleteTagCategory(category.id)
  ElMessage.success('分类及后端关联内容已删除')
  await loadCatalog()
}

const promptFrom = (prompt: TagPrompt): EditablePrompt => ({
  localKey: prompt.block_id, block_id: prompt.block_id, title: prompt.title,
  content: prompt.content, shared_count: prompt.shared_count
})

const openTagCreate = (category: TagCategory) => {
  Object.assign(tagDialog, { visible: true, editingId: 0, categoryId: category.id, categoryName: category.name })
  Object.assign(tagForm, { value: '', prompts: [] })
}

const openTagDetail = (category: TagCategory, tag: ManagedTag) => {
  Object.assign(detailDialog, { visible: true, categoryId: category.id, tagId: tag.id })
}

const openTagEdit = (category: TagCategory, tag: ManagedTag) => {
  Object.assign(tagDialog, { visible: true, editingId: tag.id, categoryId: category.id, categoryName: category.name })
  tagForm.value = tag.value
  tagForm.prompts = tag.prompts.map(promptFrom)
}

const editSelectedTag = () => {
  if (!selectedCategory.value || !selectedTag.value) return
  const category = selectedCategory.value
  const tag = selectedTag.value
  detailDialog.visible = false
  openTagEdit(category, tag)
}

const addPrompt = () => tagForm.prompts.push({
  localKey: `new-${Date.now()}-${tagForm.prompts.length}`, title: '', content: '', shared_count: 1
})

const saveTag = async () => {
  if (!tagForm.value.trim()) { ElMessage.warning('请输入标签名称'); return }
  if (tagForm.prompts.some((prompt) => !prompt.title.trim() || !prompt.content.trim())) {
    ElMessage.warning('请完整填写提示词标题和内容')
    return
  }
  saving.value = true
  try {
    const payload = {
      value: tagForm.value.trim(),
      prompts: tagForm.prompts.map(({ block_id, title, content }) => ({ block_id, title: title.trim(), content: content.trim() }))
    }
    if (tagDialog.editingId) await updateTag(tagDialog.editingId, payload)
    else await createTag(tagDialog.categoryId, payload)
    tagDialog.visible = false
    ElMessage.success('标签及提示词已保存并生效')
    await loadCatalog()
  } finally { saving.value = false }
}

const removeTag = async (tag: ManagedTag) => {
  await ElMessageBox.confirm(
    `确定删除标签“${tag.value}”吗？已有客户画像中的该标签和 ${tag.prompts.length} 条提示词关联会同步清除。`,
    '删除标签', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
  )
  await deleteTagRequest(tag.id)
  ElMessage.success('标签及后端关联内容已删除')
  await loadCatalog()
}

const removeSelectedTag = async () => {
  if (!selectedTag.value) return
  const tag = selectedTag.value
  detailDialog.visible = false
  await removeTag(tag)
}

onMounted(loadCatalog)
</script>

<style scoped>
.page-head,
.category-head,
.category-title,
.category-actions,
.toolbar,
.prompt-editor-head,
.prompt-editor-title,
.tag-card-head,
.detail-prompt-head {
  display: flex;
  align-items: center;
}

.page-head,
.category-head,
.toolbar,
.prompt-editor-head,
.prompt-editor-title,
.tag-card-head,
.detail-prompt-head {
  justify-content: space-between;
}

.page-head { gap: 20px; }
.page-head h1 { margin: 0; color: #18352d; }
.page-head p { margin: 7px 0 0; color: #708079; }

.metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(140px, 1fr));
  gap: 12px;
  margin: 20px 0;
}
.metrics div { padding: 16px; border: 1px solid #e2e9e6; border-radius: 12px; background: #f8fbfa; }
.metrics span { display: block; color: #75857f; font-size: 13px; }
.metrics strong { display: block; margin-top: 7px; color: #18352d; font-size: 26px; }

.toolbar { gap: 20px; margin-bottom: 18px; }
.toolbar .el-input { max-width: 420px; }
.toolbar > span { color: #82918c; font-size: 12px; }
.category-list { min-height: 180px; }
.category-card { margin-bottom: 14px; border: 1px solid #dfe8e4; border-radius: 14px; overflow: hidden; background: #fff; }
.category-head { gap: 16px; padding: 14px 16px; background: linear-gradient(135deg, #f7fbf9 0%, #f1f7f4 100%); }

.category-toggle {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 13px;
  min-width: 0;
  padding: 3px 0;
  color: inherit;
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 0;
}
.fold-icon {
  display: grid;
  flex: 0 0 30px;
  width: 30px;
  height: 30px;
  place-items: center;
  color: #3e7665;
  font-size: 27px;
  background: #e1efe9;
  border-radius: 9px;
  transform: rotate(0deg);
  transition: transform .2s ease;
}
.fold-icon.expanded { transform: rotate(90deg); }
.category-overview { display: block; min-width: 0; }
.category-title { flex-wrap: wrap; gap: 8px; }
.category-title h2 { margin: 0; color: #23463b; font-size: 18px; }
.category-title code { color: #71827c; font-size: 11px; }
.category-stats { display: flex; align-items: center; gap: 6px; margin-top: 7px; color: #71827c; font-size: 12px; }
.category-stats b { color: #31594c; }
.category-stats i { width: 1px; height: 12px; margin: 0 3px; background: #cbd9d4; }
.category-actions { gap: 8px; flex-shrink: 0; }

.category-body { padding: 16px; border-top: 1px solid #e5ece9; }
.category-rule { margin: 0 0 14px; color: #71827c; font-size: 13px; line-height: 1.6; }
.tag-board { display: grid; grid-template-columns: repeat(auto-fill, minmax(245px, 1fr)); gap: 12px; }
.tag-card {
  display: flex;
  min-height: 132px;
  padding: 15px;
  overflow: hidden;
  flex-direction: column;
  color: #2f4941;
  text-align: left;
  cursor: pointer;
  background: #fff;
  border: 1px solid #dce7e3;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgb(22 74 57 / 4%);
  transition: border-color .18s ease, box-shadow .18s ease, transform .18s ease;
}
.tag-card:hover { border-color: #69ad94; box-shadow: 0 8px 20px rgb(22 74 57 / 10%); transform: translateY(-2px); }
.tag-card-head { gap: 10px; }
.tag-card-head strong { overflow: hidden; color: #1d4538; font-size: 16px; text-overflow: ellipsis; white-space: nowrap; }
.tag-card-head em { flex-shrink: 0; padding: 3px 8px; color: #257a5c; font-size: 11px; font-style: normal; background: #e7f5ef; border-radius: 999px; }
.tag-card-head em.empty { color: #85938e; background: #eef2f0; }
.tag-token { margin-top: 6px; color: #8a9994; font-size: 10px; }
.prompt-name-list { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 13px; }
.prompt-name-list > span { max-width: 100%; padding: 4px 7px; overflow: hidden; color: #566c65; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; background: #f2f6f4; border-radius: 6px; }
.empty-prompt { margin-top: 15px; color: #98a39f; font-size: 12px; }
.view-detail { display: flex; align-items: center; gap: 5px; margin-top: auto; padding-top: 13px; color: #39866b; font-size: 12px; }
.view-detail b { font-size: 14px; transition: transform .18s ease; }
.tag-card:hover .view-detail b { transform: translateX(3px); }

.detail-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 16px; }
.detail-summary > div { padding: 13px; background: #f4f8f6; border: 1px solid #e2eae7; border-radius: 10px; }
.detail-summary span { display: block; margin-bottom: 5px; color: #82918c; font-size: 12px; }
.detail-summary strong { color: #294d41; }
.detail-prompts { display: grid; gap: 12px; max-height: 52vh; overflow: auto; padding-right: 4px; }
.detail-prompts article { padding: 15px; border: 1px solid #dce6e2; border-radius: 11px; background: #fbfdfc; }
.detail-prompt-head { gap: 12px; }
.detail-prompt-head strong,
.detail-prompt-head code { display: block; }
.detail-prompt-head code { margin-top: 4px; color: #8a9994; font-size: 11px; }
.detail-prompts p { margin: 12px 0 0; color: #4d635c; line-height: 1.7; white-space: pre-wrap; }

.switch-row { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.prompt-editor-head { margin: 8px 0 12px; }
.prompt-editor-head strong,
.prompt-editor-head small { display: block; }
.prompt-editor-head small,
.prompt-editors small,
.el-form-item small { margin-top: 5px; color: #83918c; }
.prompt-editors { display: grid; gap: 12px; max-height: 55vh; overflow: auto; padding-right: 4px; }
.prompt-editors section { padding: 14px 14px 4px; border: 1px solid #dce6e2; border-radius: 10px; background: #fbfdfc; }
.prompt-editor-title { margin-bottom: 10px; }
.prompt-editor-title span { display: flex; align-items: center; gap: 8px; }

@media (max-width: 1000px) {
  .category-head,
  .toolbar { align-items: flex-start; flex-direction: column; }
  .category-toggle { width: 100%; }
  .category-actions { flex-wrap: wrap; padding-left: 43px; }
  .toolbar .el-input { width: 100%; max-width: none; }
}

@media (max-width: 640px) {
  .page-head { align-items: flex-start; flex-direction: column; }
  .metrics,
  .switch-row,
  .detail-summary { grid-template-columns: 1fr; }
  .tag-board { grid-template-columns: 1fr; }
  .category-actions { padding-left: 0; }
  .category-actions .el-button { margin-left: 0; }
}
</style>
