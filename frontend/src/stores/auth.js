import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { login as apiLogin } from '@/composables/api'

export const useAuthStore = defineStore('auth', () => {
  const token   = ref(localStorage.getItem('ptf_token') || null)
  const devMode = ref(false)
  const user    = ref(null)

  const isAuthenticated = computed(() => !!token.value)

  async function doLogin(username, password) {
    const res = await apiLogin(username, password)
    token.value = res.data.access_token
    localStorage.setItem('ptf_token', token.value)
  }

  function logout() {
    token.value = null
    user.value  = null
    localStorage.removeItem('ptf_token')
  }

  function setUser(u) { user.value = u; devMode.value = u?.dev_mode ?? false }

  return { token, devMode, user, isAuthenticated, doLogin, logout, setUser }
})

