<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useUsage } from '@/composables/useUsage'
import { useI18n } from '@/composables/useI18n'

const router = useRouter()
const { t } = useI18n()
const { summary, detail, loading, fetchSummary, fetchDetail } = useUsage()

const period = ref('month')

const periods = [
  { value: 'today', label: '今日' },
  { value: 'week', label: '本周' },
  { value: 'month', label: '本月' },
]

async function load() {
  await fetchSummary(period.value)
}

onMounted(() => load())

function onChangePeriod(p: string) {
  period.value = p
  load()
}

const maxTokens = computed(() => {
  if (!summary.value?.by_model.length) return 1
  return Math.max(...summary.value.by_model.map(m => m.total_tokens))
})

function barWidth(tokens: number): string {
  return (tokens / maxTokens.value * 100).toFixed(1) + '%'
}

function formatTokens(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

</script>

<template>
  <div id="usage-page">
    <div id="usage-topbar">
      <button class="ub-back" @click="router.replace('/')">← {{ t('login.back') }}</button>
      <span class="ub-title">{{ t('status.usage') }}</span>
    </div>

    <div class="usage-content">
      <!-- Period selector -->
      <div class="period-tabs">
        <button
          v-for="p in periods" :key="p.value"
          :class="{ active: period === p.value }"
          @click="onChangePeriod(p.value)"
        >{{ p.label }}</button>
      </div>

      <div v-if="loading" class="usage-empty">{{ t('common.loading') }}</div>

      <template v-else-if="summary && summary.total_calls > 0">
        <!-- Summary cards -->
        <div class="summary-cards">
          <div class="scard">
            <div class="scard-value">{{ formatTokens(summary.total_tokens) }}</div>
            <div class="scard-label">Tokens</div>
          </div>
          <div class="scard">
            <div class="scard-value">{{ summary.total_calls }}</div>
            <div class="scard-label">Calls</div>
          </div>
        </div>

        <!-- Bar chart -->
        <div class="chart-section" v-if="summary.by_model.length > 0">
          <h3>Token Usage by Model</h3>
          <div class="chart-container">
            <div v-for="m in summary.by_model" :key="m.model_name" class="chart-row">
              <span class="chart-label">{{ m.model_name }}</span>
              <div class="chart-track">
                <div class="chart-fill" :style="{ width: barWidth(m.total_tokens) }"></div>
              </div>
              <span class="chart-value">{{ formatTokens(m.total_tokens) }}</span>
            </div>
          </div>
        </div>

        <!-- Model table -->
        <div class="table-section">
          <h3>Model Details</h3>
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>Calls</th>
                <th>Prompt</th>
                <th>Completion</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in summary.by_model" :key="m.model_name">
                <td>{{ m.model_name }}</td>
                <td>{{ m.calls }}</td>
                <td>{{ formatTokens(m.prompt_tokens) }}</td>
                <td>{{ formatTokens(m.completion_tokens) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <div v-else class="usage-empty">
        暂无使用数据。开始对话后将自动统计 Token 用量。
      </div>
    </div>
  </div>
</template>

<style scoped>
#usage-page {
  min-height: 100vh;
  background: var(--bg-root);
}
#usage-topbar {
  display: flex; align-items: center; gap: 16px; padding: 10px 20px;
  background: rgba(7,7,16,0.92); backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
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

.usage-content { max-width: 720px; margin: 0 auto; padding: 32px 24px; }

.period-tabs {
  display: flex; gap: 0; margin-bottom: 28px; border-radius: var(--radius-md);
  overflow: hidden; border: 1px solid var(--border); background: var(--bg-input);
}
.period-tabs button {
  flex: 1; padding: 9px; border: none; cursor: pointer; font-size: 13px;
  font-weight: 600; background: transparent; color: var(--text-secondary);
  transition: all var(--transition);
}
.period-tabs button:hover:not(.active) { color: var(--text-primary); background: var(--bg-hover); }
.period-tabs button.active { background: var(--accent); color: #fff; }

.summary-cards { display: flex; gap: 16px; margin-bottom: 28px; }
.scard {
  flex: 1; background: var(--bg-card); border: 1px solid var(--border-glass);
  border-radius: var(--radius-lg); padding: 20px; text-align: center;
  box-shadow: var(--shadow-card); transition: all var(--transition);
}
.scard:hover { border-color: var(--border); transform: translateY(-1px); box-shadow: var(--shadow-md); }
.scard-value {
  font-size: 28px; font-weight: 700;
  background: var(--accent-gradient);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
}
.scard-label { font-size: 12px; color: var(--text-muted); margin-top: 6px; }

.chart-section, .table-section {
  background: var(--bg-card); border: 1px solid var(--border-glass);
  border-radius: var(--radius-lg); padding: 24px; margin-bottom: 20px;
  box-shadow: var(--shadow-card);
}
.chart-section h3, .table-section h3 {
  font-size: 14px; margin-bottom: 20px; color: var(--text-primary); font-weight: 600;
}
.chart-container { display: flex; flex-direction: column; gap: 12px; }
.chart-row { display: flex; align-items: center; gap: 12px; }
.chart-label { width: 140px; font-size: 12px; text-align: right; flex-shrink: 0; color: var(--text-secondary); }
.chart-track { flex: 1; height: 22px; background: var(--bg-input); border-radius: 11px; overflow: hidden; }
.chart-fill {
  height: 100%;
  background: var(--accent-gradient);
  border-radius: 11px; min-width: 4px; transition: width 0.6s ease;
}
.chart-value {
  width: 55px; font-size: 11px; color: var(--text-muted); flex-shrink: 0;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
}

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 8px 12px; border-bottom: 2px solid var(--border); color: var(--text-muted); font-weight: 600; }
td {
  padding: 10px 12px; border-bottom: 1px solid var(--border-light); color: var(--text-primary);
}
td:first-child { font-weight: 500; }

.usage-empty {
  text-align: center; padding: 80px 20px; color: var(--text-muted); font-size: 14px;
}

@media (max-width: 640px) {
  .summary-cards { flex-direction: column; }
  .chart-label { width: 100px; font-size: 11px; }
  .chart-value { width: 44px; font-size: 10px; }
  .chart-section, .table-section { padding: 16px; }
  .scard-value { font-size: 24px; }
}

@media (min-width: 1400px) {
  .usage-content { max-width: 900px; }
}
</style>
