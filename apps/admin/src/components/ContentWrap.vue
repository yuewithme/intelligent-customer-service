<template>
  <section class="content-wrap">
    <slot />
  </section>
</template>

<style scoped>
.content-wrap {
  width: 100%;
  max-width: 1680px;
  min-height: calc(100dvh - var(--app-header-height));
  padding: 28px 32px 40px;
  margin: 0 auto;
}

.content-wrap :deep(.page-head) {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px 24px;
  margin-bottom: 24px;
}
.content-wrap :deep(.page-head h1) {
  margin: 0;
  color: var(--app-text);
  font-size: 26px;
  font-weight: 600;
  line-height: 1.4;
  letter-spacing: -.5px;
}
.content-wrap :deep(.page-head p) {
  max-width: 72ch;
  margin: 8px 0 0;
  color: var(--app-text-secondary);
  font-size: 14px;
  line-height: 1.7;
}
.content-wrap :deep(.page-head > .el-button) { flex-shrink: 0; }
.content-wrap :deep(.el-table) { border: 1px solid var(--app-border); border-radius: var(--app-radius); }
.content-wrap :deep(.toolbar > .el-button + .el-button) { margin-left: 0; }

@media (max-width: 1280px) {
  .content-wrap { padding: 24px; }
}

@media (max-width: 820px), (hover: none) and (pointer: coarse) {
  .content-wrap {
    padding: 20px 16px 28px;
  }
  .content-wrap :deep(.page-head) { gap: 14px; margin-bottom: 20px; }
  .content-wrap :deep(.page-head h1) { font-size: 24px; }
}
</style>
