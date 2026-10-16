"""timer_core 的单元测试。

全部用 FakeClock 驱动,不 sleep、不看真实时间,所以结果确定、跑得也快。

运行(在项目根目录):
    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import unittest

from timer_core import (
    MAX_COUNTDOWN_SECONDS,
    Countdown,
    State,
    Stopwatch,
    format_hms,
    parse_hms,
)


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


def make_countdown(target: int = 10) -> tuple[Countdown, FakeClock]:
    """造一个由假时钟驱动、设定时长 ``target`` 秒的倒计时。"""
    clock = FakeClock()
    return Countdown(clock=clock, target=target), clock


class CountdownStateTest(unittest.TestCase):
    """倒计时:开始 / 暂停 / 继续 / 重置 的语义。"""

    def setUp(self) -> None:
        self.countdown, self.clock = make_countdown(10)

    def test_initial_state_is_idle_at_the_full_duration(self) -> None:
        self.assertIs(self.countdown.state, State.IDLE)
        self.assertFalse(self.countdown.is_running())
        self.assertEqual(self.countdown.remaining_seconds(), 10.0)
        self.assertEqual(self.countdown.formatted(), "00:00:10")
        self.assertEqual(self.countdown.target(), 10)

    def test_remaining_decreases_while_running(self) -> None:
        """计时中读数要**跟着时钟走**,不能等到暂停才更新。

        曾经写错过:读数只拿"已结算的累计秒数"去减,而那个值只在暂停时才更新。
        于是计时中的倒计时读数一直卡在设定时长不动(跑满 3 秒还显示 00:00:10),
        一暂停却突然跳到正确值 —— 表现得像读数慢了一拍。
        """
        self.countdown.start()
        self.clock.advance(3.0)
        self.assertEqual(self.countdown.remaining_seconds(), 7.0)
        self.assertEqual(self.countdown.formatted(), "00:00:07")
        self.clock.advance(2.0)
        self.assertEqual(self.countdown.remaining_seconds(), 5.0)

    def test_pause_freezes_the_reading(self) -> None:
        self.countdown.start()
        self.clock.advance(4.0)
        self.assertTrue(self.countdown.pause())
        self.clock.advance(100.0)   # 暂停期间时钟照走,读数不该动
        self.assertEqual(self.countdown.remaining_seconds(), 6.0)
        self.assertIs(self.countdown.state, State.PAUSED)

    def test_resume_continues_from_where_it_paused(self) -> None:
        self.countdown.start()
        self.clock.advance(4.0)
        self.countdown.pause()
        self.clock.advance(50.0)
        self.assertTrue(self.countdown.start())
        self.clock.advance(2.0)
        self.assertEqual(self.countdown.remaining_seconds(), 4.0)

    def test_repeated_start_keeps_the_remaining_time(self) -> None:
        self.countdown.start()
        self.clock.advance(3.0)
        self.assertFalse(self.countdown.start())   # 已在跑,忽略
        self.clock.advance(2.0)
        self.assertEqual(self.countdown.remaining_seconds(), 5.0)

    def test_repeated_pause_is_ignored(self) -> None:
        self.countdown.start()
        self.clock.advance(3.0)
        self.assertTrue(self.countdown.pause())
        self.assertFalse(self.countdown.pause())

    def test_reset_returns_to_the_target_not_to_zero(self) -> None:
        """倒计时里"重置"是**重新来一遍**,不是归零 —— 和秒表正好相反。"""
        self.countdown.start()
        self.clock.advance(6.0)
        self.assertTrue(self.countdown.reset())
        self.assertIs(self.countdown.state, State.IDLE)
        self.assertEqual(self.countdown.formatted(), "00:00:10")

    def test_reset_is_idempotent(self) -> None:
        self.countdown.reset()
        self.assertFalse(self.countdown.reset())   # 本来就在起点上

    def test_toggle_starts_then_pauses(self) -> None:
        self.countdown.toggle()
        self.assertIs(self.countdown.state, State.RUNNING)
        self.countdown.toggle()
        self.assertIs(self.countdown.state, State.PAUSED)

    def test_set_target_only_while_stopped(self) -> None:
        self.assertTrue(self.countdown.set_target(60))
        self.assertEqual(self.countdown.target(), 60)
        self.assertEqual(self.countdown.formatted(), "00:01:00")

        self.countdown.start()
        self.assertFalse(self.countdown.set_target(30))   # 跑着的时候不给改
        self.assertEqual(self.countdown.target(), 60)

    def test_set_target_resets_progress(self) -> None:
        self.countdown.start()
        self.clock.advance(5.0)
        self.countdown.pause()
        self.countdown.set_target(20)
        self.assertEqual(self.countdown.remaining_seconds(), 20.0)

    def test_rejects_invalid_targets(self) -> None:
        for bad in (0, -1, MAX_COUNTDOWN_SECONDS + 1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.countdown.set_target(bad)

    def test_rejects_non_integer_targets(self) -> None:
        # bool 是 int 的子类,不专门排除的话 True 会被当成 1 秒
        for bad in (True, 1.5, "60", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.countdown.set_target(bad)  # type: ignore[arg-type]

    def test_two_instances_do_not_share_state(self) -> None:
        other, _ = make_countdown(30)
        self.countdown.start()
        self.clock.advance(4.0)
        self.assertEqual(other.formatted(), "00:00:30")


class CountdownFinishTest(unittest.TestCase):
    """数到 0 之后会发生什么。"""

    def setUp(self) -> None:
        self.countdown, self.clock = make_countdown(10)

    def test_reaching_zero_settles_to_idle(self) -> None:
        """跑完之后状态要自己落回就绪,不能一直挂着"计时中"。

        不落定的话 ``is_running()`` 永远为真、按钮上永远写着"暂停",
        一个已经跑完的倒计时看起来像是卡死了。
        """
        self.countdown.start()
        self.clock.advance(10.0)
        self.assertIs(self.countdown.state, State.IDLE)
        self.assertFalse(self.countdown.is_running())
        self.assertEqual(self.countdown.formatted(), "00:00:00")

    def test_is_finished_only_after_it_actually_ran_down(self) -> None:
        self.assertFalse(self.countdown.is_finished())      # 刚建出来
        self.countdown.start()
        self.assertFalse(self.countdown.is_finished())      # 跑着
        self.clock.advance(10.0)
        self.assertTrue(self.countdown.is_finished())
        self.countdown.reset()
        self.assertFalse(self.countdown.is_finished())      # 重置回设定时长

    def test_settles_even_when_read_after_overshooting(self) -> None:
        """界面每 100 毫秒才读一次,所以"跑过头再读"是常态,必须也能落定。"""
        self.countdown.start()
        self.clock.advance(999.0)
        self.assertTrue(self.countdown.is_finished())
        self.assertEqual(self.countdown.remaining_seconds(), 0.0)

    def test_state_read_before_remaining_still_settles(self) -> None:
        """先读 state 还是先读读数,结果必须一样。

        落定发生在读操作里,所以两个入口都得触发它 —— 只在一个入口做的话,
        调用顺序就会改变结果,那是最难查的一类 bug。
        """
        self.countdown.start()
        self.clock.advance(10.0)
        self.assertIs(self.countdown.state, State.IDLE)      # 先读状态
        self.assertEqual(self.countdown.remaining_seconds(), 0.0)

    def test_remaining_read_before_state_still_settles(self) -> None:
        self.countdown.start()
        self.clock.advance(10.0)
        self.assertEqual(self.countdown.remaining_seconds(), 0.0)  # 先读读数
        self.assertIs(self.countdown.state, State.IDLE)

    def test_remaining_never_goes_negative(self) -> None:
        self.countdown.start()
        self.clock.advance(999.0)
        self.assertEqual(self.countdown.remaining_seconds(), 0.0)

    def test_start_after_finishing_runs_another_round(self) -> None:
        """跑完之后停在 00:00:00,这时点"开始"必须能重新跑。

        什么都不做的话,用户在 00:00:00 上点开始会毫无反应,像程序坏了。
        """
        self.countdown.start()
        self.clock.advance(10.0)
        self.assertTrue(self.countdown.start())
        self.assertIs(self.countdown.state, State.RUNNING)
        self.assertEqual(self.countdown.formatted(), "00:00:10")

    def test_pausing_exactly_at_zero_counts_as_finished(self) -> None:
        """正好卡在 0 上按暂停,要按"跑完了"处理。

        否则会留下"状态是已暂停、读数却是 00:00:00"这种收不了场的局面 ——
        按钮写着"开始",点了却什么都不发生(读数已经不能更小了)。
        """
        self.countdown.start()
        self.clock.advance(10.0)
        self.countdown.pause()
        self.assertIs(self.countdown.state, State.IDLE)
        self.assertTrue(self.countdown.is_finished())

    def test_countdown_does_not_drift_over_many_cycles(self) -> None:
        countdown, clock = make_countdown(100)
        for _ in range(1000):
            countdown.start()
            clock.advance(0.1)
            countdown.pause()
        self.assertAlmostEqual(countdown.remaining_seconds(), 0.0, delta=1e-6)


class ParseHmsTest(unittest.TestCase):
    """用户手打的时长 -> 秒数。"""

    def test_three_fields(self) -> None:
        self.assertEqual(parse_hms("1:30:00"), 5400)
        self.assertEqual(parse_hms("1:02:03"), 3723)
        self.assertEqual(parse_hms("00:00:01"), 1)

    def test_two_fields_are_minutes_and_seconds(self) -> None:
        self.assertEqual(parse_hms("05:00"), 300)
        self.assertEqual(parse_hms("00:01"), 1)

    def test_bare_number_is_seconds(self) -> None:
        """纯数字按秒算。不能按"分:秒"去校验 —— 那样 90 会被当成
        "90 分 0 秒"、因为分位 ≥ 60 而被拒,一个合法的秒数就进不去了。"""
        self.assertEqual(parse_hms("90"), 90)
        self.assertEqual(parse_hms("5"), 5)

    def test_surrounding_whitespace_is_ignored(self) -> None:
        self.assertEqual(parse_hms("  05:00  "), 300)

    def test_full_width_colon_is_accepted(self) -> None:
        """中文输入法下打出来的是全角冒号,字符串看着一模一样。
        不顺手换掉的话,用户会看到一个完全看不懂为什么失败的报错。"""
        self.assertEqual(parse_hms("1：30"), 90)

    def test_accepts_the_maximum(self) -> None:
        self.assertEqual(parse_hms("99:59:59"), MAX_COUNTDOWN_SECONDS)

    def test_rejects_garbage(self) -> None:
        for bad in ("", "   ", "abc", "3.5", "1:2:3:4", "1::30", "-5", "-1:00"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_hms(bad)

    def test_rejects_non_strings(self) -> None:
        for bad in (None, 300, 3.5, ["05:00"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_hms(bad)  # type: ignore[arg-type]

    def test_rejects_zero(self) -> None:
        for bad in ("0", "00:00", "0:00", "00:00:00"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_hms(bad)

    def test_rejects_out_of_range_minutes_and_seconds(self) -> None:
        for bad in ("5:99", "99:99", "1:60:00", "1:00:60"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_hms(bad)

    def test_rejects_above_the_maximum(self) -> None:
        with self.assertRaises(ValueError):
            parse_hms("100:00:00")

    def test_round_trips_with_format_hms(self) -> None:
        for seconds in (1, 59, 60, 300, 3599, 3600, 5400, 86399, MAX_COUNTDOWN_SECONDS):
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_hms(format_hms(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
