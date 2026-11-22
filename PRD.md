# FlowOS (`fos`) 产品需求文档（PRD · AI-Native 架构版）

> **一句话定位**：面向 AI Agent 原生设计的个人 **「思考 · 选择 · 经验错误」认知编译与记忆引擎**。
> 采用 **「家庭 M3 Max 48GB 常驻大脑（`fosd`）+ 随身设备轻量客户端（`fos` CLI/MCP + 离线缓存）」** 双端架构；以 **不可变后验账本（Verbatim Experience Ledger）+ AI 原生分层压缩编译（L0/L1/L2）+ 时序图谱混合重排 + 睡眠编译（Dream）与回归门禁（Eval Gate）** 为核心——让人零摩擦沉淀，让 AI 毫秒级低 Token 精准召回，且每次记忆演化**可评测、可验证、可回滚**。

---

## 1. 为什么推翻「纯 Markdown 笔记库」方案？（第一性原理）

1. **格式不是 AI 原生**：人类长文段落冗余度极高，AI 读几篇文档就吃掉上万 Token，且缺乏结构化的因果、时态和触发条件字段。
2. **检索与压缩低效**：纯 `ripgrep` 漏语义，纯 Vector RAG 丢精确词和上下文，有损摘要又会悄悄洗掉关键约束（边界条件、例外情况）。
3. **录入依赖人工自律**：让人每次踩坑、做决定后手写模板，90% 会在两周内荒废；真正高价值的经验藏在日常和 AI/工具交互后的 **后验信号（Hindsight：纠偏、报错、重试、结果）** 里。
4. **记忆演化没有回归测试门禁**：随着记忆不断合并、压缩、更新，极易出现「新总结覆盖旧约束」「已推翻的旧决策仍被召回」「越压越糊涂」的静默退化，必须像代码一样引入 **自动化回归评测与原子回滚**。
5. **单机笔记本会合盖休眠**：后台后验采集、夜间 `dream` 编译、多端统一记忆必须有一个**永不关机、算力充足的常驻主节点**。

---

## 2. 吸收的社区前沿成熟实践

| 借鉴来源 | 核心机制 | 在 `fos` 中的落地方式 |
|---|---|---|
| **[Songe](https://github.com/sous-labs/songe)**（Sous Labs） | **Hindsight（后验反馈）作核心信号**；Experience Log 是永久资产，编译产物/权重只是缓存；**Quick/Deep Dream** 离线编译；**Held-out Regression Gate（Do no harm, provably）** + 原子切换与 `undo` | 不可变账本与可重建编译引擎分离；从日常对话与 `fops` 轨迹自动抽后验教训；M3 Max 夜间跑 Dream，必须过回归门禁才生效，失败自动丢弃，随时 `fos undo` |
| **[QMD](https://github.com/tobi/qmd)**（Shopify / Tobi） | 纯本地 **SQLite FTS5 (BM25) + `sqlite-vec` 向量 + Query Expansion + RRF + 本地 Cross-Encoder Rerank**，Token 节省 95%+，原生 MCP | `fos recall` 引擎在 M3 Max 常驻该四段本地混合检索流水线（零云端向量库依赖、延迟 < 30ms） |
| **[OpenViking](https://github.com/volcengine/OpenViking)**（ByteDance） | **L0 / L1 / L2 三层渐进式上下文加载**（Abstract ~100t / Overview / Full Details） | 编译阶段生成 L0 唤醒核与 L1 结构化压缩卡，按严格 Token Budget 逐级展开 |
| **[MemPalace](https://github.com/milla-jovovich/mempalace)** | **Verbatim 无损存储** + ~170 Token 极轻唤醒核 + 结构化域隔离 | 原始账本永远无损保留原话与证据，压缩只在 L0/L1 编译视图中进行，随时可回溯 L2 原文 |
| **[Zep / Graphiti](https://github.com/getzep/graphiti)** | **双时态知识图谱（Bi-Temporal Graph）**，`valid_from / valid_until / superseded_by` 表达认知演化 | 旧决策被推翻不覆盖删除，而是写失效时间与演进边；默认查当前生效版本，`--as-of` 可查历史切片 |
| **[Hindsight](https://github.com/vectorize-io/hindsight) & Claude Auto Dream** | `Retain / Recall / Reflect` 三阶段 + 区分事实/经历/演化信念 + 空闲后台整理去噪 | 睡眠编译区分「客观事件」「主观选择」「防呆规则」，定期反思冲突并提炼稳定心智模型 |

---

## 3. 分布式拓扑：M3 Max 48GB 家庭大脑 + 随身设备客户端

延续 `fops`「中心常驻 + 本地极速直连 + 离线可用」的工程美学，但针对 AI 记忆引擎做**单一编译主节点**设计，彻底杜绝多端冲突：

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ 随身设备（MacBook / 公司电脑，随时合盖离线）                                   │
│  · fos CLI & fos mcp（轻量零 ML 依赖，直连 Antigravity / Claude Code / Codex） │
│  · 被动采集守护：fos ingest（增量抽取本机 AI 会话 & fops 后验轨迹）             │
│  · 离线韧性层：本地只读镜像缓存（L0/L1 + FTS5，仅数 MB）+ outbox.jsonl 队列    │
└───────────────────┬───────────────────────────────────────▲─────────────────┘
                    │ Tailscale 内网 HTTPS / fops SSH 隧道   │ 自动增量同步
                    │ （在线直调完整四路检索 + 实时推送事件）   │ 最新编译快照
                    ▼                                       │
┌─────────────────────────────────────────────────────────────────────────────┐
│ 家里常驻节点：M3 Max 48GB Mac（fosd · Single Brain & Dream Master）           │
│  1. 永久资产层（唯一真相，自动定时 git push 私有库灾备）                        │
│     · ledger/events-YYYY-MM.jsonl（ULID 主键、Append-Only 无损原话账本）      │
│     · eval/golden.jsonl（永久黄金回归测试用例集）                              │
│  2. 常驻实时检索引擎（launchd KeepAlive，常驻内存 < 2 GB）                     │
│     · 单文件 SQLite WAL（FTS5 + sqlite-vec + 双时态图谱 + 快照指针）           │
│     · 本地常驻 Embedding (~300M) + Cross-Encoder Reranker (~0.6B)            │
│  3. 离线睡眠编译与门禁（fos dream，按需拉起本地 MLX 14B-32B，完即释放内存）     │
│     · Clean → Extract → Consolidate → Compile(vN+1) → ★ Regression Gate ★   │
│     · 0 API 费用 · 100% 隐私不出内网 · 可选 Deep Dream 无缝对接 Songe MLX     │
│  4. 派生投影（可选）：fos export 生成只读 Markdown 供人类按需浏览               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 多端在线 / 离线协同机制
- **在线模式（通过 Tailscale 互联，零公网端口暴露）**：
  - 随身电脑上的 `fos` CLI / `fos mcp` 直接请求 M3 Max 的 `fosd` API，享受完整的 **BM25 + 本地向量 + 时序图谱 + Cross-Encoder Rerank** 与 `mirror` 能力。
  - 每次录入（`fos add`）或会话采集（`fos ingest`）以 **ULID 全局有序唯一 ID** 追加到 M3 Max 账本，多台电脑并行写入**零冲突**。
- **离线降级模式（飞机 / 无网 / 回家前）**：
  - 随身电脑定期（或每次 `fos boot` 后台静默）从 M3 Max 拉取最新的精简编译快照（L0 + L1 + FTS5，体积极小）；
  - 断网时 `fos boot` / `fos recall` 自动无缝降级读本地快照（标记 `"mode": "offline_cache"`），写入暂存到本地 `outbox.jsonl`；
  - 联网后自动把 `outbox.jsonl` 冲刷到 M3 Max 并拉回最新快照。
- **单一编译主节点（Single Writer）**：
  - 只有家里的 M3 Max 执行 `fos dream`、生成编译快照与跑回归门禁，彻底避免多端并发编译导致的版本分叉。

### 3.2 M3 Max 48GB 统一内存资源预算
| 阶段 | 运行组件 | 内存占用 | 说明 |
|---|---|---|---|
| **全天常驻** | `fosd`（SQLite WAL + API）+ 本地 Embedding (~300M) + Reranker (~0.6B) | **~1.5 – 2.0 GB** | 几乎零存在感，搜索与重排延迟 < 30ms |
| **空闲/夜间 Quick Dream** | 按需加载 MLX 量化模型（如 Qwen3 / Qwen2.5 14B–32B 4-bit）跑提炼、去重、冲突检测与 Eval Judge | **~9 – 19 GB（峰值）** | 跑完即卸载释放内存；全程 **\$0 云端 Token 成本**，支持随时切换强云端模型做高阶 Judge |
| **可选 Deep Dream** | 联动 `songe` 执行 MLX LoRA On-Policy Self-Distillation | **~16 – 28 GB（空闲独占）** | 48GB 统一内存完全胜任 7B–14B 级别本地适配器训练 |

---

## 4. 已敲定的技术栈选型

**核心结论：全栈 Python 3.12+（`uv` 管理），按 `fos`（轻量客户端）与 `fos[brain]`（M3 Max 算力端）拆分可选依赖。**

| 层 | 选型 | 选型理由 |
|---|---|---|
| **语言与包管理** | **Python 3.12+ (`uv`)** | 单一代码库覆盖 CLI、MCP、HTTP 服务、本地 MLX 推理、Eval 评测与 `songe` 生态；`uv tool install` 跨 Mac 安装极快。 |
| **数据库与检索** | **SQLite WAL + 内置 FTS5 + `sqlite-vec`** | 单文件零运维，可从 `ledger/*.jsonl` 一键重建；Python 侧 `sqlite-vec` 开箱即用，彻底绕开 Go 纯静态编译不支持 CGO 扩展的痛点。 |
| **模型推理与高阶 Judge（M3 Max）** | **本地 MLX (`mlx-embeddings` / `mlx-lm`) + 非交互 CLI Provider（`agy -p` / `claude -p` / `codex exec`）** | 不依赖任何厂商专有 Agent SDK：日常 Embedding/Rerank 与基础 Dream 在 M3 Max 本地 MLX 零成本跑完；需要强模型做高阶反思、复杂冲突消解或 Behavior Eval Judge 时，统一通过子进程调用 `agy -p` 等非交互 CLI（或 OpenAI 兼容接口）。 |
| **服务与协议** | **FastAPI / Starlette + Uvicorn (`fosd`) + 官方 `mcp` SDK (`fos mcp`)** | 客户端走轻量 `httpx` + 懒加载，CLI 冷启动 < 60ms；MCP 长驻进程工具调用 < 20ms。日常使用由 Antigravity / Claude Code / Codex 通过 `fos mcp` / `fos` CLI 反向调用记忆引擎。 |
| **跨设备互联与守护** | **Tailscale（首选）+ macOS `launchd` (`KeepAlive`)** | 零公网暴露、内网穿透稳定；M3 Max 开机自动拉起 `fosd` 与空闲 Dream 调度器。 |
| **账本同步与灾备** | **ULID + Append-Only JSONL + 私有 Git 仓库自动备份** | 永久资产纯文本化，M3 Max 每日定时 `git commit && push` 私有库（仅备份 `ledger/` 与 `eval/`，不提交可重建的 SQLite 缓存）。 |

---

## 5. AI 原生数据结构设计

### 5.1 原始原子事件（Ledger Atom，无损存储）
所有输入以不可变 JSONL 落盘（按月分片 `ledger/events-YYYY-MM.jsonl`）：
```json
{
  "id": "atm_01J...",
  "ts": "2026-11-22T13:45:00+08:00",
  "device": "macbook-pro",
  "kind": "lesson",
  "scope": "fops",
  "source": "hindsight:session_abc | manual",
  "raw": "原话 / 完整上下文片段 / 工具报错证据（绝不做有损改写）",
  "meta": {
    "severity": "high",
    "entities": ["deploy", "rsync", "macos"],
    "evidence_ref": "git:955d311"
  }
}
```

### 5.2 编译态记忆节点（Compiled Node，AI 原生三层结构）
- **L0（唤醒核条目，~15 tokens）**：`[LSN-012|P0|seen:3] macOS openrsync 不支持 --mkpath → 先 ssh mkdir -p`
- **L1（结构化压缩卡，~80–120 tokens，默认召回单元）**：
  ```json
  {
    "id": "LSN-012",
    "type": "lesson",
    "status": "active",
    "valid_from": "2026-11-20",
    "valid_until": null,
    "superseded_by": null,
    "seen_count": 3,
    "confidence": 0.94,
    "trigger_when": ["编写跨平台部署脚本", "在 macOS 调用 rsync"],
    "rule": "禁止依赖 rsync --mkpath，必须先 ssh mkdir -p 创建远端目录",
    "rationale": "macOS 默认 openrsync 无此参数会导致生产发布中断",
    "contradicts": [],
    "evidence_ids": ["atm_01J...", "atm_01K..."]
  }
  ```
- **L2（无损证据，按需展开）**：按 `evidence_ids` 回溯 `ledger` 原始记录。

### 5.3 双时态关系图（Bi-Temporal Graph）
SQLite 内维护轻量节点与边表：
- **边类型**：`SUPERSEDES`（新选择推翻旧选择）、`CONTRADICTS`（观点冲突）、`CAUSED_BY`（教训源于某次选择）、`SUPPORTS`（证据支撑某思考）、`APPLIES_TO`（作用于某项目/实体）。
- 旧选择被推翻时写入 `DEC-B --[SUPERSEDES]--> DEC-A` 并设置 `DEC-A.valid_until`：默认查询自动过滤失效决策，`--as-of` 可还原任意历史时间点的认知快照。

---

## 6. 核心 AI 原生能力详解

### 6.1 高效压缩与预算制检索（Token-Budgeted Hybrid Recall）
1. **极轻唤醒（`fos boot`）**：AI 开新会话仅注入 **≤ 300 tokens 的 L0 Kernel**（最高红线 + 当前项目活跃决策指纹 + 高危错题触发词）。
2. **四路混合召回 + 本地 Rerank（`fos recall`）**：
   - **路 1 — 精确词（FTS5 BM25）**；
   - **路 2 — 语义向量（`sqlite-vec` + 本地 Embedding）**；
   - **路 3 — 触发器/图谱扩散（1–2 Hop 关联 P0 教训与最新决策）**；
   - **路 4 — 时态与权重过滤（剔除失效节点，按 `severity × log(1 + seen_count) × recency` 加权）**；
   - **RRF 融合 + 本地 Cross-Encoder Rerank** → 严格在 `--budget 1200` Token 内返回最高信噪比的 **L1 结构化卡片**。
3. **按需钻取（`fos get <id> --tier L2`）**：仅需核对原话细节时展开 L2。

### 6.2 决策前「AI 原生照镜子」（`fos mirror`）
输入一个新想法、技术选型或行动计划，并发执行 4 项结构化检查：
1. **红线匹配**：比对 `principles` 与高危 `lessons` 的 `trigger_when`；
2. **历史决策冲突**：检索同域历史 `decisions`（含曾被否决的方案与理由）；
3. **错题复发预警**：识别是否命中过往 `seen >= 1` 的踩坑模式；
4. **未验证假设标记**：标出尚未经过真实结果验证的前提。

输出紧凑 JSON 审查报告（`conflicts[]`、`lesson_warnings[]`、`prior_decisions[]`、`blind_spots[]`）。

### 6.3 后验自动采集与「睡眠编译」（`fos dream`）
- **被动 Hindsight 采集（`fos ingest`）**：在随身电脑与 M3 Max 增量解析 Antigravity / Claude Code / Codex / `fops` 轨迹，只抽取带后验信号的对偶片段 `(Context + Action) → (用户纠偏 / 报错 / 重试 / 事后结果)`，丢弃 80% 无信号闲聊。
- **M3 Max 自动 Quick Dream**：按空闲周期或每日定时触发：`Clean` → `Extract` → `Consolidate（同类错误 seen++ / 冲突检测 / 升格候选）` → `Compile（构建快照 vN+1）` → **`Regression Gate`**。
- **Deep Dream（可选扩展，联动 `songe`）**：将清洗后的高价值 Hindsight 经验流直接喂给运行在同一台 M3 Max 上的 `songe`，做本地模型权重级内化。

### 6.4 AI 原生「数据查询与记忆回归门禁」（Eval & Regression Gate）
每次 `fos dream` 编译新快照 `v(N+1)`，必须全绿通过 4 道自动门禁才原子切换 `current` 指针，否则拦截上线并输出退化报告，随时可 `fos undo`：

| 回归维度 | 测试内容 | 通过标准 |
|---|---|---|
| **1. 召回回归（Recall Gate）** | 对 `eval/golden.jsonl` 历史真实场景 Query 跑混合检索 | `Recall@5` 与 `MRR` 不低于 `vN`，新增教训/决策触发词必须命中 Top-3 |
| **2. 时态与冲突回归（Temporal Gate）** | 测试已被推翻的旧决策与新决策边界 Query | 默认查询 **0% 误召回已失效旧决策**；`--as-of` 历史切片 100% 可还原 |
| **3. 压缩保真回归（Fidelity Gate）** | 抽检重压后的 L1 与 L2 原文对比否定词、数字、边界例外 | 关键约束保留率 100%，防止“越压越笼统” |
| **4. 行为防呆回归（Behavior Gate）** | 用真实踩坑场景上下文对比加载 `vN` vs `v(N+1)` 的回答表现 | 新增错题显著改善，且历史 Held-out 错题集无退化 |

- **用例自动沉淀**：每次新增高危 `Lesson` / 重要 `Decision` / 人工纠偏，自动合成 1–2 条回归断言写入 `eval/golden.jsonl`；重要错题可 `fos eval pin <id>` 钉入永久 P0 回归集。

---

## 7. CLI & MCP 接口契约（严格 AI-First）

全命令非交互、支持 `--json`、确定性退出码（`0=ok, 1=error, 2=usage, 3=not_found, 4=gate_failed`）：

```bash
# --- 服务端（M3 Max 48GB） ---
fosd serve [--host 0.0.0.0 --port 7622] # 启动常驻大脑 HTTP 服务（建议绑定 Tailscale 网卡）
fos dream [--dry-run] [--no-gate]       # 执行睡眠编译 + 回归门禁 + 原子切快照
fos eval [--suite all|recall|temporal|fidelity|behavior] [--json]
fos eval pin <id>                       # 钉入永久 P0 回归集
fos undo                                # 原子回滚上一版编译快照

# --- 客户端（随身电脑 & 本机通用） ---
fos init --brain https://m3max.tailnet:7622 --token <tok>  # 绑定家庭大脑并拉取初始缓存
fos sync                                # 冲刷离线 outbox + 同步最新编译快照
fos boot [--project P] [--budget 300] [--json]
fos recall "<query>" [--kind ...] [--scope P] [--as-of 2026-08] [--budget 1200] [--json]
fos get <id> [--tier L1|L2] [--graph] [--json]
fos mirror "<proposal>" [--scope P] [--json]
fos add --kind thought|decision|lesson|outcome --scope P "<content>" [--supersedes DEC-xxx]
fos ingest --from antigravity|claude|codex|fops [--since 24h]
fos export --format md --out ./views/   # 按需导出人类只读视图
fos mcp                                 # 标准 MCP Server（供各 AI 工具原生挂载）
```

---

## 8. 隐私与安全边界
1. **单人 Token 鉴权 + 私有网络**：`fosd` 仅监听 Tailscale 内网或本机环回，强制 Bearer Token（SHA-256 哈希存储，复用 `fops` 认证习惯），公网零暴露。
2. **凭据零入库**：`fos ingest` 与 `fos add` 内置 Secret 正则脱敏过滤器，严禁将密码、私钥、API Key 写入 `ledger`（基础设施凭据继续由 `fops` Hub 托管）。
3. **数据永不丢失**：`ledger/*.jsonl` 与 `eval/golden.jsonl` 仅追加不原地修改，M3 Max 每日自动备份至私有 Git 仓库。

---

## 9. 分阶段落地路线

### Phase 1：M3 Max 大脑骨架 + 混合检索压缩 + MCP（第 1 周，核心跑通）
- [ ] 初始化 `/Users/clive/projects/clive/fos` Python `uv` 项目（拆分 `core` 客户端与 `brain` M3 Max 依赖）。
- [ ] 实现 `ledger/*.jsonl`、SQLite（WAL + FTS5 + `sqlite-vec` + 双时态图谱表）与删库一键重建引擎。
- [ ] 在 M3 Max 上跑通 `fosd`（`launchd` 常驻 + 本地 MLX Embedding/Rerank）与随身电脑 `fos init/sync/boot/recall/get/add/mirror/mcp`。
- [ ] 配套极简全局 `fos` Skill，挂载到 Antigravity / Claude Code / Codex。

### Phase 2：Hindsight 自动采集 + `fos dream` + 回归门禁（第 2–3 周，自进化闭环）
- [ ] 实现被动采集器（Antigravity / Claude Code / Codex / `fops` 轨迹 → Hindsight 抽取）。
- [ ] 在 M3 Max 上跑通 **Quick Dream**（本地 MLX 14B–32B 免费夜间编译）与四维度 **Regression Gate** + 原子快照切换 + `fos undo`。
- [ ] 实现离线缓存降级、Outbox 自动冲刷、私有 Git 自动备份与 `fos export` 只读视图。

### Phase 3：深度演进与外部通道（按需扩展）
- [ ] **Deep Dream 联动 `songe`**：在 M3 Max 上将清洗后的 Hindsight 经验流对接 `songe`，训练个人专属本地模型。
- [ ] **外部入口挂载**：后续随时可将飞书、定时简报、`fops` 告警作为独立客户端接入 `fosd` API / MCP，内核零改动。
