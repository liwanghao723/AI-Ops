# H3C 云桌面智能运维助手（H3COpsAssistant）

![CI](https://github.com/liwanghao723/AI-Ops/actions/workflows/ci.yml/badge.svg)

> 一款运行在 Windows 桌面端的**本地化 H3C Workspace 云桌面运维工具**：通过 H3C 官方 REST API 实时拉取告警与桌面/主机性能数据，并由大模型（OpenAI 兼容接口，可热插拔 DeepSeek / GLM / 通义千问）提供告警解释、故障排查建议与智能问答。

- **版本**：v1.0.0
- **适用平台**：H3C Workspace（E2010）云桌面
- **形态**：Python + PyQt5 桌面应用，PyInstaller 打包为单文件 `H3COpsAssistant.exe`

## ✨ 功能特性

### 1. 智能告警中心
- 实时告警列表，按 **紧急 / 重要 / 次要 / 警告** 分级高亮
- 点击告警，AI 直接给出「故障含义解释 + 3 条排查/解决建议」
- 支持按级别、类别、来源、时间、状态等条件筛选与排序

### 2. 桌面健康监控
- 一张表格纵览所有云桌面：名称、IPv4、操作系统、状态、所属宿主机及 **CPU / 内存 / 磁盘利用率**
- 利用率超阈值（默认 CPU / 内存 ≥ 85%）自动红色标注
- 一键刷新性能数据，展示最近刷新时间

### 3. 知识库与联网分析（RAG）
- 分析/问答前先到 IMA 共享知识库检索，命中内容作为上下文
- 本地知识库兜底层（`knowledge/` 目录离线检索），保证无网可用
- 知识库与模型都无法确定时自动联网搜索并汇总报告
- 智能问答入口：提问 → 关键词 → 检索 → 模型 →（必要时联网）

### 4. 版本更新与软件库
- 启动按版本号（`v1.0.0`）在固定目录定位/创建子目录，统一管理 `logs/`、`config/`、`knowledge/`、`updates/`
- 可检测新版本并引导把更新包下载到 `updates/` 后重启升级

## 🧩 技术栈

| 维度 | 选型 |
|---|---|
| 语言 | Python 3 |
| GUI | PyQt5 |
| HTTP / 认证 | requests + HTTP Digest / Spring-Cookie 双认证 |
| 大模型 | OpenAI 兼容接口，可热插拔 DeepSeek / GLM / 通义千问 |
| 检索增强 | IMA 知识库 + 本地知识库 + 联网搜索兜底 |
| 打包 | PyInstaller（单文件 Windows exe） |
| 测试 | pytest |

## 🚀 快速开始

### 方式一：运行打包产物（推荐）
直接用 PyInstaller 构建出的 `H3COpsAssistant.exe`（或向维护者获取发布包），双击运行，**无需** Python 环境。

### 方式二：源码运行
```bash
# 1. 安装依赖（建议虚拟环境）
pip install -r requirements.txt

# 2. 运行
python src/main.py
```

## ⚙️ 配置

首次启动若 `config/app.yaml` 缺失，会自动从 `config/app.yaml.example` 拷贝并提示填写。

```yaml
platform:
  base_url: "http://10.1.1.201:8083"   # H3C Workspace 平台地址
  user: "<外部接口认证用户-管理员>"       # 接口 Digest 认证账号
  password: "<password>"
  frontend_user: "admin"               # 平台前端会话账号（用于完整告警拉取）
  frontend_password: ""                 # 前端会话密码（与接口账号通常不同）
llm:
  provider: "deepseek"
  base_url: "https://api.deepseek.com/v1"
  api_key: "<key>"
  model: "deepseek-chat"
rag:
  ima_enabled: true
  ima_client_id: ""
  ima_api_key: ""
```

> ⚠️ **安全提示**：`config/app.yaml` 含明文平台账号与 LLM API Key，已被 `.gitignore` 排除，**不会**提交到仓库。请勿手动将其加入版本控制。

### 双认证域说明
- **接口认证（Digest）**：使用「外部接口认证用户」调用 REST（告警、桌面概要、性能等）。
- **前端会话（Spring Cookie）**：填写 `frontend_user/frontend_password` 后，告警走平台「告警管理页」同款 `/vdi/warnManage/realTimeAlarms`，可拿到完整告警（含 License / 终端 / 虚拟应用）；留空则回退旧接口，总量会被截断且拿不到多子系统告警。

## 📁 目录结构

```
H3COpsAssistant/
├── src/                 # 源码
│   ├── main.py          # 程序入口
│   ├── core/            # 配置、认证、H3C 客户端、数据模型
│   ├── ui/              # 界面（主窗口、告警面板、健康面板、配置弹窗）
│   ├── workers/         # 后台采集线程（告警轮询 / 健康采集 / 版本自检）
│   ├── ai/              # 检索 / LLM / 知识库
│   └── update/          # 版本管理
├── config/              # app.yaml（敏感，不入库）/ app.yaml.example
├── knowledge/           # 本地知识库（离线兜底）
├── tests/               # pytest 测试套件（基线 396 passed）
├── docs/                # 架构 / 时序图（mermaid）
├── build.spec           # PyInstaller 打包配置
├── requirements.txt
├── PRD.md / ARCHITECTURE.md   # 产品需求 / 技术架构文档
└── README.md
```

运行时数据目录（产物落盘）：`C:\Users\liwanghao\Desktop\AI工具文件\v1.0.0\`，含 `logs/`、`config/`、`knowledge/`、`updates/`。

## 📦 构建打包

```bash
pip install pyinstaller
pyinstaller build.spec --noconfirm
# 产物：dist/H3COpsAssistant.exe
```

## 🧪 测试

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
# 基线：396 passed / 3 xpassed
```

## 📚 更多文档

- `PRD.md` —— 产品需求文档（功能清单与验收标准）
- `ARCHITECTURE.md` —— 技术架构设计
- `ARCHITECTURE-health.md` / `PRD-health.md` —— 健康监控专项
- `docs/*.mermaid` —— 类图 / 时序图

## ⚠️ 免责声明

本工具为内部运维辅助工具，所有操作均通过 H3C 官方 Workspace REST API 完成。请确保在授权范围内使用，并妥善保管 `config/app.yaml` 中的凭据。
