<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>真实问答日志</h1>
        <p>查看客户原话和 Agent 实际发出的完整回复；测试对话会标记评测编号。</p>
      </div>
      <ElButton type="primary" @click="load">刷新</ElButton>
    </div>

    <div class="filters">
      <ElInput
        v-model="filters.keyword"
        clearable
        placeholder="搜索客户原话或 Agent 回复"
        @clear="search"
        @keyup.enter="search"
      />
      <ElInput
        v-model="filters.user_id"
        clearable
        placeholder="客户 ID"
        @clear="search"
        @keyup.enter="search"
      />
      <ElInput
        v-model="filters.session_id"
        clearable
        placeholder="会话 ID"
        @clear="search"
        @keyup.enter="search"
      />
      <ElSelect v-model="filters.status" clearable placeholder="状态" @change="search">
        <ElOption label="成功" value="success" />
        <ElOption label="失败" value="failed" />
      </ElSelect>
      <ElButton @click="search">查询</ElButton>
    </div>

    <ElTable v-loading="loading" :data="items" row-key="trace_id" @row-dblclick="openDetail">
      <ElTableColumn label="时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </ElTableColumn>
      <ElTableColumn label="客户原话" min-width="260">
        <template #default="{ row }"><div class="message-cell">{{ row.user_message }}</div></template>
      </ElTableColumn>
      <ElTableColumn label="Agent 实际回复" min-width="360">
        <template #default="{ row }"><div class="message-cell">{{ row.answer || row.error_message || '-' }}</div></template>
      </ElTableColumn>
      <ElTableColumn label="识别结果" width="190">
        <template #default="{ row }">
          <div>{{ row.primary_intent || '-' }}</div>
          <small>{{ row.sales_stage || '-' }} · {{ row.route || '-' }}</small>
        </template>
      </ElTableColumn>
      <ElTableColumn label="类型" width="110">
        <template #default="{ row }">
          <ElTag v-if="row.evaluation_id" type="warning">测试</ElTag>
          <ElTag v-else type="success" effect="plain">真实</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <ElButton link type="primary" @click="openDetail(row)">查看</ElButton>
        </template>
      </ElTableColumn>
    </ElTable>

    <div class="pagination">
      <ElPagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="search"
      />
    </div>

    <ElDrawer v-model="drawerVisible" title="完整问答详情" size="min(820px, 94vw)">
      <template v-if="detail">
        <section class="detail-card customer">
          <span>客户原话</span>
          <p>{{ detail.user_message }}</p>
        </section>
        <section class="detail-card agent">
          <span>Agent 实际回复</span>
          <p>{{ detail.answer || detail.error_message || '-' }}</p>
        </section>
        <ElDescriptions :column="2" border>
          <ElDescriptionsItem label="时间">{{ formatTime(detail.created_at) }}</ElDescriptionsItem>
          <ElDescriptionsItem label="评测编号">{{ detail.evaluation_id || '真实会话' }}</ElDescriptionsItem>
          <ElDescriptionsItem label="客户 ID">{{ detail.user_id }}</ElDescriptionsItem>
          <ElDescriptionsItem label="会话 ID">{{ detail.session_id || '-' }}</ElDescriptionsItem>
          <ElDescriptionsItem label="意图">{{ detail.primary_intent || '-' }}</ElDescriptionsItem>
          <ElDescriptionsItem label="销售阶段">{{ detail.sales_stage || '-' }}</ElDescriptionsItem>
          <ElDescriptionsItem label="路由">{{ detail.route || '-' }}</ElDescriptionsItem>
          <ElDescriptionsItem label="下一动作">{{ detail.next_action || '-' }}</ElDescriptionsItem>
        </ElDescriptions>
      </template>
    </ElDrawer>
  </ContentWrap>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  getChatLog,
  getChatLogs,
  type ChatLogDetail,
  type ChatLogItem
} from '@/api/admin/chatLogs'

const loading = ref(false)
const items = ref<ChatLogItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const drawerVisible = ref(false)
const detail = ref<ChatLogDetail | null>(null)
const filters = reactive({ keyword: '', user_id: '', session_id: '', status: '' })

const load = async () => {
  loading.value = true
  try {
    const result = await getChatLogs({
      page: page.value,
      page_size: pageSize.value,
      ...Object.fromEntries(Object.entries(filters).filter(([, value]) => value))
    })
    items.value = result.items
    total.value = result.total
  } finally {
    loading.value = false
  }
}

const search = () => {
  page.value = 1
  void load()
}

const openDetail = async (row: ChatLogItem) => {
  detail.value = await getChatLog(row.trace_id)
  drawerVisible.value = true
}

const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })

onMounted(load)
</script>

<style scoped>
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 20px; }
h1 { margin: 0 0 6px; color: #173d32; font-size: 24px; }
.page-head p { margin: 0; color: #71807b; }
.filters { display: grid; grid-template-columns: minmax(240px, 2fr) repeat(3, minmax(150px, 1fr)) auto; gap: 12px; margin-bottom: 18px; }
.message-cell { display: -webkit-box; overflow: hidden; white-space: pre-wrap; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }
small { color: #87948f; }
.pagination { display: flex; justify-content: flex-end; margin-top: 18px; }
.detail-card { padding: 18px; margin-bottom: 16px; border-radius: 12px; }
.detail-card span { color: #66766f; font-size: 12px; }
.detail-card p { margin: 8px 0 0; color: #20322c; line-height: 1.75; white-space: pre-wrap; }
.customer { background: #f4f7f6; }
.agent { background: #eaf7f1; }
@media (max-width: 1000px) {
  .filters { grid-template-columns: 1fr 1fr; }
}
</style>
