<script setup lang="ts">
import { ref, onMounted, watch, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { TooltipComponent, GridComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useResourceStats } from '@/composables/useResourceStats'
import type { ResourceStat } from '@/types'

echarts.use([BarChart, LineChart, TooltipComponent, GridComponent, LegendComponent, CanvasRenderer])

const router = useRouter()
const { stats, loading, fetchStats, exportCSV } = useResourceStats()

const period = ref('all')
const periods = [
  { value: 'all', label: '全部' },
  { value: 'today', label: '今日' },
  { value: 'week', label: '本周' },
  { value: 'month', label: '本月' },
]

const chartContainer = ref<HTMLElement | null>(null)
const chartInstance = shallowRef<echarts.ECharts | null>(null)

onMounted(() => load())

watch(period, () => load())

async function load() {
  await fetchStats(period.value)
  if (chartContainer.value) renderChart()
}

function renderChart() {
  const el = chartContainer.value
  if (!el || stats.value.length === 0) return
  if (chartInstance.value) chartInstance.value.dispose()

  const chart = echarts.init(el, undefined, { renderer: 'canvas' })
  chartInstance.value = chart

  const maxAvg = Math.max(...stats.value.map(d => d.avg_duration_ms), 1)

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(17,17,34,0.95)',
      borderColor: 'rgba(255,255,255,0.06)',
      textStyle: { color: '#e4e4ed', fontSize: 12 },
    },
    legend: {
      data: ['Success', 'Failure', 'Avg Duration'],
      bottom: 0,
      textStyle: { color: '#9d9db8', fontSize: 11 },
    },
    grid: { left: '12%', right: '8%', top: 16, bottom: 36 },
    xAxis: {
      type: 'category',
      data: stats.value.map(d => d.name),
      axisLabel: { color: '#9d9db8', fontSize: 10, rotate: 30 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
    },
    yAxis: [
      {
        type: 'value',
        name: 'Count',
        nameTextStyle: { color: '#9d9db8', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
      },
      {
        type: 'value',
        name: 'ms',
        nameTextStyle: { color: '#9d9db8', fontSize: 10 },
        max: maxAvg * 1.5,
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: 'Success',
        type: 'bar',
        stack: 'calls',
        data: stats.value.map(d => d.success_count),
        itemStyle: { color: '#22c55e' },
        emphasis: { itemStyle: { color: '#4ade80' } },
      },
      {
        name: 'Failure',
        type: 'bar',
        stack: 'calls',
        data: stats.value.map(d => d.failure_count),
        itemStyle: { color: '#ef4444' },
        emphasis: { itemStyle: { color: '#f87171' } },
      },
      {
        name: 'Avg Duration',
        type: 'line',
        yAxisIndex: 1,
        data: stats.value.map(d => d.avg_duration_ms),
        itemStyle: { color: '#6366f1' },
        lineStyle: { type: 'dashed', width: 2 },
        symbol: 'circle',
        symbolSize: 6,
      },
    ],
  })

  const ro = new ResizeObserver(() => chart.resize())
  ro.observe(el)
}

function successRate(r: ResourceStat): string {
  if (r.call_count === 0) return '-'
  return ((r.success_count / r.call_count) * 100).toFixed(0) + '%'
}

function formatMs(ms: number) {
  if (ms >= 1000) return (ms / 1000).toFixed(1) + 's'
  return Math.round(ms) + 'ms'
}

function handleExport() {
  exportCSV(period.value)
}

function goDetail(name: string) {
  router.push({ name: 'resource-detail', params: { name } })
}
</script>

<template>
  <div id="rs-page">
    <div id="rs-topbar">
      <button class="ub-back" @click="router.replace('/')">← 返回</button>
      <span class="ub-title">Resource Stats</span>
    </div>

    <div class="rs-content">
      <!-- Period selector -->
      <div class="period-tabs">
        <button
          v-for="p in periods" :key="p.value"
          :class="{ active: period === p.value }"
          @click="period = p.value"
        >{{ p.label }}</button>
        <button class="btn-csv" @click="handleExport">导出 CSV</button>
      </div>

      <div v-if="loading" class="rs-empty">加载中...</div>

      <template v-else-if="stats.length > 0">
        <!-- Summary cards -->
        <div class="summary-cards">
          <div class="scard">
            <div class="scard-value">{{ stats.reduce((s, r) => s + r.call_count, 0) }}</div>
            <div class="scard-label">Total Calls</div>
          </div>
          <div class="scard">
            <div class="scard-value">{{ stats.reduce((s, r) => s + r.success_count, 0) }}</div>
            <div class="scard-label">Success</div>
          </div>
          <div class="scard">
            <div class="scard-value">{{ stats.reduce((s, r) => s + r.failure_count, 0) }}</div>
            <div class="scard-label">Failure</div>
          </div>
        </div>

        <!-- Bar chart -->
        <div class="chart-section">
          <h3>Resource Call Statistics</h3>
          <div ref="chartContainer" class="rs-chart"></div>
        </div>

        <!-- Detail table -->
        <div class="table-section">
          <h3>Detail</h3>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Calls</th>
                <th>Success</th>
                <th>Failure</th>
                <th>Rate</th>
                <th>Avg Duration</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="r in stats" :key="r.name"
                class="clickable"
                @click="goDetail(r.name)"
              >
                <td class="td-name">{{ r.name }}</td>
                <td>{{ r.call_count }}</td>
                <td class="td-ok">{{ r.success_count }}</td>
                <td class="td-err">{{ r.failure_count }}</td>
                <td>{{ successRate(r) }}</td>
                <td>{{ formatMs(r.avg_duration_ms) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <div v-else class="rs-empty">
        暂无资源调用数据。开始使用工具后将自动统计。
      </div>
    </div>
  </div>
</template>

<style scoped>
#rs-page {
  min-height: 100vh;
  background: var(--bg-root);
}
#rs-topbar {
  display: flex; align-items: center; gap: 16px; padding: 10px 20px;
  background: rgba(7,7,16,0.92); backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-glass);
  font-size: 13px; position: sticky; top: 0; z-index: 10;
}
.ub-back {
  background: none; border: none; color: var(--text-muted); cursor: pointer;
  font-size: 13px; padding: 4px 12px; border-radius: var(--radius-sm);
  transition: all var(--transition);
}
.ub-back:hover { color: var(--text-primary); background: var(--bg-hover); }
.ub-title { font-weight: 700; color: var(--text-primary); }

.rs-content { max-width: 880px; margin: 0 auto; padding: 32px 24px; }

.period-tabs {
  display: flex; gap: 8px; margin-bottom: 28px; align-items: center;
  flex-wrap: wrap;
}
.period-tabs button {
  padding: 8px 16px; border: 1px solid var(--border);
  border-radius: var(--radius-sm); cursor: pointer; font-size: 13px;
  font-weight: 600; background: transparent; color: var(--text-secondary);
  transition: all var(--transition);
}
.period-tabs button:hover:not(.active):not(.btn-csv) {
  color: var(--text-primary); background: var(--bg-hover);
}
.period-tabs button.active {
  background: var(--accent); color: #fff; border-color: var(--accent);
}
.btn-csv {
  margin-left: auto; background: var(--accent-light) !important;
  color: var(--accent) !important; border-color: rgba(99,102,241,0.25) !important;
}
.btn-csv:hover { background: rgba(99,102,241,0.2) !important; }

.summary-cards { display: flex; gap: 16px; margin-bottom: 28px; }
.scard {
  flex: 1; background: var(--bg-card); border: 1px solid var(--border-glass);
  border-radius: var(--radius-lg); padding: 20px; text-align: center;
  box-shadow: var(--shadow-card);
}
.scard-value {
  font-size: 28px; font-weight: 700;
  background: var(--accent-gradient);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
  font-family: 'JetBrains Mono', monospace;
}
.scard-label { font-size: 12px; color: var(--text-muted); margin-top: 6px; }

.chart-section, .table-section {
  background: var(--bg-card); border: 1px solid var(--border-glass);
  border-radius: var(--radius-lg); padding: 24px; margin-bottom: 20px;
  box-shadow: var(--shadow-card);
}
.chart-section h3, .table-section h3 {
  font-size: 14px; margin-bottom: 16px; color: var(--text-primary); font-weight: 600;
}
.rs-chart { width: 100%; height: 320px; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 8px 12px; border-bottom: 2px solid var(--border); color: var(--text-muted); font-weight: 600; }
td { padding: 10px 12px; border-bottom: 1px solid var(--border-light); color: var(--text-primary); }
.td-name { font-weight: 500; font-family: monospace; color: var(--accent); }
.td-ok { color: var(--success); }
.td-err { color: var(--error); }
tr.clickable { cursor: pointer; transition: background var(--transition); }
tr.clickable:hover { background: var(--bg-hover); }

.rs-empty {
  text-align: center; padding: 80px 20px; color: var(--text-muted); font-size: 14px;
}

@media (max-width: 640px) {
  .summary-cards { flex-direction: column; }
  .scard-value { font-size: 24px; }
  .chart-section, .table-section { padding: 16px; }
  .rs-chart { height: 250px; }
}
@media (min-width: 1400px) {
  .rs-content { max-width: 1100px; }
}
</style>
