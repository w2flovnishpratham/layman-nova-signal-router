import axios from 'axios'

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '')
const baseURL = configuredBaseUrl ? `${configuredBaseUrl}/api` : '/api'

const api = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
})

export default api
