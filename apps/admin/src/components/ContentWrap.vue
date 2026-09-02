<template>
  <section class="content-wrap">
    <slot />
  </section>
</template>

<style scoped>
.content-wrap {
  min-height: calc(100vh - 96px);
  padding: 20px;
  margin: 18px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
}

@media (max-width: 820px), (hover: none) and (pointer: coarse) {
  .content-wrap {
    min-height: calc(100dvh - 72px);
    padding: 12px;
    margin: 8px;
    border-radius: 9px;
  }
}
</style>
