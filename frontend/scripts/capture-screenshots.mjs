import fs from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const frontendDir = path.resolve(__dirname, '..')
const repoRoot = path.resolve(frontendDir, '..')
const outputDir = path.resolve(repoRoot, 'docs', 'screenshots', 'runtime')

const UI_BASE = process.env.PTF_SCREENSHOT_UI_BASE_URL ?? 'http://127.0.0.1:5173'
const API_BASE = process.env.PTF_SCREENSHOT_API_BASE_URL ?? 'http://127.0.0.1:8000/api'
const USERNAME = process.env.PTF_SCREENSHOT_USERNAME ?? 'admin'
const PASSWORD = process.env.PTF_SCREENSHOT_PASSWORD ?? 'changeme'
const DEV_MODE = (process.env.PTF_SCREENSHOT_DEV_MODE ?? 'true').toLowerCase() === 'true'
const VIEWPORT = { width: 1440, height: 1024 }

async function sleep(ms) {
  await new Promise(resolve => setTimeout(resolve, ms))
}

async function waitForHttp(url, label, attempts = 60, delayMs = 1000) {
  let lastError = null
  for (let i = 0; i < attempts; i += 1) {
    try {
      const response = await fetch(url)
      if (response.ok) return
      lastError = new Error(`${label} returned HTTP ${response.status}`)
    } catch (error) {
      lastError = error
    }
    await sleep(delayMs)
  }
  throw new Error(`Timed out waiting for ${label} at ${url}: ${lastError?.message ?? 'unknown error'}`)
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, options)
  if (!response.ok) {
    const text = await response.text()
    throw new Error(`Request failed (${response.status}) for ${url}: ${text}`)
  }
  return response.json()
}

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function getToken() {
  const form = new URLSearchParams({ username: USERNAME, password: PASSWORD })
  const body = await apiJson(`${API_BASE}/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
  })

  if (!body.access_token) {
    throw new Error('No access_token returned from /api/auth/token')
  }
  return body.access_token
}

async function createJob(token, body) {
  return apiJson(`${API_BASE}/jobs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(token),
    },
    body: JSON.stringify(body),
  })
}

async function triggerJob(token, jobId) {
  return apiJson(`${API_BASE}/jobs/${jobId}/run`, {
    method: 'POST',
    headers: authHeaders(token),
  })
}

async function fetchHistory(token, jobId) {
  return apiJson(`${API_BASE}/jobs/${jobId}/history?limit=5`, {
    headers: authHeaders(token),
  })
}

async function seedData(token) {
  const suffix = Date.now()

  const dashboardJob = await createJob(token, {
    name: `Screenshot Dashboard Job ${suffix}`,
    description: 'Seeded automatically for dashboard capture',
    script_path: 'hello_world.py',
    execution_mode: 'venv',
    trigger_type: 'interval',
    trigger_config: { seconds: 3600 },
    requirements: null,
    env_vars: { SCREENSHOT_MODE: 'dashboard' },
    timeout_seconds: 30,
  })

  const logJob = await createJob(token, {
    name: `Screenshot Logs Job ${suffix}`,
    description: 'Seeded automatically for log capture',
    script_path: 'hello_world.py',
    execution_mode: 'venv',
    trigger_type: 'interval',
    trigger_config: { seconds: 3600 },
    requirements: null,
    env_vars: { SCREENSHOT_MODE: 'logs' },
    timeout_seconds: 30,
  })

  await triggerJob(token, logJob.id)

  for (let attempt = 0; attempt < 30; attempt += 1) {
    const history = await fetchHistory(token, logJob.id)
    if (Array.isArray(history) && history.length > 0) {
      return { dashboardJob, logJob }
    }
    await sleep(1000)
  }

  return { dashboardJob, logJob }
}

async function prepareAuthenticatedPage(browser, token) {
  const page = await browser.newPage({ viewport: VIEWPORT })
  await page.addInitScript(value => {
    window.localStorage.setItem('ptf_token', value)
  }, token)
  return page
}

async function capture() {
  await fs.mkdir(outputDir, { recursive: true })

  await waitForHttp(`${API_BASE}/health`, 'backend API')
  await waitForHttp(`${UI_BASE}/login`, 'frontend UI')

  const token = await getToken()
  const { logJob } = await seedData(token)

  const browser = await chromium.launch({ headless: true })

  try {
    const loginPage = await browser.newPage({ viewport: VIEWPORT })
    await loginPage.goto(`${UI_BASE}/login`, { waitUntil: 'domcontentloaded' })
    await loginPage.screenshot({
      path: path.join(outputDir, 'login-runtime.png'),
      fullPage: true,
    })

    const dashboardPage = await prepareAuthenticatedPage(browser, token)
    await dashboardPage.goto(`${UI_BASE}/`, { waitUntil: 'networkidle' })
    await dashboardPage.screenshot({
      path: path.join(outputDir, 'dashboard-runtime.png'),
      fullPage: true,
    })

    const formPage = await prepareAuthenticatedPage(browser, token)
    await formPage.goto(`${UI_BASE}/jobs/new`, { waitUntil: 'networkidle' })
    await formPage.screenshot({
      path: path.join(outputDir, 'job-form-runtime.png'),
      fullPage: true,
    })

    const logsPage = await prepareAuthenticatedPage(browser, token)
    await logsPage.goto(`${UI_BASE}/jobs/${logJob.id}/logs`, { waitUntil: 'domcontentloaded' })
    await logsPage.waitForTimeout(2500)
    await logsPage.screenshot({
      path: path.join(outputDir, 'job-logs-runtime.png'),
      fullPage: true,
    })
  } finally {
    await browser.close()
  }

  console.log(`Screenshots saved to ${outputDir}`)
  console.log(`Capture mode: ${DEV_MODE ? 'dev-mode friendly' : 'production-like auth flow'}`)
}

capture().catch(error => {
  console.error(error)
  process.exitCode = 1
})

