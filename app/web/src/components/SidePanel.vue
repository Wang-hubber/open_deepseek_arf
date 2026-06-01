<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useChat } from '@/composables/useChat'
import { useApi } from '@/composables/useApi'

const { toolCalls, streamingText } = useChat()
const api = useApi()

// ---- Panel collapse state ----
const plannerOpen = ref(true)
const toolsOpen = ref(true)
const workspaceOpen = ref(true)

// ---- Planner state ----
interface PlanStep {
  id: string; description: string; status: 'pending' | 'in_progress' | 'completed'
}
const planSteps = ref<PlanStep[]>([])
const planTitle = ref('')

watch(toolCalls, (calls) => {
  for (const tc of calls) {
    if (tc.status !== 'completed' || !tc.result) continue
    parseToolResult(tc.name, tc.result)
  }
}, { deep: true })

function parseToolResult(tool: string, result: string) {
  if (tool === 'planner') {
    try {
      const r = extractDict(result)
      if (r?.steps) {
        planTitle.value = r.description || r.title || 'Plan'
        planSteps.value = r.steps.map((s: any) => ({
          id: s.id || s.tool || '',
          description: s.description || s.tool || String(s),
          status: s.status || 'pending',
        }))
      }
    } catch {}
  }
  if (tool === 'todo') {
    try {
      const r = extractDict(result)
      if (r?.task) {
        const id = r.id || ''
        const step = planSteps.value.find(s => s.id === id)
        if (step) {
          if (r.status === 'completed') step.status = 'completed'
          else if (r.status === 'in_progress') step.status = 'in_progress'
        }
      }
    } catch {}
  }
}

function extractDict(raw: string): Record<string, any> | null {
  // Handle Python repr strings like "{'result': {'ok': True, ...}}"
  const trimmed = raw.trim()
  if (trimmed.startsWith('{')) {
    try { return JSON.parse(trimmed) } catch {}
    // Try Python repr → JSON conversion
    try {
      const jsonStr = trimmed
        .replace(/'/g, '"').replace(/True/g, 'true')
        .replace(/False/g, 'false').replace(/None/g, 'null')
      return JSON.parse(jsonStr)
    } catch {}
  }
  return null
}

const plannerProgress = computed(() => {
  const done = planSteps.value.filter(s => s.status === 'completed').length
  return `${done}/${planSteps.value.length}`
})

// ---- Tool Calls (sync from useChat toolCalls ref) ----
const expandedToolId = ref('')
function toggleTool(id: string) {
  expandedToolId.value = expandedToolId.value === id ? '' : id
}
function formatArgs(args: string): string {
  try { return JSON.stringify(JSON.parse(args), null, 2) } catch { return args }
}

// ---- Workspace files ----
interface FileEntry {
  name: string; type: 'file' | 'dir'; path: string; size?: number; suffix?: string
}
const workspaceFiles = ref<FileEntry[]>([])
const workspaceRoot = ref('')
let filePollTimer: any = null

async function loadFiles() {
  try {
    const data = await api.get<{ ok: boolean; root: string; files: FileEntry[] }>('/api/files')
    if (data?.ok) {
      workspaceFiles.value = data.files || []
      workspaceRoot.value = data.root || ''
    }
  } catch {}
}

function fileUrl(entry: FileEntry): string {
  const suffixes = ['.md', '.txt', '.html', '.htm', '.json', '.csv', '.yaml', '.yml', '.py', '.log']
  const previewable = suffixes.includes(entry.suffix || '')
  return `/api/files/${encodeURIComponent(entry.path)}${previewable ? '' : '?download=1'}`
}

onMounted(() => {
  loadFiles()
  filePollTimer = setInterval(loadFiles, 5000)
})
onUnmounted(() => { clearInterval(filePollTimer) })
</script>

<template>
  <aside id="side-panel">
    <!-- Planner -->
    <section class="sp-section" :class="{ collapsed: !plannerOpen }">
      <div class="sp-header" @click="plannerOpen = !plannerOpen">
        <span class="sp-title">Planner</span>
        <span v-if="planSteps.length" class="sp-badge">{{ plannerProgress }}</span>
        <span class="sp-arrow">{{ plannerOpen ? '▾' : '▸' }}</span>
      </div>
      <div v-if="plannerOpen" class="sp-body">
        <div v-if="!planSteps.length" class="sp-empty">等待计划生成...</div>
        <div v-for="s in planSteps" :key="s.id" class="plan-step" :class="s.status">
          <span class="ps-icon">{{ s.status === 'completed' ? '✓' : s.status === 'in_progress' ? '⏳' : '○' }}</span>
          <span class="ps-desc">{{ s.description }}</span>
        </div>
      </div>
    </section>

    <!-- Tool Calls -->
    <section class="sp-section" :class="{ collapsed: !toolsOpen }">
      <div class="sp-header" @click="toolsOpen = !toolsOpen">
        <span class="sp-title">Tool Calls</span>
        <span v-if="toolCalls.length" class="sp-badge">{{ toolCalls.length }}</span>
        <span class="sp-arrow">{{ toolsOpen ? '▾' : '▸' }}</span>
      </div>
      <div v-if="toolsOpen" class="sp-body">
        <div v-if="!toolCalls.length" class="sp-empty">等待工具调用...</div>
        <div v-for="tc in toolCalls.slice(-20).reverse()" :key="tc.id" class="tc-entry"
             :class="{ expanded: expandedToolId === tc.id }" @click="toggleTool(tc.id)">
          <div class="tc-summary">
            <span class="tc-icon">{{ tc.status === 'completed' ? '✓' : tc.status === 'failed' ? '✗' : '⏳' }}</span>
            <span class="tc-name">{{ tc.name }}</span>
            <span class="tc-status">{{ tc.status }}</span>
          </div>
          <div v-if="expandedToolId === tc.id" class="tc-detail">
            <div v-if="tc.args" class="tc-block">
              <div class="tc-label">Args</div>
              <pre class="tc-pre">{{ formatArgs(tc.args) }}</pre>
            </div>
            <div v-if="tc.result" class="tc-block">
              <div class="tc-label">Result</div>
              <pre class="tc-pre">{{ tc.result }}</pre>
            </div>
            <div v-if="tc.error" class="tc-block tc-error-block">
              <div class="tc-label">Error</div>
              <pre class="tc-pre">{{ tc.error }}</pre>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Workspace -->
    <section class="sp-section" :class="{ collapsed: !workspaceOpen }">
      <div class="sp-header" @click="workspaceOpen = !workspaceOpen">
        <span class="sp-title">Workspace</span>
        <span class="sp-arrow">{{ workspaceOpen ? '▾' : '▸' }}</span>
      </div>
      <div v-if="workspaceOpen" class="sp-body">
        <div v-if="!workspaceFiles.length" class="sp-empty">加载中...</div>
        <div v-for="f in workspaceFiles" :key="f.path" class="ws-entry">
          <template v-if="f.type === 'dir'">
            <span class="ws-icon">📁</span>
            <span class="ws-name">{{ f.name }}</span>
          </template>
          <template v-else>
            <span class="ws-icon">📄</span>
            <a class="ws-name ws-link" :href="fileUrl(f)" target="_blank">{{ f.name }}</a>
            <span v-if="f.size" class="ws-size">{{ f.size > 1024 ? `${(f.size/1024).toFixed(1)}KB` : `${f.size}B` }}</span>
          </template>
        </div>
      </div>
    </section>
  </aside>
</template>

<style scoped>
#side-panel {
  display: flex; flex-direction: column;
  height: 100%; overflow-y: auto;
  background: var(--bg-surface, #1a1a2e);
  border-left: 1px solid var(--border-color, #333);
  font-size: 13px;
}
.sp-section {
  border-bottom: 1px solid var(--border-color, #333);
}
.sp-section.collapsed .sp-body { display: none; }
.sp-header {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 12px; cursor: pointer;
  background: var(--bg-header, #222);
  user-select: none; position: sticky; top: 0; z-index: 1;
}
.sp-header:hover { background: var(--bg-hover, #2a2a3e); }
.sp-title { font-weight: 600; color: var(--text-primary, #eee); flex: 1; }
.sp-badge { font-size: 11px; background: var(--accent, #3b82f6); color: #fff;
  padding: 1px 6px; border-radius: 8px; }
.sp-arrow { color: var(--text-muted, #888); font-size: 10px; }
.sp-body { padding: 4px 0; max-height: 40vh; overflow-y: auto; }
.sp-empty { padding: 12px; color: var(--text-muted, #888); font-style: italic; text-align: center; }

/* Planner */
.plan-step { display: flex; align-items: flex-start; gap: 6px;
  padding: 4px 12px; border-left: 3px solid transparent; }
.plan-step.completed { border-left-color: #22c55e; opacity: 0.7; }
.plan-step.in_progress { border-left-color: #f59e0b; }
.plan-step.pending { border-left-color: #6b7280; }
.ps-icon { flex-shrink: 0; width: 16px; text-align: center;
  font-size: 12px; color: var(--text-muted, #888); }
.plan-step.completed .ps-icon { color: #22c55e; }
.plan-step.in_progress .ps-icon { color: #f59e0b; }
.ps-desc { color: var(--text-primary, #ddd); line-height: 1.4; }

/* Tool Calls */
.tc-entry { padding: 2px 12px; cursor: pointer; border-bottom: 1px solid rgba(255,255,255,0.03); }
.tc-entry:hover { background: var(--bg-hover, #2a2a3e); }
.tc-entry.expanded { background: var(--bg-hover, #2a2a3e); }
.tc-summary { display: flex; align-items: center; gap: 6px; padding: 4px 0; }
.tc-icon { width: 14px; text-align: center; font-size: 11px; }
.tc-name { color: var(--text-primary, #ddd); font-weight: 500; flex: 1; }
.tc-status { font-size: 11px; color: var(--text-muted, #888); }
.tc-detail { padding: 4px 0 8px 20px; }
.tc-block { margin-bottom: 4px; }
.tc-label { font-size: 10px; text-transform: uppercase; color: var(--text-muted, #888); margin-bottom: 2px; }
.tc-pre { font-size: 11px; color: var(--text-primary, #ddd); background: rgba(0,0,0,0.2);
  padding: 4px 6px; border-radius: 4px; overflow-x: auto; max-height: 120px;
  white-space: pre-wrap; word-break: break-all; margin: 0; }
.tc-error-block .tc-pre { color: #ef4444; }

/* Workspace */
.ws-entry { display: flex; align-items: center; gap: 4px;
  padding: 3px 12px; font-size: 12px; }
.ws-icon { flex-shrink: 0; width: 16px; text-align: center; }
.ws-name { color: var(--text-primary, #ddd); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ws-link { color: var(--accent, #3b82f6); text-decoration: none; cursor: pointer; }
.ws-link:hover { text-decoration: underline; }
.ws-size { font-size: 10px; color: var(--text-muted, #888); flex-shrink: 0; }
</style>
