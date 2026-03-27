<template>
  <div class="main-view">
    <!-- Header -->
    <header class="app-header">
      <div class="header-left">
        <div class="brand" @click="router.push('/')">PolicySim</div>
      </div>
      
      <div class="header-center">
        <div class="view-switcher">
          <button 
            v-for="mode in ['graph', 'split', 'workbench']" 
            :key="mode"
            class="switch-btn"
            :class="{ active: viewMode === mode }"
            @click="viewMode = mode"
          >
            {{ { graph: '그래프', split: '분할 화면', workbench: '작업대' }[mode] }}
          </button>
        </div>
      </div>

      <div class="header-right">
        <div class="workflow-step">
          <span class="step-num">Step 2/5</span>
          <span class="step-name">환경 설정</span>
        </div>
        <div class="step-divider"></div>
        <span class="status-indicator" :class="statusClass">
          <span class="dot"></span>
          {{ statusText }}
        </span>
      </div>
    </header>

    <!-- Main Content Area -->
    <main class="content-area">
      <!-- Left Panel: Graph -->
      <div class="panel-wrapper left" :style="leftPanelStyle">
        <GraphPanel 
          :graphData="graphData"
          :loading="graphLoading"
          :currentPhase="2"
          @refresh="refreshGraph"
          @toggle-maximize="toggleMaximize('graph')"
        />
      </div>

      <!-- Right Panel: Step2 환경 설정 -->
      <div class="panel-wrapper right" :style="rightPanelStyle">
        <Step2EnvSetup
          :simulationId="currentSimulationId"
          :projectData="projectData"
          :graphData="graphData"
          :systemLogs="systemLogs"
          @go-back="handleGoBack"
          @next-step="handleNextStep"
          @add-log="addLog"
          @update-status="updateStatus"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GraphPanel from '../components/GraphPanel.vue'
import Step2EnvSetup from '../components/Step2EnvSetup.vue'
import { getProject, getGraphData } from '../api/graph'
import { getSimulation, stopSimulation, getEnvStatus, closeSimulationEnv } from '../api/simulation'

const route = useRoute()
const router = useRouter()

// Props
const props = defineProps({
  simulationId: String
})

// Layout State
const viewMode = ref('split')

// Data State
const currentSimulationId = ref(route.params.simulationId)
const projectData = ref(null)
const graphData = ref(null)
const graphLoading = ref(false)
const systemLogs = ref([])
const currentStatus = ref('processing') // processing | completed | error

// --- Computed Layout Styles ---
const leftPanelStyle = computed(() => {
  if (viewMode.value === 'graph') return { width: '100%', opacity: 1, transform: 'translateX(0)' }
  if (viewMode.value === 'workbench') return { width: '0%', opacity: 0, transform: 'translateX(-20px)' }
  return { width: '50%', opacity: 1, transform: 'translateX(0)' }
})

const rightPanelStyle = computed(() => {
  if (viewMode.value === 'workbench') return { width: '100%', opacity: 1, transform: 'translateX(0)' }
  if (viewMode.value === 'graph') return { width: '0%', opacity: 0, transform: 'translateX(20px)' }
  return { width: '50%', opacity: 1, transform: 'translateX(0)' }
})

// --- Status Computed ---
const statusClass = computed(() => {
  return currentStatus.value
})

const statusText = computed(() => {
  if (currentStatus.value === 'error') return 'Error'
  if (currentStatus.value === 'completed') return 'Ready'
  return 'Preparing'
})

// --- Helpers ---
const addLog = (msg) => {
  const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) + '.' + new Date().getMilliseconds().toString().padStart(3, '0')
  systemLogs.value.push({ time, msg })
  if (systemLogs.value.length > 100) {
    systemLogs.value.shift()
  }
}

const updateStatus = (status) => {
  currentStatus.value = status
}

// --- Layout Methods ---
const toggleMaximize = (target) => {
  if (viewMode.value === target) {
    viewMode.value = 'split'
  } else {
    viewMode.value = target
  }
}

const handleGoBack = () => {
  // 돌아가기 process 페이지
  if (projectData.value?.project_id) {
    router.push({ name: 'Process', params: { projectId: projectData.value.project_id } })
  } else {
    router.push('/')
  }
}

const handleNextStep = (params = {}) => {
  addLog('진입 Step 3: 시뮬레이션 시작')
  
  // 기록시뮬레이션라운드 수설정
  if (params.maxRounds) {
    addLog(`사용자 정의시뮬레이션라운드 수: ${params.maxRounds} 라운드`)
  } else {
    addLog('사용자동설정의시뮬레이션라운드 수')
  }
  
  // 구축라우팅파라미터
  const routeParams = {
    name: 'SimulationRun',
    params: { simulationId: currentSimulationId.value }
  }
  
  // 만약 사용자 정의 라운드 수，query 파라미터 전달  
  if (params.maxRounds) {
    routeParams.query = { maxRounds: params.maxRounds }
  }
  
  // 이동 Step 3 페이지
  router.push(routeParams)
}

// --- Data Logic ---

/**
 * 체크 및 닫기 진행 중: 실행의 시뮬레이션  
 * 사용자부터 Step 3 돌아가기 Step 2 시，기본값 사용자 종료 시뮬레이션  
 */
const checkAndStopRunningSimulation = async () => {
  if (!currentSimulationId.value) return
  
  try {
    // 먼저 체크 시뮬레이션 환경이 살아 있는지  
    const envStatusRes = await getEnvStatus({ simulation_id: currentSimulationId.value })
    
    if (envStatusRes.success && envStatusRes.data?.env_alive) {
      addLog('감지시뮬레이션환경진행 중: 실행，진행 중: 닫기...')
      
      // 시도 우아하게 닫기 시뮬레이션 환경  
      try {
        const closeRes = await closeSimulationEnv({ 
          simulation_id: currentSimulationId.value,
          timeout: 10  // 10초시간 초과
        })
        
        if (closeRes.success) {
          addLog('✓ 시뮬레이션환경이미닫기')
        } else {
          addLog(`닫기시뮬레이션환경실패: ${closeRes.error || '알 수 없음오류'}`)
          // 만약 우아하게 닫기 실패，시도 강제 중지  
          await forceStopSimulation()
        }
      } catch (closeErr) {
        addLog(`닫기시뮬레이션환경예외: ${closeErr.message}`)
        // 만약 우아하게 닫기 예외，시도 강제 중지  
        await forceStopSimulation()
      }
    } else {
      // 환경이 실행되지 않지만 프로세스가 아직，체크 시뮬레이션 상태  
      const simRes = await getSimulation(currentSimulationId.value)
      if (simRes.success && simRes.data?.status === 'running') {
        addLog('감지시뮬레이션상태로실행중，진행 중: 중지...')
        await forceStopSimulation()
      }
    }
  } catch (err) {
    // 체크 환경 상태 실패가 후속 프로세스에 영향을 미치지 않음  
    console.warn('체크 시뮬레이션 상태 실패:', err)  
  }
}

/**
 * 강제 중지 시뮬레이션  
 */
const forceStopSimulation = async () => {
  try {
    const stopRes = await stopSimulation({ simulation_id: currentSimulationId.value })
    if (stopRes.success) {
      addLog('✓ 시뮬레이션이 이미 강제 중지됨')  
    } else {
      addLog(`강제 중지 시뮬레이션 실패: ${stopRes.error || '알 수 없는 오류'}`)  
    }
  } catch (err) {
    addLog(`강제 중지 시뮬레이션 예외: ${err.message}`)  
  }
}

const loadSimulationData = async () => {
  try {
    addLog(`로드시뮬레이션데이터: ${currentSimulationId.value}`)
    
    // 가져오기 simulation 정보
    const simRes = await getSimulation(currentSimulationId.value)
    if (simRes.success && simRes.data) {
      const simData = simRes.data
      
      // 가져오기 project 정보
      if (simData.project_id) {
        const projRes = await getProject(simData.project_id)
        if (projRes.success && projRes.data) {
          projectData.value = projRes.data
          addLog(`프로젝트로드 성공: ${projRes.data.project_id}`)
          
          // 가져오기 graph 데이터
          if (projRes.data.graph_id) {
            await loadGraph(projRes.data.graph_id)
          }
        }
      }
    } else {
      addLog(`로드시뮬레이션데이터실패: ${simRes.error || '알 수 없음오류'}`)
    }
  } catch (err) {
    addLog(`로드예외: ${err.message}`)
  }
}

const loadGraph = async (graphId) => {
  graphLoading.value = true
  try {
    const res = await getGraphData(graphId)
    if (res.success) {
      graphData.value = res.data
      addLog('그래프데이터로드 성공')
    }
  } catch (err) {
    addLog(`그래프로드 실패: ${err.message}`)
  } finally {
    graphLoading.value = false
  }
}

const refreshGraph = () => {
  if (projectData.value?.graph_id) {
    loadGraph(projectData.value.graph_id)
  }
}

onMounted(async () => {
  addLog('SimulationView 초기화')
  
  // 체크 및 닫기 진행 중: 실행의 시뮬레이션（사용자부터 Step 3 돌아가기 시）
  await checkAndStopRunningSimulation()
  
  // 로드시뮬레이션데이터
  loadSimulationData()
})
</script>

<style scoped>
.main-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #FFF;
  overflow: hidden;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}

/* Header */
.app-header {
  height: 60px;
  border-bottom: 1px solid #EAEAEA;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: #FFF;
  z-index: 100;
  position: relative;
}

.brand {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  font-size: 18px;
  letter-spacing: 1px;
  cursor: pointer;
}

.header-center {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
}

.view-switcher {
  display: flex;
  background: #F5F5F5;
  padding: 4px;
  border-radius: 6px;
  gap: 4px;
}

.switch-btn {
  border: none;
  background: transparent;
  padding: 6px 16px;
  font-size: 12px;
  font-weight: 600;
  color: #666;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.switch-btn.active {
  background: #FFF;
  color: #000;
  box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.workflow-step {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
}

.step-num {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  color: #999;
}

.step-name {
  font-weight: 700;
  color: #000;
}

.step-divider {
  width: 1px;
  height: 14px;
  background-color: #E0E0E0;
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #666;
  font-weight: 500;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #CCC;
}

.status-indicator.processing .dot { background: #FF5722; animation: pulse 1s infinite; }
.status-indicator.completed .dot { background: #4CAF50; }
.status-indicator.error .dot { background: #F44336; }

@keyframes pulse { 50% { opacity: 0.5; } }

/* Content */
.content-area {
  flex: 1;
  display: flex;
  position: relative;
  overflow: hidden;
}

.panel-wrapper {
  height: 100%;
  overflow: hidden;
  transition: width 0.4s cubic-bezier(0.25, 0.8, 0.25, 1), opacity 0.3s ease, transform 0.3s ease;
  will-change: width, opacity, transform;
}

.panel-wrapper.left {
  border-right: 1px solid #EAEAEA;
}
</style>

