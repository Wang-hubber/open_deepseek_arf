# ARF Explanation — [Reserved]

> 💭 Diátaxis 桶位：**Explanation** — 设计哲学、技术选型、关键决策背后的"为什么"。
> 当前为空，本桶保留给系统级的设计讨论散文。

当前 ARF 的 explanation 性内容散落在两个地方，临时想理解"为什么"可以先看这两处：

- [`README.md` — 设计意图与三层模型](../../README.md) — Agent / Engine / Bus Actors 三层通信模型的来由与边界
- [`docs/dev/v1.x-design.md`](../../dev/v1.x-design.md) — V1.x 设计草案，原始设计意图与决策
- [`docs/dev/phase*/`](../../dev/) — 各 Phase 的设计文档，含 trade-off 与取舍记录

未来 explanation 桶会聚合成"独立于具体实现的散文"，讨论诸如：
- 为什么选 CAN 总线模型而不是 P2P 路由？
- 5 个 Checkpoint trigger 怎么从 10 个 v0.x lifecycle hook 演化来的？
- Engine 为什么要做 Bus node 而不是中央调度器？

需要模块 API 参考请走 [`docs/api/reference/`](../reference/)。
