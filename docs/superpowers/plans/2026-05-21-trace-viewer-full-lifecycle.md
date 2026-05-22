# Trace Viewer 全生命周期展示增强 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 升级 TraceView 为 session 全生命周期监控面板，所有 15 种事件按时间戳统一时间线排列。

**Architecture:** 全部前端改动，集中在一个文件 `TraceView.vue` + 类型扩展 `types/index.ts`。后端不动。扩展 `timeline` computed 替代 `turnGroups`，将所有事件（含 lifecycle + graph）按 `created_at` 排序渲染。为每种事件类型提供独立的摘要行渲染和 metadata 折叠面板。

**Tech Stack:** Vue 3 + TypeScript + ECharts + Pinia（无后端改动）

---

### Task 1: 扩展类型定义

**Files:**
- Modify: `frontend/src/types/index.ts:250-254`

- [ ] **Step 1: 添加 lifecycle 事件的 metadata 类型**

在 `TraceMetadataAny` 之后添加 lifecycle 专用的 metadata 类型：

```typescript
export interface TraceMetadataLifecycleSession {
  session_id?: string
  workspace?: string
  new_session?: boolean
  transport?: string
  message_count?: number
  duration_seconds?: number
  trigger?: string
}

export interface TraceMetadataLifecycleHandoff {
  phase?: string
  intent?: string
  required_actions?: string[]
  user_turns_used?: number
  sys_model?: string
  sys_turns_used?: number
  remaining_turns?: number
}

export interface TraceMetadataLifecycleCompaction {
  turns_compacted?: number
  turns_kept?: number
  tokens_before?: number
  tokens_kept?: number
  threshold?: number
}

export interface TraceMetadataLifecycleHookExec {
  hook_name?: string
  hook_event?: string
  command?: string
  exit_code?: number
  stdout?: string
  stderr?: string
}

export interface TraceMetadataLifecyclePromptSnapshot {
  prompt_hash?: string
  prompt_length?: number
  active_tools_count?: number
  tools_list?: string[]
}

export interface TraceMetadataLifecycleModelSwitch {
  to_model?: string
  tool?: string
}

export interface TraceMetadataLifecycleInit {
  stage?: string
  counts?: { models: number; tools: number; skills: number }
  agent_mode?: string
  user_model?: string
  sys_model?: string
}

export interface TraceMetadataLifecycleConfig {
  action?: string
  config_name?: string
  model_name?: string
  reason?: string
}

// Union type for all lifecycle metadata
export type TraceMetadataLifecycleAny = TraceMetadataLifecycleSession
  & TraceMetadataLifecycleHandoff
  & TraceMetadataLifecycleCompaction
  & TraceMetadataLifecycleHookExec
  & TraceMetadataLifecyclePromptSnapshot
  & TraceMetadataLifecycleModelSwitch
  & TraceMetadataLifecycleInit
  & TraceMetadataLifecycleConfig
  & TraceMetadataAny
```

- [ ] **Step 2: 验证类型编译**

Run: `cd frontend && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: No new type errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts
git commit -m "feat: add lifecycle trace metadata types"
```

---

### Task 2: 构建统一时间线 computed

**Files:**
- Modify: `frontend/src/views/TraceView.vue` — replace `turnGroups` computed, add `timeline` computed

- [ ] **Step 1: 增强 nodeIcon/nodeLabel 辅助函数**

在 `<script setup>` 替换 `nodeIcon`：

```typescript
function eventTypeIcon(evt: TraceEvent): string {
  if (evt.node) {
    const icons: Record<string, string> = {
      classify: '🎯', call_model: '🧠', execute_tools: '🔧',
      respond: '✅', recovery: '🔄', hook: '🔗',
    }
    return icons[evt.node] || '⚙️'
  }
  // Lifecycle events by event_type
  const lc = evt as any
  const icons: Record<string, string> = {
    'lifecycle.session_start': '🚀',
    'lifecycle.session_end': '🏁',
    'lifecycle.hook_execution': '🪝',
    'lifecycle.handoff': '🤝',
    'lifecycle.compaction': '📦',
    'lifecycle.prompt_snapshot': '📸',
    'lifecycle.model_switch': '🔄',
    'lifecycle.init': '⚡',
    'lifecycle.config': '⚙️',
  }
  return icons[lc.event_type] || '📌'
}

function eventTypeLabel(evt: TraceEvent): string {
  if (evt.node) return evt.node
  const lc = evt as any
  return (lc.event_type || '').replace('lifecycle.', '').replace('graph.', '')
}
```

- [ ] **Step 2: 替换 turnGroups 为 timeline computed**

移除 `turnGroups` computed，添加：

```typescript
interface TimelineItem {
  event: TraceEvent
  time: Date
  timeStr: string
  meta: ParsedTraceMetadata
}

const timeline = computed<TimelineItem[]>(() => {
  const items: TimelineItem[] = []
  for (const e of events.value) {
    const time = e.created_at ? new Date(e.created_at) : new Date()
    items.push({
      event: e,
      time,
      timeStr: time.toLocaleTimeString('zh-CN', { hour12: false }) + '.' + String(time.getMilliseconds()).padStart(3, '0'),
      meta: parseMeta(e),
    })
  }
  items.sort((a, b) => a.time.getTime() - b.time.getTime())
  return items
})
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/TraceView.vue
git commit -m "feat: add unified timeline computed for all trace events"
```

---

### Task 3: 渲染统一时间线（替代 turn groups）

**Files:**
- Modify: `frontend/src/views/TraceView.vue` — template section for timeline

- [ ] **Step 1: 替换时间线模板**

删除现有的 `tv-timeline` 块（`<!-- Round-by-round timeline -->` 到 `</div>` 闭合），替换为：

```html
<!-- Unified timeline -->
<div class="tv-timeline">
  <div v-for="(item, idx) in timeline" :key="item.event.id ?? idx" class="tv-timeline-item">
    <div class="tv-tl-time">
      <span class="tv-tl-time-text">{{ item.timeStr }}</span>
      <span v-if="item.event.turn" class="tv-tl-turn">T{{ item.event.turn }}</span>
    </div>
    <div
      class="tv-card"
      :class="'tv-card-' + item.event.node"
      :style="{ borderLeftColor: eventBorderColor(item.event) }"
    >
      <div class="tv-card-head">
        <span class="tv-card-icon">{{ eventTypeIcon(item.event) }}</span>
        <span class="tv-card-node">{{ eventTypeLabel(item.event) }}</span>
        <!-- event-specific details -->
        <span v-if="item.event.model" class="tv-card-model-name">{{ item.event.model }}</span>
        <span v-if="item.event.tool_name" class="tv-card-model-name">{{ item.event.tool_name }}</span>
        <span :class="'tv-badge ' + statusBadgeClass(item.event.status)">{{ statusLabel(item.event.status) }}</span>
        <span v-if="item.event.duration_ms" class="tv-card-dur">{{ formatMs(item.event.duration_ms) }}</span>
        <span v-if="item.event.total_tokens" class="tv-card-tokens">{{ tokensDisplay(item.event) }}</span>
        <!-- copy button -->
        <button class="tv-copy-btn" title="复制 JSON" @click.stop="copyEventJson(item.event)">⎘</button>
      </div>
      <!-- Expandable metadata for every event -->
      <CollapsibleSection title="Metadata" :defaultOpen="false">
        <pre class="tv-snippet">{{ formatMetaJson(item.meta, item.event) }}</pre>
      </CollapsibleSection>
    </div>
  </div>
</div>
```

- [ ] **Step 2: 添加 eventBorderColor 辅助函数**

```typescript
function eventBorderColor(evt: TraceEvent): string {
  const m: Record<string, string> = {
    classify: 'var(--accent)',
    call_model: '#6366f1',
    execute_tools: 'var(--success)',
    respond: 'var(--success)',
    recovery: '#f59e0b',
    hook: '#8b5cf6',
  }
  if (evt.node) return m[evt.node] || '#646480'
  // lifecycle event colors
  const lc = evt as any
  const lm: Record<string, string> = {
    'lifecycle.session_start': '#3b82f6',
    'lifecycle.session_end': '#6366f1',
    'lifecycle.hook_execution': '#a78bfa',
    'lifecycle.handoff': '#06b6d4',
    'lifecycle.compaction': '#f97316',
    'lifecycle.prompt_snapshot': '#64748b',
    'lifecycle.model_switch': '#eab308',
    'lifecycle.init': '#22c55e',
    'lifecycle.config': '#64748b',
  }
  return lm[lc.event_type] || '#646480'
}
```

- [ ] **Step 3: 添加 copyEventJson 和 formatMetaJson**

```typescript
function copyEventJson(evt: TraceEvent) {
  navigator.clipboard.writeText(JSON.stringify(evt, null, 2))
}

function formatMetaJson(meta: ParsedTraceMetadata, evt: TraceEvent): string {
  // Merge top-level fields not in metadata for full picture
  const enriched: any = { ...meta }
  if (evt.model && !enriched.model) enriched.model = evt.model
  if (evt.tool_name && !enriched.tool_name) enriched.tool_name = evt.tool_name
  if (evt.error_msg && !enriched.error_msg) enriched.error_msg = evt.error_msg
  return JSON.stringify(enriched, null, 2)
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/TraceView.vue
git commit -m "feat: replace turn groups with unified timeline rendering"
```

---

### Task 4: 汇总面板增强

**Files:**
- Modify: `frontend/src/views/TraceView.vue` — add summary stats computed + template

- [ ] **Step 1: 添加 sessionStats computed**

```typescript
const sessionStats = computed(() => {
  let modelCalls = 0
  let toolCalls = 0
  let hookOk = 0
  let hookErr = 0
  let hookBlocked = 0

  for (const e of events.value) {
    if (e.node === 'call_model') modelCalls++
    if (e.node === 'execute_tools') toolCalls++
    if (e.node === 'hook' || (e as any).event_type === 'lifecycle.hook_execution') {
      const s = e.status
      if (s === 'ok') hookOk++
      else if (s === 'error') hookErr++
      else if (s === 'blocked_by_hook') hookBlocked++
    }
  }

  let totalDur = 0
  for (const e of events.value) {
    if (e.duration_ms) totalDur = Math.max(totalDur, totalDur + e.duration_ms)
  }

  return { modelCalls, toolCalls, hookOk, hookErr, hookBlocked, totalEvents: events.value.length }
})
```

- [ ] **Step 2: 在详情标题面包屑下方增加统计行**

```html
<div v-if="sessionStats.totalEvents" class="tv-session-stats">
  <span class="tv-stat-chip">📊 {{ sessionStats.totalEvents }} 事件</span>
  <span class="tv-stat-chip">🧠 {{ sessionStats.modelCalls }} 模型调用</span>
  <span class="tv-stat-chip">🔧 {{ sessionStats.toolCalls }} 工具调用</span>
  <span class="tv-stat-chip">🪝 {{ sessionStats.hookOk + sessionStats.hookErr + sessionStats.hookBlocked }} hooks</span>
  <span v-if="sessionStats.hookErr" class="tv-stat-chip tv-stat-chip-err">{{ sessionStats.hookErr }} hook err</span>
  <span v-if="sessionStats.hookBlocked" class="tv-stat-chip tv-stat-chip-warn">{{ sessionStats.hookBlocked }} hook blocked</span>
</div>
```

- [ ] **Step 3: 添加统计 chip 样式**

```css
.tv-session-stats {
  display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px;
}
.tv-stat-chip {
  font-size: 11px; padding: 2px 8px; border-radius: var(--radius-full, 99px);
  background: rgba(255,255,255,0.05); color: var(--text-secondary);
}
.tv-stat-chip-err { background: rgba(239,68,68,0.12); color: #ef4444; }
.tv-stat-chip-warn { background: rgba(245,158,11,0.12); color: #f59e0b; }
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/TraceView.vue
git commit -m "feat: add session summary stats panel"
```

---

### Task 5: 时间线样式与布局

**Files:**
- Modify: `frontend/src/views/TraceView.vue` — CSS section

- [ ] **Step 1: 添加时间线布局样式**

在 `<style scoped>` 添加（替换旧 `.tv-timeline` / `.tv-round` / `.tv-round-body` 样式）：

```css
/* ── Unified Timeline ── */
.tv-timeline {
  flex: 1;
  position: relative;
  padding-left: 20px;
}
.tv-timeline::before {
  content: '';
  position: absolute; left: 8px; top: 0; bottom: 0;
  width: 2px;
  background: linear-gradient(to bottom, rgba(255,255,255,0.08), rgba(255,255,255,0.02));
}
.tv-timeline-item {
  position: relative;
  margin-bottom: 10px;
  padding-left: 18px;
}
.tv-timeline-item::before {
  content: '';
  position: absolute; left: -15px; top: 10px;
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--accent);
}
.tv-tl-time {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 4px; font-family: 'JetBrains Mono', monospace;
}
.tv-tl-time-text {
  font-size: 11px; color: var(--text-muted);
}
.tv-tl-turn {
  font-size: 10px; color: var(--accent);
  background: rgba(99,102,241,0.12);
  padding: 0 5px; border-radius: var(--radius-full, 99px);
}

/* Copy button */
.tv-copy-btn {
  margin-left: auto; background: none; border: none;
  color: var(--text-muted); cursor: pointer; font-size: 12px;
  padding: 2px 6px; border-radius: 4px;
  opacity: 0; transition: opacity 0.15s;
}
.tv-card-head:hover .tv-copy-btn { opacity: 1; }
.tv-copy-btn:hover { background: rgba(255,255,255,0.08); color: var(--text-primary); }

/* Remove old round styles, keep the rest */
```

- [ ] **Step 2: 移除旧 round 样式**

删除 `.tv-round`, `.tv-round-header`, `.tv-round-num`, `.tv-round-summary`, `.tv-round-body` 样式。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/TraceView.vue
git commit -m "feat: add unified timeline CSS with connector line and time labels"
```

---

### Task 6: 导出 Session Trace 按钮

**Files:**
- Modify: `frontend/src/views/TraceView.vue` — template only

- [ ] **Step 1: 确认按钮已存在并可用**

现有 `handleExport` 函数和 "导出 JSON" 按钮在 `.tv-header-actions` 中已实现（第 195-197, 248 行）。确认无误即可。

- [ ] **Step 2: 验证导出功能**

手动测试：选择 session → 点击 "导出 JSON" → 浏览器下载 `trace-<sessionId>.json`。

无需代码改动。如果按钮未显示，确认 `selectedSession` 有值。

---

### Task 7: 清理旧代码 & 修复 chart

**Files:**
- Modify: `frontend/src/views/TraceView.vue` — `renderChart` function

- [ ] **Step 1: 更新 renderChart 使用 timeline 而不是 turnGroups**

```typescript
function renderChart() {
  const el = chartContainer.value
  if (!el || timeline.value.length === 0) return

  if (chartInstance.value) {
    chartInstance.value.dispose()
  }

  const chart = echarts.init(el!, undefined, { renderer: 'canvas' })
  chartInstance.value = chart

  const categories: string[] = []
  const durations: number[] = []
  const colors: string[] = []

  for (const item of timeline.value) {
    const e = item.event
    if (e.duration_ms && e.duration_ms > 0) {
      const label = e.tool_name
        ? `${item.timeStr} ${eventTypeLabel(e)}:${e.tool_name}`
        : `${item.timeStr} ${eventTypeLabel(e)}`
      categories.push(label)
      durations.push(e.duration_ms)
      colors.push(bgColor(e.status))
    }
  }

  if (categories.length === 0) return

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(17,17,34,0.95)',
      borderColor: 'rgba(255,255,255,0.06)',
      textStyle: { color: '#e4e4ed', fontSize: 12 },
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params
        return `${p.name}<br/>Duration: ${formatMs(p.value)}`
      },
    },
    grid: { left: '30%', right: '8%', top: 10, bottom: 10 },
    xAxis: {
      type: 'value',
      axisLabel: { color: '#9d9db8', fontSize: 10, formatter: (v: number) => formatMs(v) },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
    },
    yAxis: {
      type: 'category',
      data: categories,
      axisLabel: { color: '#9d9db8', fontSize: 10 },
      inverse: true,
    },
    series: [{
      type: 'bar',
      data: durations.map((v, i) => ({
        value: v,
        itemStyle: { color: colors[i], borderRadius: [0, 3, 3, 0] },
      })),
      barMaxWidth: 18,
    }],
  })

  const ro = new ResizeObserver(() => chart.resize())
  ro.observe(el)
}
```

- [ ] **Step 2: 移除旧 turnGroups 相关代码**

删除 `turnGroups`, `maxDurationForTurn` computed 和引用。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/TraceView.vue
git commit -m "refactor: update chart to use timeline, remove turnGroups"
```

---

### Task 8: 端到端验证

- [ ] **Step 1: 启动 dev server**

```bash
cd frontend && npm run dev
```

- [ ] **Step 2: 验证所有 15 种事件类型在时间线中出现**

打开 TraceView → 选择一个有内容的 session → 确认事件列表包含：
- lifecycle.session_start / session_end
- lifecycle.hook_execution (SessionStart/SessionEnd)
- lifecycle.handoff / compaction / prompt_snapshot / model_switch
- lifecycle.init / config（如果有）
- graph.classify / call_model / hook / execute_tools / respond / recovery
- 每张卡片可展开 metadata JSON
- 每张卡片可复制 JSON

- [ ] **Step 3: 验证导出**

点击 "导出 JSON" → 文件下载成功，JSON 格式正确。

- [ ] **Step 4: 验证时间线排序**

事件按时间升序排列，左侧时间标签正确。

- [ ] **Step 5: 验证汇总面板**

Session 详情顶部显示事件数、模型调用、工具调用、hook 统计。

---

## 验收清单

- [ ] 15 种事件类型全部出现在统一时间线中
- [ ] 事件按 `created_at` 排序
- [ ] 每个事件可展开 metadata JSON
- [ ] 每个事件有复制按钮（鼠标悬停显示）
- [ ] SessionStart / SessionEnd hook_execution 可见
- [ ] "导出 Session Trace" 按钮正常下载
- [ ] 汇总面板展示 event/model/tool/hook 统计
- [ ] 耗时图表（bar chart）正常工作
- [ ] `vue-tsc --noEmit` 无新增类型错误
