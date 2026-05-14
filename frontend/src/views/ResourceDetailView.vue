<script setup lang="ts">
import { ref, onMounted, watch, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { TooltipComponent, GridComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useResourceStats } from '@/composables/useResourceStats'
import type { ResourceDailyStat } from '@/types'

echarts.use([BarChart, LineChart, TooltipComponent, GridComponent, LegendComponent, CanvasRenderer])

const route = useRoute()
const router = useRouter()
const { dailyStats, loading, fetchDetail, exportDetailCSV } = useResourceStats()

const resourceName = ref(route.params.name as string)
const fromDate = ref('')
const toDate = ref('')

const chartContainer = ref<HTMLElement | null>(null)
const chartInstance = shallowRef<echarts.ECharts | null>(null)

onMounted(() => load())

async function load() {
  await fetchDetail(resourceName.value, fromDate.value, toDate.value)
  if (chartContainer.value) renderChart()
}

function applyFilter() {
  load()
}

function renderChart() {
  const el = chartContainer.value
  if (!el || dailyStats.value.length === 0) return
  if (chartInstance.value) chartInstance.value.dispose()

  const chart = echarts.init(el, undefined, { renderer: 'canvas' })
  chartInstance.value = chart

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
    grid: { left: '8%', right: '8%', top: 16, bottom: 36 },
    xAxis: {
      type: 'category',
      data: dailyStats.value.map(d => d.day),
      axisLabel: { color: '#9d9db8', fontSize: 10, rotate: 45 },
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
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: 'Success',
        type: 'bar',
        stack: 'calls',
        data: dailyStats.value.map(d => d.success_count),
        itemStyle: { color: '#22c55e' },
        emphasis: { itemStyle: { color: '#4ade80' } },
      },
      {
        name: 'Failure',
        type: 'bar',
        stack: 'calls',
        data: dailyStats.value.map(d => d.failure_count),
        itemStyle: { color: '#ef4444' },
        emphasis: { itemStyle: { color: '#f87171' } },
      },
      {
        name: 'Avg Duration',
        type: 'line',
        yAxisIndex: 1,
        data: dailyStats.value.map(d => d.avg_duration_ms),
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

function totalCalls(): number {
  return dailyStats.value.reduce((s, d) => s + d.call_count, 0)
}

function totalSuccess(): number {
  return dailyStats.value.reduce((s, d) => s + d.success_count, 0)
}

function totalFailure(): number {
  return dailyStats.value.reduce((s, d) => s + d.failure_count, 0)
}

function avgDuration(): string {
  if (dailyStats.value.length === 0) return '-'
  const sum = dailyStats.value.reduce((s, d) => s + (d.avg_duration_ms || 0) * d.call_count, 0)
  const total = dailyStats.value.reduce((s, d) => s + d.call_count, 0)
  if (total === 0) return '-'
  const avg = sum / total
  return avg >= 1000 ? (avg / 1000).toFixed(1) + 's' : Math.round(avg) + 'ms'
}

function formatMs(ms: number | null) {
  if (ms == null) return '-'
  if (ms >= 1000) return (ms / 1000).toFixed(1) + 's'
  return Math.round(ms) + 'ms'
}

function handleExport() {
  exportDetailCSV(resourceName.value, fromDate.value, toDate.value)
}
</script>

<template>
  <div id="rd-page">
    <div id="rd-topbar">
      <button class="ub-back" @click="router.push({ name: 'resource-stats' })">← 返回</button>
      <span class="ub-title">{{ resourceName }}</span>
      <button class="btn-csv" @click="handleExport">导出 CSV</button>
    </div>

    <div class="rd-content">
      <!-- Date filter -->
      <div class="filter-row">
        <label class="filter-label">
          From:
          <input type="date" v-model="fromDate" class="date-input" />
        </label>
        <label class="filter-label">
          To:
          <input type="date" v-model="toDate" class="date-input" />
        </label>
        <button class="btn-apply" @click="applyFilter">Apply</button>
      </div>

      <div v-if="loading" class="rd-empty">加载中...</div>

      <template v-else-if="dailyStats.length > 0">
        <!-- Summary cards -->
        <div class="summary-cards">
          <div class="scard">
            <div class="scard-value">{{ totalCalls() }}</div>
            <div class="scard-label">Total Calls</div>
          </div>
          <div class="scard">
            <div class="scard-value">{{ totalSuccess() }}</div>
            <div class="scard-label">Success</div>
          </div>
          <div class="scard">
            <div class="scard-value">{{ totalFailure() }}</div>
            <div class="scard-label">Failure</div>
          </div>
          <div class="scard">
            <div class="scard-value">{{ avgDuration() }}</div>
            <div class="scard-label">Avg Duration</div>
          </div>
        </div>

        <!-- Trend chart -->
        <div class="chart-section">
          <h3>Daily Trend</h3>
          <div ref="chartContainer" class="rd-chart"></div>
        </div>

        <!-- Daily detail table -->
        <div class="table-section">
          <h3>Daily Detail</h3>
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Calls</th>
                <th>Success</th>
                <th>Failure</th>
                <th>Avg Duration</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in dailyStats" :key="d.day">
                <td class="td-date">{{ d.day }}</td>
                <td>{{ d.call_count }}</td>
                <td class="td-ok">{{ d.success_count }}</td>
                <td class="td-err">{{ d.failure_count }}</td>
                <td>{{ formatMs(d.avg_duration_ms) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <div v-else class="rd-empty">
        暂无该资源的调用数据。
      </div>
    </div>
  </div>
</template>

<style scoped>
#rd-page {
  min-height: 100vh;
  background: var(--bg-root);
}
#rd-topbar {
  display: flex; align-items: center; gap: 12px; padding: 10px 20px;
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
.ub-title { font-weight: 700; color: var(--accent); font-family: monospace; }
.btn-csv {
  margin-left: auto; padding: 5px 14px; font-size: 12px;
  background: var(--accent-light); color: var(--accent);
  border: 1px solid rgba(99,102,241,0.25); border-radius: var(--radius-sm);
  cursor: pointer; transition: all var(--transition);
}
.btn-csv:hover { background: rgba(99,102,241,0.2); }

.rd-content { max-width: 880px; margin: 0 auto; padding: 32px 24px; }

.filter-row {
  display: flex; gap: 12px; align-items: center; margin-bottom: 28px;
  flex-wrap: wrap;
}
.filter-label {
  font-size: 12px; color: var(--text-secondary);
  display: flex; align-items: center; gap: 6px;
}
.date-input {
  background: var(--bg-input); border: 1px solid var(--border);
  border-radius: var(--radius-sm); color: var(--text-primary);
  padding: 5px 10px; font-size: 12px;
}
.date-input:focus { border-color: var(--accent); outline: none; }
.btn-apply {
  padding: 6px 16px; font-size: 12px;
  background: var(--accent); color: #fff; border: none;
  border-radius: var(--radius-sm); cursor: pointer;
  transition: all var(--transition);
}
.btn-apply:hover { background: var(--accent-hover); }

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
.rd-chart { width: 100%; height: 300px; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 8px 12px; border-bottom: 2px solid var(--border); color: var(--text-muted); font-weight: 600; }
td { padding: 10px 12px; border-bottom: 1px solid var(--border-light); color: var(--text-primary); }
.td-date { font-family: monospace; color: var(--text-secondary); }
.td-ok { color: var(--success); }
.td-err { color: var(--error); }

.rd-empty {
  text-align: center; padding: 80px 20px; color: var(--text-muted); font-size: 14px;
}

@media (max-width: 640px) {
  .summary-cards { flex-direction: column; }
  .scard-value { font-size: 24px; }
  .chart-section, .table-section { padding: 16px; }
  .rd-chart { height: 250px; }
}
@media (min-width: 1400px) {
  .rd-content { max-width: 1100px; }
}
</style>
