import service, { requestWithRetry } from './index'

/**
 * 생성시뮬레이션
 * @param {Object} data - { project_id, graph_id?, enable_twitter?, enable_reddit? }
 */
export const createSimulation = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/create', data), 3, 1000)
}

/**
 * 준비시뮬레이션환경（비동기작업）
 * @param {Object} data - { simulation_id, entity_types?, use_llm_for_profiles?, parallel_profile_count?, force_regenerate? }
 */
export const prepareSimulation = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/prepare', data), 3, 1000)
}

/**
 * 조회준비작업진행률
 * @param {Object} data - { task_id?, simulation_id? }
 */
export const getPrepareStatus = (data) => {
  return service.post('/api/simulation/prepare/status', data)
}

/**
 * 가져오기시뮬레이션상태
 * @param {string} simulationId
 */
export const getSimulation = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}`)
}

/**
 * 가져오기시뮬레이션의 Agent Profiles
 * @param {string} simulationId
 * @param {string} platform - 'reddit' | 'twitter'
 */
export const getSimulationProfiles = (simulationId, platform = 'reddit') => {
  return service.get(`/api/simulation/${simulationId}/profiles`, { params: { platform } })
}

/**
 * 실시간가져오기생성 중의 Agent Profiles
 * @param {string} simulationId
 * @param {string} platform - 'reddit' | 'twitter'
 */
export const getSimulationProfilesRealtime = (simulationId, platform = 'reddit') => {
  return service.get(`/api/simulation/${simulationId}/profiles/realtime`, { params: { platform } })
}

/**
 * 가져오기시뮬레이션설정
 * @param {string} simulationId
 */
export const getSimulationConfig = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/config`)
}

/**
 * 실시간가져오기생성 중의시뮬레이션설정
 * @param {string} simulationId
 * @returns {Promise} 돌아가기 설정 정보，포함 메타데이터와 설정 내용
 */
export const getSimulationConfigRealtime = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/config/realtime`)
}

/**
 * 리스트 소 시뮬레이션
 * @param {string} projectId - 선택, 프로젝트 ID로 필터링
 */
export const listSimulations = (projectId) => {
  const params = projectId ? { project_id: projectId } : {}
  return service.get('/api/simulation/list', { params })
}

/**
 * 시작시뮬레이션
 * @param {Object} data - { simulation_id, platform?, max_rounds?, enable_graph_memory_update? }
 */
export const startSimulation = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/start', data), 3, 1000)
}

/**
 * 중지시뮬레이션
 * @param {Object} data - { simulation_id }
 */
export const stopSimulation = (data) => {
  return service.post('/api/simulation/stop', data)
}

/**
 * 가져오기시뮬레이션실행실시간상태
 * @param {string} simulationId
 */
export const getRunStatus = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/run-status`)
}

/**
 * 가져오기 시뮬레이션 실행 상세 상태（포함 최근 액션）
 * @param {string} simulationId
 */
export const getRunStatusDetail = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/run-status/detail`)
}

/**
 * 가져오기 시뮬레이션의 게시물
 * @param {string} simulationId
 * @param {string} platform - 'reddit' | 'twitter'
 * @param {number} limit - 돌아가기수량
 * @param {number} offset - 편차량
 */
export const getSimulationPosts = (simulationId, platform = 'reddit', limit = 50, offset = 0) => {
  return service.get(`/api/simulation/${simulationId}/posts`, {
    params: { platform, limit, offset }
  })
}

/**
 * 가져오기 시뮬레이션 시간선(라운드 차수 합계 기준)
 * @param {string} simulationId
 * @param {number} startRound - 시작 라운드 차수
 * @param {number} endRound - 종료 라운드 차수
 */
export const getSimulationTimeline = (simulationId, startRound = 0, endRound = null) => {
  const params = { start_round: startRound }
  if (endRound !== null) {
    params.end_round = endRound
  }
  return service.get(`/api/simulation/${simulationId}/timeline`, { params })
}

/**
 * 가져오기Agent통계정보
 * @param {string} simulationId
 */
export const getAgentStats = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/agent-stats`)
}

/**
 * 가져오기 시뮬레이션 액션 역사
 * @param {string} simulationId
 * @param {Object} params - { limit, offset, platform, agent_id, round_num }
 */
export const getSimulationActions = (simulationId, params = {}) => {
  return service.get(`/api/simulation/${simulationId}/actions`, { params })
}

/**
 * 닫기 시뮬레이션 환경（우아한 종료）
 * @param {Object} data - { simulation_id, timeout? }
 */
export const closeSimulationEnv = (data) => {
  return service.post('/api/simulation/close-env', data)
}

/**
 * 가져오기시뮬레이션환경상태
 * @param {Object} data - { simulation_id }
 */
export const getEnvStatus = (data) => {
  return service.post('/api/simulation/env-status', data)
}

/**
 * 배치 인터뷰 Agent
 * @param {Object} data - { simulation_id, interviews: [{ agent_id, prompt }] }
 */
export const interviewAgents = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/interview/batch', data), 3, 1000)
}

/**
 * 가져오기 역사 시뮬레이션 목록（포함 프로젝트 상세）
 * 용 홈페이지 역사 프로젝트 표시
 * @param {number} limit - 돌아가기 수량 제한
 */
export const getSimulationHistory = (limit = 20) => {
  return service.get('/api/simulation/history', { params: { limit } })
}

