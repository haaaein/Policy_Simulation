import service, { requestWithRetry } from './index'

/**
 * 생성온톨로지（업로드문서와시뮬레이션 요구사항）
 * @param {Object} data - 포함files, simulation_requirement, project_name등
 * @returns {Promise}
 */
export function generateOntology(formData) {
  return requestWithRetry(() => 
    service({
      url: '/api/graph/ontology/generate',
      method: 'post',
      data: formData,
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  )
}

/**
 * 구축그래프
 * @param {Object} data - 포함project_id, graph_name등
 * @returns {Promise}
 */
export function buildGraph(data) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/build',
      method: 'post',
      data
    })
  )
}

/**
 * 조회작업상태
 * @param {String} taskId - 작업ID
 * @returns {Promise}
 */
export function getTaskStatus(taskId) {
  return service({
    url: `/api/graph/task/${taskId}`,
    method: 'get'
  })
}

/**
 * 가져오기그래프데이터
 * @param {String} graphId - 그래프ID
 * @returns {Promise}
 */
export function getGraphData(graphId) {
  return service({
    url: `/api/graph/data/${graphId}`,
    method: 'get'
  })
}

/**
 * 가져오기프로젝트 정보
 * @param {String} projectId - 프로젝트 ID
 * @returns {Promise}
 */
export function getProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'get'
  })
}
