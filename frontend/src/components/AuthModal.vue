<template>
  <div v-if="authState.showModal" class="auth-overlay" @click.self="closeAuth">
    <section class="auth-modal" role="dialog" aria-modal="true" aria-label="用户账号">
      <button type="button" class="auth-close" aria-label="关闭" @click="closeAuth">×</button>

      <template v-if="authState.user">
        <div class="auth-account-view">
          <div class="auth-large-avatar">{{ accountInitial }}</div>
          <h2>{{ authState.user.display_name }}</h2>
          <p>@{{ authState.user.username }}</p>
          <div class="auth-account-meta">登录后投注记录仅当前账号可见</div>
          <button type="button" class="auth-submit auth-logout" :disabled="submitting" @click="handleLogout">
            {{ submitting ? '正在退出…' : '退出登录' }}
          </button>
        </div>
      </template>

      <template v-else>
        <div class="auth-brand">
          <div class="auth-logo">⚽</div>
          <h2>{{ mode === 'login' ? '登录账号' : '创建账号' }}</h2>
          <p>保存并同步属于你的投注记录</p>
        </div>

        <div class="auth-tabs">
          <button type="button" :class="{ active: mode === 'login' }" @click="switchMode('login')">登录</button>
          <button type="button" :class="{ active: mode === 'register' }" @click="switchMode('register')">注册</button>
        </div>

        <form class="auth-form" @submit.prevent="submit">
          <label>
            <span>用户名</span>
            <input v-model.trim="form.username" autocomplete="username" maxlength="32" placeholder="中文、字母、数字或下划线" required />
          </label>
          <label v-if="mode === 'register'">
            <span>昵称</span>
            <input v-model.trim="form.displayName" autocomplete="nickname" maxlength="32" placeholder="页面展示名称" required />
          </label>
          <label>
            <span>密码</span>
            <input v-model="form.password" type="password" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" minlength="6" maxlength="128" placeholder="至少6位" required />
          </label>
          <div v-if="error" class="auth-error">{{ error }}</div>
          <button type="submit" class="auth-submit" :disabled="submitting">
            {{ submitting ? '请稍候…' : mode === 'login' ? '登录' : '注册并登录' }}
          </button>
        </form>
      </template>
    </section>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { authState, closeAuth, login, logout, register } from '../auth'

const mode = ref(authState.mode === 'register' ? 'register' : 'login')
const submitting = ref(false)
const error = ref('')
const form = reactive({ username: '', displayName: '', password: '' })
const accountInitial = computed(() => (authState.user?.display_name || authState.user?.username || '我').slice(0, 1))

watch(() => authState.mode, (value) => {
  if (value === 'login' || value === 'register') mode.value = value
})

watch(() => authState.showModal, (visible) => {
  if (!visible || authState.user) return
  mode.value = authState.mode === 'register' ? 'register' : 'login'
  form.username = ''
  form.displayName = ''
  form.password = ''
  error.value = ''
})

const switchMode = (value) => {
  mode.value = value
  error.value = ''
}

const submit = async () => {
  error.value = ''
  submitting.value = true
  try {
    if (mode.value === 'login') await login(form.username, form.password)
    else await register(form.username, form.displayName, form.password)
    form.password = ''
    closeAuth()
  } catch (err) {
    error.value = err.message || '操作失败'
  } finally {
    submitting.value = false
  }
}

const handleLogout = async () => {
  submitting.value = true
  try {
    await logout()
    closeAuth()
  } catch (err) {
    error.value = err.message || '退出失败'
  } finally {
    submitting.value = false
  }
}
</script>
