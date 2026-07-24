<template>
  <div class="app-container primary-page bet-records-page">
    <header class="top-header">
      <span class="header-side-spacer"></span>
      <span class="header-title">投注记录</span>
      <AccountButton />
    </header>

    <main class="records-content">
      <section v-if="authState.initialized && !authState.user" class="account-gate">
        <div class="account-gate-icon">▤</div>
        <h2>登录后查看投注记录</h2>
        <p>每个账号的投注方案独立保存，其他用户无法查看。</p>
        <button type="button" @click="openAuth('login')">登录 / 注册</button>
      </section>

      <template v-else-if="authState.user">
        <section class="records-summary">
          <div>
            <span>累计方案</span>
            <strong>{{ stats.total_bets || 0 }}</strong>
          </div>
          <div>
            <span>累计投入</span>
            <strong>{{ money(stats.total_stake) }} 元</strong>
          </div>
          <div>
            <span>净盈亏</span>
            <strong>{{ signedMoney(stats.net_profit) }} 元</strong>
          </div>
        </section>

        <div class="records-toolbar">
          <div>
            <h2>我的方案</h2>
            <p>仅当前账号可见</p>
          </div>
          <div class="records-toolbar-actions">
            <button
              type="button"
              class="ticket-upload-trigger"
              :disabled="loading || ticketUploadLoading"
              @click="openTicketUpload"
            >上传票据</button>
            <button type="button" :disabled="loading" @click="fetchRecords">{{ loading ? '刷新中…' : '刷新' }}</button>
          </div>
        </div>
        <p v-if="ticketNotice" class="ticket-upload-notice">{{ ticketNotice }}</p>
        <input
          ref="ticketFileInput"
          class="ticket-file-input"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          @change="handleTicketFileUpload"
        />

        <div class="records-filter" aria-label="方案筛选">
          <button
            type="button"
            :class="{ active: recordFilter === 'all' }"
            @click="setRecordFilter('all')"
          >全部方案</button>
          <button
            type="button"
            :class="{ active: recordFilter === 'won' }"
            @click="setRecordFilter('won')"
          >盈利单</button>
        </div>

        <div v-if="loading && records.length === 0" class="page-loading">正在加载投注记录…</div>
        <section v-else-if="records.length === 0" class="records-empty">
          <div>{{ recordFilter === 'won' ? '暂无盈利方案' : '暂无投注记录' }}</div>
          <router-link v-if="recordFilter === 'all'" to="/calculator">去计算器选择比赛</router-link>
        </section>

        <section v-else class="records-list">
          <article
            v-for="record in records"
            :key="record.id"
            class="record-card"
            :class="'record-card--' + record.status"
            @click="selectedRecord = record"
          >
            <div class="record-card-head">
              <span class="record-status" :class="'record-status--' + record.status">
                {{ statusText(record.status) }}
              </span>
              <time>{{ formatTime(record.created_at) }}</time>
              <button type="button" class="record-delete" aria-label="删除" @click.stop="removeRecord(record.id)">×</button>
            </div>
            <div class="record-description">{{ recordDescription(record) }}</div>
            <div class="record-meta">
              <span>{{ record.match_count }}场</span>
              <span>{{ record.option_count }}个选项</span>
              <span>{{ record.notes }}注</span>
              <span
                v-if="record.status === 'pending' && record.result_progress?.completed"
                class="record-meta-result"
              >
                已有赛果 {{ record.result_progress.completed }}/{{ record.result_progress.total }}
              </span>
            </div>
            <div class="record-money" :class="{ 'record-money--settled': record.status !== 'pending' }">
              <span>投入 <strong>{{ money(record.stake) }}元</strong></span>
              <span v-if="record.status === 'pending'">理论最高 <strong>{{ money(record.max_bonus) }}元</strong></span>
              <template v-else>
                <span>实际返还 <strong>{{ money(record.actual_return) }}元</strong></span>
                <span>
                  {{ profitLabel(record.profit) }}
                  <strong :class="profitClass(record.profit)">{{ signedMoney(record.profit) }}元</strong>
                </span>
              </template>
            </div>
          </article>
        </section>
      </template>

      <div v-else class="page-loading">正在确认登录状态…</div>
    </main>

        <div v-if="selectedRecord" class="record-detail-overlay" @click.self="selectedRecord = null">
      <section class="record-detail-modal">
        <header>
          <div>
            <h2>投注方案</h2>
            <p>{{ formatTime(selectedRecord.created_at) }}</p>
          </div>
          <div class="record-detail-actions">
            <button
              type="button"
              class="record-share-button"
              :disabled="preparingRecordShare || sharingRecord || !recordShareBlob"
              :aria-label="preparingRecordShare ? '正在生成分享图片' : '分享投注方案'"
              @click="shareRecord"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 16V3m0 0L7.5 7.5M12 3l4.5 4.5M5 11v8h14v-8" />
              </svg>
              <span>{{ preparingRecordShare ? '生成中' : sharingRecord ? '分享中' : '分享' }}</span>
            </button>
            <button
              type="button"
              class="record-detail-close"
              aria-label="关闭投注方案"
              @click="selectedRecord = null"
            >×</button>
          </div>
        </header>
        <div class="record-ticket-scroll">
          <article ref="recordTicketRef" class="record-ticket">
            <div class="record-ticket-brand">
              <span>中国体育彩票</span>
              <strong>竞彩足球</strong>
              <small>竞彩足球胜平负 · 让球胜平负</small>
            </div>

            <div class="record-ticket-id">
              <span>方案号 {{ ticketNumber(selectedRecord) }}</span>
              <span>{{ formatTime(selectedRecord.created_at) }}</span>
            </div>

            <div class="record-ticket-summary">
              <strong>{{ ticketPassTitle(selectedRecord) }}</strong>
              <b>{{ selectedRecord.multiplier }}倍</b>
              <strong>合计 {{ money(selectedRecord.stake) }}元</strong>
            </div>
            <div class="record-ticket-status">
              <span :class="'record-status--' + selectedRecord.status">
                {{ statusText(selectedRecord.status) }}
              </span>
            </div>

            <div class="record-ticket-matches">
              <section
                v-for="(group, index) in groupedItems(selectedRecord)"
                :key="group.matchId"
                class="record-ticket-match"
              >
                <div class="record-ticket-match-head">
                  <strong>第{{ index + 1 }}场</strong>
                  <span>{{ group.items[0].match_num || '比赛' }}</span>
                  <em v-if="handicapText(group)">{{ handicapText(group) }}</em>
                  <small v-if="group.isPartial" class="record-ticket-result-ready">已有赛果</small>
                  <b v-if="group.fullScore">{{ group.fullScore }}</b>
                  <b v-else-if="group.isVoid" class="record-ticket-void">退</b>
                </div>
                <div class="record-ticket-teams">
                  <span>主队：{{ group.items[0].home_team || '-' }}</span>
                  <i>VS</i>
                  <span>客队：{{ group.items[0].away_team || '-' }}</span>
                </div>
                <div class="record-ticket-picks">
                  <span
                    v-for="item in group.items"
                    :key="item.pool + item.opt"
                    :class="item.result ? 'record-ticket-pick--' + item.result : ''"
                  >
                    <b v-if="item.result">{{ resultIcon(item.result) }}</b>
                    {{ ticketPickText(item) }}
                  </span>
                </div>
                <div v-if="group.fullScore || group.resultStatus" class="record-ticket-result">
                  <template v-if="group.fullScore">
                    全场 {{ group.fullScore }}<span v-if="group.halfScore"> · 半场 {{ group.halfScore }}</span>
                  </template>
                  <template v-else>{{ group.resultStatus }}</template>
                </div>
              </section>
            </div>

            <p class="record-ticket-notice">（选项固定奖金为每1元投注对应的奖金金额）</p>

            <div class="record-ticket-award">
              <span>本票最高可能固定奖金</span>
              <strong>{{ money(selectedRecord.max_bonus) }}元</strong>
            </div>

            <div class="record-ticket-notes">
              单倍注数：{{ passNotesText(selectedRecord) }}；共{{ selectedRecord.notes }}注
            </div>

            <div v-if="selectedRecord.status !== 'pending'" class="record-ticket-settlement">
              <span>实际返还 <strong>{{ money(selectedRecord.actual_return) }}元</strong></span>
              <span>
                {{ profitLabel(selectedRecord.profit) }}
                <strong :class="profitClass(selectedRecord.profit)">{{ signedMoney(selectedRecord.profit) }}元</strong>
              </span>
            </div>

            <div class="record-ticket-barcode" aria-hidden="true"></div>
            <small class="record-ticket-disclaimer">模拟记录，仅用于个人投注统计与赛后复盘</small>
          </article>
        </div>
        <div v-if="recordShareNotice" class="record-share-notice">{{ recordShareNotice }}</div>
      </section>
    </div>

    <div v-if="ticketImportModal" class="ticket-import-overlay" @click.self="closeTicketImportModal">
      <section class="ticket-import-modal">
        <header>
          <h2>识别票据</h2>
          <button type="button" class="ticket-import-close" @click="closeTicketImportModal">×</button>
        </header>
        <div class="ticket-import-body">
          <div v-if="ticketUploadLoading" class="ticket-import-loading">正在识别票据，请稍候…</div>
          <template v-else>
            <p class="ticket-import-tip">
              请核对结果后提交入库。你可以手动修改 JSON（需保留字段结构）。
            </p>
            <textarea
              v-model="ticketImportJson"
              rows="16"
              class="ticket-import-textarea"
            />
            <div v-if="ticketImportWarnings.length" class="ticket-import-warnings">
              <strong>识别提示</strong>
              <ul>
                <li v-for="item in ticketImportWarnings" :key="item">{{ item }}</li>
              </ul>
            </div>
            <div v-if="ticketImportError" class="ticket-import-error">{{ ticketImportError }}</div>
          </template>
        </div>
        <footer class="ticket-import-actions">
          <button type="button" @click="closeTicketImportModal">取消</button>
          <button
            type="button"
            :disabled="ticketUploadLoading || ticketImportLoading || !ticketImportJson"
            @click="confirmTicketImport"
          >
            {{ ticketImportLoading ? '入库中…' : '确认入库' }}
          </button>
        </footer>
      </section>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, reactive, ref, watch } from 'vue'
import AccountButton from '../components/AccountButton.vue'
import { apiRequest, authState, loadCurrentUser, openAuth } from '../auth'
import { calculatePassNotes } from '../utils/betMath'

const records = ref([])
const stats = reactive({ total_bets: 0, total_stake: 0, total_notes: 0, net_profit: 0 })
const loading = ref(false)
const selectedRecord = ref(null)
const recordFilter = ref('all')
const recordTicketRef = ref(null)
const recordShareBlob = ref(null)
const preparingRecordShare = ref(false)
const sharingRecord = ref(false)
const recordShareNotice = ref('')
const ticketFileInput = ref(null)
const ticketImportModal = ref(false)
const ticketImportJson = ref('')
const ticketUploadLoading = ref(false)
const ticketImportLoading = ref(false)
const ticketImportError = ref('')
const ticketImportWarnings = ref([])
const ticketNotice = ref('')
const ticketNoticeTimer = ref(null)
let recordShareToken = 0
let recordShareNoticeTimer = null

const money = (value) => Number(value || 0).toFixed(2)
const signedMoney = (value) => {
  const number = Number(value || 0)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}`
}
const statusText = (status) => ({
  pending: '待结算',
  won: '已盈利',
  lost: '已结算',
  draw: '已返还'
}[status] || '待结算')
const profitLabel = (value) => Number(value || 0) > 0 ? '净盈利' : Number(value || 0) < 0 ? '净亏损' : '盈亏'
const profitClass = (value) => Number(value || 0) > 0 ? 'profit-positive' : Number(value || 0) < 0 ? 'profit-negative' : 'profit-zero'
const resultIcon = (result) => ({ win: '✓', lose: '×', void: '退' }[result] || '')
const ticketOdds = value => Number(value || 0).toFixed(3)

const ticketNumber = record => String(record?.id || '')
  .replace(/-/g, '')
  .slice(0, 20)
  .toUpperCase() || 'MYGOAL'

const passCountText = record => {
  const passes = (record?.pass_counts || []).map(Number).filter(Number.isFinite)
  if (passes.length === 0) return ''
  if (passes.length === 1 && passes[0] === 1) return '单关'
  if (passes.includes(1)) {
    return passes.map(count => count === 1 ? '单关' : `${count}关`).join('，')
  }
  return `${passes.join('，')}关`
}

const recordDescription = record =>
  `${record?.match_count || 0}场 · ${passCountText(record)} · ${record?.multiplier || 1}倍`

const ticketPassTitle = record =>
  `${record?.match_count || 0}场-${passCountText(record)}`

const passNotesText = record => (record?.pass_counts || [])
  .map(count => {
    const size = Number(count)
    const label = size === 1 ? '单关' : `${size}串1`
    return `${label}×${calculatePassNotes(record?.selected_items, size)}注`
  })
  .join('，')

const handicapText = group => {
  const item = group?.items?.find(entry => entry.pool === 'hhad')
  if (!item) return ''
  const value = Number(item.handicap)
  if (!Number.isFinite(value) || value === 0) return '让球0'
  return value < 0 ? `主队让${Math.abs(value)}球` : `主队受让${value}球`
}

const ticketPickLabel = item => {
  if (item.pool === 'score' || item.pool === 'goals') {
    return String(item.opt || item.label || '').replace(/球$/, '')
  }
  return item.label || item.opt
}

const ticketPickText = item => {
  const label = ticketPickLabel(item)
  const odds = ticketOdds(item.odd)
  return item.pool === 'score' || item.pool === 'goals'
    ? `${label}(${odds})`
    : `${label}@${odds}`
}

const showRecordShareNotice = (message) => {
  recordShareNotice.value = message
  if (recordShareNoticeTimer) window.clearTimeout(recordShareNoticeTimer)
  recordShareNoticeTimer = window.setTimeout(() => {
    recordShareNotice.value = ''
  }, 2800)
}

const showTicketNotice = (message) => {
  ticketNotice.value = message
  if (ticketNoticeTimer.value) window.clearTimeout(ticketNoticeTimer.value)
  ticketNoticeTimer.value = window.setTimeout(() => {
    ticketNotice.value = ''
  }, 3000)
}

const closeTicketImportModal = () => {
  ticketImportModal.value = false
  ticketImportJson.value = ''
  ticketImportWarnings.value = []
  ticketImportError.value = ''
  ticketUploadLoading.value = false
  ticketImportLoading.value = false
}

const openTicketUpload = () => {
  if (ticketUploadLoading.value) return
  if (ticketFileInput.value) ticketFileInput.value.value = ''
  ticketImportError.value = ''
  ticketFileInput.value?.click()
}

const parseTicketImportResponse = (responseText) => {
  try {
    return JSON.parse(responseText)
  } catch {
    throw new Error('票据识别结果不是合法 JSON，请检查文本格式')
  }
}

const submitTicketRecognize = async (file) => {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch('/api/user/bets/recognize-ticket', {
    method: 'POST',
    credentials: 'same-origin',
    body: formData,
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok || payload.success === false) {
    throw new Error(payload.message || '识别失败')
  }
  return payload
}

const handleTicketFileUpload = async (event) => {
  const files = event?.target?.files
  const file = files && files.length > 0 ? files[0] : null
  if (!file) return
  if (ticketUploadLoading.value) return
  ticketUploadLoading.value = true
  ticketImportError.value = ''
  ticketImportWarnings.value = []
  ticketNotice.value = ''

  try {
    const result = await submitTicketRecognize(file)
    ticketImportJson.value = JSON.stringify(result.data || {}, null, 2)
    ticketImportWarnings.value = Array.isArray(result.data?.warnings) ? result.data.warnings : []
    ticketImportModal.value = true
  } catch (error) {
    showTicketNotice(error.message || '票据识别失败')
  } finally {
    ticketUploadLoading.value = false
    if (ticketFileInput.value) ticketFileInput.value.value = ''
  }
}

const confirmTicketImport = async () => {
  if (ticketImportLoading.value) return
  let payload
  try {
    payload = parseTicketImportResponse(ticketImportJson.value)
  } catch (error) {
    ticketImportError.value = error.message
    return
  }

  ticketImportError.value = ''
  ticketImportLoading.value = true
  try {
    const result = await apiRequest('/api/user/bets/import-ticket', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    if (!result?.success || !result?.data) {
      throw new Error('保存失败')
    }
    await fetchRecords()
    closeTicketImportModal()
    showTicketNotice('票据已入库')
  } catch (error) {
    ticketImportError.value = error.message || '保存失败'
  } finally {
    ticketImportLoading.value = false
  }
}

const imageBlobFromCanvas = canvas => new Promise((resolve, reject) => {
  canvas.toBlob(
    blob => blob ? resolve(blob) : reject(new Error('分享图片生成失败')),
    'image/png'
  )
})

const prepareRecordShare = async (record, token) => {
  preparingRecordShare.value = true
  try {
    await nextTick()
    if (token !== recordShareToken || !recordTicketRef.value) return

    const { default: html2canvas } = await import('html2canvas')
    const canvas = await html2canvas(recordTicketRef.value, {
      backgroundColor: '#f8f3eb',
      scale: 2,
      useCORS: true,
      logging: false
    })
    const blob = await imageBlobFromCanvas(canvas)
    if (token === recordShareToken && selectedRecord.value?.id === record?.id) {
      recordShareBlob.value = blob
    }
  } catch (error) {
    if (token === recordShareToken) {
      showRecordShareNotice(error?.message || '分享图片生成失败')
    }
  } finally {
    if (token === recordShareToken) preparingRecordShare.value = false
  }
}

const downloadRecordImage = (blob, fileName) => {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const shareRecord = () => {
  if (!recordShareBlob.value || sharingRecord.value || !selectedRecord.value) return

  const fileName = `mygoal-投注方案-${ticketNumber(selectedRecord.value)}.png`
  const shareFile = typeof File === 'function'
    ? new File([recordShareBlob.value], fileName, { type: 'image/png' })
    : null
  const canShareFile = !!(
    shareFile &&
    typeof navigator.share === 'function' &&
    typeof navigator.canShare === 'function' &&
    navigator.canShare({ files: [shareFile] })
  )

  if (!canShareFile) {
    downloadRecordImage(recordShareBlob.value, fileName)
    showRecordShareNotice('当前浏览器不支持直接分享，图片已下载')
    return
  }

  sharingRecord.value = true
  navigator.share({
    files: [shareFile],
    title: '竞彩足球投注方案',
    text: `${ticketPassTitle(selectedRecord.value)} · 合计${money(selectedRecord.value.stake)}元`
  }).then(() => {
    showRecordShareNotice('分享操作已完成')
  }).catch(error => {
    if (error?.name !== 'AbortError') {
      downloadRecordImage(recordShareBlob.value, fileName)
      showRecordShareNotice('无法直接分享，图片已下载')
    }
  }).finally(() => {
    sharingRecord.value = false
  })
}

const formatTime = (value) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 16).replace('T', ' ')
  return date.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const groupedItems = (record) => {
  const map = new Map()
  const settledMatches = new Map(
    (record.settlement?.matches || []).map(match => [String(match.match_id), match])
  )
  const partialMatches = new Map(
    (record.partial_results || []).map(match => [String(match.match_id), match])
  )
  ;(record.selected_items || []).forEach(item => {
    const matchId = String(item.match_id)
    if (!map.has(matchId)) {
      const settledMatch = settledMatches.get(matchId)
      const partialMatch = partialMatches.get(matchId)
      const settled = settledMatch || partialMatch || {}
      map.set(matchId, {
        matchId,
        fullScore: settled.full_score || '',
        halfScore: settled.half_score || '',
        resultStatus: settled.result_status || '',
        isVoid: !!settled.is_void,
        isPartial: !settledMatch && !!partialMatch,
        settled,
        items: []
      })
    }
    const group = map.get(matchId)
    const itemResult = (group.settled.item_results || []).find(
      result => result.pool === item.pool && result.opt === item.opt
    )
    group.items.push({ ...item, result: itemResult?.result || '' })
  })
  return [...map.values()]
}

const fetchRecords = async () => {
  if (!authState.user) return
  loading.value = true
  try {
    const [recordResult, statsResult] = await Promise.all([
      apiRequest(recordFilter.value === 'won' ? '/api/user/bets?status=won' : '/api/user/bets'),
      apiRequest('/api/user/bet-stats')
    ])
    records.value = recordResult.data || []
    Object.assign(stats, statsResult.data || {})
  } catch (error) {
    if (error.status === 401) openAuth('login')
  } finally {
    loading.value = false
  }
}

const setRecordFilter = (filter) => {
  if (recordFilter.value === filter) return
  recordFilter.value = filter
  records.value = []
  selectedRecord.value = null
  fetchRecords()
}

const removeRecord = async (id) => {
  if (!window.confirm('确定删除这条投注记录吗？')) return
  try {
    await apiRequest(`/api/user/bets/${id}`, { method: 'DELETE' })
    if (selectedRecord.value?.id === id) selectedRecord.value = null
    await fetchRecords()
  } catch (error) {
    window.alert(error.message || '删除失败')
  }
}

watch(() => authState.user?.id, () => {
  records.value = []
  recordFilter.value = 'all'
  Object.assign(stats, { total_bets: 0, total_stake: 0, total_notes: 0, net_profit: 0 })
  if (authState.user) fetchRecords()
})

watch(selectedRecord, record => {
  recordShareToken += 1
  recordShareBlob.value = null
  preparingRecordShare.value = false
  sharingRecord.value = false
  recordShareNotice.value = ''
  if (record) prepareRecordShare(record, recordShareToken)
})

onMounted(async () => {
  await loadCurrentUser()
  if (authState.user) fetchRecords()
})
</script>
