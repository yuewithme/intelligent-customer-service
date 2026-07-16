<template>
  <div class="gate-page">
    <section class="gate-panel">
      <div class="brand">
        <img src="@/assets/imgs/logo.png" alt="" />
        <span>智能客服后台</span>
      </div>
      <h1>访问门禁</h1>
      <p>输入访问密码后进入客服工作台。</p>
      <ElForm :model="form" label-position="top" size="large" @submit.prevent="unlock">
        <ElFormItem label="访问密码">
          <ElInput
            v-model="form.password"
            autofocus
            placeholder="请输入访问密码"
            show-password
            type="password"
            @keyup.enter="unlock"
          />
        </ElFormItem>
        <ElButton :loading="loading" class="submit" type="primary" @click="unlock">
          进入后台
        </ElButton>
      </ElForm>
    </section>
  </div>
</template>

<script lang="ts" setup>
import { ElMessage } from 'element-plus'

defineOptions({ name: 'Gate' })

const route = useRoute()
const loading = ref(false)
const form = reactive({ password: '' })

const unlock = async () => {
  if (!form.password.trim()) {
    ElMessage.warning('请输入访问密码')
    return
  }
  loading.value = true
  try {
    const response = await fetch('/api/gate', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: form.password })
    })
    if (!response.ok) {
      ElMessage.error('访问密码不正确')
      return
    }
    const redirect =
      typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/')
        ? route.query.redirect
        : '/workbench'
    window.location.replace(redirect)
  } finally {
    loading.value = false
  }
}
</script>

<style lang="scss" scoped>
.gate-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f4f7fb;
  padding: 24px;
}

.gate-panel {
  width: min(420px, 100%);
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 36px;
  box-shadow: 0 18px 48px rgb(15 23 42 / 8%);

  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
    color: #111827;
    font-size: 20px;
    font-weight: 700;

    img {
      width: 40px;
      height: 40px;
    }
  }

  h1 {
    margin: 32px 0 10px;
    color: #111827;
    font-size: 28px;
    font-weight: 700;
  }

  p {
    margin: 0 0 28px;
    color: #64748b;
    line-height: 1.7;
  }

  .submit {
    width: 100%;
  }
}
</style>
