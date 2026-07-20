import { createRouter, createWebHistory } from 'vue-router'

import HomeView from './views/HomeView.vue'
import CalculatorView from './views/CalculatorView.vue'
import BettingListView from './views/BettingListView.vue'
import MatchDetailView from './views/MatchDetailView.vue'
import StatsView from './views/StatsView.vue'
import ResultsView from './views/ResultsView.vue'
import RecommendationsView from './views/RecommendationsView.vue'
import MineView from './views/MineView.vue'

const routes = [
  { path: '/', redirect: '/calculator' },
  { path: '/home', name: 'home', component: HomeView, meta: { title: '足彩分析', mainTab: true } },
  {
    path: '/recommendations',
    name: 'recommendations',
    component: RecommendationsView,
    meta: { title: 'FAE 推荐', mainTab: true, keepAlive: true }
  },
  { path: '/calculator', name: 'calculator', component: CalculatorView, meta: { title: '足球计算器', mainTab: true } },
  { path: '/mine', name: 'mine', component: MineView, meta: { title: '我的', mainTab: true } },
  { path: '/bets', name: 'bets', component: BettingListView, meta: { title: '投注记录', mainTab: true, navTab: 'mine' } },
  { path: '/match/:id', name: 'match-detail', component: MatchDetailView, meta: { title: '比赛详情' } },
  { path: '/stats', name: 'stats', component: StatsView, meta: { title: '个人统计', mainTab: true, navTab: 'mine' } },
  { path: '/results', name: 'results', component: ResultsView, meta: { title: '赛果', mainTab: true } }
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition || { top: 0 }
  }
})

router.afterEach((to) => {
  if (to.meta && to.meta.title) {
    document.title = to.meta.title
  }
})

export default router
