# 本地客制化修改记录（Hermes Agent）

- 上游：`git@github.com:NousResearch/hermes-agent.git`
- 基线提交：`b167f21d82`（安装器形态：detached checkout，本机 `~/.hermes/hermes-agent`）
- 记录分支：`local/customizations-20261026`
- fork（本机客制化记录仓）：`git@github.com:wangguanran/hermes-agent.git`，本地 remote 名 `fork`
- 同步命令：`git -C ~/.hermes/hermes-agent push fork local/customizations-20261026`
- 记录日期：2026-10-26

本文件与同分支提交一起，记录本机 Hermes 相对上游的**全部客制化**。仓库内改动直接体现为提交内容；仓库外改动（插件、脚本、技能、配置）因不在本仓库，复制或指向见下。

## 1. 仓库内改动（即本分支提交）

| 文件 | 改动 | 目的 |
|:---|:---|:---|
| `agent/agent_runtime_helpers.py` | `_CN_PLAN_TAIL_RE` / `_CN_PLAN_ANY_RE` / `_cn_plan_tail()`，接入 `trailing_continue_intent()`、`promoted_reasoning_announces_action()` | 续跑判定器原先只认英文/泰文，「我先…」「接下来我…」这类中文计划尾句不触发纠偏 → 回合零工具调用直接结束（表现为「任务跑一半自己停」） |
| `agent/display.py` | `fence_safe()` + 工具进度命令排版 | 工具命令按顶层 `;` / `&&` / `\|\|` 换行（运算符留行尾）；JSON 参数按 `indent=2` 输出；引号内文本、`$( )`、heredoc 正文原样保留 |
| `agent/tool_executor.py` | `_commit_tool_result` 为 `terminal` 补 `progress_args`（经 `_redact_tool_args_for_display`） | 工具完成事件携带（已脱敏的）参数，verbose 进度才能显示命令 |
| `gateway/platforms/base.py` | verbose 进度用 ```json 代码段 + `indent=2` | 参数以 JSON 代码段上报，避免一行挤在一起 |
| `gateway/run_turn_runner.py` | `_progress_terminal_result()` 等 | 每次完成的调用发一条结果（不做相同行去重），失败时 fail-closed 不发布原始输出 |
| `tests/gateway/test_terminal_output_progress.py` | 新增 | 覆盖上述进度输出行为 |

## 2. 仓库外客制化

### 2.1 cursor-provider 插件 fork（结构化工具调用）

- 位置：`~/.hermes/plugins/cursor-provider/`（上游 `anpicasso/hermes-cursor-provider` 0.4.1）
- fork 快照与重放脚本：`~/.hermes/local-fork/cursor-provider/`（`MANIFEST.json` 含 sha256 与改动清单）
- 关键改动：Hermes 工具经 `cursor_sdk.CustomTool` 注册；agent 选项 `tools: ["mcp"]`（`mcp` 是承载自定义工具的包装器，`tools: []` 会把它一起关掉，导致模型只叙述不调用）；结构化提示词与纠错词；工具集合变化时重建 SDK agent；`_action_only` 认中文计划尾句与英文 `I'll now …`，且「只叙述」的重试不再判回合失败
- 升级后被覆盖时重放：`python3 ~/.hermes/local-fork/restore-cursor-provider.py --check`（drift 时无参执行同步）

### 2.2 维护脚本（`~/.hermes/scripts/`，独立 git 仓库，本地 commit 即完成版本管理）

客制化点（现网版本以该目录为准）：

| 脚本 | 客制化点 |
|:---|:---|
| `team-token-usage.py` | 工作日 09:00–18:00 每半小时必投；其余时段按本人用量增量 ≥1000 万门限；00:00 必投昨日全天；`--dry-run` 不推进基线 |
| `cron-retry-failed.py` | 个人 profile 目录按存在性解析（`personal-assistant` → 回退 `entry`），不再硬编码 |
| `delivery-retry.py` | 同上；状态键保留 `entry` 以免重置补投计数 |
| `task_exec_audit.py` | 同上（双 profile cron 目录解析） |
| `hermes-gw-restart-watchdog.sh` | 读 `.env` 取 Telegram token 时按 `personal-assistant` → `entry` → `~/.hermes` 顺序尝试 |

### 2.3 技能（`~/.hermes/skills/`）

客制化经验写入技能而非仓库代码，例如：`hermes-model-management`（含 `references/cursor-structured-tools-patch.md`、`references/stall-detector-cn-patch.md`）、`hermes-gateway-ops`、`macos-cron-scripts`、`feishu-group-read` 等。

### 2.4 配置（`~/.hermes/config.yaml`）

关键口径（非密钥）：主模型 `cursor / grok-4.7`；cron 默认 `meigsmart / deepseek-v4.1-flash`；辅助模型走 meigsmart；`agent.intent_ack_continuation=true`、`agent.tool_use_enforcement=true`、`display.busy_input_mode=steer`、`agent.api_max_retries=4`。

## 3. 升级（`hermes update`）后的重放顺序

1. 仓库代码：`git fetch origin` 后回到安装器基线，再从本分支 cherry-pick 客制化提交。
2. 插件：`python3 ~/.hermes/local-fork/restore-cursor-provider.py --check`，drift 则执行同步。
3. 脚本与技能在 `~/.hermes/` 下，不随上游升级覆盖。
4. 配置项按 §2.4 回读核对。
5. **提交前注意**：本仓库 pre-commit 走 lefthook + `ruff`（`lefthook.yml` 的 lint python）。`ruff` 不在 PATH 时提交会直接失败（`exit status 127`）。本机装法：`uv tool install ruff`（隔离在 `~/.local/bin`，不动 Hermes venv）。

_Author: Hermes Agent_