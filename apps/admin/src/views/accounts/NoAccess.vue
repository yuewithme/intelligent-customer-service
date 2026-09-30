<template>
  <ElEmpty description="当前账号没有此应用或页面权限，请联系统一平台管理员。">
    <a :href="libraryApp ? libraryLogin() : '/api/gate/login'">重新检查平台授权</a>
  </ElEmpty>
</template>

<script setup lang="ts">
import { libraryApp, libraryLogin } from '@/utils/library'
</script>
