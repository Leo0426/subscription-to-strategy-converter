# Issue Tracker

此仓库的 issues 和 PRD 以 Markdown 文件形式存放在 `.scratch/` 目录下。

## 约定

- 每个功能一个目录：`.scratch/<feature-slug>/`
- PRD 文件：`.scratch/<feature-slug>/PRD.md`
- 实现 issues：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 开始编号
- Triage 状态记录在每个 issue 文件顶部附近的 `Status:` 行
- Issue 类别记录在 `Labels:` 行；至少包含 `bug` 或 `enhancement` 之一
- 评论和对话历史追加到文件底部的 `## 评论` 标题下

## 发布和读取

当 skill 要求“发布到 issue tracker”时，在 `.scratch/<feature-slug>/` 下创建对应文件。

当 skill 要求“获取相关工单”时，读取用户给出的路径或 issue 编号。

## Wayfinding operations

- 地图：`.scratch/<effort-slug>/MAP.md`，标注 `Labels: wayfinder:map`
- 子任务：`.scratch/<effort-slug>/issues/<NN>-<slug>.md`
- 子任务类型：`wayfinder:research`、`wayfinder:prototype`、`wayfinder:grilling` 或 `wayfinder:task`
- 认领：使用 issue 顶部的 `Assignee:` 行
- 依赖：使用 `Blocked-by: <NN>, <NN>` 行
- Frontier：筛选状态开放、依赖已解决且 `Assignee:` 为空的任务，按编号排序
- 解决：将 `Status:` 改为 `resolved`，在 `## Resolution` 写入结论，并向 `MAP.md` 的 Decisions so far 添加结果链接
