import axios from 'axios'
import { useAuthStore } from '../stores/auth'

const api = axios.create({
  baseURL: '/api',
  timeout: 20000,
})

api.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    let detail = err.response?.data?.detail
    // blob requests (e.g. excerpt download) deliver the JSON error as a Blob
    if (detail === undefined && err.response?.data instanceof Blob) {
      try {
        const text = await err.response.data.text()
        detail = JSON.parse(text)?.detail
      } catch {
        detail = undefined
      }
    }
    if (typeof detail === 'string') {
      err.message = detail
    } else if (Array.isArray(detail)) {
      err.message = detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
    }
    return Promise.reject(err)
  },
)

export async function login(username, password) {
  const { data } = await api.post('/auth/login', { username, password })
  return data
}

export async function getHealth() {
  const { data } = await api.get('/health')
  return data
}

export async function listSamples() {
  const { data } = await api.get('/samples')
  return data
}

export async function listJobs() {
  const { data } = await api.get('/jobs')
  return data
}

export async function getJob(id) {
  const { data } = await api.get(`/jobs/${id}`)
  return data
}

export async function getJobStages(id) {
  const { data } = await api.get(`/jobs/${id}/stages`)
  return data
}

export async function createJob(body) {
  const { data } = await api.post('/jobs', body)
  return data
}

// The excerpt body is generated and signed by the backend; the browser only
// saves the returned bytes, it never assembles the excerpt itself.
export async function downloadJobExcerpt(id) {
  const res = await api.get(`/jobs/${id}/excerpt`, { responseType: 'blob' })
  const disposition = res.headers['content-disposition'] || ''
  let filename = ''
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match) {
    filename = decodeURIComponent(utf8Match[1])
  } else {
    const asciiMatch = disposition.match(/filename="?([^";]+)"?/)
    filename = asciiMatch ? asciiMatch[1] : `qc_excerpt_job${id}.txt`
  }
  const url = window.URL.createObjectURL(
    new Blob([res.data], { type: 'text/plain;charset=utf-8' }),
  )
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}

export default api
