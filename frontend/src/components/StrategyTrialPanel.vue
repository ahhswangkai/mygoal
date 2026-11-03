<template>
  <section class="strategy-trial">
    <header class="trial-heading">
      <div><strong>固定策略 · 前瞻验收</strong><small>仅模拟，不创建投注记录，不自动下注</small></div>
      <button type="button" :disabled="loading || starting" @click="load">{{ loading ? '加载中…' : '刷新' }}</button>
    </header>
    <p v-if="error" class="trial-error" role="alert">{{ error }}</p>
    <p v-if="message" class="trial-note" role="status">{{ message }}</p>
    <div v-if="!report" class="trial-empty">
      <strong>尚未启动试运行</strong>
      <p>固定版本观察30天，每天18:00最多一张2.8～3.3倍二串一，每张1模拟单位。不足条件不出单，错过时间不补选。</p>
      <button v-if="canManage" type="button" :disabled="starting || loading" @click="start">{{ starting ? '登记中…' : '开始30天模拟' }}</button>
      <small v-else>由管理员启动，启动后不能重置历史成绩。</small>
    </div>
    <template v-else>
      <div class="trial-notice">
        <strong>{{ report.phase === 'collecting' ? '正在积累前瞻样本' : '观察窗口已结束，等待评估' }}</strong>
        <span>{{ report.trial.start_date }} 至 {{ report.trial.end_date }} · {{ report.trial.experiment_id }}</span>
        <small>18:00固定出单（5分钟任务容错窗口）· 最多1张/天 · 每张1单位</small>
      </div>
      <p v-if="!report.implementation_matches" class="trial-error">模型实现已改变，暂停后续出单。原赛前留档仍然保留并继续结算。</p>
      <div class="trial-stat-grid">
        <article><span>整张命中</span><strong>{{ percent(report.summary.hit_rate) }}</strong><small>{{ report.summary.hits }}/{{ report.summary.settled }} 已结算</small></article>
        <article><span>模拟净收益率</span><strong>{{ percent(report.summary.roi) }}</strong><small>待结算 {{ report.summary.pending }} 张不计入</small></article>
        <article><span>模拟净收益</span><strong>{{ units(report.summary.profit_units) }}</strong><small>累计结算投入 {{ report.summary.stake_units }} 单位</small></article>
        <article><span>最大回撤</span><strong>{{ report.summary.max_drawdown_units }} 单位</strong><small>最长连错 {{ report.summary.max_losing_streak }} 张</small></article>
        <article><span>纯赔率基准ROI</span><strong>{{ percent(report.baseline.roi) }}</strong><small>{{ report.baseline.settled }} 张结算 · 同时留档</small></article>
        <article><span>未出单记录</span><strong>{{ report.no_ticket_days + report.missed_days + report.suspended_days }} 天</strong><small>无合格 {{ report.no_ticket_days }} · 错过 {{ report.missed_days }} · 暂停 {{ report.suspended_days }}</small></article>
      </div>
      <p class="trial-note">{{ report.verdict }}<template v-if="report.summary.hit_rate_interval_95">。命中率95%区间 {{ report.summary.hit_rate_interval_95[0] }}%～{{ report.summary.hit_rate_interval_95[1] }}%（小样本不确定性高）</template></p>
      <p v-if="report.summary.roi_interval_95" class="trial-note">ROI重采样95%区间 {{ report.summary.roi_interval_95[0] }}%～{{ report.summary.roi_interval_95[1] }}%。仅描述现有样本，不包含全部跨日相关性和筛选偏差。</p>
      <section class="trial-day">
        <header><strong>{{ selectedDate }} 赛前留档</strong><span>{{ decisionLabel(day?.decision) }}</span></header>
        <template v-if="day">
          <p>{{ day.reason }}</p>
          <small>记录于 {{ formatTime(day.captured_at) }} · 不随重新研判覆盖</small>
          <template v-if="day.ticket">
            <div v-for="pick in day.ticket.picks" :key="pick.match_id" class="trial-pick">
              <div><strong>{{ pick.match_number }}</strong><span>{{ pick.home_team }} vs {{ pick.away_team }}</span><small>{{ pick.match_time }}</small></div>
              <div><b>{{ pick.selection_text }} @{{ pick.odds }}</b><small>{{ statusLabel(pick.status) }}<template v-if="pick.result_score"> · {{ pick.result_score }}</template></small></div>
            </div>
            <footer><span>合计 {{ day.ticket.combined_odds }}倍 · 1单位</span><strong>{{ statusLabel(day.ticket.status) }}<template v-if="day.ticket.profit_units != null"> · {{ units(day.ticket.profit_units) }}</template></strong></footer>
            <small>理论盈亏平衡命中率 {{ day.ticket.breakeven_probability }}%；不是模型承诺胜率。</small>
          </template>
          <details v-if="day.baseline_ticket"><summary>纯赔率对照单（不使用模型选场）</summary><p v-for="pick in day.baseline_ticket.picks" :key="pick.match_id">{{ pick.match_number }} {{ pick.selection_text }} @{{ pick.odds }} · {{ statusLabel(pick.status) }}</p><small>合计 {{ day.baseline_ticket.combined_odds }}倍 · {{ statusLabel(day.baseline_ticket.status) }}</small></details>
          <details v-if="day.excluded_matches?.length"><summary>未通过条件的比赛 {{ day.excluded_matches.length }}场</summary><p v-for="item in day.excluded_matches" :key="item.match_id"><b>{{ item.match_number }}</b> {{ item.selection || '' }}：{{ item.reasons.join('；') }}</p></details>
          <small class="trial-hash">快照 {{ day.snapshot_hash?.slice(0, 16) }}</small>
        </template>
        <p v-else>这一天尚无留档。固定出单前不提前生成，历史日期不补选。</p>
      </section>
      <details class="trial-rules"><summary>已冻结的实验规则与统计口径</summary>
        <p>使用固定权重FAE确定性引擎，不调用大模型，不加载自动调权或新发布Skill；代码变动暂停新出单。现有平／让平规则不修改。</p>
        <p>胜平负与竞彩让球六项中，先排除低于1.50赔率，再保留每场融合估计最高的一项。单腿估计≥45%、未校准期望≥1.00，且亚盘/大小球完整、主选投注偏离≤+10个百分点、快照60分钟内、距开赛超过10分钟。失败后不改推次选凑单。</p>
        <p>融合估计＝35%固定引擎＋65%同市场去水概率。不同比赛两两组合，合计2.8～3.3倍内取独立近似命中估计最高的一张；这些门槛是实验设定，不是已验证规律。</p>
        <p>每张固定1单位，整张两场均出结果才计入收益；改期按赔率1.00处理。命中率不包含全退票，ROI包含全部已结算单。纯赔率基准同样每场只保留第一方向，再按相同赔率范围组单。</p>
        <p>30天结束不自动发布。需要继续检查样本量、基准对比、收益集中度和不确定性；不使用倍投，不以回本为目标加注。</p>
        <small>规则指纹 {{ report.trial.policy_hash?.slice(0, 16) }} · 模型指纹 {{ report.trial.model_fingerprint?.slice(0, 16) }}</small>
      </details>
      <section v-if="report.days.length" class="trial-history"><strong>完整模拟流水</strong><button v-for="item in [...report.days].reverse()" :key="item.owner_date" type="button" @click="$emit('select-date', item.owner_date)"><span>{{ item.owner_date }} · {{ decisionLabel(item.decision) }}</span><b>{{ item.ticket ? statusLabel(item.ticket.status) : '—' }}<template v-if="item.ticket?.profit_units != null"> {{ units(item.ticket.profit_units) }}</template></b></button></section>
    </template>
  </section>
</template>

<script setup>
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
const props = defineProps({ selectedDate: String, refreshKey: Number })
defineEmits(['select-date'])
const report = ref(null)
const loading = ref(false)
const starting = ref(false)
const canManage = ref(false)
const error = ref('')
const message = ref('')
const day = computed(() => report.value?.days?.find(item => item.owner_date === props.selectedDate))
let controller
let timer
let visible = true
const percent = value => value == null ? '—' : `${Number(value).toFixed(1)}%`
const units = value => `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)} 单位`
const formatTime = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const decisionLabel = value => ({ ticket: '模拟单', no_ticket: '未出单', missed: '错过窗口', suspended: '版本暂停' }[value] || '等待留档')
const statusLabel = value => ({ hit: '✓ 命中', miss: '× 未中', push: '已退回', pending: '待结算' }[value] || '待赛')
async function load() {
  controller?.abort()
  const active = new AbortController()
  controller = active
  loading.value = true
  error.value = ''
  try {
    const response = await fetch('/api/fae/strategy-trial', { credentials: 'same-origin', cache: 'no-store', signal: active.signal })
    const payload = await response.json()
    if (!response.ok || !payload.success) throw new Error(payload.message || '试运行加载失败')
    if (controller !== active) return
    report.value = payload.data
    canManage.value = Boolean(payload.can_manage)
  } catch (e) { if (e.name !== 'AbortError') error.value = e.message }
  finally { if (controller === active) loading.value = false }
}
async function start() {
  if (!window.confirm('开始固定版本30天模拟？每日18:00留档；不能重置成绩；不创建真实投注。')) return
  starting.value = true
  error.value = ''
  try {
    const response = await fetch('/api/fae/strategy-trial/start', { method: 'POST', credentials: 'same-origin' })
    const payload = await response.json()
    if (!response.ok || !payload.success) throw new Error(payload.message || '启动失败')
    message.value = payload.message
    await load()
  } catch (e) { error.value = e.message }
  finally { starting.value = false }
}
watch(() => [props.selectedDate, props.refreshKey], load)
onMounted(() => { load(); timer = setInterval(() => { if (visible && document.visibilityState === 'visible' && !loading.value) load() }, 60000) })
onActivated(() => { visible = true })
onDeactivated(() => { visible = false })
onBeforeUnmount(() => { controller?.abort(); clearInterval(timer) })
</script>

<style scoped>
.strategy-trial { padding: 16px; background: #fff; border: 1px solid #ecdfe3; border-radius: 14px; color: #444; font-size: 14px; }
.trial-heading, .trial-day header, .trial-day footer, .trial-history button { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.trial-heading strong { font-size: 16px; }
.strategy-trial small { display: block; margin-top: 5px; color: #8a8c93; font-size: 12px; line-height: 1.6; }
.strategy-trial button { padding: 8px 12px; border: 1px solid #e7b9c2; border-radius: 8px; background: #fff; color: #d63f5e; font: inherit; cursor: pointer; }
.strategy-trial button:disabled { opacity: .55; cursor: wait; }
.trial-notice, .trial-empty { margin-top: 16px; padding: 14px; border-radius: 10px; background: #f6f8fb; }
.trial-notice span { display: block; margin-top: 7px; overflow-wrap: anywhere; font-size: 12px; color: #777; }
.strategy-trial p { line-height: 1.75; overflow-wrap: anywhere; }
.trial-error { padding: 10px; color: #b94355; background: #fff1f2; border-radius: 8px; }
.trial-stat-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }
.trial-stat-grid article { padding: 14px 8px; text-align: center; background: #fcfafb; border: 1px solid #eee5e8; border-radius: 10px; }
.trial-stat-grid article span { font-size: 12px; color: #888; }
.trial-stat-grid article strong { display: block; margin-top: 8px; font-size: 21px; color: #323c4a; }
.trial-note { font-size: 12px; color: #777; }
.trial-day { margin-top: 18px; padding: 14px; border: 1px solid #e6e8ed; border-radius: 10px; }
.trial-day header span { color: #a17432; font-size: 12px; white-space: nowrap; }
.trial-pick { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; padding: 14px 0; border-bottom: 1px dashed #e4e4e7; }
.trial-pick span { display: block; margin-top: 5px; line-height: 1.5; }
.trial-pick b { color: #df3f5e; }
.trial-pick > div:last-child { text-align: right; }
.trial-day footer { padding: 14px 0; flex-wrap: wrap; font-size: 13px; }
.trial-hash { overflow-wrap: anywhere; }
.strategy-trial details { margin-top: 14px; padding: 12px; border-radius: 8px; background: #f8f8fa; font-size: 12px; }
.strategy-trial summary { cursor: pointer; color: #626978; line-height: 1.5; }
.trial-history { margin-top: 18px; }
.trial-history button { width: 100%; margin-top: 8px; text-align: left; border-color: #ececf0; font-size: 12px; }
@media (max-width: 360px) { .trial-pick { grid-template-columns: minmax(0, 1fr); } .trial-pick > div:last-child { text-align: left; } }
</style>
