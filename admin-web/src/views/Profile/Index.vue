<template>
  <ContentWrap>
    <div class="profile">
      <ElAvatar :size="64" :src="avatar" />
      <div>
        <h2>{{ userStore.user.nickname || '管理员' }}</h2>
        <p>智能客服后台管理员</p>
      </div>
    </div>
  </ContentWrap>
</template>

<script lang="ts" setup>
import avatarImg from '@/assets/imgs/avatar.gif'
import { useUserStore } from '@/store/modules/user'

defineOptions({ name: 'Profile' })

const userStore = useUserStore()
const avatar = computed(() => userStore.user.avatar || avatarImg)
</script>

<style scoped>
.profile {
  display: flex;
  gap: 16px;
  align-items: center;
}

h2 {
  margin: 0 0 8px;
  font-size: 20px;
  font-weight: 600;
  color: #111827;
}

p {
  margin: 0;
  color: #6b7280;
}
</style>
