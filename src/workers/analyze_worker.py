"""分析 Worker（对应架构 T13 / workers/analyze_worker.py）。

封装 Analyzer 三方法（explain_alarm / explain_health / answer），
在后台线程异步执行，结果通过信号回调到对应 Panel，不阻塞 UI。

- analyze_alarm(warn)  -> analysis_ready(str)
- explain_health(row)  -> health_analysis_ready(str)
- answer(query)        -> answer_ready(str)

健康解读的阈值（``threshold_cpu`` / ``threshold_mem``）由本 Worker 统一透传给
``Analyzer.explain_health``，保证 **AI 判定与 HealthPanel 表格着色同源**
（否则用户在设置里改阈值后会出现「表格标红但 AI 说正常」的自相矛盾）。
"""

from __future__ import annotations

import collections
from PyQt5.QtCore import pyqtSignal, QTimer

from core.logging_setup import get_logger
from .base_worker import BaseWorker


logger = get_logger(__name__)

# 与 HealthPanel / Analyzer 默认值保持一致（不传阈值时的兜底）
DEFAULT_THRESHOLD_CPU = 85
DEFAULT_THRESHOLD_MEM = 85


class AnalyzeWorker(BaseWorker):
    analysis_ready = pyqtSignal(str)            # 告警分析文本
    health_analysis_ready = pyqtSignal(str)     # 健康解读文本
    answer_ready = pyqtSignal(str)              # 问答文本
    answer_source = pyqtSignal(str)             # 问答来源提示 banner
    answer_chunks = pyqtSignal(list)            # 问答命中片段（list[Chunk]，供 UI 点击展开）

    def __init__(self, analyzer: object, threshold_cpu: int = DEFAULT_THRESHOLD_CPU,
                 threshold_mem: int = DEFAULT_THRESHOLD_MEM) -> None:
        super().__init__()
        self.analyzer = analyzer
        self._threshold_cpu = int(threshold_cpu)
        self._threshold_mem = int(threshold_mem)
        self._queue: "collections.deque" = collections.deque()
        self._timer: QTimer | None = None

    def set_health_thresholds(self, threshold_cpu: int, threshold_mem: int) -> None:
        """热更新健康解读阈值（配置保存后调用，与 HealthPanel 保持一致）。

        Args:
            threshold_cpu: CPU / 磁盘阈值（%）。
            threshold_mem: 内存阈值（%）。
        """
        self._threshold_cpu = int(threshold_cpu)
        self._threshold_mem = int(threshold_mem)

    # ---- 对外入口（主线程调用，仅入队） ----
    def analyze_alarm(self, warn) -> None:
        self._queue.append(("alarm", warn))

    def explain_health(self, row) -> None:
        self._queue.append(("health", row))

    def answer(self, query: str) -> None:
        self._queue.append(("answer", query))

    # ---- 线程循环 ----
    def run(self) -> None:
        self._timer = QTimer()
        self._timer.timeout.connect(self._pump)
        self._timer.start(200)  # 每 200ms 检查队列
        self.exec_()

    def _pump(self) -> None:
        if not self._queue:
            return
        kind, payload = self._queue.popleft()
        try:
            if kind == "alarm":
                text = self.analyzer.explain_alarm(payload)
                self.analysis_ready.emit(text)
            elif kind == "health":
                text = self.analyzer.explain_health(
                    payload, self._threshold_cpu, self._threshold_mem)
                self.health_analysis_ready.emit(text)
            elif kind == "answer":
                text, banner, chunks = self.analyzer.answer(payload)
                self.answer_ready.emit(text)
                self.answer_source.emit(banner)
                self.answer_chunks.emit(chunks)
        except Exception as exc:
            self.bridge(exc)
