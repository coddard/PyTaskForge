<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createJob, updateJob, fetchJob } from '@/composables/api'

const route  = useRoute()
const router = useRouter()

const isEdit = computed(() => !!route.params.id)
const title  = computed(() => isEdit.value ? 'Edit Job' : 'New Job')

const form = ref({
  name: '', description: '', script_path: '',
  execution_mode: 'venv', docker_image: '',
  trigger_type: 'interval', trigger_config: '{"seconds": 60}',
  requirements: '', env_vars: '{}', timeout_seconds: null
})
const error   = ref('')
const loading = ref(false)
const saving  = ref(false)

onMounted(async () => {
  if (!isEdit.value) return
  loading.value = true
  try {
    const res = await fetchJob(route.params.id)
    const j = res.data
    form.value = {
      name: j.name, description: j.description || '',
      script_path: j.script_path,
      execution_mode: j.execution_mode,
      docker_image: j.docker_image || '',
      trigger_type: j.trigger_type,
      trigger_config: JSON.stringify(j.trigger_config, null, 2),
      requirements: j.requirements || '',
      env_vars: JSON.stringify(j.env_vars, null, 2),
      timeout_seconds: j.timeout_seconds
    }
  } catch (e) { error.value = e.response?.data?.detail || e.message }
  finally { loading.value = false }
})

async function save() {
  error.value = ''
  saving.value = true
  try {
    const payload = {
      ...form.value,
      trigger_config: JSON.parse(form.value.trigger_config || '{}'),
      env_vars: JSON.parse(form.value.env_vars || '{}'),
      docker_image: form.value.docker_image || null,
      timeout_seconds: form.value.timeout_seconds || null
    }
    if (isEdit.value) await updateJob(route.params.id, payload)
    else await createJob(payload)
    router.push('/')
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally { saving.value = false }
}

const triggerHint = computed(() => ({
  interval: '{"seconds": 60} or {"minutes": 5, "hours": 1}',
  cron:     '{"hour": "9", "minute": "0", "day_of_week": "mon-fri"}',
  date:     '{"run_date": "2025-12-31T09:00:00"}'
})[form.value.trigger_type])
</script>

<template>
  <div class="min-h-screen bg-gray-950">
    <header class="border-b border-gray-800 bg-gray-900 sticky top-0 z-30">
      <div class="max-w-3xl mx-auto px-4 h-14 flex items-center gap-3">
        <router-link to="/" class="text-gray-400 hover:text-white transition">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
          </svg>
        </router-link>
        <h1 class="font-bold text-white">{{ title }}</h1>
      </div>
    </header>

    <main class="max-w-3xl mx-auto px-4 py-8">
      <div v-if="loading" class="card animate-pulse h-64" />

      <form v-else @submit.prevent="save" class="space-y-6">
        <!-- Basic Information -->
        <div class="card space-y-4">
          <h3 class="font-semibold text-white">Basic Information</h3>
          <div>
            <label class="label">Job Name *</label>
            <input v-model="form.name" class="input" required placeholder="Example: Daily Backup" />
          </div>
          <div>
            <label class="label">Description</label>
            <textarea v-model="form.description" class="input" rows="2" placeholder="Optional…" />
          </div>
          <div>
            <label class="label">Script Path * <span class="text-gray-500 font-normal">(relative to the jobs/ directory)</span></label>
            <input v-model="form.script_path" class="input font-mono" required placeholder="backup/run.py" />
          </div>
        </div>

        <!-- Execution Mode -->
        <div class="card space-y-4">
          <h3 class="font-semibold text-white">Execution Mode</h3>
          <div class="flex gap-3">
            <label v-for="m in ['venv','docker']" :key="m"
              class="flex-1 flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition"
              :class="form.execution_mode === m ? 'border-brand bg-brand/10' : 'border-gray-700 hover:border-gray-600'">
              <input type="radio" v-model="form.execution_mode" :value="m" class="text-brand" />
              <div>
                <p class="text-sm font-medium text-white capitalize">{{ m }}</p>
                <p class="text-xs text-gray-500">{{ m === 'venv' ? 'Virtual environment (recommended)' : 'Docker container' }}</p>
              </div>
            </label>
          </div>
          <div v-if="form.execution_mode === 'docker'">
            <label class="label">Docker Image</label>
            <input v-model="form.docker_image" class="input font-mono" placeholder="python:3.11-slim" />
          </div>
        </div>

        <!-- Schedule -->
        <div class="card space-y-4">
          <h3 class="font-semibold text-white">Schedule</h3>
          <div>
            <label class="label">Trigger Type</label>
            <select v-model="form.trigger_type" class="input">
              <option value="interval">Interval (every N seconds/minutes/hours)</option>
              <option value="cron">Cron (scheduled)</option>
              <option value="date">Date (one-time)</option>
            </select>
          </div>
          <div>
            <label class="label">
              Trigger Configuration
              <span class="ml-2 text-gray-500 font-mono text-xs">JSON</span>
            </label>
            <textarea v-model="form.trigger_config" class="input font-mono text-xs" rows="3" />
            <p class="text-xs text-gray-500 mt-1">Example: <code class="text-brand">{{ triggerHint }}</code></p>
          </div>
          <div>
            <label class="label">Timeout <span class="text-gray-500 font-normal">(seconds, blank = unlimited)</span></label>
            <input v-model.number="form.timeout_seconds" class="input" type="number" min="1" placeholder="300" />
          </div>
        </div>

        <!-- Dependencies & Environment -->
        <div class="card space-y-4">
          <h3 class="font-semibold text-white">Dependencies & Environment</h3>
          <div>
            <label class="label">requirements.txt <span class="text-gray-500 font-normal">(content, for venv mode)</span></label>
            <textarea v-model="form.requirements" class="input font-mono text-xs" rows="4" placeholder="requests>=2.31.0&#10;pandas" />
          </div>
          <div>
            <label class="label">Environment Variables <span class="text-gray-500 font-mono text-xs">JSON</span></label>
            <textarea v-model="form.env_vars" class="input font-mono text-xs" rows="3" placeholder='{"DB_HOST": "localhost"}' />
          </div>
        </div>

        <!-- Error -->
        <div v-if="error" class="text-red-400 bg-red-950 rounded-lg p-3 text-sm">{{ error }}</div>

        <!-- Save -->
        <div class="flex gap-3 justify-end">
          <router-link to="/" class="btn-ghost">Cancel</router-link>
          <button type="submit" class="btn-primary" :disabled="saving">
            <svg v-if="saving" class="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
            </svg>
            {{ saving ? 'Saving…' : (isEdit ? 'Update' : 'Create') }}
          </button>
        </div>
      </form>
    </main>
  </div>
</template>

