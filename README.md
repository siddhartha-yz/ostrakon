# Ostrakon

Ostrakon（陶片放逐）是一个 **AstrBot moderation plugin**，用于 QQ 群中的 reaction 投票式管理。

群成员给某条消息添加指定 reaction；当不同投票用户达到阈值后，Ostrakon 通过 OneBot v11 / NapCat 禁言**原消息发送者**。

Ostrakon 不再是独立 QQ Bot 框架。连接、重连、通用聊天、LLM、Agent、其他插件和平台生命周期全部交给 AstrBot；Ostrakon 只负责自己的群管理规则和持久化状态。

```text
QQ
 ↓
NapCat
 ↓ OneBot v11 Reverse WebSocket
AstrBot
 ├─ LLM / Agent / 通用 QQ Bot 能力
 ├─ 其他插件
 └─ Ostrakon
      ├─ reaction 投票
      ├─ 阈值与处罚状态机
      └─ SQLite
```

## 当前规则

默认策略：

- 只统计配置的目标 reaction。
- 同一条消息需要 **10 个不同 QQ 用户**的有效 reaction。
- 同一用户对同一消息最多贡献 1 票。
- 达到阈值后，同一条消息最多成功处罚一次。
- 首次处罚：禁言 10 分钟。
- 同一群内最近 7 天再次因 Ostrakon 被处罚：禁言 2 小时。
- 第 3 次及以后只要仍处于滚动 7 天窗口内，仍为 2 小时。
- 距离上一次成功处罚超过 7 天后恢复为 10 分钟。
- 群、消息、投票状态按 `group_id` 隔离。
- 群主、管理员和 Bot 自身不会被禁言。
- OneBot 调用失败不会被错误记录为成功处罚。

当前实测“愤怒机器人”的 `emoji_id` 为 `326`。reaction ID 属于 NapCat/QQNT 扩展行为，不应把这个值视为所有版本永久固定的标准。

## AstrBot / NapCat 要求

当前插件面向：

- AstrBot `>=4.27,<5`
- AstrBot `aiocqhttp` 平台适配器
- OneBot v11
- NapCat 提供 `group_msg_emoji_like` reaction notice 和相关扩展 API

NapCat 应作为 OneBot v11 客户端连接 AstrBot 的反向 WebSocket，例如同一 Docker 网络中：

```text
ws://astrbot:6199/ws
```

OneBot token、反向 WebSocket 地址和 QQ 登录状态属于 **AstrBot / NapCat 平台配置**，不再由 Ostrakon 保存或管理。

## 安装

推荐从 AstrBot WebUI 的插件管理安装本仓库：

```text
https://github.com/siddhartha-yz/ostrakon
```

也可以在 AstrBot 数据目录中手动安装：

```bash
cd data/plugins
git clone https://github.com/siddhartha-yz/ostrakon.git ostrakon
```

然后在 AstrBot 中重载插件。

插件运行数据写入 AstrBot 的标准插件数据目录：

```text
data/plugin_data/ostrakon/ostrakon.sqlite3
```

源码目录不会保存运行时 SQLite。

## 配置

仓库内 `_conf_schema.json` 会让 AstrBot WebUI 生成插件配置界面。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled_groups` | `[]` | 启用 Ostrakon 的 QQ 群号列表 |
| `target_reaction_id` | 空 | 目标 reaction / emoji ID；为空时仅诊断，不处罚 |
| `vote_threshold` | `10` | 不同投票用户阈值 |
| `first_mute_seconds` | `600` | 首次处罚时长 |
| `repeat_mute_seconds` | `7200` | 7 天内重复处罚时长 |
| `repeat_window_seconds` | `604800` | 重复处罚窗口 |

当 `target_reaction_id` 已配置时，`enabled_groups` 必须至少包含一个群，否则插件拒绝启用处罚逻辑。

## 管理员命令

在已启用群中，群主或管理员可以使用：

```text
/ostrakon status
```

返回当前 reaction、阈值、禁言策略和 SQLite 健康状态。

要清除某位成员的 7 天重复处罚历史，回复该成员的一条消息并发送：

```text
/ostrakon reset
```

这只影响该成员在当前群下一次处罚的时长计算：下一次重新按首次 10 分钟计算。它不会解除当前已经生效的禁言，也不会让已经处罚过的旧消息再次触发。

不使用裸 `/reset` 作为公开命令，因为 AstrBot 自身已经有 `/reset` 会话命令。

## reaction 事件与可靠计票

Ostrakon 依赖 NapCat 的扩展 notice：

```text
post_type = notice
notice_type = group_msg_emoji_like
```

插件只为这类 notice 注册 AstrBot `CustomFilter`，不会因为 Ostrakon 的存在把普通群消息额外唤醒到 LLM 管线。

收到目标 reaction 后：

1. 若 notice 提供可靠的 `user_id + is_add`，先做幂等增减票；
2. 调用 `get_emoji_likes` 获取当前 reaction 用户快照；
3. 以快照对齐 SQLite，补回短暂掉线期间可能遗漏的投票；
4. 快照不可用时，退回事件增量计票；
5. 达到阈值后，以 SQLite 事务原子领取一次处罚执行权。

标准/扩展 OneBot 操作包括：

- `get_msg`：解析被 reaction 的原消息发送者；
- `get_emoji_likes`：读取当前 reaction 用户列表；
- `get_group_member_info`：执行前检查目标和 Bot 权限；
- `set_group_ban`：执行禁言；
- `send_group_msg`：管理员命令反馈。

## 状态与幂等

SQLite 保存：

- 每条消息的处罚状态；
- 每个投票用户当前有效 reaction 状态；
- 每个群内用户最近一次成功处罚时间；
- 禁言 API 尝试次数和失败原因。

消息状态：

```text
collecting → mute_pending → punished
                    ↘ mute_failed → 后续事件可重试
                    ↘ mute_exhausted
                    ↘ ineligible
```

单条消息的 `set_group_ban` 最多自动尝试 3 次。成功后永久进入 `punished`，插件/AstrBot 重启后不会再次处罚同一 `(group_id, message_id)`。

SQLite 使用 WAL、唯一约束和 `BEGIN IMMEDIATE` 事务，避免并发达到阈值时重复执行处罚。

## 从独立 Ostrakon Bot 迁移

`0.2.0` 起，仓库定位正式变为 AstrBot 插件。旧架构：

```text
NapCat → 独立 Ostrakon WebSocket Client → SQLite
```

新架构：

```text
NapCat → AstrBot → Ostrakon Plugin → SQLite
```

因此以下职责已经从 Ostrakon 移除：

- 独立 OneBot WebSocket 连接与重连；
- 独立 Bot 主循环；
- Ostrakon 自己的 Docker Bot 镜像/Compose；
- `ONEBOT_WS_URL` / `ONEBOT_ACCESS_TOKEN` 等传输层环境变量。

旧版 SQLite **不会自动复制**到 AstrBot 的插件数据目录。生产迁移时应在停掉旧 Ostrakon Bot 后复制数据库文件，再启用插件，避免两个进程同时处理相同 reaction 事件。

## 开发与测试

核心业务层不依赖 AstrBot，因此大部分状态机测试仍可独立运行：

```bash
python -m pip install -e '.[dev]'
ruff check main.py ostrakon tests
pytest -q
```

发布前还应使用真实 AstrBot 版本做插件加载冒烟测试，确认：

- `metadata.yaml` 可读取；
- `_conf_schema.json` 可生成配置；
- `main.py` 可加载；
- OneBot `notice` 能进入 reaction handler；
- 普通消息不会被 Ostrakon 的 reaction handler 唤醒；
- `get_msg` / `get_emoji_likes` / `set_group_ban` 等调用可由 AstrBot 的 aiocqhttp 连接转发。

## 安全边界

- 处罚仅在 `enabled_groups` 白名单中启用；
- 禁言前重新检查目标成员和 Bot 权限；
- 不处罚群主、管理员或 Bot 自身；
- 普通群员不能使用 Ostrakon 管理员命令；
- 不通过聊天修改 reaction、阈值或群白名单；
- SQLite 和 AstrBot runtime data 不进入 Git；
- 日志不应记录 access token、cookie、密码或完整聊天内容。

## 已知限制

1. QQ reaction 是 NapCat/QQNT 扩展能力，不属于 OneBot v11 核心标准，行为可能随 NapCat/QQ 版本变化。
2. 如果平台根本不推送其他成员的 reaction notice，Ostrakon 无法仅靠插件层可靠触发投票机制。
3. `set_group_ban` 没有业务幂等键；Ostrakon 能保证本地状态机只领取一次处罚，并在 API 错误时核验目标禁言状态，但无法在极端网络故障中实现严格的分布式 exactly-once。
4. 当前只定位为 moderation plugin，不提供通用 AI 聊天、知识库、Agent 或 Web 管理能力；这些由 AstrBot 及其他插件负责。
