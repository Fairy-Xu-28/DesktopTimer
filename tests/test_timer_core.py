"""timer_core 的单元测试。

全部用 FakeClock 驱动,不 sleep、不看真实时间,所以结果确定、跑得也快。

运行(在项目根目录):
    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import unittest

from timer_core import State, Stopwatch, format_hms


class FakeClock:
    """手工推进的假时钟,替代 time.monotonic。

    测试替身只放在测试代码里,不混进生产模块。
    """

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


def make_watch() -> tuple[Stopwatch, FakeClock]:
    """造一个由假时钟驱动的秒表。"""
    clock = FakeClock()
    return Stopwatch(clock=clock), clock


class StopwatchStateTest(unittest.TestCase):
    """状态机:开始 / 暂停 / 继续 / 重置 的语义。"""

    def setUp(self) -> None:
        self.watch, self.clock = make_watch()

    def test_initial_state_is_idle(self) -> None:
        self.assertIs(self.watch.state, State.IDLE)
        self.assertFalse(self.watch.is_running())
        self.assertEqual(self.watch.total_seconds(), 0.0)

    def test_start_moves_to_running(self) -> None:
        self.assertTrue(self.watch.start())
        self.assertIs(self.watch.state, State.RUNNING)
        self.assertTrue(self.watch.is_running())

    def test_elapsed_advances_with_clock(self) -> None:
        self.watch.start()
        self.clock.advance(5.0)
        self.assertEqual(self.watch.total_seconds(), 5.0)

    def test_repeated_start_keeps_elapsed(self) -> None:
        """重复开始不能重置起点,否则已经走过的时间会凭空丢失。"""
        self.watch.start()
        self.clock.advance(3.0)
        self.assertFalse(self.watch.start())  # 已在计时,忽略
        self.clock.advance(2.0)
        self.assertEqual(self.watch.total_seconds(), 5.0)

    def test_pause_keeps_accumulated_time(self) -> None:
        self.watch.start()
        self.clock.advance(30.0)
        self.assertTrue(self.watch.pause())
        self.assertIs(self.watch.state, State.PAUSED)
        self.assertEqual(self.watch.total_seconds(), 30.0)

    def test_time_passes_while_paused_but_reading_is_frozen(self) -> None:
        """暂停后时间照样在流逝,但读数必须冻住。"""
        self.watch.start()
        self.clock.advance(30.0)
        self.watch.pause()
        self.clock.advance(100.0)
        self.assertEqual(self.watch.total_seconds(), 30.0)

    def test_resume_accumulates_instead_of_restarting(self) -> None:
        """暂停后按开始是接着走,不是从头来。"""
        self.watch.start()
        self.clock.advance(30.0)
        self.watch.pause()
        self.watch.start()
        self.clock.advance(20.0)
        self.assertEqual(self.watch.total_seconds(), 50.0)

    def test_repeated_pause_is_idempotent(self) -> None:
        self.watch.start()
        self.clock.advance(10.0)
        self.assertTrue(self.watch.pause())
        self.clock.advance(5.0)
        self.assertFalse(self.watch.pause())  # 本来就没在跑
        self.assertEqual(self.watch.total_seconds(), 10.0)

    def test_pause_without_start_does_nothing(self) -> None:
        self.assertFalse(self.watch.pause())
        self.assertIs(self.watch.state, State.IDLE)

    def test_pause_at_zero_elapsed_still_counts_as_paused(self) -> None:
        """时钟一秒都没走就暂停时,状态必须仍是"已暂停",不能塌回"就绪"。

        这不是理论边角:time.monotonic() 在 Windows 上基于 GetTickCount64,
        分辨率约 16 毫秒,同一 tick 内"开始"紧接着"暂停"读到的时钟值完全相同,
        累计秒数就是 0.0。早期实现从"累计秒数是否为 0"推导状态,这里就会
        误判成"就绪",进而把按钮联动和状态提示全部带错。
        """
        self.watch.start()
        self.watch.pause()  # 时钟没动,累计秒数停在 0.0
        self.assertIs(self.watch.state, State.PAUSED)
        self.assertTrue(self.watch.reset())  # 确实有东西可重置

    def test_reset_while_running(self) -> None:
        self.watch.start()
        self.clock.advance(42.0)
        self.assertTrue(self.watch.reset())
        self.assertEqual(self.watch.total_seconds(), 0.0)
        self.assertIs(self.watch.state, State.IDLE)
        self.assertFalse(self.watch.is_running())

    def test_reset_while_paused(self) -> None:
        self.watch.start()
        self.clock.advance(42.0)
        self.watch.pause()
        self.assertTrue(self.watch.reset())
        self.assertEqual(self.watch.total_seconds(), 0.0)

    def test_reset_then_start_begins_from_zero(self) -> None:
        self.watch.start()
        self.clock.advance(42.0)
        self.watch.reset()
        self.watch.start()
        self.clock.advance(1.0)
        self.assertEqual(self.watch.total_seconds(), 1.0)

    def test_reset_on_idle_reports_no_change(self) -> None:
        self.assertFalse(self.watch.reset())

    def test_toggle_cycles_through_states(self) -> None:
        """空格键语义:IDLE -> RUNNING -> PAUSED -> RUNNING。"""
        self.watch.toggle()
        self.assertIs(self.watch.state, State.RUNNING)
        self.watch.toggle()
        self.assertIs(self.watch.state, State.PAUSED)
        self.watch.toggle()
        self.assertIs(self.watch.state, State.RUNNING)

    def test_instances_are_independent(self) -> None:
        other, other_clock = make_watch()
        self.watch.start()
        self.clock.advance(10.0)
        other.start()
        other_clock.advance(3.0)
        self.assertEqual(self.watch.total_seconds(), 10.0)
        self.assertEqual(other.total_seconds(), 3.0)

    def test_idle_never_reads_the_clock(self) -> None:
        """没在计时就不该去读时钟 —— 用一个会炸的时钟证明这一点。"""

        def exploding_clock() -> float:
            raise AssertionError("IDLE 状态下不应该读取时钟")

        watch = Stopwatch(clock=exploding_clock)
        self.assertEqual(watch.total_seconds(), 0.0)
        self.assertFalse(watch.pause())


class FormatHmsTest(unittest.TestCase):
    """读数格式化。这里是纯函数,不需要时钟。"""

    def test_zero(self) -> None:
        self.assertEqual(format_hms(0.0), "00:00:00")

    def test_fractional_seconds_truncate(self) -> None:
        """界面只显示整秒,不足 1 秒向下取整。"""
        self.assertEqual(format_hms(0.999), "00:00:00")
        self.assertEqual(format_hms(1.0), "00:00:01")
        self.assertEqual(format_hms(59.999), "00:00:59")

    def test_minute_and_hour_carry(self) -> None:
        self.assertEqual(format_hms(60.0), "00:01:00")
        self.assertEqual(format_hms(3600.0), "01:00:00")
        self.assertEqual(format_hms(3661.0), "01:01:01")

    def test_just_below_an_hour(self) -> None:
        self.assertEqual(format_hms(3599.999), "00:59:59")

    def test_hour_field_widens_past_99(self) -> None:
        """小时位不足两位补零,超过 99 小时自然扩宽,不报错。"""
        self.assertEqual(format_hms(359999.0), "99:59:59")
        self.assertEqual(format_hms(360000.0), "100:00:00")

    def test_negative_is_clamped_to_zero(self) -> None:
        self.assertEqual(format_hms(-1.0), "00:00:00")

    def test_formatted_matches_reading(self) -> None:
        watch, clock = make_watch()
        watch.start()
        clock.advance(3600.0)
        self.assertEqual(watch.formatted(), "01:00:00")


class PrecisionTest(unittest.TestCase):
    """精度:读数基于时间差,不受刷新次数和暂停次数影响。"""

    def test_many_small_advances_do_not_drift(self) -> None:
        watch, clock = make_watch()
        watch.start()
        for _ in range(1000):
            clock.advance(0.1)
            watch.total_seconds()  # 频繁读数不应该影响结果
        self.assertAlmostEqual(watch.total_seconds(), 100.0, delta=1e-6)

    def test_many_pause_resume_cycles_do_not_drift(self) -> None:
        watch, clock = make_watch()
        for _ in range(1000):
            watch.start()
            clock.advance(0.1)
            watch.pause()
        self.assertAlmostEqual(watch.total_seconds(), 100.0, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
