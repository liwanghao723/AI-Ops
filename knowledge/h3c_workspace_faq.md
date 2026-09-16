# H3C Workspace 云桌面运维 FAQ

## 平台连接
- H3C Workspace REST 基础地址格式：`http://<ip>:<port>/vdi/rest/workspace/{URI}`，默认端口 8083。
- 接口使用 HTTP Digest（摘要）认证，账号需具备「外部接口认证用户-管理员」权限。
- 认证失败返回 401，请检查账号/密码；4xx 非 401 表示请求被拒绝，5xx 表示服务端异常。

## 实时告警（2.12.1）
- 告警级别编码：1=紧急、2=重要、3=次要、4=警告。
- 告警状态：1=已确认、2=未确认。
- 事件类型：1=主机、2=虚拟机、3=集群、4=CPU、5=内存、6=虚拟化软件。
- 列表接口必填参数：limit、offset、sortDir（1 升序 / 2 降序）、sortField。

## 桌面与主机性能
- 查询全部桌面：GET /vms/queryVmList（可选 domainName）。
- 单桌面概要：GET /virtual/domain/{id}/summary，含 title、osVersion、status、server.hostId、server.addresses。
- 宿主机性能：GET /hosts/{id}/cpumemdiskrate，返回 cpuRate、memRate、disk[]（含 device、usage）。
- 健康监控阈值默认 CPU/内存 ≥ 85% 视为过高，需红色预警。

## 常见问题
- 桌面无法连接：先确认宿主机 CPU/内存是否过载，再检查网络与虚拟机状态。
- 告警风暴：批量同类告警可合并关注，优先处理级别为「紧急/重要」的事件。
