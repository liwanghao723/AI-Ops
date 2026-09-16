# 桌面健康监控 — 增量增强 PRD（v1.0.0 补遗）

> 文档版本：v1.0.0-health
> 产品经理：许清楚（Xu）
> 关联主文档：`PRD.md`（模块二 F2-01 / F2-02 / F2-03）
> API 依据：`Workspace REST API文档（E2010）.docx`
> 语言：中文 ｜ 技术栈：Python 3.14 + PyQt5 + PyInstaller（Windows 桌面 EXE）
> 性质：**增量增强**（非从零开发；`health_worker.py` / `health_panel.py` 骨架已存在）

---

## 0. 一句话结论（给架构师）

当前 `health_worker.collect()` 已通过 **2.9.11 概要接口**取桌面 IPv4/OS/状态、通过 **2.4.12 主机性能接口**取 **宿主机** CPU/内存/磁盘利用率并展示为「宿主机CPU%」（与 `PRD.md` F2-03 和截图列名一致）。

用户原话「桌面的 CPU」与截图列名「宿主CPU%」存在**口径歧义**：API 既有「宿主机级」性能（2.4.12），也有「虚拟机/桌面级」性能（**2.27.30 / 2.27.31**）。本 PRD 已把两类接口与字段全部在下方 §1 实证列出，P0 四字段的**数据来源口径**待 §5 确认后由架构师据此落地。

---

## 1. API 文档实证字段（接口路径 + 字段名，可直接照此设计）

> 以下接口路径、字段 key、类型/单位均摘自 `Workspace REST API文档（E2010）.docx`，已逐一核对。
> 统一外层：`{ success, errorCode, failureMessage, state, data }`；基础前缀：`/vdi/rest/workspace`。

| 需求字段 | 文档章节 | 接口路径 | 方法 | 响应字段 key | 类型 / 单位 | 说明 |
|---|---|---|---|---|---|---|
| 桌面名称 | 2.9.11 | `/virtual/domain/{id}/summary` | GET | `data.title` | string | 当前 `RsDomainSummary.title` 已映射 |
| **桌面 IPv4** | 2.9.11 | 同上 | GET | `data.server.addresses[*].addr` | string(IPv4) | **当前实现已验证 60 行**；`addresses` 为嵌套对象（按网卡名索引，取首个非空 addr） |
| 操作系统 | 2.9.11 | 同上 | GET | `data.osVersion` | string | 已映射 |
| 状态 | 2.9.11 / 2.27.30 | 同上 / `/vms/monitor/{domainUuid}` | GET | `data.status` | string / int | 2.27.30 取值：0模板/1未知/2运行/3关闭/4暂停 |
| **宿主 CPU%** | 2.4.12 | `/hosts/{id}/cpumemdiskrate` | GET | `data.cpuRate` | number(double) | **CPU利用率**；当前 `HostPerf.cpuRate` 已映射（即截图「宿主CPU%」） |
| 宿主 内存% | 2.4.12 | 同上 | GET | `data.memRate` | number(double) | **内存利用率**；已映射 |
| 宿主 磁盘% | 2.4.12 | 同上 | GET | `data.disk[].usage` | number | **磁盘利用率**；多盘取 `max(usage)`（当前 `HostPerf.diskMaxUsage`） |
| **桌面的 CPU%**（VM 级·备选/升级） | 2.27.30 | `/vms/monitor/{domainUuid}` | GET | `data.cpuRate` | number(double) | **虚拟机CPU利用率**；路径参数 `domainUuid`（必填） |
| 桌面的 内存%（VM 级） | 2.27.30 | 同上 | GET | `data.memRate` | number(double) | **虚拟机内存利用率** |
| 桌面的 磁盘%（VM 级） | 2.27.30 | 同上 | GET | `data.disk[].usage` 或 `data.partition[].usage` | number | **磁盘利用率，单位 %**；`partition` 为分区级数组（device/size/usage/usedSize） |
| 桌面的 CPU/内存/磁盘%（VM 级·批量） | 2.27.31 | `/vms/monitors/all` | GET | `data[].{cpuRate,memRate,disk[].usage}` | number | **批量获取所有「开机」虚拟机**性能；分页必填 `limit`/`offset`，可选 `clientIp`/`hostName`/`loginName`/`sortDir`/`sortField` |

**IPv4 备选字段**：`2.27.22 虚拟机详细信息` 返回 `data.vmIp`；`2.27.40 虚拟机列表（不分页）`  widespread `ipAddr` 字段。若 `server.addresses` 解析不稳定，可改取 `vmIp`/`ipAddr`。

**VM 级性能 DTO（2.27.30 / 2.27.31 同一结构，节选自文档）**

| 字段 | 中文 | 类型 | 备注 |
|---|---|---|---|
| `data.id` | 虚拟机 id | int64 | |
| `data.uuid` | 虚拟机 uuid | string | 2.27.30 路径参数即此值 |
| `data.cpuRate` | 虚拟机CPU利用率 | number(double) | |
| `data.memRate` | 虚拟机内存利用率 | number(double) | |
| `data.disk[]` | 磁盘 | CDisk | `device`(名称)/`usage`(利用率)/`ioStat`/`readLatency`… |
| `data.partition[]` | 分区 | RsDiskStat | `device`/`size`/`usage`(磁盘利用率，单位 %)/`usedSize` |
| `data.net` | 网络 | CNet | `mac`/`readFlow`/`writeFlow`/`readPackets`/`writePackets` |
| `data.status` | 虚拟机状态 | int32 | 0模板/1未知/2运行/3关闭/4暂停 |

---

## 2. 产品目标

在已有「桌面健康监控」页签基础上，按 H3C Workspace（E2010）REST API 把**桌面 IPv4、CPU 利用率、内存利用率、磁盘利用率**四要素做实、做准：补齐/核对接口与字段映射，确保表格中每一项指标都有 API 文档实证来源；在不改变轻量本地化运维定位的前提下，厘清「宿主机级」与「虚拟机/桌面级」性能口径，使运维人员一眼识别过载桌面或宿主，并能一键刷新与阈值预警。

---

## 3. 用户故事

- 作为管理员，我希望在健康监控表格中直接看到每台云桌面的 **IPv4、CPU%、内存%、磁盘%**，以便快速定位性能异常桌面，而不必逐台登录查证。
- 作为管理员，我希望明确知道表格里的 CPU% 是「该桌面自身」还是「所在宿主机」的口径，以便正确解读瓶颈归属（桌面过载 vs 宿主机过载）。
- 作为运维人员，我希望利用率超过阈值（默认 CPU/内存 ≥ 85%）的单元格红色高亮，并标注最后刷新时间，以便及时干预。
- 作为运维人员，我希望点击某行即可让 AI 基于该桌面的真实指标给出健康解读，以便获得针对性排查建议。
- 作为管理员，我希望刷新动作与现有采集节奏对齐、不重复拉取、不阻塞界面，以便大规模桌面环境下仍流畅可用。

---

## 4. 需求池（P0 / P1 / P2）

| 编号 | 功能点 | 优先级 | 验收标准 | 来源 |
|---|---|---|---|---|
| F2-H-01 | 桌面 IPv4 采集 | **P0** | 通过 2.9.11 `server.addresses[*].addr` 解析 IPv4；解析失败回退 `vmIp`(2.27.22) / `ipAddr`(2.27.40)；冒烟日志可见有效 IP 行数 | 用户需求 |
| F2-H-02 | CPU 利用率采集（四字段之一） | **P0** | 按 §5 确认的口径取 `cpuRate`：宿主级=2.4.12 `data.cpuRate` / 桌面级=2.27.30 `data.cpuRate`；数值为 double，UI 显示整数 % | 用户需求 |
| F2-H-03 | 内存利用率采集（四字段之一） | **P0** | 同上取 `memRate`；宿主级=2.4.12 / 桌面级=2.27.30 | 用户需求 |
| F2-H-04 | 磁盘利用率采集（四字段之一） | **P0** | 取 `disk[].usage`（单位 %）；多盘取 `max`；桌面级可改用 `partition[].usage` | 用户需求 |
| F2-H-05 | 四字段聚合行 `HealthRow` | **P0** | `HealthRow` 同时携带「宿主级」与「桌面级」指标字段，UI 按 §5 口径选择展示列 | 增量 |
| F2-H-06 | 健康表格列与阈值高亮 | **P1** | 列：名称/IP/操作系统/状态/CPU%/内存%/磁盘%；CPU/内存≥85% 红色（`health_panel.ThresholdCell` 已支持） | PRD F2-03 |
| F2-H-07 | 一键刷新 + 最后刷新时间 | **P1** | 复用现有「一键刷新」；刷新中禁用重复触发；展示最后刷新时间 | PRD F2-04 |
| F2-H-08 | 采集频率/自动刷新策略 | **P1** | 与现有 `HealthWorker` 节奏对齐（手动触发 + 可选定时）；单 VM 轮询改批量（2.27.31）以降低请求数 | §5 |
| F2-H-09 | 关机桌面性能兜底 | **P2** | 2.27.31 仅返回「开机」虚拟机；关机桌面 CPU/内存/磁盘置空或标记「关机」，不计入过载预警 | 增量 |
| F2-H-10 | AI 健康解读带入真实指标 | **P2** | 选中行「AI 解读」时把 IPv4/CPU%/内存%/磁盘% 作为上下文传入 Prompt | PRD F2-03 |

---

## 5. UI 设计稿（目标列 & 与现有 `health_panel.py` 的差异）

**目标表格列**（与现有 7 列基本一致，差异在 CPU 口径说明）：

| 列 | 字段 | 来源（按 §5 口径） | 与现有差异 |
|---|---|---|---|
| 名称 | `title` | 2.9.11 | 不变 |
| IP | `server.addresses[*].addr` | 2.9.11 | 不变（回退 `vmIp`/`ipAddr`） |
| 操作系统 | `osVersion` | 2.9.11 | 不变 |
| 状态 | `status` | 2.9.11 | 不变 |
| CPU% | `cpuRate` | **2.4.12（宿主）或 2.27.30（桌面）** | 列名需明确「宿主/桌面」前缀 |
| 内存% | `memRate` | 同上口径 | 同 CPU |
| 磁盘% | `disk[].usage`(max) | 同上口径 | 不变 |

**与现有 `health_panel.py` 的差异点**
1. 现有列名写死为「宿主CPU%」（硬编码于 `setHorizontalHeaderLabels` 与 `ThresholdCell` 绑定 `host_cpu`）。若 §5 确认改用桌面级，需：
   - 将 `HealthRow.host_cpu/host_mem/host_disk` 扩展为 `vm_cpu/vm_mem/vm_disk`（或并行保留宿主字段）；
   - 表头改为「桌面CPU% / 内存% / 磁盘%」或加「宿主CPU%」双列。
2. 现有 `health_worker.collect()` 流程：`query_vm_list` → 逐台 `get_vm_summary` → 逐 host `get_host_cpumemdisk`。若切桌面级，可改为：`query_vm_list` → 直接 `GET /vms/monitors/all`（2.27.31 批量，一次拿全部开机桌面 CPU/内存/磁盘），**大幅减少请求数**，但需补充关机桌面处理（F2-H-09）。
3. 阈值：`health_panel` 默认 `threshold_cpu=threshold_mem=85`，磁盘复用 `threshold_cpu`；建议磁盘阈值独立配置（§5 待确认）。

**采集方案对比（Mermaid）**

```mermaid
graph TD
    A[query_vm_list 2.27.40 拿全部桌面 id/uuid] --> B{CPU 口径?}
    B -->|宿主级 现状| C[逐 host 调 2.4.12 cpumemdiskrate<br/>cpuRate/memRate/disk[].usage]
    B -->|桌面级 升级| D[GET /vms/monitors/all 2.27.31<br/>批量拿开机桌面 cpuRate/memRate/disk]
    C --> E[聚合 HealthRow 展示 宿主CPU%]
    D --> F[聚合 HealthRow 展示 桌面CPU%<br/>关机桌面置空/标记]
```

---

## 6. 待确认问题（Open Questions）

1. **「桌面的 CPU」= 宿主 CPU 还是 桌面自身 CPU？**（最高优先级）
   - 截图列名是「**宿主CPU%**」，且 `PRD.md` F2-03 明确设计为宿主机 CPU（来自 2.4.12）；
   - 用户原话「桌面的 CPU」字面上指虚拟机自身（2.27.30/2.27.31 的 `data.cpuRate`=虚拟机CPU利用率）。
   - **需主理人/用户拍板**：维持宿主级（与 v1.0.0 一致、改动最小）还是升级为桌面级（更贴合原话，但需重构采集流程）。

2. **接口返回单条还是列表？**
   - 桌面级有两种：单台 `2.27.30 /vms/monitor/{domainUuid}`（需逐台循环，N 次请求）vs 批量 `2.27.31 /vms/monitors/all`（1 次分页返回全部开机桌面）。建议优先批量；需确认分页上限与总桌面规模。

3. **磁盘利用率：单盘还是多盘聚合？**
   - 宿主/桌面均返回 `disk[]`（多盘）。现有实现取 `max(usage)`；是否需展示「系统盘/数据盘」分别或平均？文档 `partition[]` 提供分区级 `usage`。

4. **采集频率 / 自动刷新如何与现有 `health_worker` 对齐？**
   - 现有为手动「一键刷新」+ `request_collect` 跨线程排队。是否增加定时自动刷新？批量接口（2.27.31）是否替代逐台轮询？

5. **IPv4 字段稳定性**
   - 当前 `server.addresses[*].addr` 已验证可用；若部分桌面无 `addresses`，是否回退 `vmIp`(2.27.22)/`ipAddr`(2.27.40)？

6. **阈值口径**
   - 磁盘% 是否沿用 CPU 的 85% 阈值，还是独立阈值？现有 `health_panel` 磁盘复用 `threshold_cpu`。

---

## 7. 字段映射与采集策略建议（供架构师参考，非结论）

- **最小改动路径（维持宿主级）**：沿用现状 `2.9.11 + 2.4.12`，仅核对字段名（`cpuRate`/`memRate`/`disk[].usage` 已实证），补齐 IPv4 回退解析。风险最低，与截图/`PRD.md` 完全一致。
- **贴合原话路径（升级桌面级）**：`query_vm_list`(2.27.40) → `GET /vms/monitors/all`(2.27.31) 批量拉取 CPU/内存/磁盘；状态/OS/IP 仍由 2.9.11 或 2.27.22 补充。注意 2.27.31 仅含「开机」桌面，关机桌面需兜底（F2-H-09）。
- 两份路径在 `workspace_client.py` 中均可新增方法（`get_vm_monitor` / `list_vm_monitors_all`），与现有 5 个接口风格一致（`RpcResult`/`RpcPagingResult` 外层）。

---
*本 PRD 所有接口路径、字段 key、类型/单位均实证于 `Workspace REST API文档（E2010）.docx`，可直接据此进入架构设计。*
