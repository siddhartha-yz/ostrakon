# Ostrakon

Ostrakon（陶片放逐法）是一个极小的 QQ 群管理 Bot：群成员对某条群消息使用指定 reaction 投票，达到阈值后，Bot 自动禁言**原消息发送者**。

当前项目只实现这一条规则，不包含 AI 审核、关键词审核、Web 管理后台、积分、欢迎消息或其他群管理功能。

另外提供两个窄范围管理员命令：`/ostrakon status` 用于只读健康检查；回复某个成员的消息发送 `/reset`，可清除该成员在当前群的 7 天重复处罚记录。两者都只在 `ENABLED_GROUPS` 中生效，且仅群主/管理员可用。

## 规则

默认策略：

- 只统计配置的目标 reaction（“愤怒机器人”的实际 ID 需要在真实 QQ/NapCat 环境中确认）。
- 同一条消息需要 5 个不同 QQ 用户的有效 reaction。
- 同一用户对同一消息最多贡献 1 票。
- 达到阈值后，同一条消息最多成功处罚一次。
- 首次处罚：禁言 600 秒。
- 同一群内，同一用户最近 7 天曾因 Ostrakon 成功被处罚：禁言 7200 秒；第 3 次及以后只要仍处于滚动 7 天窗口内，也保持 7200 秒。
- 距离上一次成功处罚超过 7 天后恢复为 600 秒。
- 群、消息、投票状态全部按 group_id 隔离。
- 群主、管理员、Bot 自身不会被禁言。

架构：

```text
QQ → NapCat → OneBot 11 WebSocket → Ostrakon → SQLite
```

## 为什么没有使用 NoneBot2

本项目只有一个规则，并且依赖 NapCat 的 OneBot 11 扩展事件 `group_msg_emoji_like`。直接使用一个很小的 WebSocket 客户端可以完整保留 NapCat 的扩展字段（尤其是 `user_id` 和 `is_add`），同时减少框架层转换和依赖。

标准操作仍通过 OneBot API 完成：

- `get_msg`：根据 message_id 获取原消息和发送者；
- `get_group_member_info`：禁言前重新检查 Bot 与目标成员权限；
- `set_group_ban`：执行群禁言；
- `get_emoji_likes`：收到目标 reaction 事件时拉取当前 reaction 用户列表做权威对账；若快照调用失败，再退回事件增量计票。

## NapCat reaction 调查结果

调查时间：2026-08-08。

当前 NapCat 主分支源码（检查到 commit `0fcd0e80e07b85deeed24d85e09e62cbfdb2dfe3`，提交时间 2026-08-07）中：

- 群 reaction 的底层 push 会解析 group、操作者 UID、emoji code、消息 seq、增/减状态和计数；
- OneBot 事件类型为 `notice_type = group_msg_emoji_like`；
- 事件结构包含 `group_id`、`user_id`、`message_id`、`likes[]`、`is_add`；
- `likes[]` 中至少包含 `emoji_id` 与 `count`；
- `get_emoji_likes` 可以按 group/message/emoji 拉取当前点击者 QQ 列表；
- `get_msg` 返回原消息 `user_id`，并可返回 `emoji_likes_list`；
- `set_group_ban(group_id, user_id, duration)` 为官方 OneBot API。

需要特别注意：NapCat 官方“事件兼容情况”页面目前仍写着 `group_msg_emoji_like` “仅收自己的，其余扩展接口拉取”。这与当前主分支源码的 Packet reaction 解析能力存在文档/实现版本差异，因此仍应以实际部署版本为准。

本项目部署时已在 **NapCat 4.18.18** 上做真机验证：另一名群成员对普通群消息添加目标 reaction 后收到了 `is_add=true` 事件，撤销同一 reaction 后收到了同一 group/message/emoji 的 `is_add=false` 事件；`get_emoji_likes` 在撤销后也返回 0 个当前用户，与事件状态一致。此次实测中 QQ 客户端“愤怒机器人”的 `emoji_id` 为 **`326`**。不同版本仍建议按下文诊断流程重新确认，不依赖猜测。

Ostrakon 不伪造撤销支持：

- 若事件提供可靠 `user_id + is_add`，先做幂等增减票，再调用 `get_emoji_likes` 用当前投票者快照对齐 SQLite，从而恢复掉线期间漏掉的票；
- 若事件缺少可靠操作者/增减字段，则直接尝试 `get_emoji_likes` 快照对齐；
- 若实际 NapCat 版本根本不向 Bot 推送其他成员的 reaction 变化，则无法仅靠事件可靠触发该机制，必须升级/调整 NapCat，而不是假装支持。

## 配置

复制空模板：

```bash
cp .env.example runtime.env
```

`runtime.env` 不进入 Git。

可用变量及默认值：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ACCOUNT` | 空 | NapCat 快速登录 QQ 号；仅写入本地 `runtime.env` |
| `ONEBOT_WS_URL` | `ws://napcat:3001` | NapCat 正向 WebSocket 服务地址 |
| `ONEBOT_ACCESS_TOKEN` | 空 | NapCat OneBot token；如设置，两端必须一致 |
| `ENABLED_GROUPS` | 空 | 允许处理的群号，多个用英文逗号分隔 |
| `TARGET_REACTION_ID` | 空 | 目标 reaction/emoji ID |
| `VOTE_THRESHOLD` | `5` | 不同投票用户阈值 |
| `FIRST_MUTE_SECONDS` | `600` | 首次处罚时长 |
| `REPEAT_MUTE_SECONDS` | `7200` | 7 天内重复处罚时长 |
| `REPEAT_WINDOW_SECONDS` | `604800` | 重复处罚窗口 |
| `DATABASE_PATH` | `/data/ostrakon.sqlite3` | SQLite 路径 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

当 `TARGET_REACTION_ID` 已配置时，`ENABLED_GROUPS` 必须至少包含一个群，否则 Bot 拒绝启动。

## 如何确定“愤怒机器人” reaction ID

不要猜 ID。

首次部署时保持：

```text
TARGET_REACTION_ID=
ENABLED_GROUPS=
```

此时 Ostrakon 进入**只诊断、不处罚**模式。它只记录 reaction 的：

- emoji ID；
- 可取得的 emoji type；
- message_id；
- group_id；
- is_add。

不会记录完整聊天内容。

在你准备启用 Ostrakon 的目标群中，对任意消息贴一次“愤怒机器人”，然后查看：

```bash
docker compose logs -f bot
```

确认实际 `emoji_id` 和 `group_id` 后，把它们写入本地 `runtime.env` 的 `TARGET_REACTION_ID` 和 `ENABLED_GROUPS`，再重启 Bot。之后 INFO 日志不再输出所有未知 reaction 的诊断详情。

## Docker Compose 部署

要求：Docker Engine + Docker Compose v2。

项目使用官方 NapCat Docker 镜像：`mlikiowa/napcat-docker:latest`。

Compose 只把 NapCat WebUI 绑定到宿主机回环地址：

```text
127.0.0.1:6099
```

远程服务器上建议通过 SSH 隧道访问，而不是把 WebUI 暴露公网：

```bash
ssh -L 6099:127.0.0.1:6099 ubuntu@your-server
```

然后浏览器访问本机 `http://127.0.0.1:6099/webui`。

首次只启动 NapCat：

```bash
docker compose up -d napcat
docker compose logs -f napcat
```

完成首次 QQ 扫码登录后，把该 Bot QQ 号写入本地 `runtime.env` 的 `ACCOUNT`。NapCat Docker 镜像会在后续容器重启时使用 `-q ACCOUNT` 快速登录，避免重复扫码。

然后在 NapCat WebUI 中创建/启用 **WebSocket 服务端（正向 WS）**，监听容器内 `0.0.0.0:3001`。如果设置 OneBot token，把同一个值放入本地 `runtime.env` 的 `ONEBOT_ACCESS_TOKEN`。

Bot 与 NapCat 在 Compose 私有 bridge 网络中通信，不需要把 3001 暴露到宿主机公网。

启动整个项目：

```bash
docker compose up -d --build
```

停止：

```bash
docker compose down
```

查看日志：

```bash
docker compose logs -f napcat
docker compose logs -f bot
```

Compose 使用 `restart: unless-stopped`，SSH 断开不影响运行，Docker daemon/服务器重启后容器会自动恢复。

## 管理员命令

在已启用的群中，群主或管理员发送：

```text
/ostrakon status
```

Bot 会返回当前是否激活、reaction ID、票数阈值、禁言时长、SQLite 状态和 OneBot 连接状态。

若要清除某个成员的 7 天重复处罚记录，**回复该成员的一条消息**并发送：

```text
/reset
```

成功后，该成员下一次达到处罚阈值会重新按首次处罚 10 分钟计算。`/reset` 只影响当前群的重复处罚记录，不解除已经生效的禁言，也不会让已经成功处罚过的旧消息再次触发。普通群员和未启用群不能使用这些命令。

## SQLite 状态

默认容器内数据库：

```text
/data/ostrakon.sqlite3
```

Compose 使用 Docker named volume `ostrakon-data` 持久化 `/data`。这样 Bot 可以保持非 root 运行，同时避免宿主机目录的 UID/GID 权限问题。

SQLite 至少保存：

- 每条消息的处罚状态；
- 每个投票用户的当前有效 reaction 状态；
- 每个群内用户最近一次成功处罚时间；
- 禁言 API 尝试次数与失败原因。

消息状态区分：

```text
collecting → mute_pending → punished
                    ↘ mute_failed → 可在后续事件再次尝试
                    ↘ mute_exhausted
                    ↘ ineligible
```

单条消息的实际 `set_group_ban` 最多自动尝试 3 次；成功后永久进入 `punished`，Bot 重启也不会再次处罚同一 `(group_id, message_id)`。

SQLite 使用 WAL、唯一约束和 `BEGIN IMMEDIATE` 事务。第 5/6 票并发时只能有一个事件把消息从可处理状态原子切换成 `mute_pending`，因此不会并发重复调用禁言。

## 本地开发与测试

标准 Python 环境：

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check src tests
pytest -q
```

如果宿主机没有 pip/venv，也可以在一次性 Python 容器中跑：

```bash
docker run --rm -v "$PWD:/app" -w /app python:3.12-slim sh -lc \
  'python -m pip install -q "websockets>=15,<16" "pytest>=8.3,<9" "pytest-asyncio>=0.24,<1" && PYTHONPATH=src python -m pytest -q'
```

测试完全 mock OneBot/NapCat，不要求真实 QQ 在线。

## 权限与安全

- moderation 开启后只处理 `ENABLED_GROUPS` 白名单中的群；
- 禁言前重新读取目标成员和 Bot 的群角色；
- 不禁言群主、管理员、Bot 自身；
- OneBot 调用失败不会被记录成成功处罚；
- API 失败后会检查目标当前禁言状态，避免“实际成功但响应丢失”时错误记录失败；
- 只实现 `/ostrakon status` 与回复式 `/reset` 两个窄范围管理员命令；普通成员不能使用，也不能通过聊天修改配置；
- `runtime.env`、QQ 登录数据、NapCat runtime 配置、SQLite、日志均不进入 Git；
- 日志不记录 access token、cookie、密码或完整聊天内容。

## 已知限制

1. QQ reaction 是 NapCat/QQNT 的扩展能力，不是 OneBot 11 核心标准；不同 NapCat/QQNT 版本可能改变可见事件。
2. 当前官方兼容文档与 NapCat 最新主分支源码对“其他成员 reaction 是否直接上报”存在版本差异，必须以部署实例的真实事件为准。
3. `set_group_ban` 不是带业务幂等键的事务 API。Ostrakon 能保证本地并发下只发出一个调用，并在 API 报错后核验当前禁言状态，但网络在“服务端已成功、客户端完全无法确认”的极端故障窗口中无法实现严格的分布式 exactly-once。
4. 当前阶段没有 Web 管理后台或通用聊天管理系统；仅提供 `/ostrakon status` 与回复式 `/reset`，不支持通过聊天修改阈值、白名单或 reaction 配置。
