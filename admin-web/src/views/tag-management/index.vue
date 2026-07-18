<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>标签管理</h1>
        <p>统一维护客户标签与关联提示词，修改后会直接作用于画像识别和回复策略。</p>
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
      <span>删除标签时，会同步清除客户画像中的该标签及其提示词绑定。</span>
    </div>

    <div v-loading="loading" class="category-list">
      <section v-for="category in filteredCategories" :key="category.id" class="category-card">
        <div class="category-head">
          <div>
            <div class="category-title">
              <h2>{{ category.name }}</h2>
              <code>{{ category.id }}</code>
              <ElTag size="small" :type="category.ai_assignable ? 'success' : 'info'">
                {{ category.ai_assignable ? 'AI 可分配' : '仅业务分配' }}
              </ElTag>
              <ElTag size="small" effect="plain">
                {{ category.exclusive ? '单选互斥' : '允许多选' }}
              </ElTag>
            </div>
            <p>{{ category.prompt_rule || '暂无分类使用说明' }}</p>
          </div>
          <div class="category-actions">
            <ElButton @click="openTagCreate(category)">新增标签</ElButton>
            <ElButton @click="openCategoryEdit(category)">编辑分类</ElButton>
            <ElButton type="danger" plain @click="removeCategory(category)">删除分类</ElButton>
          </div>
        </div>

        <ElTable :data="category.tags" row-key="id" empty-text="该分类暂无标签">
          <ElTableColumn type="expand">
            <template #default="{ row }">
              <div v-if="row.prompts.length" class="prompt-list">
                <article v-for="prompt in row.prompts" :key="prompt.block_id">
                  <div>
                    <strong>{{ prompt.title }}</strong>
                    <ElTag v-if="prompt.shared_count > 1" size="small" type="warning" effect="plain">
                      {{ prompt.shared_count }} 个标签共用
                    </ElTag>
                    <code>{{ prompt.block_id }}</code>
                  </div>
                  <p>{{ prompt.content }}</p>
                </article>
              </div>
              <ElEmpty v-else description="该标签没有关联提示词" :image-size="52" />
            </template>
          </ElTableColumn>
          <ElTableColumn prop="value" label="标签名称" min-width="220">
            <template #default="{ row }"><ElTag effect="plain">{{ row.value }}</ElTag></template>
          </ElTableColumn>
          <ElTableColumn label="提示词" min-width="280">
            <template #default="{ row }">
              <span v-if="row.prompts.length">{{ row.prompts.length }} 条 · {{ row.prompts.map((item: TagPrompt) => item.title).join('、') }}</span>
              <span v-else class="muted">未配置</span>
            </template>
          </ElTableColumn>
          <ElTableColumn label="操作" width="180" align="right">
            <template #default="{ row }">
              <ElButton link type="primary" @click="openTagEdit(category, row)">编辑</ElButton>
              <ElButton link type="danger" @click="removeTag(row)">删除</ElButton>
            </template>
          </ElTableColumn>
        </ElTable>
      </section>
      <ElEmpty v-if="!loading && !filteredCategories.length" description="没有匹配的标签" />
    </div>

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
const categoryDialog = reactive({ visible: false, editingId: '' })
const tagDialog = reactive({ visible: false, editingId: 0, categoryId: '', categoryName: '' })
const categoryForm = reactive({ id: '', name: '', prompt_rule: '', ai_assignable: true, exclusive: true })
const tagForm = reactive<{ value: string; prompts: EditablePrompt[] }>({ value: '', prompts: [] })

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
  try { Object.assign(catalog, await getTagCatalog()) } finally { loading.value = false }
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
    `确定删除“${category.name}”及其中 ${category.tags.length} 个标签吗？客户画像中的对应标签和提示词绑定也会清除。`,
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

const openTagEdit = (category: TagCategory, tag: ManagedTag) => {
  Object.assign(tagDialog, { visible: true, editingId: tag.id, categoryId: category.id, categoryName: category.name })
  tagForm.value = tag.value
  tagForm.prompts = tag.prompts.map(promptFrom)
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

onMounted(loadCatalog)
</script>

<style scoped>
.page-head,.category-head,.category-title,.category-actions,.toolbar,.prompt-editor-head,.prompt-editor-title{display:flex;align-items:center}.page-head,.category-head,.toolbar,.prompt-editor-head,.prompt-editor-title{justify-content:space-between}.page-head{gap:20px}.page-head h1{margin:0;color:#18352d}.page-head p,.category-head p{margin:7px 0 0;color:#708079}.metrics{display:grid;grid-template-columns:repeat(3,minmax(140px,1fr));gap:12px;margin:20px 0}.metrics div{padding:16px;border:1px solid #e2e9e6;border-radius:12px;background:#f8fbfa}.metrics span{display:block;color:#75857f;font-size:13px}.metrics strong{display:block;margin-top:7px;color:#18352d;font-size:26px}.toolbar{gap:20px;margin-bottom:18px}.toolbar .el-input{max-width:420px}.toolbar>span{color:#82918c;font-size:12px}.category-list{min-height:180px}.category-card{margin-bottom:16px;border:1px solid #e1e9e6;border-radius:12px;overflow:hidden}.category-head{gap:20px;padding:18px;background:#f8fbfa;border-bottom:1px solid #e6ece9}.category-title{flex-wrap:wrap;gap:8px}.category-title h2{margin:0;color:#23463b;font-size:18px}.category-title code,.prompt-list code{color:#71827c;font-size:11px}.category-actions{gap:8px;flex-shrink:0}.prompt-list{display:grid;gap:10px;padding:4px 24px 16px 48px}.prompt-list article{padding:14px;border:1px solid #dde7e3;border-radius:9px;background:#fbfdfc}.prompt-list article>div{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.prompt-list p{margin:8px 0 0;color:#536761;line-height:1.6;white-space:pre-wrap}.muted{color:#9aa6a2}.switch-row{display:grid;grid-template-columns:1fr 1fr;gap:20px}.prompt-editor-head{margin:8px 0 12px}.prompt-editor-head strong,.prompt-editor-head small{display:block}.prompt-editor-head small,.prompt-editors small,.el-form-item small{margin-top:5px;color:#83918c}.prompt-editors{display:grid;gap:12px;max-height:55vh;overflow:auto;padding-right:4px}.prompt-editors section{padding:14px 14px 4px;border:1px solid #dce6e2;border-radius:10px;background:#fbfdfc}.prompt-editor-title{margin-bottom:10px}.prompt-editor-title span{display:flex;align-items:center;gap:8px}@media(max-width:900px){.category-head,.toolbar{align-items:flex-start;flex-direction:column}.category-actions{flex-wrap:wrap}.toolbar .el-input{max-width:none;width:100%}}@media(max-width:640px){.page-head{align-items:flex-start;flex-direction:column}.metrics,.switch-row{grid-template-columns:1fr}.category-actions .el-button{margin-left:0}}
</style>
