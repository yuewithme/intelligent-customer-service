<template>
  <ContentWrap>
    <h1>{{ title }}</h1>
    <ElEmpty description="功能建设中" />
  </ContentWrap>
</template>

<script setup lang="ts">
defineProps<{ title: string }>()
</script>

<style scoped>h1 { margin: 0 0 24px; font-size: 22px; }</style>
