# Frontend Session Sync — 后端升级后的前端适配

## Context

后端 Session 管理做了三项升级：
1. 会话 ID 加微秒精度 (22 字符)
2. 移除 MAX_ARCHIVES=10 限制
3. 软删除 (hidden=1, 归档文件保留)

前端需要同步适配。审计发现 5 项待改：2 项必须修、2 项建议修、1 项可增强。

## Changes

### 1. 移除 10 会话上限

**SessionPanel.vue**: 删除 `handleCreate` 中 `total >= 10` 的门禁检查及 `confirm()` 弹窗。
**stores/sessions.ts**: 删除 `totalCount()` 函数。
**locales**: 删除 `sessionLimit` 的 i18n 文案 (zh-CN + en-US)。

### 2. 空会话刷新丢失 → 纯前端占位

**stores/sessions.ts** — `createSession()`:
- 不再调用 `POST /api/sessions`
- 改为直接设置 `pendingNewSession = true` + 清空 `activeSession`

**ChatLayout.vue** — `onMounted`:
- 若 `activeSession` 为空且无归档查看状态，自动调用 `startNewSession()` 进入占位模式
- 用户发第一条消息时走惰性创建路径 (`new_session: true`) → SSE done 带回真实 ID

### 3. 删除确认文案

**locales**: `"删除后无法撤销"` → `"删除会话"` (zh-CN) / `"Delete session"` (en-US)

### 4. Session 列表排序

**SessionPanel.vue** — `combinedItems()`:
- archive 部分按 `updated_at DESC` 排序，最近使用的会话排前面

### 5. TraceView hook 事件默认渲染

**TraceView.vue**:
- 当前只渲染 `PreToolUse` / `PostToolUse` 两种 hook
- 增加 `v-else` 默认分支，渲染其他 hook 事件 (PreModelCall, PostModelCall, SessionEnd 等)

## Files Modified

| File | Change |
|------|--------|
| `frontend/src/components/SessionPanel.vue` | Remove limit check, sort archives, simplify createSession |
| `frontend/src/stores/sessions.ts` | Remove totalCount(), simplify createSession() |
| `frontend/src/views/ChatLayout.vue` | Auto-enter placeholder on mount when no active session |
| `frontend/src/views/TraceView.vue` | Default hook event rendering |
| `frontend/src/locales/zh-CN.json` | Update session i18n strings |
| `frontend/src/locales/en-US.json` | Update session i18n strings |

## Verification

1. 点击"新建会话"不再调 API，聊天区立即清空
2. 快速创建 15 个会话，无上限弹窗
3. 页面刷新后自动显示"新会话"占位
4. 删除会话配置文案为 "删除会话" (zh) / "Delete session" (en)
5. TraceView 显示完整的 hook 事件列表
6. `npm run build` 无 TypeScript 错误
