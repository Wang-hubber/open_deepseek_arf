# ARF 逐任务开发工作流

> 从任务 1.1 实战中提炼的方法论。逐任务、逐行审核、边界优先。

## 路线

```
Phase spec (宏观设计)
  └─ Task doc (逐行解释 + 测试)
       ├─ 用户精校 → 补充/修正/加强
       └─ 用户批准 → 写代码 → cargo test → push
```

## 核心原则

### 1. Doc before code

- **先写 task 文档**（`docs/v1.x/task-X.Y-<name>.md`），包含完整代码 + 逐行解释 + 所有测试
- 用户审核通过后，代码只是文档的翻译——不再构思，只做机械转录
- 文档永远比代码先 push，让用户在 Gitee 上精校

### 2. Push for review

- **每次修改立即 commit + push**，不让用户对着对话窗口审核
- 用户在 Gitee 上逐行精校，给出修改意见
- 修改后再次 push，循环直到批准

### 3. Boundary-first testing

测试按角度标注，每种角度系统性覆盖：

| 标签 | 覆盖内容 |
|------|---------|
| `[构造]` | 正常输入构造、字段默认行为、Into trait |
| `[方法]` | 实例方法返回值正确性 |
| `[边界]` | 空串、零值、超长、Unicode、自指、null、空容器 |
| `[trait]` | Display/Debug/Clone/Hash/Eq/Ord/PartialEq/Error |
| `[序列化]` | serde 往返、to/from 一致性 |
| `[唯一性]` | UUID/ID 不重复 |
| `[时间]` | timestamp 单调性 |
| `[兼容]` | 旧版本/外部 JSON 反序列化 |
| `[类型]` | 不同 variant/node_type 的分类行为 |
| `[覆盖]` | 枚举所有变体编译验证 |

每个测试上方一行注释标注角度：`// [边界] 描述`

### 4. One question at a time

- 每轮审核只改一个问题
- 用户逐条提意见，逐条改，不批量处理
- 每改完一条 commit + push

### 5. Verify before claim

- 代码写完立即 `cargo test --workspace`，确认全 workspace 无回归
- 从不口头声称"通过了"——贴命令输出为证

## 何时使用

当接到 "实施 Phase X 的任务 Y" 指令时，自动进入此工作流。用户说 "下一个任务" 时继续下一项。
