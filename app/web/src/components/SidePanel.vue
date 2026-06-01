<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useChat } from '@/composables/useChat'
import { useApi } from '@/composables/useApi'

const { toolCalls, streamingReasoning, streamingText } = useChat()

// ---- Planner running state (declared before watches that reference it) ----
const plannerRunning = ref(false)
const plannerLiveText = ref('')

watch([streamingReasoning, streamingText], () => {
  if (plannerRunning.value) {
    const parts: string[] = []
    if (streamingReasoning.value) parts.push(streamingReasoning.value)
    if (streamingText.value) parts.push(streamingText.value)
    plannerLiveText.value = parts.join('\n')
  }
})
watch(plannerRunning, (running) => {
  if (!running) plannerLiveText.value = ''
})
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

// ---- Watch tool calls for planner updates ----
watch(toolCalls, (calls) => {
  const list = calls || []
  const running = list.some((tc: any) => tc.name === 'planner' && tc.status === 'executing')
  if (running) plannerRunning.value = true
  for (const tc of list) {
    if (tc.status !== 'completed' || !tc.result) continue
    parseToolResult(tc.name, tc.result)
  }
  if (!list.some((tc: any) => tc.name === 'planner' && tc.status === 'executing')) {
    plannerRunning.value = false
  }
}, { deep: true })

// ---- Tool Calls (sync from useChat toolCalls ref) ----
const expandedToolId = ref('')
const toolCallList = computed(() => toolCalls.value || [])
function toggleTool(id: string) {
  expandedToolId.value = expandedToolId.value === id ? '' : id
}
function formatArgs(args: string): string {
  try { return JSON.stringify(JSON.parse(args), null, 2) } catch { return args }
}

// ---- Workspace files (tree structure) ----
interface FileEntry {
  name: string; type: 'file' | 'dir'; path: string; size?: number; suffix?: string; children?: FileEntry[]
}
const workspaceTree = ref<FileEntry[]>([])
const workspaceRoot = ref('')
let filePollTimer: any = null

async function loadFiles() {
  try {
    const data = await api.get<{ ok: boolean; root: string; files: FileEntry[] }>('/api/files')
    if (data?.ok) {
      workspaceRoot.value = data.root || ''
      workspaceTree.value = buildTree(data.files || [])
    }
  } catch {}
}

function buildTree(flat: FileEntry[]): FileEntry[] {
  const dirs = new Map<string, FileEntry>()
  const roots: FileEntry[] = []
  for (const f of flat) {
    f.children = []
    dirs.set(f.path, f)
    const parentPath = f.path.includes('/') ? f.path.substring(0, f.path.lastIndexOf('/')) : ''
    if (parentPath && dirs.has(parentPath)) {
      dirs.get(parentPath)!.children!.push(f)
    } else {
      roots.push(f)
    }
  }
  // Only keep top-level dirs with children
  return roots.filter(f => {
    if (f.type === 'dir' && (!f.children || f.children.length === 0)) return false
    return true
  })
}

function fileUrl(entry: FileEntry): string {
  const previewable = ['.md', '.txt', '.html', '.htm', '.json', '.csv', '.yaml', '.yml', '.py', '.log']
  const isPreview = previewable.includes(entry.suffix || '')
  return `/api/files/${encodeURIComponent(entry.path)}${isPreview ? '' : '?download=1'}`
}

const collapsedDirs = ref<Set<string>>(new Set())

function toggleDir(path: string) {
  if (collapsedDirs.value.has(path)) collapsedDirs.value.delete(path)
  else collapsedDirs.value.add(path)
}
function isCollapsed(path: string) { return collapsedDirs.value.has(path) }

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
        <div v-if="plannerRunning" class="sp-running">
          <div class="sp-running-title">⏳ Planner 正在编制计划...</div>
          <div v-if="plannerLiveText" class="sp-reasoning">{{ plannerLiveText.slice(-800) }}</div>
        </div>
        <div v-if="!planSteps.length && !plannerRunning" class="sp-empty">等待计划生成...</div>
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
        <span v-if="toolCallList.length" class="sp-badge">{{ toolCallList.length }}</span>
        <span class="sp-arrow">{{ toolsOpen ? '▾' : '▸' }}</span>
      </div>
      <div v-if="toolsOpen" class="sp-body">
        <div v-if="!toolCallList.length" class="sp-empty">等待工具调用...</div>
        <div v-for="tc in toolCallList.slice(-20).reverse()" :key="tc.id" class="tc-entry"
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
        <div v-if="!workspaceTree.length" class="sp-empty">加载中...</div>
        <template v-for="f in workspaceTree" :key="f.path">
          <WorkspaceNode :entry="f" :collapsed-dirs="collapsedDirs"
            @toggle-dir="toggleDir" :file-url="fileUrl" :is-collapsed="isCollapsed" />
        </template>
      </div>
    </section>
  </aside>
</template>

<!-- WorkspaceNode: recursive tree node -->
<script lang="ts">
import { defineComponent, h, type PropType } from 'vue'

interface FileEntry {
  name: string; type: 'file' | 'dir'; path: string; size?: number; suffix?: string; children?: FileEntry[]
}

export const WorkspaceNode = defineComponent({
  name: 'WorkspaceNode',
  props: {
    entry: { type: Object as PropType<FileEntry>, required: true },
    collapsedDirs: { type: Object as PropType<Set<string>>, required: true },
    fileUrl: { type: Function as PropType<(f: FileEntry) => string>, required: true },
    isCollapsed: { type: Function as PropType<(p: string) => boolean>, required: true },
    depth: { type: Number, default: 0 },
  },
  emits: ['toggle-dir'],
  setup(props, { emit }) {
    return () => {
      const f = props.entry
      const collapsed = props.isCollapsed(f.path)
      const indent = props.depth * 12
      const sizeStr = f.size ? (f.size > 1024 ? `${(f.size / 1024).toFixed(1)}KB` : `${f.size}B`) : ''

      const nodes: any[] = []

      if (f.type === 'dir') {
        nodes.push(h('div', {
          class: 'ws-entry ws-dir',
          style: { paddingLeft: `${8 + indent}px` },
          onClick: () => emit('toggle-dir', f.path),
        }, [
          h('span', { class: 'ws-icon' }, collapsed ? '📁' : '📂'),
          h('span', { class: 'ws-name' }, f.name),
          h('span', { class: 'ws-arrow' }, collapsed ? '▸' : '▾'),
        ]))
        if (!collapsed && f.children) {
          for (const child of f.children) {
            nodes.push(h(WorkspaceNode, {
              entry: child, collapsedDirs: props.collapsedDirs,
              fileUrl: props.fileUrl, isCollapsed: props.isCollapsed,
              depth: props.depth + 1,
              onToggleDir: (p: string) => emit('toggle-dir', p),
            }))
          }
        }
      } else {
        nodes.push(h('div', {
          class: 'ws-entry ws-file',
          style: { paddingLeft: `${8 + indent}px` },
        }, [
          h('span', { class: 'ws-icon' }, '📄'),
          h('a', { class: 'ws-name ws-link', href: props.fileUrl(f), target: '_blank' }, f.name),
          sizeStr ? h('span', { class: 'ws-size' }, sizeStr) : null,
        ]))
      }

      return nodes
    }
  },
})
</script>

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
.sp-running { padding: 8px 12px; }
.sp-running-title { color: var(--accent, #3b82f6); text-align: center; animation: pulse 1.5s ease infinite; margin-bottom: 6px; }
.sp-reasoning { font-size: 11px; color: var(--text-muted, #999); line-height: 1.4; max-height: 120px; overflow-y: auto;
  white-space: pre-wrap; word-break: break-word; background: rgba(0,0,0,0.15); padding: 6px 8px; border-radius: 4px; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }

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
.ws-dir { cursor: pointer; user-select: none; }
.ws-dir:hover { background: var(--bg-hover, #2a2a3e); }
.ws-icon { flex-shrink: 0; width: 16px; text-align: center; }
.ws-name { color: var(--text-primary, #ddd); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ws-link { color: var(--accent, #3b82f6); text-decoration: none; cursor: pointer; }
.ws-link:hover { text-decoration: underline; }
.ws-size { font-size: 10px; color: var(--text-muted, #888); flex-shrink: 0; }
.ws-arrow { font-size: 10px; color: var(--text-muted, #888); flex-shrink: 0; }
</style>
