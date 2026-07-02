<template>
  <el-form
    ref="formLogin"
    :model="loginData.loginForm"
    :rules="LoginRules"
    class="login-form"
    label-position="top"
    size="large"
  >
    <el-form-item>
      <LoginFormTitle class="w-full" />
    </el-form-item>

    <el-form-item label="管理员标识" prop="username">
      <el-input
        v-model="loginData.loginForm.username"
        :prefix-icon="iconAvatar"
        placeholder="例如 admin"
      />
    </el-form-item>

    <el-form-item label="API Key / Token" prop="password">
      <el-input
        v-model="loginData.loginForm.password"
        :prefix-icon="iconLock"
        placeholder="输入后端 API Key"
        show-password
        type="password"
        @keyup.enter="handleLogin"
      />
    </el-form-item>

    <el-form-item>
      <el-checkbox v-model="loginData.loginForm.rememberMe">记住 API Key</el-checkbox>
    </el-form-item>

    <el-form-item>
      <el-button :loading="loginLoading" class="w-full" type="primary" @click="handleLogin">
        登录
      </el-button>
    </el-form-item>
  </el-form>
</template>

<script lang="ts" setup>
import { ElLoading } from 'element-plus'
import LoginFormTitle from './LoginFormTitle.vue'
import type { RouteLocationNormalizedLoaded } from 'vue-router'
import { useIcon } from '@/hooks/web/useIcon'
import * as authUtil from '@/utils/auth'
import * as LoginApi from '@/api/login'
import { useFormValid } from './useLogin'

defineOptions({ name: 'LoginForm' })

const iconAvatar = useIcon({ icon: 'ep:avatar' })
const iconLock = useIcon({ icon: 'ep:lock' })
const formLogin = ref()
const { validForm } = useFormValid(formLogin)
const { currentRoute, push } = useRouter()
const redirect = ref<string>('')
const loginLoading = ref(false)

const LoginRules = {
  username: [required],
  password: [required]
}

const loginData = reactive({
  loginForm: {
    tenantName: '',
    username: 'admin',
    password: '',
    captchaVerification: '',
    rememberMe: true
  }
})

const getLoginFormCache = () => {
  const loginForm = authUtil.getLoginForm()
  if (loginForm) {
    loginData.loginForm = {
      ...loginData.loginForm,
      username: loginForm.username || loginData.loginForm.username,
      password: loginForm.password || loginData.loginForm.password,
      rememberMe: loginForm.rememberMe
    }
  }
}

const loading = ref<ReturnType<typeof ElLoading.service>>()

const handleLogin = async () => {
  loginLoading.value = true
  try {
    const data = await validForm()
    if (!data) return

    const loginForm = { ...loginData.loginForm }
    const token = await LoginApi.login({
      username: loginForm.username,
      password: loginForm.password,
      token: loginForm.password
    })

    loading.value = ElLoading.service({
      lock: true,
      text: '正在加载系统中...',
      background: 'rgba(0, 0, 0, 0.7)'
    })

    if (loginForm.rememberMe) {
      authUtil.setLoginForm(loginForm)
    } else {
      authUtil.removeLoginForm()
    }
    authUtil.setToken(token)
    await push({ path: redirect.value || '/workbench' })
  } finally {
    loginLoading.value = false
    loading.value?.close()
  }
}

watch(
  () => currentRoute.value,
  (route: RouteLocationNormalizedLoaded) => {
    redirect.value = route?.query?.redirect as string
  },
  {
    immediate: true
  }
)

onMounted(() => {
  getLoginFormCache()
})
</script>
