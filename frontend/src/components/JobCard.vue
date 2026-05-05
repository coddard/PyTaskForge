<script setup>
import { useRouter } from 'vue-router'
defineProps({ job: Object })
defineEmits(['delete', 'run'])
const router = useRouter()

const statusLabel = s => ({ active: 'Active', paused: 'Paused', deleted: 'Deleted' }[s] ?? s)
const modeIcon    = m => m === 'docker' ? '🐳' : '🐍'
const triggerIcon = t => ({ cron: '⏱', interval: '🔁', date: '📅' }[t] ?? '⚡')
</script>

<template>
  <div class="card flex flex-col gap-4 hover:border-gray-700 transition group">
    <!-- Header row -->
    <div class="flex items-start justify-between gap-2">
      <div class="min-w-0">
        <h3 class="font-semibold text-white truncate text-sm group-hover:text-brand-light transition">{{ job.name }}</h3>
        <p v-if="job.description" class="text-xs text-gray-500 truncate mt-0.5">{{ job.description }}</p>
      </div>
      <span :class="`badge-${job.status}`">{{ statusLabel(job.status) }}</span>
    </div>

    <!-- Metadata -->
    <dl class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
      <div><dt class="text-gray-600">Mode</dt><dd class="text-gray-300 font-medium">{{ modeIcon(job.execution_mode) }} {{ job.execution_mode }}</dd></div>
      <div><dt class="text-gray-600">Trigger</dt><dd class="text-gray-300 font-medium">{{ triggerIcon(job.trigger_type) }} {{ job.trigger_type }}</dd></div>
      <div class="col-span-2"><dt class="text-gray-600">Script</dt><dd class="text-brand font-mono truncate">{{ job.script_path }}</dd></div>
    </dl>

    <!-- Actions -->
    <div class="flex items-center gap-2 pt-1 border-t border-gray-800">
      <button @click="$emit('run')" title="Run now"
        class="flex-1 btn-ghost text-xs py-1.5 justify-center text-emerald-400 hover:bg-emerald-950">
        ▶ Run
      </button>
      <button @click="router.push(`/jobs/${job.id}/logs`)" title="View logs"
        class="flex-1 btn-ghost text-xs py-1.5 justify-center">
        📋 Logs
      </button>
      <router-link :to="`/jobs/${job.id}/edit`" title="Edit"
        class="btn-ghost text-xs py-1.5 px-2.5">✏️</router-link>
      <button @click="$emit('delete')" title="Delete"
        class="btn-ghost text-xs py-1.5 px-2.5 text-red-400 hover:bg-red-950">🗑</button>
    </div>
  </div>
</template>

