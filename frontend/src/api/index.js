import axios from 'axios'

// 생성axios인스턴스
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001',
  timeout: 300000, // 5분시간 초과（온톨로지 생성더 오래 걸릴 수 있음시간）
  headers: {
    'Content-Type': 'application/json'
  }
})

// 요청인터셉터
service.interceptors.request.use(
  config => {
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 응답인터셉터（내결함성재시도메커니즘）
service.interceptors.response.use(
  response => {
    const res = response.data
    
    // 반환된 상태 코드가 success가 아닌 경우, 오류 발생
    if (!res.success && res.success !== undefined) {
      console.error('API Error:', res.error || res.message || 'Unknown error')
      return Promise.reject(new Error(res.error || res.message || 'Error'))
    }
    
    return res
  },
  error => {
    console.error('Response error:', error)
    
    // 처리시간 초과
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      console.error('Request timeout')
    }
    
    // 처리네트워크오류
    if (error.message === 'Network Error') {
      console.error('Network error - please check your connection')
    }
    
    return Promise.reject(error)
  }
)

// 포함재시도의요청함수
export const requestWithRetry = async (requestFn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      if (i === maxRetries - 1) throw error
      
      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
}

export default service
