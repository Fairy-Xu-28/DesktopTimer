"""计时核心逻辑 —— 纯 Python,不依赖任何界面库。

这是"前后端"里的**后端**:只管时间,不管怎么显示。界面层(见 timer_ui.py)
通过调用这里的 Stopwatch / Countdown 读写计时状态。

两种计时器,一套接口:

- ``Stopwatch`` 从 0 往上数
- ``Countdown`` 从设定的时长往下数

两者的 ``state`` / ``start`` / ``pause`` / ``toggle`` / ``reset`` / ``formatted``
完全同形(见 ``Timer`` 协议),所以界面层拿同一个变量驱动就行,
不用在每处操作前面判断"现在跑的是哪种"。它们的区别只有读数的算法。

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
from typing import Callable, Protocol, runtime_checkable

__all__ = [
    "State",
    "Stopwatch",
    "Countdown",
    "Timer",
    "format_hms",
    "parse_hms",
    "DEFAULT_COUNTDOWN_SECONDS",
    "MIN_COUNTDOWN_SECONDS",
    "MAX_COUNTDOWN_SECONDS",
]

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE
FIELD_MIN_WIDTH = 2  # 时/分/秒各自的最小位数,不足补零

# 倒计时的默认时长(5 分钟)和上下限。
# 上限取 99:59:59 —— 显示区的小时位是两位宽(format_hms 超过 99 小时会自己扩宽,
# 那样数字会突然变宽、把布局挤歪),所以干脆在入口处就挡住。
MIN_COUNTDOWN_SECONDS = 1
MAX_COUNTDOWN_SECONDS = 99 * SECONDS_PER_HOUR + 59 * SECONDS_PER_MINUTE + 59
DEFAULT_COUNTDOWN_SECONDS = 5 * SECONDS_PER_MINUTE

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


def parse_hms(text: str) -> int:
    """把用户手打的时长解析成秒数,不合法就抛 ``ValueError``。

    接受三种写法(和 ``format_hms`` 的输出是对应的):

    ==================  ================  ==========
    写法                 意思              例子
    ==================  ================  ==========
    ``时:分:秒``        完整写法          ``1:30:00`` → 5400
    ``分:秒``           省掉小时位        ``05:00`` → 300
    纯数字              按**秒**算        ``90`` → 90
    ==================  ================  ==========

    纯数字按秒而不是按分,是刻意选的:秒是本模块的基本单位,``parse`` 和
    ``format`` 才对得上(``parse_hms("90") == 90``,而 ``format_hms(90)``
    就是 ``00:01:30``)。输入框的提示语里会写清楚,不会让人猜。

    拒绝的情况:空串、非数字、负数、分或秒 ≥ 60、超过三段、
    时长为 0、以及超过 ``MAX_COUNTDOWN_SECONDS``。
    **一律抛 ValueError 而不是返回 0 或 None** —— 调用方要把原因告诉用户,
    静默吞掉的话用户会以为程序坏了。
    """
    if not isinstance(text, str):
        raise ValueError(f"时长必须是文字,收到 {type(text).__name__}")

    # 中文输入法下打出来的是全角冒号,顺手换掉 —— 不然用户会看到一个
    # 完全看不懂为什么失败的报错(字符串看着跟正常的一模一样)。
    body = text.strip().replace("：", ":")
    if not body:
        raise ValueError("没有输入任何内容")

    parts = body.split(":")
    if len(parts) > 3:
        # 冒号数量不对时报错要带上"收到了几段",否则用户对着 "1:2:3:4"
        # 看不出哪里不对劲
        raise ValueError(f"最多三段(时:分:秒),收到 {len(parts)} 段")

    fields: list[int] = []
    for part in parts:
        field = part.strip()
        # isdigit() 对负数、小数点、正负号都返回 False,正好一并挡掉
        if not field.isdigit():
            raise ValueError(f"「{part}」不是数字")
        fields.append(int(field))

    if len(fields) == 1:
        # 纯数字整体按秒算,**不能**走下面的"分/秒各自小于 60"那两道检查 ——
        # 90 是个合法的秒数,但当作"90 分 0 秒"去校验就会因为分位 90 ≥ 60 被拒。
        return _check_total(fields[0])

    if len(fields) == 2:
        hours, minutes, secs = 0, fields[0], fields[1]
    else:
        hours, minutes, secs = fields

    if minutes >= SECONDS_PER_MINUTE:
        raise ValueError(f"分钟位要小于 60,收到 {minutes}")
    if secs >= SECONDS_PER_MINUTE:
        raise ValueError(f"秒位要小于 60,收到 {secs}")

    return _check_total(hours * SECONDS_PER_HOUR + minutes * SECONDS_PER_MINUTE + secs)


def _check_total(total: int) -> int:
    """总秒数的上下限检查,两种写法共用。"""
    if total < MIN_COUNTDOWN_SECONDS:
        raise ValueError("时长要大于 0")
    if total > MAX_COUNTDOWN_SECONDS:
        raise ValueError(f"最长只能到 {format_hms(MAX_COUNTDOWN_SECONDS)}")
    return total


@runtime_checkable
class Timer(Protocol):
    """``Stopwatch`` 和 ``Countdown`` 的共同接口。

    界面层只依赖这几样东西,所以它们可以互换 —— 切模式时界面不用改一行逻辑,
    只要换掉手里这个对象。放在这里是为了把这份约定写下来:以后再加一种计时器
    (比如番茄钟),照着实现这六个成员就能直接插进界面。
    """

    @property
    def state(self) -> State: ...

    def is_running(self) -> bool: ...

    def formatted(self) -> str: ...

    def start(self) -> bool: ...

    def pause(self) -> bool: ...

    def toggle(self) -> bool: ...

    def reset(self) -> bool: ...


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


def _check_target(seconds: int) -> int:
    """校验设定时长。越界一律抛 ValueError,理由见 parse_hms 的说明。"""
    # bool 是 int 的子类,不先排除的话 True 会被当成 1 秒
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise ValueError(f"时长必须是整数秒,收到 {type(seconds).__name__}")
    if not MIN_COUNTDOWN_SECONDS <= seconds <= MAX_COUNTDOWN_SECONDS:
        raise ValueError(
            f"时长要在 {MIN_COUNTDOWN_SECONDS} 到 {format_hms(MAX_COUNTDOWN_SECONDS)} 之间,"
            f"收到 {seconds}"
        )
    return seconds


class Countdown:
    """倒计时状态机 —— ``Stopwatch`` 的兄弟:接口一样,方向相反。

    ``Stopwatch`` 把"已经过去多久"往上数,``Countdown`` 把"还剩多久"往下数。
    内部结构和 Stopwatch 完全同构(记累计秒数 + 当前段起点,读数用时间差算),
    所以同样不受刷新频率影响、不会累积误差、操作全部幂等。

    只有两处和 Stopwatch 不一样,都是倒计时这个语义本身要求的:

    1. ``reset()`` 回到**设定的时长**,不是回到 0 —— "重置"一个倒计时
       就是"再来一遍"。
    2. 数到 0 时状态自己落回就绪(见 ``_settle``),并且此时再点"开始"
       会从设定时长重新开始。
    """

    def __init__(
        self,
        clock: Callable[[], float] = DEFAULT_CLOCK,
        target: int = DEFAULT_COUNTDOWN_SECONDS,
    ) -> None:
        self._clock = clock
        self._target: int = _check_target(target)
        self._state: State = State.IDLE
        self._accumulated: float = 0.0    # 已跑过的秒数(含义同 Stopwatch)
        self._segment_start: float = 0.0  # 当前段起点;仅 RUNNING 时有意义

    # ---------------- 状态查询 ----------------

    def _elapsed(self) -> float:
        """当前已经跑掉的秒数,顺便把"已经跑完"这件事落定。

        计时中的秒数等于"已结算的各段累计"加上"当前这一段走到哪儿了" ——
        和 ``Stopwatch.total_seconds()`` 是同一个算法,只是那儿的读数往上加、
        这儿的读数往下减。

        **落定这个动作必须在这里做**,不能只放在暂停里:界面每 100 毫秒读一次
        读数,如果没人把"已经到 0"记下来,状态就永远停在 RUNNING、
        ``is_running()`` 永远为真、按钮上永远写着"暂停" —— 一个已经跑完的
        倒计时看起来像卡死了。

        读操作里改状态一般是该警惕的,这里可以接受:这个函数幂等,而且
        **所有公开的读方法都先调它**,所以调用方按任何顺序读,拿到的都自洽。
        """
        if self._state is not State.RUNNING:
            return self._accumulated
        elapsed = self._accumulated + (self._clock() - self._segment_start)
        if elapsed >= self._target:
            # 超过设定时长的那部分直接丢掉,读数就停在 00:00:00
            self._accumulated = float(self._target)
            self._segment_start = 0.0
            self._state = State.IDLE
            return self._accumulated
        return elapsed

    @property
    def state(self) -> State:
        self._elapsed()
        return self._state

    def is_running(self) -> bool:
        return self.state is State.RUNNING

    def is_finished(self) -> bool:
        """是不是"跑完了"—— 读数是 0 且已经停下。

        刚建出来时读数等于设定时长、不是 0;``reset()`` 之后读数回到设定时长、
        也不是 0。所以这个条件**只在真的跑完过之后**才成立,可以安全地拿它
        当"该响铃了"的信号。
        """
        return self.state is State.IDLE and self.remaining_seconds() <= 0.0

    # ---------------- 读数 ----------------

    def target(self) -> int:
        """设定的时长(秒)。"""
        return self._target

    def elapsed_seconds(self) -> float:
        """已经跑掉的秒数。"""
        return self._elapsed()

    def remaining_seconds(self) -> float:
        """还剩多少秒。永远不小于 0。"""
        return float(self._target) - self._elapsed()

    def formatted(self) -> str:
        """剩余时间的 时:分:秒 文本。"""
        return format_hms(self.remaining_seconds())

    # ---------------- 操作(全部幂等,返回本次是否改变了状态) ----------------

    def start(self) -> bool:
        """开始倒数,或在暂停后继续。已在计时中则什么都不做。"""
        self._elapsed()
        if self._state is State.RUNNING:
            return False
        if self._accumulated >= self._target:
            # 上一轮已经跑完,读数停在 00:00:00。这时点"开始"如果什么都不做,
            # 在用户眼里就是"程序坏了" —— 所以当成"再来一轮"。
            self._accumulated = 0.0
        self._segment_start = self._clock()
        self._state = State.RUNNING
        return True

    def pause(self) -> bool:
        """暂停并保留剩余时间。本来就没在跑则什么都不做。"""
        self._elapsed()
        if self._state is not State.RUNNING:
            return False
        self._accumulated += self._clock() - self._segment_start
        self._segment_start = 0.0
        if self._accumulated >= self._target:
            # 正好卡在 0 上按的暂停:按"跑完了"处理,和 _settle 保持一致。
            # 不这样写会留下"状态是已暂停、读数却是 00:00:00"这种没法收场的情况
            # —— 按钮写着"开始",点了却什么都不发生。
            self._accumulated = float(self._target)
            self._state = State.IDLE
            return True
        self._state = State.PAUSED
        return True

    def toggle(self) -> bool:
        """跑着则暂停,否则开始。对应界面上的空格键。"""
        return self.pause() if self.is_running() else self.start()

    def reset(self) -> bool:
        """回到设定的时长并停下 —— 注意不是归零。"""
        self._elapsed()
        changed = self._state is not State.IDLE or self._accumulated != 0.0
        self._accumulated = 0.0
        self._segment_start = 0.0
        self._state = State.IDLE
        return changed

    def set_target(self, seconds: int) -> bool:
        """改设定时长,改完回到就绪。

        **只在没跑的时候允许改。** 跑着的时候改会让读数当场跳一下,而用户
        并没有做任何"改变读数"的操作 —— 那种无缘无故的跳变最容易被当成 bug。
        拒绝时返回 False,调用方可以据此告诉用户"先停一下再改"。
        """
        checked = _check_target(seconds)
        self._elapsed()
        if self._state is State.RUNNING:
            return False
        self._target = checked
        self._accumulated = 0.0
        self._segment_start = 0.0
        self._state = State.IDLE
        return True
