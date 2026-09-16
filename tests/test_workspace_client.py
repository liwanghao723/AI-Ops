"""core.workspace_client 契约测试：用 FakeHTTP 替换底层调用，验证各接口字段解析。

覆盖：2.12.1 实时告警、2.27.40 虚拟机列表、2.9.11 概要、2.4.12 主机性能、
2.27.31 批量虚拟机性能（桌面级口径）、2.27.39 批量 UUID、2.4.7 主机列表，
以及 RpcResult 失败传播。
"""

import pytest

from core.errors import WorkspaceAPIError
from core.models import VmBrief, VmPerf
from core.workspace_client import H3CWorkspaceClient

_OK = {"success": True, "errorCode": 0, "failureMessage": "", "state": 0}


class FakeHTTP:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, uri, params=None, json=None):
        self.calls.append(("GET", uri, params, json))
        return self.responses[uri]

    def post(self, uri, json=None, params=None):
        self.calls.append(("POST", uri, params, json))
        return self.responses[uri]


def _client(responses):
    return H3CWorkspaceClient(FakeHTTP(responses))


def test_list_realtime_alarms_parsing():
    data = {
        "items": [
            {"id": 1, "eventName": "CPU过高", "eventDesc": "cpu 97%",
             "eventLevel": 1, "eventTime": 1000, "eventType": 4, "state": 2,
             "eventSrc": "host03", "eventCount": 5},
            {"id": 2, "eventName": "内存不足", "eventLevel": 2, "eventTime": 2000,
             "eventType": 5, "state": 1, "eventSrc": "vm1", "eventCount": 1,
             "eventDesc": "mem low"},
        ],
        "total": 2,
    }
    c = _client({"/realtimeAlarms/list": {**_OK, "data": data}})
    res = c.list_realtime_alarms(limit=50, offset=0)
    assert res.total == 2
    assert len(res.items) == 2
    w = res.items[0]
    assert w.id == 1 and w.eventLevel == 1 and w.eventName == "CPU过高" and w.eventCount == 5
    # 校验请求参数
    _, uri, params, _ = c.http.calls[0]
    assert uri == "/realtimeAlarms/list"
    assert params["limit"] == 50 and params["sortDir"] == 2 and params["sortField"] == "eventTime"


def test_list_realtime_alarms_flat_list_total_fallback():
    data = [{"id": 3, "eventName": "x", "eventLevel": 4, "eventTime": 1,
             "eventType": 1, "state": 2, "eventSrc": "s", "eventCount": 1, "eventDesc": "d"}]
    c = _client({"/realtimeAlarms/list": {**_OK, "data": data}})
    res = c.list_realtime_alarms(limit=10, offset=0)
    assert len(res.items) == 1
    assert res.total == 1  # 列表无 total 字段时回退为 len


def test_query_vm_list_parsing():
    """2.27.33 /vms/page 分页接口：单页即取满（totalLength 在外层）。"""
    data = [
        {"id": 10, "hostId": 2, "title": "桌面A", "name": "vm-a",
         "status": "running", "uuid": "uu1", "clusterId": 1, "cpu": 4, "memory": 8192},
        {"id": 11, "hostId": 3, "title": "桌面B", "name": "vm-b",
         "status": 1, "uuid": "uu2", "clusterId": 1, "cpu": 2, "memory": 4096},
    ]
    c = _client({"/vms/page": {**_OK, "data": data, "totalLength": 2}})
    vms = c.query_vm_list()
    assert len(vms) == 2
    assert isinstance(vms[0], VmBrief)
    v = vms[0]
    assert v.id == 10 and v.hostId == 2 and v.title == "桌面A" and v.uuid == "uu1" and v.cpu == 4
    # 数值型 status 转为字符串
    assert vms[1].status == "1"


def test_query_vm_list_paginates_when_over_page_size():
    """桌面数超过单页时按 offset 翻页直到取满 totalLength。"""
    page1 = [{"id": i, "uuid": f"u{i}", "title": f"vm-{i}", "status": "3"}
             for i in range(1, 201)]          # 200 条 = 单页上限
    page2 = [{"id": 201, "uuid": "u201", "title": "vm-201", "status": "3"}]
    http = FakeHTTP({
        "/vms/page": {**_OK, "data": page1, "totalLength": 201},
    })
    # 翻页：第二次请求 offset=200 时返回第 2 页
    base_get = http.get

    def paging_get(uri, params=None, json=None):
        if uri == "/vms/page" and params and params.get("offset", 0) >= 200:
            return {**_OK, "data": page2, "totalLength": 201}
        return base_get(uri, params=params, json=json)

    http.get = paging_get
    c = H3CWorkspaceClient(http)
    vms = c.query_vm_list()
    assert len(vms) == 201, f"应翻 2 页取满 201 台，实际 {len(vms)}"


def test_query_vm_list_parses_ipaddr_fallback():
    """2.27.33 的 ipAddr 需被 _clean_vm 提取（IPv4 第二级兜底）。"""
    data = [
        {"id": 12, "hostId": 3, "title": "桌面C", "uuid": "uu3",
         "status": "running", "ipAddr": "10.9.9.9"},
        {"id": 13, "hostId": 3, "title": "桌面D", "uuid": "uu4"},
    ]
    c = _client({"/vms/page": {**_OK, "data": data, "totalLength": 2}})
    vms = c.query_vm_list()
    assert vms[0].ipAddr == "10.9.9.9"
    assert vms[1].ipAddr == "", "缺失 ipAddr 时回退空串，由上层继续兜底为 '-'"


def test_get_vm_ip_by_uuid_parses_networks_ipaddr():
    """2.27.57 /vms/{uuid}：从 data.networks[].ipAddr 取 IPv4（桌面主来源）。"""
    detail = {
        "id": 18, "uuid": "uu1", "title": "vm-1",
        "networks": [{"ipAddr": "10.1.1.18", "mac": "0c:da:11:22:33:44"}],
    }
    c = _client({"/vms/uu1": {**_OK, "data": detail}})
    assert c.get_vm_ip_by_uuid("uu1") == "10.1.1.18"


def test_get_vm_ip_by_uuid_empty_when_no_ip():
    """无网卡 / 无 IP / networks 缺失时返回空串（上层回退 '-'）。"""
    # 空网卡列表
    c1 = _client({"/vms/uu2": {**_OK, "data": {"id": 2, "networks": []}}})
    assert c1.get_vm_ip_by_uuid("uu2") == ""
    # networks 字段缺失
    c2 = _client({"/vms/uu3": {**_OK, "data": {"id": 3}}})
    assert c2.get_vm_ip_by_uuid("uu3") == ""
    # 网卡存在但 ipAddr 为 null
    c3 = _client({"/vms/uu4": {**_OK, "data": {"networks": [{"ipAddr": None}]}}})
    assert c3.get_vm_ip_by_uuid("uu4") == ""


# ---------------------------------------------------------------------------
# 2.27.31 批量虚拟机性能（桌面级口径）
# ---------------------------------------------------------------------------
class _PagingHTTP:
    """按 limit/offset 返回分页数据的假 HTTP（用于 2.27.31 翻页验证）。"""

    def __init__(self, all_items, total=None, fail_page=None):
        self.all_items = all_items
        self.total = total
        self.fail_page = fail_page
        self.calls = []

    def get(self, uri, params=None, json=None):
        params = params or {}
        self.calls.append((uri, dict(params)))
        if self.fail_page is not None and len(self.calls) == self.fail_page:
            return {**_OK, "success": False, "errorCode": 5001,
                    "failureMessage": "boom", "data": None}
        limit = int(params.get("limit", 200))
        offset = int(params.get("offset", 0))
        page = self.all_items[offset:offset + limit]
        data = {"items": page}
        if self.total is not None:
            data["total"] = self.total
        return {**_OK, "data": data}


def _monitors(n: int, start: int = 0):
    return [{"uuid": f"uuid-{i}", "cpuRate": 10.0 + i, "memRate": 20.0 + i,
             "disk": [{"device": "C", "usage": 30.0 + i}]}
            for i in range(start, start + n)]


def test_list_vm_monitors_all_single_page():
    c = H3CWorkspaceClient(_PagingHTTP(_monitors(3), total=3))
    out = c.list_vm_monitors_all()
    assert len(out) == 3
    assert all(isinstance(m, VmPerf) for m in out)
    assert out[0].uuid == "uuid-0" and out[0].cpuRate == 10.0
    assert out[2].diskMaxUsage == 32.0
    uri, params = c.http.calls[0]
    assert uri == "/vms/monitors/all"
    assert params["limit"] == 200 and params["offset"] == 0


def test_list_vm_monitors_all_paging_until_total():
    """total=450 时需翻 3 页（200+200+50）并聚合为 450 条。"""
    c = H3CWorkspaceClient(_PagingHTTP(_monitors(450), total=450))
    out = c.list_vm_monitors_all()
    assert len(out) == 450
    assert [p["offset"] for _, p in c.http.calls] == [0, 200, 400]
    assert len({m.uuid for m in out}) == 450, "uuid 无重复"


def test_list_vm_monitors_all_no_total_short_page_ends():
    """外层无 total 时，以「短页」判定末页，不无限翻页。"""
    c = H3CWorkspaceClient(_PagingHTTP(_monitors(5), total=None))
    out = c.list_vm_monitors_all(page_limit=2)
    assert len(out) == 5
    assert [p["offset"] for _, p in c.http.calls] == [0, 2, 4]


def test_list_vm_monitors_all_dedupes_repeated_uuid():
    """分页边界重复返回同一 uuid 时只保留一条。"""
    items = _monitors(3) + [dict(_monitors(1)[0])]
    c = H3CWorkspaceClient(_PagingHTTP(items, total=4))
    out = c.list_vm_monitors_all(page_limit=10)
    assert len(out) == 3
    assert [m.uuid for m in out] == ["uuid-0", "uuid-1", "uuid-2"]


def test_list_vm_monitors_all_empty_data():
    c = H3CWorkspaceClient(_PagingHTTP([], total=0))
    assert c.list_vm_monitors_all() == []


def test_list_vm_monitors_all_api_error_propagates():
    c = H3CWorkspaceClient(_PagingHTTP(_monitors(10), total=10, fail_page=1))
    with pytest.raises(WorkspaceAPIError):
        c.list_vm_monitors_all()


def test_list_vm_monitors_all_partition_fallback():
    """端到端：2.27.31 返回 partition 而非 disk 时仍能解析出磁盘利用率。"""
    items = [{"uuid": "uuid-p1", "cpuRate": 11.0, "memRate": 22.0,
              "disk": [], "partition": [{"device": "C", "usage": 64.0},
                                        {"device": "D", "usage": 81.0}]}]
    c = H3CWorkspaceClient(_PagingHTTP(items, total=1))
    out = c.list_vm_monitors_all()
    assert len(out) == 1
    assert out[0].uuid == "uuid-p1"
    assert out[0].diskMaxUsage == 81.0


def test_list_vm_monitors_all_flat_list_payload():
    """data 直接为数组（无 items/total）时也能解析，且只请求一页。"""
    c = H3CWorkspaceClient(_PagingHTTP(_monitors(2), total=None))
    out = c.list_vm_monitors_all(page_limit=200)
    assert len(out) == 2
    assert len(c.http.calls) == 1, "无 total 且短页 → 首页即末页"


def test_get_vm_summary_parsing():
    data = {
        "title": "桌面A", "osVersion": "Windows 10", "status": "running",
        "server": {"hostId": 7, "addresses": {"eth0": {"addr": "192.168.1.20"}}},
    }
    c = _client({"/virtual/domain/10/summary": {**_OK, "data": data}})
    s = c.get_vm_summary(10)
    assert s.title == "桌面A" and s.osVersion == "Windows 10" and s.status == "running"
    assert s.hostId == 7 and s.ip == "192.168.1.20"


def test_get_host_cpumemdisk_diskmax():
    data = {"cpuRate": 55.5, "memRate": 66.6, "disk": [{"usage": 20}, {"usage": 88}, {"usage": 40}]}
    c = _client({"/hosts/5/cpumemdiskrate": {**_OK, "data": data}})
    hp = c.get_host_cpumemdisk(5)
    assert hp.cpuRate == 55.5
    assert hp.memRate == 66.6
    assert hp.diskMaxUsage == 88.0


def test_query_vms_by_uuids_post():
    data = [{"ip": "1.1.1.1", "osVersion": "Win10", "title": "t",
             "status": "running", "uuid": "u"}]
    c = _client({"/vms/queryUuidsByVmUuids": {**_OK, "data": data}})
    res = c.query_vms_by_uuids(["u"])
    assert res[0].ip == "1.1.1.1" and res[0].uuid == "u"
    method, uri, params, json = c.http.calls[0]
    assert method == "POST" and uri == "/vms/queryUuidsByVmUuids"
    assert json == {"vmUuids": ["u"]}


def test_list_hosts_parsing():
    data = [{"id": 1, "name": "h1", "status": "up"}, {"id": 2, "name": "h2", "status": "down"}]
    c = _client({"/hosts": {**_OK, "data": data}})
    hosts = c.list_hosts()
    assert hosts[0].name == "h1" and hosts[1].status == "down"


def test_api_error_propagates():
    c = _client({"/hosts": {**_OK, "success": False, "errorCode": 5001,
                            "failureMessage": "boom", "data": None}})
    with pytest.raises(WorkspaceAPIError):
        c.list_hosts()


def test_clean_warn_parses_first_latest_and_count():
    """_clean_warn 需正确解析 firstEventTime / eventTime / eventCount，且三者不串字段。"""
    raw = {
        "id": 7, "eventName": "存储告警", "eventDesc": "disk full",
        "eventLevel": 3, "eventTime": 5000, "firstEventTime": 1000,
        "eventType": 6, "state": 2, "eventSrc": "host9", "eventCount": 12,
    }
    cleaned = H3CWorkspaceClient._clean_warn(raw)
    assert cleaned["firstEventTime"] == 1000
    assert cleaned["eventTime"] == 5000
    assert cleaned["eventCount"] == 12
    # 首次 ≠ 最新，证明两时间字段均已正确解析而非互相覆盖
    assert cleaned["firstEventTime"] != cleaned["eventTime"]


def test_list_realtime_alarms_parses_first_event_time():
    """端到端：list_realtime_alarms 经 _clean_warn 后 WarnInfoDTO 携带 firstEventTime。"""
    data = {
        "items": [
            {"id": 1, "eventName": "CPU过高", "eventDesc": "cpu 97%",
             "eventLevel": 1, "eventTime": 5000, "firstEventTime": 1000,
             "eventType": 4, "state": 2, "eventSrc": "host03", "eventCount": 5},
        ],
        "total": 1,
    }
    c = _client({"/realtimeAlarms/list": {**_OK, "data": data}})
    res = c.list_realtime_alarms(limit=50, offset=0)
    w = res.items[0]
    assert w.firstEventTime == 1000
    assert w.eventTime == 5000
    assert w.eventCount == 5
    assert w.firstEventTime != w.eventTime


def test_clean_warn_defaults_first_event_time_to_zero():
    """原始数据缺 firstEventTime 时回退为 0，不抛异常。"""
    raw = {"id": 9, "eventName": "x", "eventLevel": 2, "eventTime": 3000,
           "eventType": 1, "state": 1, "eventSrc": "s", "eventCount": 1,
           "eventDesc": "d"}
    cleaned = H3CWorkspaceClient._clean_warn(raw)
    assert cleaned["firstEventTime"] == 0
    assert cleaned["eventTime"] == 3000


def test_clean_warn_first_and_latest_distinct_raw_values():
    """独立复核（QA 严过关）：首/末时间原始值刻意不同(1000 vs 5000)，
    经 _clean_warn 后两字段互不覆盖，证明无串字段。
    注意：此处断言原始数值（与 _fmt_time 的分钟级格式化无关），必定可区分。
    """
    raw = {"id": 1, "eventName": "x", "eventLevel": 1, "eventTime": 5000,
           "firstEventTime": 1000, "eventType": 1, "state": 1, "eventSrc": "s",
           "eventCount": 2, "eventDesc": "d"}
    cleaned = H3CWorkspaceClient._clean_warn(raw)
    assert cleaned["firstEventTime"] == 1000
    assert cleaned["eventTime"] == 5000
    assert cleaned["firstEventTime"] != cleaned["eventTime"]


# ---------------------------------------------------------------------------
# getHostList / domainDetail（本次新增）
# ---------------------------------------------------------------------------
def test_get_host_list_parses_raw_host_dicts():
    """get_host_list 返回 data 内的原始 host dict 列表，不做字段强映射。

    真实平台该接口字段名不稳定（name/hostName/...），必须保留原始结构，
    由各调用方防御式读取。
    """
    data = [
        {"id": 1, "hostName": "h-01", "status": "up"},
        {"id": 2, "name": "h-02", "ip": "10.0.0.2"},
    ]
    c = _client({"/hosts/getHostList": {**_OK, "data": data}})
    hosts = c.get_host_list()
    assert len(hosts) == 2
    assert hosts[0]["hostName"] == "h-01", "原始字段（hostName）应被保留"
    assert hosts[1]["name"] == "h-02", "原始字段（name）应被保留"
    assert "ip" in hosts[1], "原始字段（ip）应被保留"
    method, uri, params, json = c.http.calls[0]
    assert method == "GET" and uri == "/hosts/getHostList"


def test_get_host_list_wraps_items_key():
    """data 为 {items:[...]} 包装时，_extract_list 仍能展开为列表。"""
    c = _client({"/hosts/getHostList": {**_OK, "data": {"items": [{"id": 9, "hostName": "x"}]}}})
    hosts = c.get_host_list()
    assert len(hosts) == 1 and hosts[0]["hostName"] == "x"


def test_get_host_list_api_error_propagates():
    c = _client({"/hosts/getHostList": {**_OK, "success": False,
                                        "errorCode": 9001,
                                        "failureMessage": "boom", "data": None}})
    with pytest.raises(WorkspaceAPIError):
        c.get_host_list()


def test_get_domain_detail_returns_full_raw():
    """get_domain_detail 返回 H3C 统一外层整包（含 success/data/...），不强制映射。"""
    raw = {**_OK, "data": {"ip": "192.168.1.5", "cpuRate": 12.0,
                           "memRate": 34.0, "hostName": "h-01"}}
    c = _client({"/virtual/domain/10/domainDetail": raw})
    detail = c.get_domain_detail(10)
    assert detail["success"] is True
    assert detail["data"]["ip"] == "192.168.1.5"
    assert detail["data"]["cpuRate"] == 12.0
    method, uri, params, json = c.http.calls[0]
    assert method == "GET" and uri == "/virtual/domain/10/domainDetail"


def test_get_domain_detail_api_error_propagates():
    """真实平台对「桌面级详情」类接口已知可能返回 400，方法须正确上抛。"""
    c = _client({"/virtual/domain/10/domainDetail": {**_OK, "success": False,
                                                     "errorCode": 400,
                                                     "failureMessage": "bad request",
                                                     "data": None}})
    with pytest.raises(WorkspaceAPIError):
        c.get_domain_detail(10)


def test_workspace_client_logs_url_on_error(caplog):
    """REST 请求出错（WorkspaceAPIError）时，日志必须包含完整 URI 与状态码，
    方便后续排查（如 2.27.31 返回 400 但无明显请求上下文）。"""
    import logging

    caplog.set_level(logging.DEBUG, logger="core.workspace_client")
    c = _client({"/vms/monitors/all": {**_OK, "success": False,
                                       "errorCode": 400,
                                       "failureMessage": "请求被拒绝"}})
    with pytest.raises(WorkspaceAPIError):
        c.list_vm_monitors_all()

    combined = "\n".join(r.getMessage() for r in caplog.records)
    # 必须包含完整请求路径（含 /vdi/rest/workspace 前缀与查询串）
    assert "/vdi/rest/workspace/vms/monitors/all" in combined, \
        "日志应包含完整 URI"
    assert "400" in combined, "日志应包含 HTTP 状态码"


# ---------------------------------------------------------------------------
# 2.26 虚拟机使用率查询（center 前缀，前缀切换验证）
# ---------------------------------------------------------------------------
def test_get_vm_usages_no_params():
    """无过滤参数调用 /vmusages（仅必填 queryType），返回 H3C 统一外层整包 dict。"""
    c = _client({"/vmusages": {**_OK, "data": {"items": []}}})
    raw = c.get_vm_usages(1)
    assert isinstance(raw, dict), "应返回 dict 整包"
    assert raw["success"] is True
    method, uri, params, _json = c.http.calls[0]
    assert method == "GET" and uri == "/vmusages"
    assert params == {"queryType": 1}, "仅必填 queryType 时应只带该查询参数"


def test_get_vm_usages_with_host_id():
    """传入 query_type=2 + host_id=5，验证 params 含 queryType=2 与 hostId=5。"""
    c = _client({"/vmusages": {**_OK, "data": []}})
    raw = c.get_vm_usages(2, host_id=5)
    method, uri, params, _json = c.http.calls[0]
    assert uri == "/vmusages"
    assert params["queryType"] == 2, "必填 queryType 必须出现在 params"
    assert params["hostId"] == 5, "host_id 应映射为查询参数 hostId"
    assert raw["success"] is True


def test_get_vm_usages_extra_params_passed_through():
    """**extra 透传：clusterId 等非固定参数应进入查询参数，且必填 queryType 同在。"""
    c = _client({"/vmusages": {**_OK, "data": {}}})
    c.get_vm_usages(3, cluster_id=7)
    method, uri, params, _json = c.http.calls[0]
    assert params["queryType"] == 3, "必填 queryType 必须出现在 params"
    assert params["clusterId"] == 7, "extra 参数应原样透传"
    assert "hostId" not in params, "未传的 host_id 不应出现在 params"


def test_get_vm_usages_requires_query_type_in_params():
    """回归：必填 queryType 必须出现在底层请求 params 中，且值正确（如 2）。"""
    c = _client({"/vmusages": {**_OK, "data": {}}})
    c.get_vm_usages(2)
    method, uri, params, _json = c.http.calls[0]
    assert method == "GET" and uri == "/vmusages"
    assert "queryType" in params, "缺少必填 queryType 会导致真实平台 400"
    assert params["queryType"] == 2, "queryType 取值应与传入一致"
    # 与 **extra 透传互不干扰
    c.get_vm_usages(5, pool_id=9)
    _, _, params2, _ = c.http.calls[1]
    assert params2["queryType"] == 5 and params2["poolId"] == 9


class PrefixSwitchHTTP:
    """模拟真实 DigestAuthHTTP：记录请求瞬间的 base_url 与 uri，用于验证前缀切换。

    具备 ``base_url`` 属性，触发 H3CWorkspaceClient 的临时前缀切换逻辑。
    """

    def __init__(self, base_url: str, responses: dict | None = None):
        self.base_url = base_url
        self.responses = responses or {"/vmusages": {**_OK, "data": {}}}
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, uri, params=None, json=None):
        # 记录「请求瞬间」的 base_url（此时应已被切到 center 前缀）
        self.calls.append((self.base_url, uri, params))
        return self.responses.get(uri, {**_OK, "data": {}})


def test_get_vm_usages_prefix_switch_on_real_http():
    """用 mock DigestAuthHTTP 验证 base_url 被临时切到 /vdi/rest/center 并在之后恢复。"""
    workspace_url = "http://10.1.1.201:8083/vdi/rest/workspace"
    center_url = "http://10.1.1.201:8083/vdi/rest/center"
    http = PrefixSwitchHTTP(workspace_url)
    c = H3CWorkspaceClient(http)

    res = c.get_vm_usages(2, host_id=5)

    # 1) 请求期间 base_url 应已被切到 center 前缀根
    base, uri, params = http.calls[0]
    assert base == center_url, f"请求期间 base_url 应为 {center_url}，实际 {base}"
    assert uri == "/vmusages"
    assert params["queryType"] == 2, "必填 queryType 必须出现在 params"
    assert params["hostId"] == 5
    # 2) 请求结束后 base_url 必须恢复为 workspace 前缀根（异常后也需恢复）
    assert http.base_url == workspace_url, \
        "请求结束后 base_url 应恢复为 workspace 前缀"
    assert res["success"] is True


def test_get_vm_usages_prefix_switch_restored_on_api_error():
    """即便接口返回业务失败（success=False），base_url 仍须恢复为 workspace 前缀。"""
    workspace_url = "http://10.1.1.201:8083/vdi/rest/workspace"
    http = PrefixSwitchHTTP(workspace_url, responses={
        "/vmusages": {**_OK, "success": False, "errorCode": 7001,
                      "failureMessage": "boom", "data": None},
    })
    c = H3CWorkspaceClient(http)
    with pytest.raises(WorkspaceAPIError):
        c.get_vm_usages(4, vm_id=3)
    # 异常后 base_url 必须恢复
    assert http.base_url == workspace_url, \
        "异常后 base_url 应恢复为 workspace 前缀"


# ---------------------------------------------------------------------------
# 2.27.30 单台虚拟机监控 + 2.4.5 主机监控（本次新增）
# ---------------------------------------------------------------------------
def test_get_vm_monitor_returns_raw_dict():
    """get_vm_monitor 返回 H3C 统一外层整包 dict（不做字段强映射）。"""
    raw = {**_OK, "data": {"cpuRate": 12.0, "memRate": 34.0, "cpu": 12.0, "mem": 34.0}}
    c = _client({"/vms/monitor/abc-123": raw})
    detail = c.get_vm_monitor("abc-123")
    assert isinstance(detail, dict), "应返回 dict 整包"
    assert detail["success"] is True
    assert detail["data"]["cpuRate"] == 12.0
    assert detail["data"]["memRate"] == 34.0
    method, uri, params, json = c.http.calls[0]
    assert method == "GET" and uri == "/vms/monitor/abc-123"


def test_get_vm_monitor_path_has_uuid():
    """底层请求路径必须包含传入的 domain_uuid，便于在真实平台核对。"""
    raw = {**_OK, "data": {}}
    c = _client({"/vms/monitor/abc-123": raw})
    c.get_vm_monitor("abc-123")
    method, uri, params, json = c.http.calls[0]
    assert method == "GET" and uri == "/vms/monitor/abc-123"
    assert "abc-123" in uri, "请求路径应包含传入的 uuid"


def test_get_vm_monitor_api_error_propagates():
    """真实平台若也返回 400，方法须正确上抛 WorkspaceAPIError。"""
    c = _client({"/vms/monitor/abc-123": {**_OK, "success": False,
                                          "errorCode": 400,
                                          "failureMessage": "bad request",
                                          "data": None}})
    with pytest.raises(WorkspaceAPIError):
        c.get_vm_monitor("abc-123")


def test_get_host_monitor_returns_raw_dict():
    """get_host_monitor 返回整包，且 data 含 vmTopCpuRate/vmTopMemRate 字段。"""
    data = {
        "items": [
            {"id": 1, "name": "host-01", "cpuRate": 45.0, "memRate": 60.0,
             "diskRate": 30.0, "occRate": 0.8,
             "vmTopCpuRate": [{"vmUuid": "v1", "cpuRate": 90.0},
                              {"vmUuid": "v2", "cpuRate": 80.0}],
             "vmTopMemRate": [{"vmUuid": "v1", "memRate": 88.0},
                              {"vmUuid": "v2", "memRate": 77.0}]},
        ],
    }
    c = _client({"/hosts/hostMonitor": {**_OK, "data": data}})
    raw = c.get_host_monitor()
    assert isinstance(raw, dict), "应返回 dict 整包"
    assert raw["success"] is True
    items = c._extract_list(raw.get("data"))
    assert len(items) == 1
    host = items[0]
    assert host["cpuRate"] == 45.0
    assert "vmTopCpuRate" in host, "应包含每台主机 top5 虚拟机 CPU 利用率"
    assert "vmTopMemRate" in host, "应包含每台主机 top5 虚拟机内存利用率"
    assert len(host["vmTopCpuRate"]) == 2
    method, uri, params, json = c.http.calls[0]
    assert method == "GET" and uri == "/hosts/hostMonitor"


def test_get_host_monitor_api_error_propagates():
    """接口返回业务失败时方法须正确上抛 WorkspaceAPIError。"""
    c = _client({"/hosts/hostMonitor": {**_OK, "success": False,
                                        "errorCode": 5001,
                                        "failureMessage": "boom", "data": None}})
    with pytest.raises(WorkspaceAPIError):
        c.get_host_monitor()


# ----------------------------------------------------------------------
# 连接池装配 / 释放（性能改动 C）
# ----------------------------------------------------------------------
def _cfg(max_concurrency: int):
    from core.config import AppConfig
    cfg = AppConfig()
    cfg.platform.base_url = "http://10.1.1.201:8083"
    cfg.platform.user = "u"
    cfg.platform.password = "p"
    cfg.health.max_concurrency = max_concurrency
    return cfg


def test_from_config_sizes_pool_from_concurrency():
    """连接池容量必须 ≥ 采集并发数，否则线程拿不到可复用连接。"""
    for conc in (5, 20, 64, 200):
        client = H3CWorkspaceClient.from_config(_cfg(conc))
        try:
            assert client.http.pool_maxsize >= conc, \
                f"并发 {conc} 时 pool_maxsize={client.http.pool_maxsize} 偏小"
            assert client.http.pool_connections >= 10
        finally:
            client.close()


def test_client_close_delegates_and_tolerates_missing_close():
    """close() 应转发到底层；底层无 close（测试替身）时也必须安全。"""
    seen = []

    class WithClose(FakeHTTP):
        def close(self):
            seen.append(True)

    H3CWorkspaceClient(WithClose({})).close()
    assert seen == [True]

    # FakeHTTP 无 close 属性 → 不应抛错
    H3CWorkspaceClient(FakeHTTP({})).close()

