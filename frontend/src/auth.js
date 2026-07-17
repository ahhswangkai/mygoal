import { reactive } from 'vue'

export const authState = reactive({
  user: null,
  initialized: false,
  loading: false,
  showModal: false,
  mode: 'login'
})

const request = async (url, options = {}) => {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok || data.success === false) {
    const error = new Error(data.message || '请求失败')
    error.status = response.status
    error.code = data.code
    throw error
  }
  return data
}

export const loadCurrentUser = async (force = false) => {
  if (authState.initialized && !force) return authState.user
  authState.loading = true
  try {
    const data = await request('/api/auth/me')
    authState.user = data.authenticated ? data.user : null
    return authState.user
  } catch (error) {
    authState.user = null
    return null
  } finally {
    authState.loading = false
    authState.initialized = true
  }
}

export const openAuth = (mode = 'login') => {
  authState.mode = authState.user ? 'account' : mode
  authState.showModal = true
}

export const closeAuth = () => {
  authState.showModal = false
}

export const login = async (username, password) => {
  const data = await request('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password })
  })
  authState.user = data.user
  authState.initialized = true
  return data.user
}

export const register = async (username, displayName, password) => {
  const data = await request('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, display_name: displayName, password })
  })
  authState.user = data.user
  authState.initialized = true
  return data.user
}

export const logout = async () => {
  await request('/api/auth/logout', { method: 'POST', body: '{}' })
  authState.user = null
  authState.initialized = true
  authState.mode = 'login'
}

export const apiRequest = request
