<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth   = useAuthStore()
const router = useRouter()
const route  = useRoute()

const username = ref('')
const password = ref('')
const loading  = ref(false)
const error    = ref('')

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await auth.doLogin(username.value, password.value)
    router.push(route.query.redirect || '/')
  } catch (e) {
    error.value = e.response?.data?.detail || 'Login failed.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-gray-950 px-4">
    <div class="w-full max-w-sm card space-y-6">
      <!-- Logo -->
      <div class="text-center">
        <span class="text-3xl">⚙️</span>
        <h1 class="mt-2 text-xl font-bold text-white">PyTaskForge</h1>
        <p class="text-xs text-gray-500 mt-1">Python Job Manager</p>
      </div>

      <!-- Form -->
      <form @submit.prevent="submit" class="space-y-4">
        <div>
          <label class="label">Username</label>
          <input v-model="username" class="input" type="text" placeholder="admin" required autocomplete="username" />
        </div>
        <div>
          <label class="label">Password</label>
          <input v-model="password" class="input" type="password" placeholder="••••••" required autocomplete="current-password" />
        </div>

        <p v-if="error" class="text-sm text-red-400 bg-red-950 rounded-lg px-3 py-2">{{ error }}</p>

        <button type="submit" class="btn-primary w-full justify-center" :disabled="loading">
          <svg v-if="loading" class="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
          </svg>
          {{ loading ? 'Signing in…' : 'Sign In' }}
        </button>
      </form>
    </div>
  </div>
</template>

