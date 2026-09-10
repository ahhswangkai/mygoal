<template>
  <div class="app-container primary-page syndicate-page">
    <header class="top-header">
      <button class="syndicate-back" type="button" aria-label="返回" @click="goBack">‹</button>
      <span class="header-title">合买账本</span>
      <AccountButton />
    </header>

    <main class="syndicate-content">
      <section class="syndicate-hero">
        <div>
          <span>主持人负责制</span>
          <strong>{{ authState.user?.display_name || authState.user?.username || '未登录' }}</strong>
          <small>份额锁定后，实际票款变化只生成补款或退款</small>
        </div>
        <button type="button" @click="startCreate">+ 新建合买</button>
      </section>

      <section v-if="!authState.user" class="syndicate-empty">
        <strong>登录后使用合买账本</strong>
        <span>每个账号只能管理自己主持的合买方案</span>
        <button type="button" @click="openAuth('login')">登录 / 注册</button>
      </section>

      <template v-else>
        <section class="daily-card">
          <header>
            <div>
              <strong>每日分账</strong>
              <span>同一竞彩日自动汇总每个人的账目</span>
            </div>
            <input v-model="selectedDate" type="date" @change="loadPlans" />
          </header>
          <div class="daily-metrics">
            <div><span>合买方案</span><strong>{{ summary.plan_count || 0 }}单</strong></div>
            <div><span>实际投入</span><strong>¥{{ money(summary.actual_stake) }}</strong></div>
            <div><span>实际返还</span><strong>¥{{ money(summary.actual_return) }}</strong></div>
            <div>
              <span>净盈亏</span>
              <strong :class="profitClass(summary.profit)">{{ signedMoney(summary.profit) }}</strong>
            </div>
          </div>
          <div v-if="summary.members?.length" class="daily-members">
            <div v-for="member in summary.members" :key="member.name">
              <span class="daily-member-name">
                {{ member.name }}
                <em v-if="member.is_host">主持人</em>
              </span>
              <span>应出 ¥{{ money(member.actual_contribution) }}</span>
              <span>返还 ¥{{ money(member.payout) }}</span>
              <strong :class="profitClass(member.profit)">{{ signedMoney(member.profit) }}</strong>
            </div>
          </div>
          <p v-else>当天暂无合买账目</p>
        </section>

        <section v-if="showForm" class="editor-card">
          <header>
            <div>
              <strong>{{ editingId ? '修改合买方案' : '创建合买方案' }}</strong>
              <span>主持人决定所有成员份额</span>
            </div>
            <button type="button" @click="closeForm">×</button>
          </header>

          <label class="field-label">
            <span>方案名称</span>
            <input v-model.trim="form.title" maxlength="100" placeholder="例如：周三欧冠合买" />
          </label>
          <div class="field-row">
            <label class="field-label">
              <span>竞彩日期</span>
              <input v-model="form.business_date" type="date" />
            </label>
            <label class="field-label">
              <span>总份数</span>
              <input v-model.number="form.total_shares" type="number" min="1" step="1" />
            </label>
          </div>
          <label class="field-label">
            <span>关联投注票据（可稍后核对）</span>
            <select v-model="form.bet_id" @change="applySelectedBet">
              <option value="">暂不关联</option>
              <option v-for="bet in availableBets" :key="bet.id" :value="bet.id">
                {{ betDate(bet) }} · {{ bet.description }} · ¥{{ money(bet.stake) }}
              </option>
            </select>
          </label>
          <label class="field-label">
            <span>预计方案金额</span>
            <div class="money-input"><i>¥</i><input v-model="form.expected_stake" inputmode="decimal" /></div>
          </label>

          <div class="member-editor">
            <div class="member-heading">
              <div>
                <strong>成员份额</strong>
                <span>已分配 {{ assignedShares }}/{{ numericShares }} 份</span>
              </div>
              <button type="button" @click="addMember">+ 添加成员</button>
            </div>
            <div
              v-for="(member, index) in form.members"
              :key="member.key"
              class="member-input-row"
            >
              <div class="member-identity">
                <input
                  v-model.trim="member.name"
                  :disabled="member.is_host"
                  maxlength="50"
                  placeholder="成员姓名"
                />
                <em v-if="member.is_host">主持人</em>
              </div>
              <div class="share-input">
                <input v-model.number="member.shares" type="number" min="1" step="1" />
                <span>份</span>
              </div>
              <button v-if="!member.is_host" type="button" @click="removeMember(index)">删除</button>
            </div>
            <p :class="{ invalid: assignedShares !== numericShares }">
              {{ assignedShares === numericShares ? '份额已分配完成' : `还需调整 ${Math.abs(numericShares - assignedShares)} 份` }}
            </p>
          </div>

          <p v-if="formError" class="form-error">{{ formError }}</p>
          <div class="editor-actions">
            <button type="button" class="secondary" @click="closeForm">取消</button>
            <button type="button" :disabled="saving" @click="savePlan">
              {{ saving ? '保存中…' : '保存方案' }}
            </button>
          </div>
        </section>

        <section class="plans-section">
          <header class="plans-heading">
            <div>
              <strong>合买方案</strong>
              <span>预计金额和票据实际金额分开记录</span>
            </div>
            <button type="button" :disabled="loading" @click="loadPlans">
              {{ loading ? '加载中…' : '刷新' }}
            </button>
          </header>

          <div v-if="!loading && !plans.length" class="syndicate-empty compact">
            <strong>当天还没有合买方案</strong>
            <span>创建方案后由主持人分配每个人的份额</span>
          </div>

          <article v-for="plan in plans" :key="plan.id" class="plan-card">
            <header>
              <div>
                <span>{{ plan.business_date }}</span>
                <strong>{{ plan.title }}</strong>
                <small v-if="plan.bet_description">{{ plan.bet_description }}</small>
              </div>
              <em :class="`status-${plan.status}`">{{ statusLabel(plan.status) }}</em>
            </header>

            <div class="plan-money-grid">
              <div><span>预计金额</span><strong>¥{{ money(plan.expected_stake) }}</strong></div>
              <div>
                <span>票据实际</span>
                <strong>{{ plan.actual_stake == null ? '待核对' : `¥${money(plan.actual_stake)}` }}</strong>
              </div>
              <div>
                <span>补款/退款</span>
                <strong :class="profitClass(-(plan.difference || 0))">
                  {{ plan.difference == null ? '—' : signedMoney(plan.difference) }}
                </strong>
              </div>
              <div>
                <span>实际返还</span>
                <strong>{{ plan.actual_return == null ? '待结算' : `¥${money(plan.actual_return)}` }}</strong>
              </div>
            </div>

            <div class="plan-members">
              <div class="plan-member-head">
                <span>成员</span><span>份额</span><span>预计 → 实际</span><span>返还/盈亏</span>
              </div>
              <div v-for="member in plan.members" :key="member.id" class="plan-member-row">
                <span>
                  {{ member.name }}
                  <em v-if="member.is_host">主持</em>
                </span>
                <strong>{{ member.shares }}份<br /><small>{{ percent(member.ratio) }}</small></strong>
                <span>
                  ¥{{ money(member.expected_contribution) }}
                  <template v-if="member.actual_contribution != null"> → ¥{{ money(member.actual_contribution) }}</template>
                  <small v-if="member.adjustment">{{ adjustmentText(member.adjustment) }}</small>
                </span>
                <span>
                  {{ member.payout == null ? '待结算' : `¥${money(member.payout)}` }}
                  <small v-if="member.profit != null" :class="profitClass(member.profit)">{{ signedMoney(member.profit) }}</small>
                </span>
              </div>
            </div>

            <div v-if="reconcileId === plan.id" class="reconcile-box">
              <strong>核对实际票据</strong>
              <select v-model="reconcile.bet_id">
                <option value="">不关联投注记录</option>
                <option v-for="bet in availableBets" :key="bet.id" :value="bet.id">
                  {{ betDate(bet) }} · {{ bet.description }} · ¥{{ money(bet.stake) }}
                </option>
              </select>
              <label>
                <span>票据实际金额</span>
                <div class="money-input"><i>¥</i><input v-model="reconcile.actual_stake" inputmode="decimal" placeholder="留空则读取关联票据" /></div>
              </label>
              <div>
                <button type="button" class="secondary" @click="reconcileId = ''">取消</button>
                <button type="button" @click="submitReconcile(plan)">确认核对</button>
              </div>
            </div>

            <footer v-if="plan.status !== 'settled'">
              <button v-if="plan.share_status === 'draft'" type="button" @click="editPlan(plan)">修改份额</button>
              <button type="button" @click="openReconcile(plan)">核对票据</button>
              <button v-if="plan.share_status === 'draft'" type="button" class="lock" @click="lockPlan(plan)">锁定份额</button>
              <button v-if="plan.share_status === 'draft'" type="button" class="danger" @click="deletePlan(plan)">删除</button>
            </footer>
          </article>
        </section>
      </template>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AccountButton from '../components/AccountButton.vue'
import { apiRequest, authState, loadCurrentUser, openAuth } from '../auth'

const router = useRouter()
const loading = ref(false)
const saving = ref(false)
const plans = ref([])
const availableBets = ref([])
const selectedDate = ref('')
const showForm = ref(false)
const editingId = ref('')
const reconcileId = ref('')
const formError = ref('')
const summary = reactive({ plan_count: 0, actual_stake: 0, actual_return: 0, profit: 0, members: [] })
const reconcile = reactive({ bet_id: '', actual_stake: '' })

const freshForm = () => ({
  title: '',
  business_date: selectedDate.value || new Date().toISOString().slice(0, 10),
  bet_id: '',
  total_shares: 10,
  expected_stake: '',
  members: [{
    key: `host-${Date.now()}`,
    name: authState.user?.display_name || authState.user?.username || '主持人',
    shares: 10,
    is_host: true
  }]
})
const form = reactive(freshForm())

const numericShares = computed(() => Math.max(0, Number(form.total_shares) || 0))
const assignedShares = computed(() => form.members.reduce((sum, member) => sum + Math.max(0, Number(member.shares) || 0), 0))

const resetForm = (data = null) => {
  Object.assign(form, freshForm(), data || {})
}

const money = value => Number(value || 0).toFixed(2)
const signedMoney = value => {
  const amount = Number(value || 0)
  return `${amount > 0 ? '+' : amount < 0 ? '-' : ''}¥${Math.abs(amount).toFixed(2)}`
}
const percent = value => `${(Number(value || 0) * 100).toFixed(1)}%`
const profitClass = value => ({ positive: Number(value) > 0, negative: Number(value) < 0 })
const statusLabel = status => ({ draft: '待锁定', locked: '待结算', settled: '已结算' }[status] || status)
const adjustmentText = value => Number(value) > 0 ? `补款 ¥${money(value)}` : `退款 ¥${money(Math.abs(value))}`
const betDate = bet => {
  const raw = String(bet.created_at || '')
  if (!raw) return ''
  const parsed = new Date(raw)
  if (Number.isNaN(parsed.getTime())) return raw.slice(0, 10)
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'
  }).format(parsed)
}

const goBack = () => {
  if (window.history.length > 1) router.back()
  else router.push('/mine')
}

const loadBets = async () => {
  try {
    const payload = await apiRequest('/api/user/bets?limit=100')
    availableBets.value = payload.data || []
  } catch (_) {
    availableBets.value = []
  }
}

const loadPlans = async () => {
  if (!authState.user) return
  loading.value = true
  try {
    const query = selectedDate.value ? `?date=${encodeURIComponent(selectedDate.value)}` : ''
    const payload = await apiRequest(`/api/user/syndicates${query}`)
    plans.value = payload.data || []
    if (!selectedDate.value) selectedDate.value = payload.business_date || ''
    Object.assign(summary, payload.summary || {})
  } catch (error) {
    formError.value = error.message
  } finally {
    loading.value = false
  }
}

const startCreate = () => {
  if (!authState.user) {
    openAuth('login')
    return
  }
  editingId.value = ''
  formError.value = ''
  resetForm()
  showForm.value = true
  window.scrollTo({ top: 120, behavior: 'smooth' })
}

const closeForm = () => {
  showForm.value = false
  editingId.value = ''
  formError.value = ''
}

const addMember = () => {
  form.members.push({ key: `member-${Date.now()}-${form.members.length}`, name: '', shares: 1, is_host: false })
}

const removeMember = index => form.members.splice(index, 1)

const applySelectedBet = () => {
  const bet = availableBets.value.find(item => item.id === form.bet_id)
  if (!bet) return
  form.expected_stake = money(bet.stake)
  form.title = form.title || bet.description || '合买方案'
  const date = betDate(bet)
  if (date) form.business_date = date
}

const savePlan = async () => {
  formError.value = ''
  if (!form.title) form.title = '合买方案'
  if (assignedShares.value !== numericShares.value) {
    formError.value = '成员份额合计必须等于总份数'
    return
  }
  if (!form.expected_stake || Number(form.expected_stake) <= 0) {
    formError.value = '请填写预计方案金额'
    return
  }
  saving.value = true
  try {
    const body = {
      title: form.title,
      business_date: form.business_date,
      bet_id: form.bet_id || null,
      total_shares: numericShares.value,
      expected_stake: form.expected_stake,
      members: form.members.map(member => ({
        name: member.name,
        shares: Number(member.shares),
        is_host: Boolean(member.is_host)
      }))
    }
    await apiRequest(
      editingId.value ? `/api/user/syndicates/${editingId.value}` : '/api/user/syndicates',
      { method: editingId.value ? 'PUT' : 'POST', body: JSON.stringify(body) }
    )
    selectedDate.value = form.business_date
    closeForm()
    await loadPlans()
  } catch (error) {
    formError.value = error.message
  } finally {
    saving.value = false
  }
}

const editPlan = plan => {
  editingId.value = plan.id
  formError.value = ''
  resetForm({
    title: plan.title,
    business_date: plan.business_date,
    bet_id: plan.bet_id || '',
    total_shares: plan.total_shares,
    expected_stake: money(plan.expected_stake),
    members: plan.members.map(member => ({
      key: member.id,
      name: member.name,
      shares: member.shares,
      is_host: member.is_host
    }))
  })
  showForm.value = true
  window.scrollTo({ top: 120, behavior: 'smooth' })
}

const openReconcile = plan => {
  reconcileId.value = plan.id
  reconcile.bet_id = plan.bet_id || ''
  reconcile.actual_stake = plan.actual_stake == null ? '' : money(plan.actual_stake)
}

const submitReconcile = async plan => {
  try {
    await apiRequest(`/api/user/syndicates/${plan.id}/reconcile`, {
      method: 'POST',
      body: JSON.stringify({
        bet_id: reconcile.bet_id || null,
        actual_stake: reconcile.actual_stake === '' ? null : reconcile.actual_stake
      })
    })
    reconcileId.value = ''
    await loadPlans()
  } catch (error) {
    alert(error.message)
  }
}

const lockPlan = async plan => {
  if (!window.confirm('锁定后不能再修改成员和份额，确认锁定吗？')) return
  try {
    await apiRequest(`/api/user/syndicates/${plan.id}/lock`, { method: 'POST', body: '{}' })
    await loadPlans()
  } catch (error) {
    alert(error.message)
  }
}

const deletePlan = async plan => {
  if (!window.confirm('确认删除这条合买方案吗？')) return
  try {
    await apiRequest(`/api/user/syndicates/${plan.id}`, { method: 'DELETE' })
    await loadPlans()
  } catch (error) {
    alert(error.message)
  }
}

onMounted(async () => {
  await loadCurrentUser()
  if (!authState.user) return
  await Promise.all([loadBets(), loadPlans()])
})
</script>

<style scoped>
.syndicate-page { background: #f5f6f8; }
.syndicate-back { width: 32px; color: #fff; font-size: 34px; line-height: 1; background: none; border: 0; }
.syndicate-content { display: grid; gap: 14px; padding: 14px 12px 30px; }
.syndicate-hero { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 18px; color: #74401b; background: linear-gradient(135deg, #fff7df, #ffe7b6); border: 1px solid #f4d28e; border-radius: 16px; }
.syndicate-hero div { display: grid; gap: 4px; }
.syndicate-hero span { font-size: 11px; }
.syndicate-hero strong { font-size: 20px; }
.syndicate-hero small { color: #a3754d; font-size: 10px; line-height: 1.5; }
.syndicate-hero button, .editor-actions button, .reconcile-box button { flex: none; padding: 10px 14px; color: #fff; background: #ff3655; border: 0; border-radius: 20px; }
.daily-card, .editor-card, .plan-card, .syndicate-empty { background: #fff; border: 1px solid #eee5e7; border-radius: 15px; box-shadow: 0 5px 18px rgb(50 31 36 / 5%); }
.daily-card { padding: 15px; }
.daily-card > header, .editor-card > header, .plans-heading, .plan-card > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.daily-card header div, .editor-card header div, .plans-heading div, .plan-card header div { display: grid; gap: 3px; }
.daily-card header strong, .editor-card header strong, .plans-heading strong, .plan-card header strong { font-size: 16px; }
.daily-card header span, .editor-card header span, .plans-heading span, .plan-card header span, .plan-card header small { color: #999; font-size: 10px; }
.daily-card input { width: 128px; padding: 8px; border: 1px solid #ecdadd; border-radius: 9px; }
.daily-metrics { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-top: 14px; }
.daily-metrics div { display: grid; gap: 5px; padding: 10px; background: #faf7f8; border-radius: 10px; }
.daily-metrics span { color: #999; font-size: 10px; }
.daily-metrics strong { font-size: 15px; }
.daily-members { margin-top: 12px; border-top: 1px dashed #eee; }
.daily-members > div { display: grid; grid-template-columns: 1.2fr 1fr 1fr .8fr; gap: 5px; padding: 10px 2px; font-size: 10px; border-bottom: 1px solid #f3f3f3; }
.daily-member-name { color: #333; font-weight: 600; }
.daily-member-name em, .plan-member-row em { margin-left: 3px; padding: 2px 4px; color: #c66b29; font-size: 8px; font-style: normal; background: #fff0db; border-radius: 5px; }
.daily-card > p { margin: 14px 0 0; color: #aaa; font-size: 11px; text-align: center; }
.positive { color: #14966b !important; }
.negative { color: #e34a61 !important; }
.editor-card { display: grid; gap: 13px; padding: 16px; }
.editor-card header button { color: #999; font-size: 26px; background: none; border: 0; }
.field-row { display: grid; grid-template-columns: 1.3fr .7fr; gap: 9px; }
.field-label { display: grid; gap: 6px; }
.field-label > span, .reconcile-box label > span { color: #777; font-size: 11px; }
.field-label > input, .field-label select, .reconcile-box select { width: 100%; height: 42px; padding: 0 11px; color: #333; background: #fafafa; border: 1px solid #e8e1e3; border-radius: 9px; }
.money-input { display: flex; align-items: center; height: 42px; padding: 0 11px; background: #fafafa; border: 1px solid #e8e1e3; border-radius: 9px; }
.money-input i { margin-right: 5px; color: #ff3655; font-style: normal; }
.money-input input { width: 100%; font-size: 15px; background: transparent; border: 0; outline: none; }
.member-editor { overflow: hidden; border: 1px solid #eee4e6; border-radius: 12px; }
.member-heading { display: flex; justify-content: space-between; padding: 11px; background: #fff9fa; }
.member-heading div { display: grid; gap: 2px; }
.member-heading span { color: #999; font-size: 9px; }
.member-heading button { color: #ff3655; font-size: 10px; background: none; border: 0; }
.member-input-row { display: grid; grid-template-columns: 1fr 82px 34px; align-items: center; gap: 7px; padding: 9px 10px; border-top: 1px solid #f3edef; }
.member-identity { position: relative; }
.member-identity input { width: 100%; height: 35px; padding: 0 8px; background: #fafafa; border: 1px solid #eee; border-radius: 7px; }
.member-identity em { position: absolute; top: 9px; right: 7px; color: #c66b29; font-size: 8px; font-style: normal; }
.share-input { display: flex; align-items: center; }
.share-input input { width: 54px; height: 35px; padding: 0 6px; border: 1px solid #eee; border-radius: 7px; }
.share-input span { margin-left: 3px; color: #999; font-size: 10px; }
.member-input-row > button { color: #c6a5aa; font-size: 9px; background: none; border: 0; }
.member-editor > p { padding: 8px 11px; color: #168763; font-size: 9px; background: #f2fbf7; }
.member-editor > p.invalid { color: #d65a65; background: #fff5f6; }
.form-error { padding: 9px; color: #d84b5e; font-size: 10px; background: #fff1f3; border-radius: 8px; }
.editor-actions { display: grid; grid-template-columns: 1fr 1.6fr; gap: 9px; }
.editor-actions .secondary, .reconcile-box .secondary { color: #777; background: #f1f1f3; }
.editor-actions button:disabled { opacity: .55; }
.plans-section { display: grid; gap: 12px; }
.plans-heading { align-items: center; padding: 3px 2px; }
.plans-heading button { padding: 7px 12px; color: #ff3655; background: #fff; border: 1px solid #ffc3cd; border-radius: 16px; }
.plan-card { overflow: hidden; }
.plan-card > header { padding: 14px; border-bottom: 1px solid #f3edef; }
.plan-card > header em { padding: 5px 9px; font-size: 9px; font-style: normal; border-radius: 10px; }
.status-draft { color: #b67524; background: #fff3d9; }
.status-locked { color: #526176; background: #edf0f4; }
.status-settled { color: #14845f; background: #eaf8f2; }
.plan-money-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1px; background: #f3edef; }
.plan-money-grid div { display: grid; gap: 4px; padding: 11px 14px; background: #fff; }
.plan-money-grid span { color: #999; font-size: 9px; }
.plan-money-grid strong { font-size: 14px; }
.plan-members { padding: 5px 13px; }
.plan-member-head, .plan-member-row { display: grid; grid-template-columns: 1.05fr .62fr 1.4fr 1fr; gap: 5px; align-items: center; }
.plan-member-head { padding: 7px 0; color: #aaa; font-size: 9px; }
.plan-member-row { min-height: 55px; font-size: 10px; border-top: 1px dashed #eee; }
.plan-member-row > strong { font-size: 11px; }
.plan-member-row small { display: block; margin-top: 3px; color: #aaa; font-size: 8px; font-weight: 400; }
.reconcile-box { display: grid; gap: 9px; margin: 0 13px 12px; padding: 12px; background: #fff8e9; border: 1px solid #f4dba6; border-radius: 10px; }
.reconcile-box > strong { color: #805d26; font-size: 12px; }
.reconcile-box label { display: grid; gap: 5px; }
.reconcile-box > div { display: flex; justify-content: flex-end; gap: 8px; }
.reconcile-box button { padding: 8px 12px; font-size: 10px; }
.plan-card > footer { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 7px; padding: 10px 13px; background: #fafafa; }
.plan-card > footer button { padding: 7px 10px; color: #666; font-size: 9px; background: #fff; border: 1px solid #ddd; border-radius: 14px; }
.plan-card > footer .lock { color: #fff; background: #ff3655; border-color: #ff3655; }
.plan-card > footer .danger { color: #d94c5d; border-color: #f2bdc4; }
.syndicate-empty { display: grid; justify-items: center; gap: 8px; padding: 35px 20px; color: #777; }
.syndicate-empty span { color: #aaa; font-size: 10px; text-align: center; }
.syndicate-empty button { margin-top: 6px; padding: 9px 18px; color: #fff; background: #ff3655; border: 0; border-radius: 18px; }
.syndicate-empty.compact { padding: 28px 18px; }
@media (max-width: 360px) {
  .daily-members > div { grid-template-columns: 1fr 1fr; }
  .plan-member-head, .plan-member-row { grid-template-columns: 1fr .55fr 1.2fr .8fr; }
}
</style>
