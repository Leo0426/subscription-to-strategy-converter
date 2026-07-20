# Triage Labels

每个已分诊的 issue 应包含一个类别角色和一个状态角色。

## 类别角色

| 规范角色      | 本地 label    | 含义             |
| ------------- | ------------- | ---------------- |
| `bug`         | `bug`         | 已有行为发生错误 |
| `enhancement` | `enhancement` | 新功能或改进     |

## 状态角色

| 规范角色          | 本地 label        | 含义             |
| ----------------- | ----------------- | ---------------- |
| `needs-triage`    | `needs-triage`    | 等待维护者评估   |
| `needs-info`      | `needs-info`      | 等待报告者补充信息 |
| `ready-for-agent` | `ready-for-agent` | Agent 可直接领取 |
| `ready-for-human` | `ready-for-human` | 需要人工处理     |
| `wontfix`         | `wontfix`         | 不予处理         |
| `resolved`        | `resolved`        | 已正常完成       |

## 状态流转

- 新 issue 从 `needs-triage` 开始
- `needs-info` 补齐后回到 `needs-triage`
- 只有 `ready-for-agent` 可由 Agent 领取
- `ready-for-agent` 和 `ready-for-human` 完成后进入 `resolved`
- `wontfix` 和 `resolved` 为终态
