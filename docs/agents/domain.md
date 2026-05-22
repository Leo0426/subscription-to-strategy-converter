# 领域文档

工程类 skills 在探索代码库时，应如何读取此仓库的领域文档。

## 探索前，先读取这些
- 仓库根目录的 **`CONTEXT.md`**，或存在 **`CONTEXT-MAP.md`**，读取它——它指向每个上下文各自的 `CONTEXT.md`，读取与当前话题相关的那些。
- **`docs/adr/`** — 读取涉及你即将操作区域的 ADR。在多上下文仓库中，也检查 `src/<context>/docs/adr/` 中的上下文专属决策。
如果这些文件不存在，**静默继续**。不要标记它们的缺失；不要主动建议创建。生产类 skill（`grill`）会在术语或决策真正需要时懒加载创建它们。

## 文件结构
单一上下文仓库（大多数仓库）：

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

多上下文仓库（根目录存在 `CONTEXT-MAP.md`）：
```
/
├── CONTEXT-MAP.md
├── docs/adr/                          ← 系统级决策
└── src/
    ├── ordering/
    │   ├── CONTEXT.md
    │   └── docs/adr/                  ← 上下文专属决策
    └── billing/
        ├── CONTEXT.md
        └── docs/adr/
```

## 使用词汇表中的术语
当输出命名一个领域概念时（issue 标题、重构提案、假设、测试名称），使用 `CONTEXT.md` 中定义的术语。不要漂移到词汇表明确避免的同义词。

如果你需要的概念不在词汇表里，这是一个信号——要么你在发明项目不使用的语言（重新考虑），要么存在真正的空缺（记录下来，留给 `grill` 处理）。

## 标记 ADR 冲突
如果你的输出与现有 ADR 相矛盾，明确标注，而不是静默覆盖：
> _与 ADR-0007（事件溯源订单）冲突——但值得重新讨论，因为……_
