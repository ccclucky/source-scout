# Source Scout

[English](README.md) | **简体中文**

[![English](https://img.shields.io/badge/README-English-0969da)](README.md)
[![简体中文](https://img.shields.io/badge/README-简体中文-d73a49)](README.zh-CN.md)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![测试](https://img.shields.io/badge/测试-26%20通过-2ea44f)](plugins/source-scout/skills/source-scout/tests/test_discover.py)
[![许可证：MIT](https://img.shields.io/badge/许可证-MIT-yellow.svg)](LICENSE)
[![在 X 上关注](https://img.shields.io/badge/X-@cclove057-000000?logo=x&logoColor=white)](https://x.com/cclove057)

Source Scout 是一个 Agent Skill 和独立 Python 工作流，用于发现公开官方文档及其直接引用的权威来源。它从一个官方文档入口 URL 出发，发现范围内的来源，清理确定性噪音，保留需要复核的候选，并导出可审计的 URL 集合。

适合用于准备 NotebookLM 资料、研究前置收集、文档索引或其他下游工具的来源清单。Source Scout 不是通用爬虫、网站镜像工具，也不是不受范围约束的网页研究 Agent。

## 核心能力

- 通过入口页、sitemap、`llms.txt`、导航和范围内链接发现文档。
- 分开判断相关性与权威性，不把官方域名下的所有页面都视为有用来源。
- 支持 Fast、Standard 和 Deep 三种收集模式。
- 使用运行目录内的 Checkpoint 恢复中断任务，不重复抓取已完成页面。
- 处理重试、robots 限制、重定向、canonical URL、动态页面证据和访问受限来源。
- 导出保留、排除和不确定候选，并记录原因与决策历史。
- 对大规模模糊候选使用分类批次和经过验证的组级规则。
- 既可由 Agent 按 Skill 流程运行，也可作为确定性 CLI 独立运行。

## 收集模式

| 模式 | 默认页面上限 | 速度 | 相对 Token 消耗 | 覆盖范围 |
| --- | ---: | --- | --- | --- |
| Fast | 100 | 最快 | 最低 | 入口页、公开清单和入口页直接引用 |
| Standard | 500 | 适中 | 适中 | 递归发现范围内文档及直接引用的权威来源 |
| Deep | 2,000 | 最慢 | 最高 | 交叉检查索引、导航、动态页面和可恢复失败 |

三种模式使用相同的来源纳入标准。模式改变的是发现强度，不改变权威性或相关性的含义。若达到页面上限时队列尚未自然收敛，运行会进入 `decision_required`，不会被报告为完成。

## 环境要求

- Python 3.10 或更高版本
- 可通过 HTTP(S) 公开访问目标文档的网络环境
- 完整流程需要一个能够读取 `SKILL.md`、运行 Shell 命令并分类模糊候选的 Agent
- Standard 或 Deep 模式处理动态页面时可选配浏览器能力

确定性 CLI 只依赖 Python 标准库。未经用户明确同意，不会安装浏览器依赖。

## 安装

### 独立 Skill

首次安装时，克隆仓库并将 Skill 目录复制到 Agent 的 Skill 目录：

```powershell
git clone https://github.com/ccclucky/source-scout.git
New-Item -ItemType Directory -Force $HOME/.codex/skills | Out-Null
Copy-Item -Recurse source-scout/plugins/source-scout/skills/source-scout $HOME/.codex/skills/source-scout
```

请在希望存放仓库的目录中执行上述命令。随后新建一个 Codex 任务，或按客户端支持的方式重新加载 Skill，并在对话中调用 `$source-scout` 验证是否已被识别。其他 Agent 可将同一目录复制到其支持的 Skill 目录。

更新现有安装时，先拉取仓库，再只删除已有的 `source-scout` Skill 目录并完整复制新版本，避免已移除的旧文件残留：

```powershell
git -C source-scout pull
$destination = Join-Path $HOME ".codex/skills/source-scout"
Remove-Item -Recurse -Force -LiteralPath $destination
Copy-Item -Recurse source-scout/plugins/source-scout/skills/source-scout $destination
```

可分发的独立 Skill 包含 [SKILL.md](plugins/source-scout/skills/source-scout/SKILL.md)、[references](plugins/source-scout/skills/source-scout/references/)、[scripts](plugins/source-scout/skills/source-scout/scripts/) 和 [tests](plugins/source-scout/skills/source-scout/tests/)，不依赖 Plugin 外壳。

### Plugin 包

仓库同时包含 [Codex](plugins/source-scout/.codex-plugin/plugin.json) 和 [Claude Code](plugins/source-scout/.claude-plugin/plugin.json) 的 Plugin 元数据，以及仓库根目录下的 marketplace 元数据。这些文件封装的是同一个独立 Skill。由于不同客户端版本的 Plugin 安装命令可能不同，本文优先推荐上面的目录复制方式。

## Agent 用法

调用 Skill 并提供一个官方文档入口 URL。版本、语言、产品和内容范围是可选项，但明确给出可以提高范围推断的准确性。

```text
$source-scout 从以下入口收集 LangGraph Python 官方文档：
https://docs.langchain.com/oss/python/langgraph/overview
```

如果缺少重要范围信息，Source Scout 会先提出 Collection Scope 供用户确认，再开始高成本发现。执行前还会说明模式之间的取舍，以及预计页面数、时间和 Token 消耗。

标准流程如下：

1. 验证 Seed Page 并确认 Collection Scope。
2. 选择 Fast、Standard 或 Deep 模式。
3. 开始或恢复确定性发现。
4. 分批分类模糊候选。
5. 复核重要的不确定候选。
6. 导出最终来源集合和审计文件。

## CLI 用法

CLI 是有状态的：`start` 创建唯一运行目录，后续命令都针对该目录执行。从仓库根目录运行：

```powershell
$script = "plugins/source-scout/skills/source-scout/scripts/discover.py"
$seedUrl = "https://docs.example.com/product/overview"

$start = python $script start --url $seedUrl --mode standard --output-root output | ConvertFrom-Json
$runDir = $start.run_dir

python $script status --run-dir $runDir
python $script resume --run-dir $runDir --max-pages 1000
python $script resume --run-dir $runDir --retry-failed
python $script classify next --run-dir $runDir --limit 50
python $script classify submit --run-dir $runDir --input decisions.json
python $script export --run-dir $runDir
```

使用 `--scope` 指定主要 URL 路径边界；可重复使用 `--scope-root` 添加其他已验证的官方根路径：

```powershell
python $script start `
  --url https://docs.example.com/product/overview `
  --scope /product `
  --scope-root https://api.example.com/product `
  --mode deep `
  --output-root output
```

`start` 会输出包含 `run_dir` 的 JSON；示例将它保存到 `$runDir`，供所有后续命令使用。分类默认每批 50 条，单批最多 100 条；整次运行不设置分类总批次数或 Token 熔断。将 `start` 替换为任意命令名即可查看其帮助，例如 `python $script start --help`。

在 Ctrl+C、进程终止或其他可恢复中断后，重新执行 `python $script resume --run-dir $runDir`。Checkpoint 会保留已完成页面和待处理工作，运行不会从头开始。

`classify next` 以 JSON 输出候选证据。Agent 审核该批次后，写入如下决策数组：

```json
[
  {
    "id": "0123456789abcdef",
    "decision": "include",
    "category": "documentation",
    "confidence": "high",
    "reason": "属于范围内的官方产品文档。"
  }
]
```

必须一次提交活动批次中的全部 ID，且每个 ID 只能出现一次。`include` 和 `exclude` 要求高置信度；中、低置信度结果必须使用 `uncertain`。正常导出要求发现已收敛且没有待分类候选。已分类的 `uncertain` 候选会写入 `uncertain.txt`；当用户解决某项不确定候选后，可在导出前使用 `classify override`。只有用户明确决定提前停止时才使用 `export --partial`。

```powershell
python $script classify override `
  --run-dir $runDir `
  --id 0123456789abcdef `
  --decision include `
  --reason "用户确认该来源属于收集范围。"
```

### 组级复核

只有当至少 50 个已分类候选为不确定，且它们占已分类候选的至少 20% 时，才可使用组级复核：

```powershell
python $script classify group-next `
  --run-dir $runDir `
  --field path `
  --operator prefix `
  --value /docs/api/

python $script classify group-submit --run-dir $runDir --input group-decision.json
```

组级规则需要 3 条结构多样的规则提议样本，以及一组不重叠的验证样本。所有验证决策和分类必须一致，规则才会应用。验证失败时，所有候选继续保持不确定，并且最多允许一次按优先级自动选择的子组尝试。最小提交结构如下：

```json
{
  "rule_id": "0123456789abcdef",
  "decision": "include",
  "category": "reference",
  "reason": "该验证组由范围内的 API Reference 页面组成。",
  "sample_decisions": [
    {
      "id": "fedcba9876543210",
      "decision": "include",
      "category": "reference",
      "confidence": "high",
      "reason": "属于范围内的 API Reference 样本。"
    }
  ]
}
```

真实文件必须包含 `group-next` 返回的全部规则提议样本和验证样本，不能只提交上述单条示例。

## 输出文件

每次运行写入 `output/<project>/<timestamp>/`。时间戳目录就是 run ID，其中包含 Checkpoint、证据、分类进度和导出结果。

| 文件 | 用途 |
| --- | --- |
| `urls.txt` | 主要交付物，只包含确认保留的 URL |
| `uncertain.txt` | 未解决候选的用户复核入口 |
| `report.md` | 完成状态、数量、发现渠道和重要缺口的简明报告 |
| `results.csv` | 候选级结果和决策的审计材料 |
| `excluded.txt` | 被排除的候选及原因 |
| `checkpoint.json` | 可恢复运行状态，应视为内部工作流文件 |
| `all-candidates.json` | Deep 或调试流程可选生成的详细审计输出 |

已完成运行是不可变记录。Collection Scope 或 Collection Mode 改变时应创建新运行，而不是修改已有结果。

## 安全边界

- Seed Page 必须是公开的官方文档入口。
- 拒绝私有、回环和本地网络目标。只有当主机名确实通过已配置代理路由时，才接受代理提供的合成 DNS 地址。
- 不绕过 robots、身份认证、CAPTCHA 或付费墙，只记录相关限制。
- 访问受限不等于无关，不会自动当作噪音排除。
- 公开清单中的畸形链接会作为无效候选忽略，不会中止整个运行。
- Fast 模式从不使用浏览器渲染。
- 仅使用环境已有浏览器能力，或在用户明确批准后安装到隔离环境。
- 被官方正文直接引用的源码仓库可以作为来源，但 Source Scout 不递归扫描仓库代码。
- 工具会报告已知缺口和收敛状态，但不承诺对任意网站做到绝对完整。

## 开发与验证

从仓库根目录运行完整测试：

```powershell
python -m unittest discover `
  -s plugins/source-scout/skills/source-scout/tests `
  -v
```

测试套件使用本地 HTTP fixture 和模拟网络边界。外部网站不属于确定性回归测试。发布前应单独执行真实文档 smoke test：

```powershell
$script = "plugins/source-scout/skills/source-scout/scripts/discover.py"

python $script start `
  --url https://docs.langchain.com/oss/python/langgraph/overview `
  --scope /oss/python/langgraph `
  --mode standard `
  --max-pages 150 `
  --output-root release-smoke

python $script start `
  --url https://docs.python.org/3/tutorial/index.html `
  --scope /3/tutorial `
  --mode standard `
  --max-pages 100 `
  --output-root release-smoke
```

站点改版和网络故障应被视为 smoke test 发现，而不是确定性单元测试回归。成功的发现 smoke run 会报告 `discovery_status: converged`；语义分类属于后续独立的 Agent 步骤。

## 仓库结构

```text
.
├── .agents/plugins/marketplace.json
├── .claude-plugin/marketplace.json
└── plugins/source-scout/
    ├── .claude-plugin/plugin.json
    ├── .codex-plugin/plugin.json
    └── skills/source-scout/
        ├── SKILL.md
        ├── references/
        │   └── classification-policy.md
        ├── scripts/
        │   └── discover.py
        └── tests/
            └── test_discover.py
```

[SKILL.md](plugins/source-scout/skills/source-scout/SKILL.md) 定义 Agent 工作流，[分类策略](plugins/source-scout/skills/source-scout/references/classification-policy.md) 定义确定性与语义判断边界。`discover.py` 提供确定性发现、Checkpoint、分类交换和导出。两个 Plugin 外壳使用同一 Skill 目录，避免实现漂移。

## 当前限制

- 首版导出 URL 集合，不镜像页面内容，也不直接导入 NotebookLM。
- 语义分类需要 Agent 或外部提供的决策；独立 CLI 发现会保留无法确定的候选。
- 动态页面覆盖取决于执行环境提供的浏览器能力。
- 版本、语言和文档边界仍依赖已确认 Collection Scope 的质量。

## 支持

请通过 [GitHub Issues](https://github.com/ccclucky/source-scout/issues) 提交可复现 Bug、文档问题和功能建议。请附上使用的命令、运行状态，以及不含敏感信息的报告或 Checkpoint 细节。不要公开凭据、私有 URL 或受限内容。

## 许可证

Source Scout 使用 [MIT License](LICENSE) 发布。Copyright © 2026 ccclucky。
