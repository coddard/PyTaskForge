<script setup>
import { onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useJobsStore } from '@/stores/jobs'
import { getMe } from '@/composables/api'
import { useRouter } from 'vue-router'
import JobCard from '@/components/JobCard.vue'
import DevModeBanner from '@/components/DevModeBanner.vue'

const auth   = useAuthStore()
const jobs   = useJobsStore()
const router = useRouter()

onMounted(async () => {
  try {
    const me = await getMe()
    auth.setUser(me.data)
  } catch { auth.logout(); router.push('/login') }
  await jobs.load()
})

function logout() { auth.logout(); router.push('/login') }
</script>

<template>
  <div class="min-h-screen bg-gray-950">
    <!-- Topbar -->
    <header class="border-b border-gray-800 bg-gray-900 sticky top-0 z-30">
      <div class="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
        <div class="flex items-center gap-3">
          <span class="text-xl">⚙️</span>
          <span class="font-bold text-white">PyTaskForge</span>
          <span class="text-xs text-gray-500 hidden sm:block">v1.0.0</span>
        </div>
        <div class="flex items-center gap-3">
          <span class="text-sm text-gray-400 hidden sm:block">{{ auth.user?.username }}</span>
          <button @click="logout" class="btn-ghost text-xs px-3 py-1.5">Logout</button>
        </div>
      </div>
    </header>

    <DevModeBanner v-if="auth.devMode" />

    <!-- Content -->
    <main class="max-w-7xl mx-auto px-4 py-8">
      <!-- Heading + New Job -->
      <div class="flex items-center justify-between mb-6">
        <div>
          <h2 class="text-2xl font-bold text-white">Jobs</h2>
          <p class="text-sm text-gray-500 mt-0.5">Manage your scheduled Python scripts.</p>
        </div>
        <router-link to="/jobs/new" class="btn-primary">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
          </svg>
          New Job
        </router-link>
      </div>

      <!-- Loading -->
      <div v-if="jobs.loading" class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div v-for="i in 3" :key="i" class="card animate-pulse h-40" />
      </div>

      <!-- Error -->
      <div v-else-if="jobs.error" class="text-red-400 bg-red-950 rounded-lg p-4 text-sm">
        {{ jobs.error }}
      </div>

      <!-- Empty state -->
      <div v-else-if="!jobs.jobs.length" class="text-center py-20 text-gray-600">
        <p class="text-5xl mb-4">📭</p>
        <p class="text-lg font-medium text-gray-400">No jobs yet.</p>
        <p class="text-sm mt-1">Click <router-link to="/jobs/new" class="text-brand underline">here</router-link> to create your first job.</p>
      </div>

      <!-- Job list -->
      <div v-else class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <JobCard
          v-for="job in jobs.jobs"
          :key="job.id"
          :job="job"
          @delete="jobs.remove(job.id)"
          @run="jobs.triggerNow(job.id)"
        />
      </div>
    </main>
  </div>
</template>

