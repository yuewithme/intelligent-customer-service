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
</style>
