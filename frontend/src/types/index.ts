export interface ModelConfig {
  base_url: string
  api_key: string
  model_name: string
  temperature: number
  max_tokens: number
  config_name?: string
}

export interface ConfigStatus {
  configured: boolean
  model_name: string
  model_type: string
  config_name?: string
  pending_required?: SlotInfo[]
}

export interface ResourceItem {
  name: string
  description?: string
  source: 'system' | 'user'
  model_type?: string
  model_name?: string
  readonly?: boolean
  configured?: boolean
  required?: boolean
  depends_on?: DepInfo[]
  config_template?: Record<string, FormField>
  config_page?: string
}

export interface ResourceMap {
  models: ResourceItem[]
  tools: ResourceItem[]
  skills: ResourceItem[]
}

export interface DepInfo {
  type: string
  name: string
  description?: string
}

export interface FormField {
  label: string
  type: 'string' | 'password' | 'number' | 'select'
  required: boolean
  placeholder?: string
  default?: any
  enum?: string[]
}

export interface SlotInfo {
  name: string
  type: 'model' | 'tool' | 'skill'
  description: string
  required: boolean
  depends_on: DepInfo[]
  config_template: Record<string, FormField>
  model_type?: string
}

export interface SessionInfo {
  id: string
  title: string
  created_at: string
  updated_at?: string | null
  ended_at?: string | null
  message_count: number
  turn_count?: number
  json_size_mb?: number
}

export interface ActiveSession {
  id: string
  title: string
  created_at: string
  message_count: number
  fast_model_configured?: boolean
}

export interface ArchivedSession {
  id: string
  title: string
  created_at: string
  ended_at: string
  message_count: number
  messages?: ChatMessage[]
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system' | 'tool_call' | 'tool_result'
  content: string
  reasoning_content?: string
  tool_calls?: {
    id: string
    type: string
    function: { name: string; arguments: string }
  }[]
  tool_call_id?: string
  name?: string
  arguments?: string
}

export interface UploadResult {
  ok: boolean
  path: string
  filename: string
  size: number
  content_type: string
  preview: string
}

export interface Attachment {
  filename: string
  size: number
  path: string
  preview: string
  content_type: string
  error: string
}

export type SSEEvent =
  | { type: 'chunk'; content?: string; reasoning?: string }
  | { type: 'tool_call'; name?: string; tool?: string; arguments?: string; id: string }
  | { type: 'tool_result'; tool?: string; id: string; result: string }
  | { type: 'registration_required'; registration_id: string; template: Record<string, FormField>; resource_type: string; resource_name: string }
  | { type: 'done'; response?: string; history?: ChatMessage[]; title?: string; session_id?: string }
  | { type: 'error'; detail?: string }

export interface DisplayMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  thinking?: string
  toolCalls?: ToolCallRecord[]
}

export interface ToolCallRecord {
  id: string
  name: string
  arguments: string
  status: 'executing' | 'completed' | 'failed'
  result?: string
  error?: string
}

export interface ProjectInfo {
  workspace: string
  system_resources: string
}

export interface AuthUser {
  id: number
  username: string
}

export interface AuthResult {
  token: string
  user: AuthUser
}

export interface TraceEvent {
  id: number
  session_id: string
  turn: number
  node: string
  model?: string
  tool_name?: string
  duration_ms?: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  status: string
  error_msg?: string
  metadata?: string
  created_at: string
}

export interface TraceEventGroup {
  turn: number
  events: TraceEvent[]
  classify?: TraceEvent
  modelCall?: TraceEvent
  toolCalls: TraceEvent[]
  hooks: TraceEvent[]
  respond?: TraceEvent
  recovery?: TraceEvent
}

export interface TraceMetadataCallModel {
  finish_reason?: string
  has_tool_calls?: boolean
  model_input_snippet?: string
  model_output_snippet?: string
  status_code?: number
}

export interface TraceMetadataExecuteTools {
  tool_input_snippet?: string
  tool_output_snippet?: string
  consecutive_failures?: number
}

export interface TraceMetadataHook {
  hook_event?: string
  hook_status?: string
  hook_message?: string
}

export interface TraceMetadataRespond {
  response_snippet?: string
  truncated?: boolean
}

export interface TraceMetadataRecovery {
  recovery_type?: string
  continuation_count?: number
  error_snippet?: string
}

export interface TraceMetadataClassify {
  classification?: string
  resolved_model?: string
  skipped?: boolean
  reason?: string
}

export interface TraceMetadataAny {
  finish_reason?: string
  has_tool_calls?: boolean
  model_input_snippet?: string
  model_output_snippet?: string
  status_code?: number
  tool_input_snippet?: string
  tool_output_snippet?: string
  tool_category?: string
  consecutive_failures?: number
  hook_event?: string
  hook_status?: string
  hook_message?: string
  response_snippet?: string
  truncated?: boolean
  recovery_type?: string
  continuation_count?: number
  error_snippet?: string
  classification?: string
  resolved_model?: string
  skipped?: boolean
  reason?: string
  [key: string]: any
}

/** @deprecated use TraceMetadataAny */
export type ParsedTraceMetadata = TraceMetadataAny

export interface TraceSession {
  session_id: string
  username: string
  started_at: string
  ended_at: string
  event_count: number
  total_tokens: number
  total_duration_ms: number
  title?: string
}

export interface TraceSummary {
  total_events: number
  total_sessions: number
  total_tokens: number
  total_duration_ms: number
  thumbs_up: number
  thumbs_down: number
  total: number
}

export interface FeedbackItem {
  id: number
  session_id: string
  message_index: number
  rating: number
  feedback_text?: string
  created_at: string
}

export interface ResourceStat {
  name: string
  call_count: number
  success_count: number
  failure_count: number
  avg_duration_ms: number
}

export interface ResourceDailyStat {
  day: string
  call_count: number
  success_count: number
  failure_count: number
  avg_duration_ms: number | null
}
