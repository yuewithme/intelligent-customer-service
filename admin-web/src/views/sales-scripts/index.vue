<template>
  <section class="page">
    <header><h2>销售话术</h2><p>按阶段、动作、分支和状态检索结构化话术。</p></header>
    <ElForm inline>
      <ElFormItem label="阶段"><ElSelect v-model="filters.stage" clearable><ElOption v-for="item in stages" :key="item.stage" :label="item.display_name" :value="item.stage" /></ElSelect></ElFormItem>
      <ElFormItem label="动作"><ElInput v-model="filters.action" clearable /></ElFormItem>
      <ElFormItem label="分支"><ElInput v-model="filters.branch" clearable /></ElFormItem>
      <ElFormItem label="状态"><ElSelect v-model="filters.status" clearable><ElOption label="启用" value="active" /><ElOption label="停用" value="inactive" /></ElSelect></ElFormItem>
      <ElButton type="primary" @click="load">查询</ElButton>
    </ElForm>
    <ElTable :data="scripts" v-loading="loading" stripe>
      <ElTableColumn prop="sales_stage" label="阶段" width="150" />
      <ElTableColumn prop="sales_action" label="动作" width="170" />
      <ElTableColumn prop="branch_code" label="分支" min-width="180" />
      <ElTableColumn prop="answer" label="话术" min-width="360" show-overflow-tooltip />
      <ElTableColumn label="事实门" min-width="180"><template #default="scope">{{ scope.row.required_fact_keys.join('、') || '-' }}</template></ElTableColumn>
      <ElTableColumn prop="status" label="状态" width="90" />
    </ElTable>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { getSalesScripts, getSalesStages, type SalesScript, type SalesStageDefinition } from '@/api/admin/sales-flow'
const filters = reactive({ stage: '', action: '', branch: '', status: 'active' })
const scripts = ref<SalesScript[]>([])
const stages = ref<SalesStageDefinition[]>([])
const loading = ref(false)
const load = async () => { loading.value = true; try { scripts.value = (await getSalesScripts(filters)).items } finally { loading.value = false } }
onMounted(async () => { stages.value = (await getSalesStages()).items; await load() })
</script>

<style scoped>
.page { padding: 24px; } header { margin-bottom: 18px; } h2, p { margin: 0; } header p { margin-top: 6px; color: #6b7280; } .el-select { width: 180px; }
</style>
