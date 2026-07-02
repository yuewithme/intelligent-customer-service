<template>
  <div :class="prefixCls" class="h-full bg-[#f5f7fb]">
    <div class="mx-auto h-full max-w-1180px flex items-center justify-center px-24px">
      <div class="grid w-full grid-cols-[1fr_440px] gap-48px lt-lg:grid-cols-1">
        <section class="flex flex-col justify-center lt-lg:hidden">
          <div class="flex items-center text-[#1f2937]">
            <img alt="" class="mr-12px h-48px w-48px" src="@/assets/imgs/logo.png" />
            <span class="text-24px font-bold">{{ underlineToHump(appStore.getTitle) }}</span>
          </div>
          <h1 class="mb-16px mt-40px text-38px font-semibold text-[#111827]">
            受控人工接管工作台
          </h1>
          <p class="max-w-560px text-16px leading-7 text-[#4b5563]">
            AI 默认回复客户，人工默认监督会话；只有转人工并领取后，客服才可以发送人工回复。
          </p>
        </section>

        <LoginForm class="rounded-6px bg-white p-28px shadow-sm" />
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { underlineToHump } from '@/utils'
import { useDesign } from '@/hooks/web/useDesign'
import { useAppStore } from '@/store/modules/app'
import { LoginForm } from './components'

defineOptions({ name: 'Login' })

const appStore = useAppStore()
const { getPrefixCls } = useDesign()
const prefixCls = getPrefixCls('login')
</script>
