<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { fetchJob, fetchHistory } from '@/composables/api'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const auth  = useAuthStore()
const jobId = route.params.id

const job      = ref(null)
const history  = ref([])
const logs     = ref([])       // live log lines
const wsStatus = ref('idle')   // idle | connecting | connected | closed
const autoScroll = ref(true)
const termEl   = ref(null)
let ws = null

// ── WebSocket connection ───────────────────────────────────────────────────────
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = auth.token
  const url   = `${proto}://${location.host}/ws/jobs/${jobId}/logs${token ? '?token=' + token : ''}`

  wsStatus.value = 'connecting'
  ws = new WebSocket(url)

  ws.onopen  = ()  => { wsStatus.value = 'connected'; logs.value.push('[SYS] Connected to the live log channel.\n') }
  ws.onclose = ()  => { wsStatus.value = 'closed' }
  ws.onerror = ()  => { wsStatus.value = 'closed' }
  ws.onmessage = e => {
    logs.value.push(e.data)
    if (autoScroll.value) nextTick(scrollBottom)
  }

  // ping
  const ping = setInterval(() => ws?.readyState === 1 && ws.send('ping'), 25000)
  ws.addEventListener('close', () => clearInterval(ping))
}

function disconnectWS() { ws?.close(); ws = null }
function scrollBottom()  { if (termEl.value) termEl.value.scrollTop = termEl.value.scrollHeight }

// ── CSS class for a log line ───────────────────────────────────────────────────
function lineClass(line) {
  if (line.startsWith('[ERR]'))   return 'log-err'
  if (line.startsWith('[INFO]'))  return 'log-info'
  if (line.startsWith('[PIP]'))   return 'log-pip'
  if (line.startsWith('[BUILD]')) return 'log-build'
  if (line.startsWith('[SYSTEM]') || line.startsWith('[SYS]')) return 'log-sys'
  if (line.startsWith('[WARN]'))  return 'log-warn'
  return 'log-out'
}

onMounted(async () => {
  const [jobRes, histRes] = await Promise.all([
    fetchJob(jobId), fetchHistory(jobId, 5)
  ])
  job.value     = jobRes.data
  history.value = histRes.data
  connectWS()
})

onUnmounted(disconnectWS)
</script>

<template>
  <div class="min-h-screen bg-gray-950 flex flex-col">
    <!-- Header -->
    <header class="border-b border-gray-800 bg-gray-900 sticky top-0 z-30">
      <div class="max-w-7xl mx-auto px-4 h-14 flex items-center gap-3">
        <router-link to="/" class="text-gray-400 hover:text-white transition">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
          </svg>
        </router-link>
        <h1 class="font-bold text-white truncate">
          {{ job?.name ?? '...' }}
          <span class="ml-2 text-xs text-gray-500 font-normal">#{{ jobId }}</span>
        </h1>
        <!-- WebSocket status -->
        <span class="ml-auto flex items-center gap-1.5 text-xs"
          :class="wsStatus === 'connected' ? 'text-emerald-400' : wsStatus === 'connecting' ? 'text-yellow-400' : 'text-gray-500'">
          <span class="w-2 h-2 rounded-full"
            :class="wsStatus === 'connected' ? 'bg-emerald-400 animate-pulse' : wsStatus === 'connecting' ? 'bg-yellow-400' : 'bg-gray-600'" />
          {{ wsStatus === 'connected' ? 'Live' : wsStatus === 'connecting' ? 'Connecting' : 'Closed' }}
        </span>
      </div>
    </header>

    <div class="flex-1 max-w-7xl mx-auto px-4 py-6 w-full grid grid-cols-1 lg:grid-cols-3 gap-6">
      <!-- Left: Terminal -->
      <div class="lg:col-span-2 flex flex-col gap-4">
        <!-- Terminal header -->
        <div class="flex items-center justify-between">
          <span class="text-sm font-medium text-gray-400">Live Log Stream</span>
          <div class="flex items-center gap-3">
            <label class="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
              <input type="checkbox" v-model="autoScroll" class="rounded" />
              Auto-scroll
            </label>
            <button @click="logs = []" class="text-xs text-gray-500 hover:text-gray-300 transition">Clear</button>
            <button v-if="wsStatus === 'closed'" @click="connectWS()" class="btn-ghost text-xs px-2 py-1">Reconnect</button>
          </div>
        </div>

        <!-- Terminal -->
        <div ref="termEl"
          class="flex-1 bg-gray-950 border border-gray-800 rounded-xl p-4 font-mono text-xs leading-5 overflow-y-auto min-h-[420px] max-h-[600px]">
          <div v-if="!logs.length" class="text-gray-600 text-center pt-12">
            Logs will appear here when the job is triggered…
          </div>
          <div v-for="(line, i) in logs" :key="i" :class="lineClass(line)" class="whitespace-pre-wrap break-all">{{ line }}</div>
        </div>
      </div>

      <!-- Right: Details + History -->
      <div class="space-y-4">
        <!-- Job Details -->
        <div class="card space-y-3">
          <h3 class="font-semibold text-white text-sm">Job Details</h3>
          <dl class="space-y-2 text-xs">
            <div class="flex justify-between">
              <dt class="text-gray-500">Mode</dt>
              <dd class="font-medium text-gray-200 capitalize">{{ job?.execution_mode }}</dd>
            </div>
            <div class="flex justify-between">
              <dt class="text-gray-500">Trigger</dt>
              <dd class="font-medium text-gray-200 capitalize">{{ job?.trigger_type }}</dd>
            </div>
            <div class="flex justify-between">
              <dt class="text-gray-500">Script</dt>
              <dd class="font-mono text-brand truncate max-w-32" :title="job?.script_path">{{ job?.script_path }}</dd>
            </div>
            <div v-if="job?.timeout_seconds" class="flex justify-between">
              <dt class="text-gray-500">Timeout</dt>
              <dd class="text-gray-200">{{ job.timeout_seconds }}s</dd>
            </div>
          </dl>
        </div>

        <!-- Run History -->
        <div class="card space-y-3">
          <h3 class="font-semibold text-white text-sm">Recent Runs</h3>
          <div v-if="!history.length" class="text-xs text-gray-500">No runs yet.</div>
          <ul v-else class="space-y-2">
            <li v-for="run in history" :key="run.id" class="text-xs">
              <div class="flex items-center justify-between mb-1">
                <span :class="`badge-${run.status}`">{{ run.status }}</span>
                <span class="text-gray-500">
                  {{ run.finished_at ? new Date(run.finished_at).toLocaleString('en-US') : '...' }}
                </span>
              </div>
              <div v-if="run.log_output" class="bg-gray-950 rounded p-2 font-mono text-gray-400 text-xs max-h-20 overflow-y-auto whitespace-pre-wrap">
                {{ run.log_output.slice(-400) }}
              </div>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </div>
</template>

