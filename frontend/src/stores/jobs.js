import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchJobs, deleteJob, runJobNow } from '@/composables/api'

export const useJobsStore = defineStore('jobs', () => {
  const jobs    = ref([])
  const loading = ref(false)
  const error   = ref(null)

  async function load() {
    loading.value = true; error.value = null
    try { jobs.value = (await fetchJobs()).data }
    catch (e) { error.value = e.response?.data?.detail || e.message }
    finally { loading.value = false }
  }

  async function remove(id) {
    await deleteJob(id)
    jobs.value = jobs.value.filter(j => j.id !== id)
  }

  async function triggerNow(id) { await runJobNow(id) }

  return { jobs, loading, error, load, remove, triggerNow }
})

