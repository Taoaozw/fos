# FlowOS (`fos`)

面向 AI Agent 原生设计的个人 **「思考 · 选择 · 经验错误」认知编译与记忆引擎**（纯 CLI + 内嵌 Skill 形态，吸收 `fops` 全部 CLI/Skill/自更新优化，零 MCP 开销）。

## 架构概览

- **永久资产层（不可变）**：`ledger/events-YYYY-MM.jsonl`（ULID 主键、Append-Only 原话账本，内置 Secret 正则脱敏）与 `eval/golden.jsonl`（黄金回归集）。
- **编译存储层（可删库一键重建）**：版本化 SQLite WAL (`snapshots/vNNNN.db`) + FTS5（中英 bigram 增强）+ `sqlite-vec` 向量检索 + 双时态图谱（`SUPERSEDES` / `CONTRADICTS` / `CAUSED_BY` / `SUPPORTS` / `APPLIES_TO`）+ `current.json` 原子切换指针。
- **双端拓扑 & 自动更新**：
  - M3 Max 常驻大脑：`fosd serve`（Bearer SHA-256 鉴权），提供 `/install.sh` 一键安装脚本、`/v1/cli/version` 与 `/v1/cli/download`（带 `X-Fos-Sha256` 校验），按需运行 `fos dream` 与四维度 **Regression Gate**，随时 `fos undo`。
  - 随身设备客户端：`fos` CLI（极轻依赖，仅 `httpx`，冷启动 ~30ms；支持短别名、拼写纠错、`--fields` 投影、管道单行紧凑 JSON、`fos describe` 自描述、断网自动降级 `offline_cache` + `outbox.jsonl`、后台防抖自更新并自动刷新内嵌 `SKILL.md`）。

## 安装与发布

```bash
# M3 Max 家庭大脑（含 FastAPI / Uvicorn / sqlite-vec）
uv tool install --editable ".[brain]"
fosd init
fosd publish --project-dir .        # 构建并发布客户端 wheel 至 ~/.fos/brain/dist/
fosd serve --host 0.0.0.0 --port 7622

# 随身电脑一键安装（自动安装 CLI、绑定大脑、同步离线缓存并安装 AI Skill）
curl -fsSL http://<m3max-tailscale-ip>:7622/install.sh | sh -s -- <TOKEN>
```

## 日常使用（本地或远端通用）

```bash
fos boot --json                                     # 自动识别 cwd git 仓库，输出 <=300t L0 红线
fos recall "macOS rsync deploy" --fields id,rule    # 四路混合召回 + --fields 极简投影
fos recall "storage engine" --as-of 2026-08 --json  # 双时态历史切片
fos mirror "部署脚本直接用 rsync --mkpath" --json    # 决策前四维照镜子
fos get LSN-001 --tier L2 --graph --json            # 按需钻取 L2 原话证据与图谱
fos add --kind lesson --scope fops --severity P0 "禁止依赖 rsync --mkpath，必须先 ssh mkdir -p"
fos sync --json                                     # 冲刷离线 outbox + 拉取最新快照 + 检查更新
fos status --json                                   # 诊断面板（版本、大脑、缓存、自更新、Skill）
fos describe [command]                              # 机器可读命令目录与参数 Schema
fos skill install                                   # 安装/刷新内嵌两级 AI Skill（SKILL.md + references/）
fos update [--check]                                # 从大脑拉取最新 wheel（SHA-256 校验）并自动刷新 Skill
```

## 后验采集、睡眠编译与回归门禁

```bash
fos ingest --from claude --since 24h --json
fos dream --json
fos eval --suite all --json
fos eval pin LSN-001 --json
fos undo --json
fos export --format md --out ./views/
```

## 退出码契约
`0=ok`, `1=error`, `2=usage`, `3=not_found`, `4=gate_failed`, `130=canceled`
