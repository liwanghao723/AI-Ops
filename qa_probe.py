"""QA 独立验证脚本（临时文件，验证后删除）。

不依赖工程师写的测试用例，用自建 mock 独立复核增量三的真实行为。
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.models import WarnInfoDTO, PageResult
from workers.alarm_worker import AlarmWorker, MAX_ALARM_COUNT

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  -> {detail}"))


def _warn(i: int) -> WarnInfoDTO:
    return WarnInfoDTO(id=str(i), eventName=f"n{i}", eventDesc="d",
                       eventLevel=1, eventTime=1000 + i, state=0)


class SliceClient:
    """正常分页服务端：按 limit/offset 如实切片，如实上报 total。"""

    def __init__(self, total: int, page_cap: int | None = None,
                 max_calls: int = 50) -> None:
        self.total = total
        self.page_cap = page_cap
        self.calls: list[tuple[int, int]] = []
        self.max_calls = max_calls

    def list_realtime_alarms(self, limit, offset=0, **kw):
        self.calls.append((limit, offset))
        if len(self.calls) > self.max_calls:
            raise RuntimeError(f"疑似死循环：调用次数超过 {self.max_calls}")
        n = min(limit, self.total - offset)
        if self.page_cap is not None:
            n = min(n, self.page_cap)
        n = max(n, 0)
        items = [_warn(offset + i) for i in range(n)]
        return PageResult(code=0, msg="ok", state=0, total=self.total, items=items)


class EmptyPageLiarClient:
    """恶意/异常服务端：上报 total=500，但 offset>0 时返回空页。

    用于探测 max_count=None（不限制）时是否存在死循环风险。
    """

    def __init__(self, total: int = 500, first_page: int = 100,
                 max_calls: int = 30) -> None:
        self.total = total
        self.first_page = first_page
        self.calls = 0
        self.max_calls = max_calls

    def list_realtime_alarms(self, limit, offset=0, **kw):
        self.calls += 1
        if self.calls > self.max_calls:
            raise RuntimeError(f"死循环确认：调用 {self.calls} 次仍未终止")
        if offset == 0:
            items = [_warn(i) for i in range(self.first_page)]
        else:
            items = []          # 服务端谎报 total，后续页恒空
        return PageResult(code=0, msg="ok", state=0, total=self.total, items=items)


def collect(worker: AlarmWorker) -> list:
    got: list = []
    worker.alarms_ready.connect(lambda lst: got.append(lst))
    return got


print("\n=== A. AlarmWorker 上限逻辑独立复核 ===")

# A1 默认上限就是 10000
w = AlarmWorker(SliceClient(total=1), interval_sec=30)
check("A1 默认 max_count == MAX_ALARM_COUNT(10000)",
      w._max_count == MAX_ALARM_COUNT, f"实际={w._max_count}")

# A2 cap=None 时拉满 total=12000（不被 10000 截断）
c = SliceClient(total=12000)
w = AlarmWorker(c, page_size=10000, max_count=None)
got = collect(w)
w.fetch_once()
check("A2 max_count=None 拉满 12000 条",
      got and len(got[0]) == 12000, f"实际={len(got[0]) if got else 'no emit'}")

# A3 cap=1000 且 page_size=10000：精确 1000 条，且只调 1 次 API
c = SliceClient(total=12000)
w = AlarmWorker(c, page_size=10000, max_count=1000)
got = collect(w)
w.fetch_once()
check("A3 cap=1000 精确截断到 1000 条",
      got and len(got[0]) == 1000, f"实际={len(got[0]) if got else 'no emit'}")
check("A3b cap=1000 只发 1 次请求（不多拉）",
      len(c.calls) == 1, f"实际调用={c.calls}")

# A4 cap=5000 精确
c = SliceClient(total=12000)
w = AlarmWorker(c, page_size=10000, max_count=5000)
got = collect(w)
w.fetch_once()
check("A4 cap=5000 精确截断到 5000 条",
      got and len(got[0]) == 5000, f"实际={len(got[0]) if got else 'no emit'}")

# A5 set_max_count 真的改变后续行为：10000 -> None -> 1000
c = SliceClient(total=12000)
w = AlarmWorker(c, page_size=10000)          # 默认 10000
got = collect(w)
w.fetch_once()
first = len(got[-1])
w.set_max_count(None)
w.fetch_once()
second = len(got[-1])
w.set_max_count(1000)
w.fetch_once()
third = len(got[-1])
check("A5 set_max_count 依次生效 10000/None(12000)/1000",
      (first, second, third) == (10000, 12000, 1000),
      f"实际={(first, second, third)}")

# A6 服务端单页上限 1000 + cap=None + total=5000 → 应拉满 5000（信任 total 翻页）
c = SliceClient(total=5000, page_cap=1000)
w = AlarmWorker(c, page_size=10000, max_count=None)
got = collect(w)
w.fetch_once()
check("A6 cap=None + 服务端单页上限1000 仍拉满 5000",
      got and len(got[0]) == 5000, f"实际={len(got[0]) if got else 'no emit'}")

# A7 cap=None 且服务端谎报 total（后续页恒空）→ 是否死循环？
c = EmptyPageLiarClient(total=500, first_page=100)
w = AlarmWorker(c, page_size=100, max_count=None)
got = collect(w)
err: list = []
w.error.connect(lambda m: err.append(m))
try:
    w.fetch_once()
    looped = False
    detail = f"正常终止，emit={len(got[0]) if got else 'no emit'} 条"
except RuntimeError as exc:
    looped = True
    detail = str(exc)
# fetch_once 内部 try/except 会把异常桥接到 error 信号
if err and "死循环" in str(err):
    looped = True
    detail = f"error 信号: {err}"
check("A7 cap=None + 服务端谎报total 不死循环", not looped, detail)

print("\n=== B. AlarmPanel UI 独立复核 ===")
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from ui.alarm_panel import AlarmPanel

p = AlarmPanel()

check("B1 cb_limit 选项 == [不限制,1000,5000,10000]",
      [p.cb_limit.itemText(i) for i in range(p.cb_limit.count())]
      == ["不限制", "1000", "5000", "10000"],
      f"实际={[p.cb_limit.itemText(i) for i in range(p.cb_limit.count())]}")
check("B2 cb_limit 默认 10000", p.cb_limit.currentText() == "10000",
      f"实际={p.cb_limit.currentText()}")
check("B3 current_cap() 默认 == 10000", p.current_cap() == 10000,
      f"实际={p.current_cap()}")
check("B4 初始 lbl_count == '共 0 条'", p.lbl_count.text() == "共 0 条",
      f"实际={p.lbl_count.text()!r}")

mapping = {}
for txt in ["不限制", "1000", "5000", "10000"]:
    p.cb_limit.setCurrentText(txt)
    mapping[txt] = p.current_cap()
check("B5 current_cap 映射正确", mapping == {"不限制": None, "1000": 1000,
                                            "5000": 5000, "10000": 10000},
      f"实际={mapping}")
check("B5b current_cap 返回 int 而非 str",
      isinstance(mapping["1000"], int), f"实际类型={type(mapping['1000'])}")

# 计数：set_alarms 后显示可见条数
alarms = [_warn(i) for i in range(7)]
p.set_alarms(alarms)
check("B6 set_alarms(7条) 后 lbl_count == '共 7 条'",
      p.lbl_count.text() == "共 7 条", f"实际={p.lbl_count.text()!r}")
check("B6b 表格行数与计数一致", p.table.rowCount() == 7,
      f"实际行数={p.table.rowCount()}")

# 过滤后计数应变化（用关键字过滤到 0）
p.le_keyword.setText("绝不匹配ZZZ")
p.apply_filters()
check("B7 全过滤后 lbl_count == '共 0 条'",
      p.lbl_count.text() == "共 0 条", f"实际={p.lbl_count.text()!r}")
p.le_keyword.setText("")
p.apply_filters()
check("B7b 清空关键字后恢复 '共 7 条'",
      p.lbl_count.text() == "共 7 条", f"实际={p.lbl_count.text()!r}")

# 关键验证：计数是「可见条数」而非「拉取总数」
alarms2 = [_warn(i) for i in range(5)]
alarms2[0].eventLevel = 1
for a in alarms2[1:]:
    a.eventLevel = 3
p.set_alarms(alarms2)
p.cb_level.setCurrentText(p.cb_level.itemText(1))  # 选一个具体等级
vis = len(p._visible)
check("B8 计数=可见条数(len(_visible)) 而非拉取总数(len(_alarms))",
      p.lbl_count.text() == f"共 {vis} 条" and len(p._alarms) == 5,
      f"lbl={p.lbl_count.text()!r} visible={vis} alarms={len(p._alarms)}")

# 切换上限是否触发重新拉取？（只连了 apply_filters，本地过滤而已）
p.cb_level.setCurrentText("全部")
p.set_alarms([_warn(i) for i in range(6)])
before = p.lbl_count.text()
p.cb_limit.setCurrentText("1000")
after = p.lbl_count.text()
check("B9 切换上限不崩溃且不改变本地计数（仅下次拉取生效）",
      before == after == "共 6 条", f"before={before!r} after={after!r}")

print("\n=== C. MainWindow 接线复核（cap 必须先于 emit） ===")
import inspect
from ui import main_window as mw_mod
src = inspect.getsource(mw_mod.MainWindow._on_alarm_query)
i_cap = src.find("current_cap")
i_set = src.find("set_max_count")
i_emit = src.find("request_fetch.emit")
check("C1 _on_alarm_query 内包含 current_cap/set_max_count/emit",
      min(i_cap, i_set, i_emit) >= 0, f"idx={(i_cap, i_set, i_emit)}")
check("C2 顺序正确：current_cap -> set_max_count -> emit",
      i_cap < i_set < i_emit, f"idx={(i_cap, i_set, i_emit)}")

print("\n=== 汇总 ===")
fails = [r for r in RESULTS if not r[1]]
print(f"总计 {len(RESULTS)} 项，通过 {len(RESULTS) - len(fails)}，失败 {len(fails)}")
for name, _, detail in fails:
    print(f"  FAILED: {name} -> {detail}")
