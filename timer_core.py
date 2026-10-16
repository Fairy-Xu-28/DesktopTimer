"""秒表核心逻辑 —— 纯 Python,不依赖任何界面库。

这是"前后端"里的**后端**:只管时间,不管怎么显示。界面层(见 timer_ui.py)
通过调用这里的 Stopwatch 读写计时状态。

三个关键设计:

1. **时钟可注入**。构造函数接受 clock 参数,默认用 time.monotonic。
   测试时传一个手工推进的假时钟,所有断言都是确定性的 —— 不用真的等几秒,
   也不受机器快慢影响。
2. **读数由时间差算出**。内部只记"已结束各段的累计秒数"和"当前段起点",
   每次读数都是 accumulated + (now - segment_start)。因此界面多久刷新一次
   完全不影响精度,长时间运行也不会累积误差。
3. **所有操作幂等**。重复点"开始"、重复点"暂停"都不会让时间跳变,
   返回值告诉你这一次调用是否真的改变了状态。
"""

from __future__ import annotations

import math
import time
from enum import Enum
from typing import Callable

__all__ = ["State", "Stopwatch", "format_hms"]

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE
FIELD_MIN_WIDTH = 2  # 时/分/秒各自的最小位数,不足补零

# 默认时钟用 monotonic 而不是 time():后者会受系统对时、时区调整影响,
# 可能让计时倒退。monotonic 只单调递增,专为测量时长设计。
DEFAULT_CLOCK: Callable[[], float] = time.monotonic


class State(Enum):
    """秒表的三种状态,互斥且穷尽。"""

    IDLE = "idle"        # 归零状态:显示 00:00:00
    RUNNING = "running"  # 计时中
    PAUSED = "paused"    # 暂停:已计时间保留


def format_hms(seconds: float) -> str:
    """把秒数格式化成 时:分:秒。

    - 不足 1 秒的部分向下取整(界面只显示整秒,所以 0.9 秒仍是 00:00:00)
    - 负数按 0 处理,不会出现 -1:59:59 这种读数
    - 小时位不足两位补零;超过 99 小时自然扩宽(如 100:00:00)
    """
    total = max(0, math.floor(seconds))
    hours, remainder = divmod(total, SECONDS_PER_HOUR)
    minutes, secs = divmod(remainder, SECONDS_PER_MINUTE)
    return (
        f"{hours:0{FIELD_MIN_WIDTH}d}:"
        f"{minutes:0{FIELD_MIN_WIDTH}d}:"
        f"{secs:0{FIELD_MIN_WIDTH}d}"
    )


class Stopwatch:
    """秒表状态机。所有时间计算都在这里,界面层只负责显示结果。"""

    def __init__(self, clock: Callable[[], float] = DEFAULT_CLOCK) -> None:
        self._clock = clock
        self._state: State = State.IDLE
        self._accumulated: float = 0.0    # 已结束各段的累计秒数
        self._segment_start: float = 0.0  # 当前段起点;仅 RUNNING 时有意义

    # ---------------- 状态查询 ----------------

    @property
    def state(self) -> State:
        """当前状态,由各个操作显式维护。

        曾经试过"从累计秒数是否为 0 推导状态"的写法,那样状态和读数不可能
        不自洽,但会带来一个真实缺陷:本机 time.monotonic() 的分辨率约 16 毫秒
        (Windows 上基于 GetTickCount64),同一 tick 内"开始"紧接着"暂停"两次
        读到的时钟值完全相同,累计秒数就是 0.0,于是"已暂停"会塌回"就绪",
        状态提示和按钮联动都会出错。显式维护状态字段就没有这个问题。
        """
        return self._state

    def is_running(self) -> bool:
        return self._state is State.RUNNING

    # ---------------- 读数 ----------------

    def total_seconds(self) -> float:
        """当前总用时(含正在进行的那一段)。"""
        if self._state is State.RUNNING:
            return self._accumulated + (self._clock() - self._segment_start)
        return self._accumulated

    def formatted(self) -> str:
        """当前读数的 时:分:秒 文本。"""
        return format_hms(self.total_seconds())

    # ---------------- 操作(全部幂等,返回本次是否改变了状态) ----------------

    def start(self) -> bool:
        """开始计时,或在暂停后继续。已在计时中则什么都不做。"""
        if self._state is State.RUNNING:
            return False  # 刻意不重置起点,否则已经走过的时间会丢失
        self._segment_start = self._clock()
        self._state = State.RUNNING
        return True

    def pause(self) -> bool:
        """暂停并保留已计时间。本来就没在计时则什么都不做。"""
        if self._state is not State.RUNNING:
            return False
        # 只读一次时钟,把这一段结算进累计值
        self._accumulated += self._clock() - self._segment_start
        self._state = State.PAUSED
        return True

    def toggle(self) -> bool:
        """计时中则暂停,否则开始。对应界面上的空格键。"""
        return self.pause() if self.is_running() else self.start()

    def reset(self) -> bool:
        """归零并停止。本来就在就绪状态则什么都不做。"""
        changed = self._state is not State.IDLE
        self._accumulated = 0.0
        self._segment_start = 0.0
        self._state = State.IDLE
        return changed
