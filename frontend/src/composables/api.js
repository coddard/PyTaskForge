import axios from 'axios'
import { useAuthStore } from '@/stores/auth'

const api = axios.create({ baseURL: '/api' })

// Attach the JWT token to every request
api.interceptors.request.use(cfg => {
  const auth = useAuthStore()
  if (auth.token) cfg.headers.Authorization = `Bearer ${auth.token}`
  return cfg
})

// If a 401 is returned, end the session
api.interceptors.response.use(
  res => res,
  err => {
    if (err.response?.status === 401) {
      const auth = useAuthStore()
      auth.logout()
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

// ── Auth ──────────────────────────────────────────────────────────────────────
export const login  = (username, password) =>
  api.post('/auth/token', new URLSearchParams({ username, password }),
    { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } })
export const getMe  = () => api.get('/auth/me')

// ── Jobs ──────────────────────────────────────────────────────────────────────
export const fetchJobs    = ()        => api.get('/jobs')
export const fetchJob     = id        => api.get(`/jobs/${id}`)
export const createJob    = body      => api.post('/jobs', body)
export const updateJob    = (id, body)=> api.put(`/jobs/${id}`, body)
export const deleteJob    = id        => api.delete(`/jobs/${id}`)
export const runJobNow    = id        => api.post(`/jobs/${id}/run`)
export const fetchHistory = (id, n=20)=> api.get(`/jobs/${id}/history?limit=${n}`)


