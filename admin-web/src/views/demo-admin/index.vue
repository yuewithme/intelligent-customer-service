<template>
  <main class="demo-admin">
    <header>
      <div>
        <strong>销售 Agent 测试后台</strong>
        <span>仅展示网页测试和 MCP 入口产生的会话</span>
      </div>
      <a href="/demo-chat">返回测试聊天</a>
    </header>
    <Workbench />
  </main>
</template>

<script setup lang="ts">
import Workbench from '@/views/workbench/index.vue'
</script>

<style scoped>
.demo-admin {
  min-height: 100vh;
  background: #f5f7fb;
}

header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 58px;
  padding: 0 20px;
  color: #fff;
  background: #174f3d;
}

header div {
  display: flex;
  align-items: baseline;
  gap: 16px;
}

header strong {
  font-size: 18px;
}

header span {
  color: #c5e3d8;
  font-size: 13px;
}

header a {
  color: #fff;
}

:deep(.workbench) {
  height: calc(100vh - 58px);
}
</style>
