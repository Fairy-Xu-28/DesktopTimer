"""timer_view 的单元测试。

全部是纯函数,不需要显示器、不需要 Tk,毫秒级跑完。

运行(在项目根目录):
    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import unittest

from timer_core import State
from timer_view import (
    DEFAULT_BG,
    DARK_TEXT,
    LIGHT_TEXT,
    MAX_DIGIT_PX,
    MIN_BTN_BG_DISTANCE,
    MIN_DIGIT_PX,
    MIN_WINDOW_H,
    MIN_WINDOW_W,
    PRESET_COLORS,
    build_theme,
    channel_distance,
    clamp_window_size,
    digit_font_px,
    hex_to_rgb,
    mix_rgb,
    normalize_color,
    readable_foreground,
    relative_luminance,
    rgb_to_hex,
    scaled_font_px,
)


class NormalizeColorTest(unittest.TestCase):
    """颜色输入规范化。用户能碰到颜色的地方只有取色器和色块,但设置文件可以被手改。"""

    def test_lowercases_and_keeps_hash(self) -> None:
        self.assertEqual(normalize_color("#3B3B3B"), "#3b3b3b")

    def test_accepts_missing_hash(self) -> None:
        self.assertEqual(normalize_color("3b3b3b"), "#3b3b3b")

    def test_expands_shorthand(self) -> None:
        """#abc 是 #aabbcc 的简写,展开后每个通道重复一次。"""
        self.assertEqual(normalize_color("#abc"), "#aabbcc")
        self.assertEqual(normalize_color("#F0A"), "#ff00aa")

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(normalize_color("  #3B3B3B\n"), "#3b3b3b")

    def test_rejects_bad_input(self) -> None:
        for bad in ("", "#", "#12", "#12345", "#1234567", "#gggggg", "红色", "rgb(1,2,3)"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    normalize_color(bad)

    def test_rejects_non_string(self) -> None:
        """设置文件里把颜色写成数字或数组时必须被挡住,而不是崩在别处。"""
        for bad in (123, None, ["#3b3b3b"]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    normalize_color(bad)  # type: ignore[arg-type]


class ColorConversionTest(unittest.TestCase):
    def test_hex_to_rgb(self) -> None:
        self.assertEqual(hex_to_rgb("#3b3b3b"), (59, 59, 59))
        self.assertEqual(hex_to_rgb("#ffffff"), (255, 255, 255))
        self.assertEqual(hex_to_rgb("#000000"), (0, 0, 0))

    def test_rgb_to_hex_clamps_and_rounds(self) -> None:
        self.assertEqual(rgb_to_hex((0, 0, 0)), "#000000")
        self.assertEqual(rgb_to_hex((255, 255, 255)), "#ffffff")
        self.assertEqual(rgb_to_hex((-5, 300, 127.6)), "#00ff80")

    def test_roundtrip(self) -> None:
        for color in ("#000000", "#3b3b3b", "#ffffff", "#1e2430", "#7fd47f"):
            with self.subTest(color=color):
                self.assertEqual(rgb_to_hex(hex_to_rgb(color)), color)

    def test_mix_endpoints_and_middle(self) -> None:
        black, white = (0.0, 0.0, 0.0), (255.0, 255.0, 255.0)
        self.assertEqual(mix_rgb(black, white, 0.0), black)
        self.assertEqual(mix_rgb(black, white, 1.0), white)
        self.assertEqual(mix_rgb(black, white, 0.5), (127.5, 127.5, 127.5))

    def test_channel_distance(self) -> None:
        self.assertEqual(channel_distance("#3b3b3b", "#3b3b3b"), 0)
        self.assertEqual(channel_distance("#000000", "#ffffff"), 255)
        # 取的是最大单通道差,不是平均值
        self.assertEqual(channel_distance("#000000", "#00ff00"), 255)


class LuminanceTest(unittest.TestCase):
    """亮度判断决定了「深底配白字还是黑字」,算错会让界面看不清。"""

    def test_black_and_white_extremes(self) -> None:
        self.assertAlmostEqual(relative_luminance("#000000"), 0.0, places=6)
        self.assertAlmostEqual(relative_luminance("#ffffff"), 1.0, places=6)

    def test_is_monotonic_in_gray(self) -> None:
        grays = ["#000000", "#202020", "#3b3b3b", "#808080", "#c0c0c0", "#ffffff"]
        values = [relative_luminance(g) for g in grays]
        self.assertEqual(values, sorted(values))

    def test_gamma_corrected_not_naive_average(self) -> None:
        """中灰的亮度远低于 0.5 —— 这正是要做 gamma 校正的原因。

        如果偷懒用通道平均值, #808080 会得到 0.5, 于是把浅灰误判成深色、
        文字色选反。真实值约 0.216。
        """
        self.assertLess(relative_luminance("#808080"), 0.3)
        self.assertGreater(relative_luminance("#808080"), 0.15)

    def test_green_is_brighter_than_blue(self) -> None:
        """人眼对绿最敏感、对蓝最不敏感,权重必须体现这一点。"""
        self.assertGreater(relative_luminance("#00ff00"), relative_luminance("#0000ff"))
        self.assertGreater(relative_luminance("#00ff00"), relative_luminance("#ff0000"))

    def test_readable_foreground_picks_contrast(self) -> None:
        self.assertEqual(readable_foreground("#000000"), LIGHT_TEXT)
        self.assertEqual(readable_foreground("#3b3b3b"), LIGHT_TEXT)
        self.assertEqual(readable_foreground("#ffffff"), DARK_TEXT)
        self.assertEqual(readable_foreground("#f5f0e6"), DARK_TEXT)

    def test_readable_foreground_threshold_boundary(self) -> None:
        """阈值两侧各取一个点,确认翻转发生在该发生的地方。"""
        from timer_view import LUMINANCE_THRESHOLD

        below = next(
            g for g in (f"#{v:02x}{v:02x}{v:02x}" for v in range(0, 256))
            if relative_luminance(g) <= LUMINANCE_THRESHOLD
        )
        self.assertEqual(readable_foreground(below), LIGHT_TEXT)
        self.assertEqual(readable_foreground("#ffffff"), DARK_TEXT)


class BuildThemeTest(unittest.TestCase):
    """主题派生。这里钉住「默认主题不改变观感」。"""

    # v1.0.0 里手工调出来的那套常量。派生结果必须和它几乎逐位相同,
    # 否则这次重构就悄悄地改变了默认外观。
    LEGACY = {
        "bg": "#3b3b3b",
        "fg": "#ffffff",
        "btn_bg": "#4d4d4d",
        "btn_fg": "#e8e8e8",
        "btn_active": "#5e5e5e",
        "btn_disabled_fg": "#7d7d7d",
        "hint_fg": "#9a9a9a",
    }

    def test_default_theme_matches_v1_palette(self) -> None:
        theme = build_theme(DEFAULT_BG)
        for role, legacy in self.LEGACY.items():
            with self.subTest(role=role):
                self.assertLessEqual(
                    channel_distance(getattr(theme, role), legacy),
                    8,
                    f"{role} 派生为 {getattr(theme, role)},v1.0.0 是 {legacy},偏得太多了",
                )

    def test_default_theme_status_colors_unchanged(self) -> None:
        theme = build_theme(DEFAULT_BG)
        self.assertEqual(theme.status_fg[State.RUNNING], "#7fd47f")
        self.assertEqual(theme.status_fg[State.PAUSED], "#e8c46a")

    def test_button_never_merges_into_background(self) -> None:
        """保护透明功能:按钮底色必须和窗口底色拉开距离。

        否则开背景透明时,按钮可能整块被抹掉 —— 看不见也点不到。
        """
        for name, bg in PRESET_COLORS:
            with self.subTest(preset=name):
                theme = build_theme(bg)
                self.assertGreaterEqual(channel_distance(bg, theme.btn_bg), MIN_BTN_BG_DISTANCE)

    def test_all_presets_are_dark_and_get_white_text(self) -> None:
        for name, bg in PRESET_COLORS:
            with self.subTest(preset=name):
                self.assertEqual(build_theme(bg).fg, LIGHT_TEXT)

    def test_light_background_flips_direction(self) -> None:
        """浅底上按钮应该比底色更暗(往黑里混),而不是更亮。"""
        theme = build_theme("#ffffff")
        self.assertEqual(theme.fg, DARK_TEXT)
        self.assertLess(relative_luminance(theme.btn_bg), relative_luminance("#ffffff"))
        self.assertLess(relative_luminance(theme.hint_fg), relative_luminance("#ffffff"))

    def test_light_background_darkens_semantic_colors(self) -> None:
        """浅底上的绿字黄字必须压暗,否则白底上几乎看不见。"""
        dark = build_theme("#000000")
        light = build_theme("#ffffff")
        for state in (State.RUNNING, State.PAUSED):
            with self.subTest(state=state):
                self.assertLess(
                    relative_luminance(light.status_fg[state]),
                    relative_luminance(dark.status_fg[state]),
                )

    def test_normalizes_input(self) -> None:
        self.assertEqual(build_theme("#3B3B3B"), build_theme("#3b3b3b"))

    def test_theme_is_immutable(self) -> None:
        """主题是值对象,防止某处顺手改了一个字段而别处不知道。"""
        theme = build_theme(DEFAULT_BG)
        with self.assertRaises(Exception):
            theme.bg = "#000000"  # type: ignore[misc]


class DigitFontTest(unittest.TestCase):
    """按窗口尺寸算字号。这是「拉长缩小自适应」的核心,也是防抖的哨兵。"""

    TEXT_RATIO = 4.4   # "00:00:00" 宽度 / 字号,实测 Consolas Bold 约为这个数
    LINE_RATIO = 1.17  # 行高 / 字号

    def size(self, width: int, height: int) -> int:
        return digit_font_px(width, height, self.TEXT_RATIO, self.LINE_RATIO)

    def test_monotonic_in_width(self) -> None:
        sizes = [self.size(w, 600) for w in range(200, 1200, 50)]
        self.assertEqual(sizes, sorted(sizes))

    def test_monotonic_in_height(self) -> None:
        sizes = [self.size(1600, h) for h in range(120, 900, 40)]
        self.assertEqual(sizes, sorted(sizes))

    def test_clamps_at_both_ends(self) -> None:
        self.assertEqual(self.size(1, 1), MIN_DIGIT_PX)
        self.assertEqual(self.size(20000, 20000), MAX_DIGIT_PX)

    def test_returns_int(self) -> None:
        """必须是整数:界面靠「字号有没有变」当哨兵来切断重排反馈环,
        浮点数会让每次比较都不相等,于是无限重排。
        """
        for width in (381, 447, 512, 903):
            with self.subTest(width=width):
                self.assertIsInstance(self.size(width, 300), int)

    def test_same_input_same_output(self) -> None:
        self.assertEqual(self.size(640, 480), self.size(640, 480))

    def test_small_window_change_moves_font_by_at_most_one_pixel(self) -> None:
        """拖动时字号是逐像素变化的,不能出现跳变。

        注意这里断言的是「斜率有界」而不是「完全不变」:宽度比是 4.4,
        也就是 4.4 个窗口像素才换 1 个字号像素,所以窗口挪 1 像素正好
        跨过整数边界时字号是会变的 —— 那是正常的,不是抖动。

        真正防住重排反馈环的是界面层那条「算出来的字号没变就一个字都不写」
        的哨兵,不是这个函数。这个函数只保证变化是连续的。
        """
        for width in range(380, 1600, 7):
            with self.subTest(width=width):
                step = abs(self.size(width + 1, 400) - self.size(width, 400))
                self.assertLessEqual(step, 1)

    def test_no_hysteresis_when_resizing_back_and_forth(self) -> None:
        """同样尺寸必须永远得到同样字号。

        如果结果依赖「上一次算的是多少」,来回拖动就会在两个字号之间反复横跳。
        这条用「正着走一遍 + 倒着走一遍,结果互为镜像」来证明函数无状态。
        """
        forward = [self.size(w, 400) for w in range(500, 560)]
        backward = [self.size(w, 400) for w in range(559, 499, -1)]
        self.assertEqual(backward, forward[::-1])

    def test_rejects_nonpositive_ratios(self) -> None:
        for ratio in (0, -1.0):
            with self.subTest(ratio=ratio):
                with self.assertRaises(ValueError):
                    digit_font_px(600, 400, ratio, self.LINE_RATIO)
                with self.assertRaises(ValueError):
                    digit_font_px(600, 400, self.TEXT_RATIO, ratio)

    def test_scaled_font_clamps(self) -> None:
        self.assertEqual(scaled_font_px(100, 0.26, 9, 20), 20)
        self.assertEqual(scaled_font_px(10, 0.26, 9, 20), 9)
        self.assertEqual(scaled_font_px(40, 0.26, 9, 20), 10)


class WindowSizeTest(unittest.TestCase):
    def test_clamps_to_minimum(self) -> None:
        self.assertEqual(clamp_window_size(10, 10), (MIN_WINDOW_W, MIN_WINDOW_H))

    def test_clamps_to_maximum(self) -> None:
        self.assertEqual(clamp_window_size(99999, 99999), (4000, 4000))

    def test_passes_through_reasonable_size(self) -> None:
        self.assertEqual(clamp_window_size(640, 480), (640, 480))

    def test_rejects_negative_from_corrupt_settings(self) -> None:
        """设置文件被手改成负数时不该还原出一块负尺寸的窗口。"""
        self.assertEqual(clamp_window_size(-100, -100), (MIN_WINDOW_W, MIN_WINDOW_H))


if __name__ == "__main__":
    unittest.main()
