<template>
  <section class="strategy-trial">
    <header class="trial-heading">
      <div><strong>火山AI · 前瞻验收 V3</strong><small>AI逐场研判，固定规则组单，仅模拟</small></div>
      <button type="button" :disabled="loading || starting" @click="load">{{ loading ? '加载中…' : '刷新' }}</button>
    </header>
    <p v-if="error" class="trial-error" role="alert">{{ error }}</p>
    <p v-if="message" class="trial-note" role="status">{{ message }}</p>
    <div v-if="!report" class="trial-empty">
      <strong>尚未启动试运行</strong>
      <p>固定Prompt与模型配置观察30天。每天北京时间14:00冻结输入，分批调用火山大模型（最多10场/批），独立生成“约3倍二串一”和“平／让平3场2、3关”两类方案。每类最多1张、各1模拟单位，不足条件不凑单。</p>
      <small>会产生火山API费用。调用失败不使用本地规则冒充AI结论，不创建真实投注。</small>
      <button v-if="canManage" type="button" :disabled="starting || loading" @click="start">{{ starting ? '登记中…' : '开始AI版30天模拟' }}</button>
      <small v-else>由管理员启动，启动后不能重置历史成绩。</small>
    </div>
    <template v-else>
      <div class="trial-notice">
        <strong>{{ report.phase === 'collecting' ? '正在积累前瞻样本' : '观察窗口已结束，等待评估' }}</strong>
        <span>{{ report.trial.start_date }} 至 {{ report.trial.end_date }} · {{ report.trial.experiment_id }}</span>
        <small>北京时间{{ decisionTimeLabel }}冻结输入（5分钟容错）· 两类方案各最多1张/天，各1单位</small>
        <small v-if="report.trial.schedule_effective_date">14:00时段自 {{ report.trial.schedule_effective_date }} 起生效；之前已留档的方案不变，不补跑。</small>
        <small v-if="report.trial.ai_settings">模型 {{ report.trial.ai_settings.model }} · Prompt {{ report.trial.ai_settings.prompt_version }} · 最多10场/批</small>
      </div>
      <p v-if="!report.implementation_matches || report.ai_configuration_matches === false" class="trial-error">模型实现、Prompt或接口配置已改变，暂停后续出单。原赛前留档仍然保留并继续结算。</p>
      <h3 class="trial-section-title">约3倍二串一 · 独立统计</h3>
      <div class="trial-stat-grid">
        <article><span>整张命中</span><strong>{{ percent(report.summary.hit_rate) }}</strong><small>{{ report.summary.hits }}/{{ report.summary.decided ?? report.summary.settled }} 胜负已定 · 退回 {{ report.summary.pushes || 0 }}</small></article>
        <article><span>模拟净收益率</span><strong>{{ percent(report.summary.roi) }}</strong><small>待结算 {{ report.summary.pending }} 张不计入</small></article>
        <article><span>模拟净收益</span><strong>{{ units(report.summary.profit_units) }}</strong><small>累计结算投入 {{ report.summary.stake_units }} 单位</small></article>
        <article><span>最大回撤</span><strong>{{ report.summary.max_drawdown_units }} 单位</strong><small>最长连亏 {{ report.summary.max_losing_streak }} 张</small></article>
        <article><span>纯赔率基准ROI</span><strong>{{ percent(report.baseline.roi) }}</strong><small>{{ report.baseline.settled }} 张结算 · 同时留档</small></article>
        <article><span>未出单记录</span><strong>{{ report.no_ticket_days + report.missed_days + report.suspended_days + (report.ai_failed_days || 0) }} 天</strong><small>无合格 {{ report.no_ticket_days }} · 错过 {{ report.missed_days }} · 暂停 {{ report.suspended_days }} · AI失败 {{ report.ai_failed_days || 0 }}</small></article>
      </div>
      <p class="trial-note">{{ report.verdict }}<template v-if="report.summary.hit_rate_interval_95">。命中率95%区间 {{ report.summary.hit_rate_interval_95[0] }}%～{{ report.summary.hit_rate_interval_95[1] }}%（小样本不确定性高）</template></p>
      <p v-if="report.summary.roi_interval_95" class="trial-note">ROI重采样95%区间 {{ report.summary.roi_interval_95[0] }}%～{{ report.summary.roi_interval_95[1] }}%。仅描述现有样本，不包含全部跨日相关性和筛选偏差。</p>
      <template v-if="hasDrawStrategy && report.draw_summary">
        <h3 class="trial-section-title">平／让平 · 3场2、3关</h3>
        <div class="trial-stat-grid">
          <article><span>有返奖率</span><strong>{{ percent(report.draw_summary.hit_rate) }}</strong><small>{{ report.draw_summary.hits }}/{{ report.draw_summary.decided ?? report.draw_summary.settled }} 张有返奖 · 全退 {{ report.draw_summary.pushes || 0 }}</small></article>
          <article><span>方案ROI</span><strong>{{ percent(report.draw_summary.roi) }}</strong><small>盈利 {{ report.draw_summary.profitable_tickets || 0 }} 张</small></article>
          <article><span>模拟净收益</span><strong>{{ units(report.draw_summary.profit_units) }}</strong><small>累计投入 {{ report.draw_summary.stake_units }} 单位</small></article>
          <article><span>最大回撤</span><strong>{{ report.draw_summary.max_drawdown_units }} 单位</strong><small>待结算 {{ report.draw_summary.pending }} 张</small></article>
        </div>
        <p class="trial-note">同一场只取平或让平中的一个；3个二串一＋1个三串一，每注0.25单位。有返奖不等于盈利，全退票不计入返奖率。</p>
        <p class="trial-note">纯赔率平／让平对照ROI {{ percent(report.draw_baseline?.roi) }} · 无合格组合 {{ report.draw_no_ticket_days || 0 }} 天<template v-if="report.draw_summary.roi_interval_95"> · ROI重采样95%区间 {{ report.draw_summary.roi_interval_95[0] }}%～{{ report.draw_summary.roi_interval_95[1] }}%</template></p>
      </template>
      <section class="trial-day">
        <header><strong>{{ selectedDate }} 赛前留档</strong><span>二串一 {{ decisionLabel(day?.decision) }}</span></header>
        <template v-if="day">
          <p>{{ day.reason }}</p>
          <small>记录于 {{ formatTime(day.captured_at) }} · 不随重新研判覆盖</small>
          <div v-if="day.ai_run" class="trial-notice"><strong>火山大模型已核验 {{ day.ai_run.verified_matches }} 场</strong><small>{{ day.ai_run.model }} · 完成 {{ day.ai_run.completed_batches }}/{{ day.ai_run.batch_count }} 批 · 调用失败 {{ day.ai_run.failed_batches }} 批</small><small v-if="hasDrawStrategy">平／让平独立评估 {{ day.ai_run.draw_assessed_matches || 0 }} 场</small><small v-if="day.published_at">AI结果留档 {{ formatTime(day.published_at) }}</small></div>
          <p v-if="day.decision === 'analyzing'" class="trial-note">输入已经保存，AI正在分批研判。刷新不会重新发起调用；已成功的批次不会重复付费。</p>
          <template v-if="day.ticket">
            <div v-for="pick in day.ticket.picks" :key="pick.match_id" class="trial-pick">
              <div><strong>{{ pick.match_number }}</strong><span>{{ pick.home_team }} vs {{ pick.away_team }}</span><small>{{ pick.match_time }}</small></div>
              <div><b>{{ pick.selection_text }} @{{ pick.odds }}</b><small>{{ statusLabel(pick.status) }}<template v-if="pick.result_score"> · {{ pick.result_score }}</template></small></div>
              <small v-if="pick.ai_reason" class="trial-ai-reason">AI依据：{{ pick.ai_reason }}</small>
            </div>
            <footer><span>合计 {{ day.ticket.combined_odds }}倍 · 1单位</span><strong>{{ statusLabel(day.ticket.status) }}<template v-if="day.ticket.profit_units != null"> · {{ units(day.ticket.profit_units) }}</template></strong></footer>
            <small>理论盈亏平衡命中率 {{ day.ticket.breakeven_probability }}%；不是模型承诺胜率。</small>
          </template>
          <details v-if="day.baseline_ticket"><summary>纯赔率对照单（不使用模型选场）</summary><p v-for="pick in day.baseline_ticket.picks" :key="pick.match_id">{{ pick.match_number }} {{ pick.selection_text }} @{{ pick.odds }} · {{ statusLabel(pick.status) }}</p><small>合计 {{ day.baseline_ticket.combined_odds }}倍 · {{ statusLabel(day.baseline_ticket.status) }}</small></details>
          <details v-if="day.excluded_matches?.length"><summary>未通过条件的比赛 {{ day.excluded_matches.length }}场</summary><p v-for="item in day.excluded_matches" :key="item.match_id"><b>{{ item.match_number }}</b> {{ item.selection || '' }}：{{ item.reasons.join('；') }}<small v-if="item.ai_reason">AI依据：{{ item.ai_reason }}</small></p></details>
          <section v-if="hasDrawStrategy" class="trial-draw-plan">
            <header><strong>平／让平 · 3场2、3关</strong><span>{{ decisionLabel(day.draw_decision) }}</span></header>
            <p>{{ day.draw_decision === 'analyzing' ? '等待火山独立评估平/让平候选，尚未出方案。' : day.draw_reason }}</p>
            <template v-if="day.draw_ticket">
              <div v-for="pick in day.draw_ticket.picks" :key="pick.match_id" class="trial-pick">
                <div><strong>{{ pick.match_number }}</strong><span>{{ pick.home_team }} vs {{ pick.away_team }}</span><small>{{ pick.match_time }}</small></div>
                <div><b>{{ pick.selection_text }} @{{ pick.odds }}</b><small>{{ statusLabel(pick.status) }}<template v-if="pick.result_score"> · {{ pick.result_score }}</template></small></div>
                <small class="trial-ai-reason">AI依据：{{ pick.ai_reason }}</small>
              </div>
              <footer><span>共4注 · 合计1单位</span><strong>{{ day.draw_ticket.profit_units == null ? '待结算' : (day.draw_ticket.profitable ? '✓ 盈利' : '未盈利') }}<template v-if="day.draw_ticket.profit_units != null"> · {{ units(day.draw_ticket.profit_units) }}</template></strong></footer>
              <small>理论最高返奖 {{ amountUnits(day.draw_ticket.max_return_units) }}（含本金），不代表实际收益。</small>
              <details><summary>逐注结算：3个二串一＋1个三串一</summary><p v-for="(line, index) in day.draw_ticket.lines" :key="index"><b>{{ line.play }}</b> {{ line.picks.map(pick => `${pick.match_number}${pick.selection_text}`).join(' × ') }}<small>投入0.25单位 · {{ statusLabel(line.status) }}<template v-if="line.return_units != null"> · 返奖 {{ amountUnits(line.return_units) }}</template></small></p></details>
            </template>
            <details v-if="day.draw_candidates?.length"><summary>平／让平独立候选与排除原因</summary><p v-for="pick in day.draw_candidates" :key="`${pick.match_id}-${pick.selection}`"><b>{{ pick.match_number }} {{ pick.selection_text }} @{{ pick.odds }}</b> · {{ pick.eligible ? '通过候选门槛' : '未入选' }}<small>AI：{{ pick.ai_reason }}</small><small v-if="pick.rejections?.length">{{ pick.rejections.join('；') }}</small></p></details>
            <details v-if="day.draw_baseline_ticket"><summary>纯赔率平／让平对照单</summary><p v-for="pick in day.draw_baseline_ticket.picks" :key="pick.match_id">{{ pick.match_number }} {{ pick.selection_text }} @{{ pick.odds }}</p><small>3场2、3关 · 1单位 · {{ statusLabel(day.draw_baseline_ticket.status) }}</small></details>
          </section>
          <small class="trial-hash">快照 {{ day.snapshot_hash?.slice(0, 16) }}</small>
        </template>
        <p v-else>这一天尚无留档。固定出单前不提前生成，历史日期不补选。</p>
      </section>
      <details class="trial-rules"><summary>已冻结的实验规则与统计口径</summary>
        <p>AI版调用火山大模型判断六项概率和主选，冻结Prompt、请求模型与接口配置。输入、Prompt、响应、调用ID逐批落库，不自动调权；配置或代码改变暂停新出单，失败不降级为本地规则。现有平／让平规则不修改。</p>
        <p>只接受大模型原始主选，赔率≥1.50、单腿融合估计≥45%、未校准期望≥1.00，且亚盘/大小球完整、主选投注偏离≤+10个百分点、快照60分钟内、冻结时距开赛超过10分钟。失败后不换成次选，AI完成时已开赛也不出单。</p>
        <p>融合估计＝35%AI估计＋65%同市场去水概率。不同比赛两两组合，合计2.8～3.3倍内取独立近似命中估计最高的一张；这些门槛是实验设定，不是已验证规律。模型使用latest别名时，服务商底层版本仍可能更新，不能声称完全锁定模型权重。</p>
        <p v-if="hasDrawStrategy">平／让平独立分支：AI明确同意候选，赔率2.50～5.00，普通平融合估计≥28%、让平≥25%，期望≥1.00，其余数据与热度门槛不变。同场只占一项，选3场组成2、3关，不要求它是二串一主选，不强制平和让平各占几场。</p>
        <p>每张固定1单位，整张两场均出结果才计入收益；改期按赔率1.00处理。命中率不包含全退票，ROI包含全部已结算单。纯赔率基准同样每场只保留第一方向，再按相同赔率范围组单。</p>
        <p>30天结束不自动发布。需要继续检查样本量、基准对比、收益集中度和不确定性；不使用倍投，不以回本为目标加注。</p>
        <small>规则指纹 {{ report.trial.policy_hash?.slice(0, 16) }} · 模型指纹 {{ report.trial.model_fingerprint?.slice(0, 16) }}</small>
      </details>
      <section v-if="report.days.length" class="trial-history"><strong>完整模拟流水</strong><button v-for="item in [...report.days].reverse()" :key="item.owner_date" type="button" @click="$emit('select-date', item.owner_date)"><span>{{ item.owner_date }}<small>二串一 {{ decisionLabel(item.decision) }}<template v-if="hasDrawStrategy"> · 平/让平 {{ decisionLabel(item.draw_decision) }}</template></small></span><b><template v-if="item.ticket?.profit_units != null">二串 {{ units(item.ticket.profit_units) }}</template><small v-if="item.draw_ticket?.profit_units != null">平/让平 {{ units(item.draw_ticket.profit_units) }}</small></b></button></section>
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
const hasDrawStrategy = computed(() => Boolean(report.value?.trial?.policy?.draw_focus?.enabled))
const decisionTimeLabel = computed(() => {
  const policy = report.value?.trial?.policy
  return `${String(policy?.decision_hour ?? 14).padStart(2, '0')}:${String(policy?.decision_minute ?? 0).padStart(2, '0')}`
})
let controller
let timer
let visible = true
const percent = value => value == null ? '—' : `${Number(value).toFixed(1)}%`
const units = value => `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)} 单位`
const amountUnits = value => `${Number(value).toFixed(2)} 单位`
const formatTime = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const decisionLabel = value => ({ ticket: '模拟单', no_ticket: '未出单', missed: '错过窗口', suspended: '版本暂停', analyzing: 'AI研判中', ai_failed: 'AI研判失败' }[value] || '等待留档')
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
  if (!window.confirm('开始AI版V3的30天模拟？二串一与平/让平2、3关各最多1张、各1单位；每天北京时间14:00分批调用火山，会产生API费用，不创建真实投注。')) return
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
.trial-ai-reason { grid-column: 1 / -1; }
.trial-section-title { margin: 20px 0 0; font-size: 14px; color: #414b59; }
.trial-draw-plan { margin-top: 18px; padding: 12px; border-radius: 10px; background: #fff8f8; border: 1px solid #eed8df; }
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
