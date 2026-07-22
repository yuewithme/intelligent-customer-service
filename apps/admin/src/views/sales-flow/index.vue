<template>
  <section class="page">
    <header><div><h2>首单销售流程</h2><p>统一展示阶段目标和流转证据。</p></div></header>
    <ElSkeleton v-if="loading" :rows="7" animated />
    <div v-else class="stage-list">
      <article v-for="stage in stages" :key="stage.stage">
        <span class="sequence">{{ stage.sequence }}</span>
        <div class="content">
          <div class="title"><h3>{{ stage.display_name }}</h3></div>
          <p>{{ stage.objective }}</p>
          <dl>
            <dt>进入条件</dt><dd>{{ join(stage.entry_evidence_any) }}</dd>
            <dt>退出条件</dt><dd>{{ join(stage.exit_evidence_any) }}</dd>
            <dt>允许动作</dt><dd>{{ join(stage.allowed_actions) }}</dd>
            <dt>可查数据源</dt><dd>{{ join(stage.allowed_knowledge_sources) }}</dd>
            <dt>按需数据源</dt><dd>{{ join(stage.conditional_knowledge_sources) }}</dd>
            <dt>禁止行为</dt><dd>{{ join(stage.prohibited_behaviors) }}</dd>
          </dl>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getSalesStages, type SalesStageDefinition } from '@/api/admin/sales-flow'

const loading = ref(false)
const stages = ref<SalesStageDefinition[]>([])
const join = (values: string[]) => values?.join('、') || '-'
onMounted(async () => {
  loading.value = true
  try { stages.value = (await getSalesStages()).items.sort((a, b) => a.sequence - b.sequence) }
  finally { loading.value = false }
})
</script>

<style scoped>
.page { padding: 24px; } header { margin-bottom: 20px; } h2, h3, p { margin: 0; } header p { margin-top: 6px; color: #6b7280; }
.stage-list { display: grid; gap: 14px; } article { display: flex; gap: 16px; padding: 20px; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; }
.sequence { display: grid; flex: 0 0 36px; height: 36px; place-items: center; color: #fff; font-weight: 700; background: #238264; border-radius: 50%; }
.content { flex: 1; } .title { display: flex; justify-content: space-between; } .content > p { margin: 8px 0 14px; color: #374151; }
dl { display: grid; grid-template-columns: 88px 1fr; gap: 8px; margin: 0; font-size: 13px; } dt { color: #6b7280; } dd { margin: 0; }
</style>
