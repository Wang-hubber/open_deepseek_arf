# multi_agent_team 示例 app

5 分钟上手：
  1. pip install -e .
  2. export DEEPSEEK_API_KEY=...
  3. python server.py
  4. curl -N http://localhost:8000/sse/team/default
  5. 在另一终端：curl -X POST http://localhost:8000/chat -d '{"message":"hi"}' -H 'Content-Type: application/json'

展示的 ARF 新设计能力：
  - Engine 单写 JSONL 持久化
  - SubagentPool 自动回收
  - Team YAML 声明
  - peer_message 跨 agent 通讯
  - SSE 实时事件流聚合
  - 崩溃 outbox resend