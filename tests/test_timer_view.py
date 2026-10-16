"""timer_view 的单元测试。

全部是纯函数,不需要显示器、不需要 Tk,毫秒级跑完。

运行(在项目根目录):
    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import unittest

from timer_view import (
    BTN_MIN_W,
    CLOSE_INSET,
    CLOSE_SIZE,
    CORNER_KEY,
    DEFAULT_BG,
    DARK_TEXT,
    LIGHT_TEXT,
    LUMINANCE_THRESHOLD,
    MAX_DIGIT_PX,
    MAX_WINDOW_H,
    MAX_WINDOW_W,
    MIN_BTN_BG_DISTANCE,
    MIN_DIGIT_PX,
    MIN_WINDOW_H,
    MIN_WINDOW_W,
    PANEL_PAD,
    PANEL_RADIUS_MAX,
    PANEL_RADIUS_MIN,
    RESIZE_BORDER_PX,
    RESIZE_CORNER_PX,
    build_theme,
    channel_distance,
    clamp_window_size,
    digit_font_px,
    hex_to_rgb,
    mix_rgb,
    normalize_color,
    panel_layout,
    panel_radius,
    readable_foreground,
    relative_luminance,
    resize_rect,
    resize_zone,
    rgb_to_hex,
    rounded_rect_points,
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
        below = next(
            g for g in (f"#{v:02x}{v:02x}{v:02x}" for v in range(0, 256))
            if relative_luminance(g) <= LUMINANCE_THRESHOLD
        )
        self.assertEqual(readable_foreground(below), LIGHT_TEXT)
        self.assertEqual(readable_foreground("#ffffff"), DARK_TEXT)


class BuildThemeTest(unittest.TestCase):
    """主题派生。

    v2.0.0 起只有一套配色(用户不再能换),所以这里不再有「派生结果要和
    v1.0.0 的调色板一致」那条 —— 这次是**故意**换了配色。
    现在的重点是几条保证可读性的性质。
    """

    def test_default_theme_is_dark_with_white_digits(self) -> None:
        """面板必须够暗、数字必须够亮 —— 面板是半透明的,数字底下是用户的桌面,
        对比度不够就会糊在背景里。"""
        theme = build_theme(DEFAULT_BG)
        self.assertEqual(theme.fg, LIGHT_TEXT)
        self.assertLess(relative_luminance(theme.bg), LUMINANCE_THRESHOLD)

    def test_button_never_merges_into_background(self) -> None:
        """按钮底色必须和面板底色拉开距离,否则按钮会"陷"进面板里、
        看不出那是颗能点的按钮。"""
        theme = build_theme(DEFAULT_BG)
        self.assertGreaterEqual(
            channel_distance(theme.bg, theme.btn_bg), MIN_BTN_BG_DISTANCE
        )

    def test_pressed_is_darker_than_normal(self) -> None:
        """按下态要比常态暗。搞反的话,"按住"看起来像"变亮了",
        和按下去的手感正好相反。"""
        theme = build_theme(DEFAULT_BG)
        self.assertLess(
            relative_luminance(theme.btn_pressed), relative_luminance(theme.btn_bg)
        )

    def test_hover_is_brighter_than_normal(self) -> None:
        theme = build_theme(DEFAULT_BG)
        self.assertGreater(
            relative_luminance(theme.btn_active), relative_luminance(theme.btn_bg)
        )

    def test_button_text_is_readable_on_the_button(self) -> None:
        theme = build_theme(DEFAULT_BG)
        self.assertGreater(
            channel_distance(theme.btn_fg, theme.btn_bg), MIN_BTN_BG_DISTANCE
        )

    def test_no_palette_colour_equals_the_corner_key(self) -> None:
        """面板四个角是靠"正好等于 CORNER_KEY 的像素被整片抹掉"做出来的。

        所以调色板里**任何**一个颜色都不能碰巧等于那个键值 —— 否则那一块
        (可能就是一整颗按钮)会被顺带挖个洞,变成一个看不见也点不到的洞。
        """
        theme = build_theme(DEFAULT_BG)
        for role in (
            "bg", "fg", "hint_fg", "btn_bg", "btn_fg", "btn_active", "btn_pressed",
        ):
            with self.subTest(role=role):
                self.assertNotEqual(getattr(theme, role).lower(), CORNER_KEY.lower())

    def test_light_background_flips_direction(self) -> None:
        """浅底上按钮应该比底色更暗(往黑里混),而不是更亮。"""
        theme = build_theme("#ffffff")
        self.assertEqual(theme.fg, DARK_TEXT)
        self.assertLess(relative_luminance(theme.btn_bg), relative_luminance("#ffffff"))
        self.assertLess(relative_luminance(theme.hint_fg), relative_luminance("#ffffff"))

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


class PanelRadiusTest(unittest.TestCase):
    """面板圆角半径。跟着高度走,这样小窗不是胶囊、大窗也看得出来是圆角。"""

    def test_clamps_at_both_ends(self) -> None:
        self.assertEqual(panel_radius(0), PANEL_RADIUS_MIN)
        self.assertEqual(panel_radius(10), PANEL_RADIUS_MIN)
        self.assertEqual(panel_radius(100000), PANEL_RADIUS_MAX)

    def test_stays_in_range_at_every_real_window_size(self) -> None:
        """真实窗口尺寸区间内都落在上下限之内 —— 尤其是最小窗口不能在限外。"""
        for height in range(MIN_WINDOW_H, 1500, 7):
            with self.subTest(height=height):
                self.assertGreaterEqual(panel_radius(height), PANEL_RADIUS_MIN)
                self.assertLessEqual(panel_radius(height), PANEL_RADIUS_MAX)

    def test_monotonic_in_height(self) -> None:
        values = [panel_radius(h) for h in range(0, 2000, 25)]
        self.assertEqual(values, sorted(values))

    def test_returns_int(self) -> None:
        """必须整数:它会直接喂给 Canvas 的坐标和 Windows 的圆角 API。"""
        for height in (220, 333, 480, 1000):
            with self.subTest(height=height):
                self.assertIsInstance(panel_radius(height), int)

    def test_never_exceeds_half_the_minimum_height(self) -> None:
        """半径不能大到让最小窗口看起来像颗胶囊 —— 上下都圆到中间去了。"""
        self.assertLessEqual(panel_radius(MIN_WINDOW_H) * 2, MIN_WINDOW_H)


class RoundedRectPointsTest(unittest.TestCase):
    """圆角矩形的轮廓点。它是"形状对不对"的全部依据,画错了整个界面就歪了。"""

    def points(self, *args, **kwargs) -> list[tuple[float, float]]:
        flat = rounded_rect_points(*args, **kwargs)
        return list(zip(flat[0::2], flat[1::2]))

    def test_point_count(self) -> None:
        """每段圆弧走 steps + 1 个点,四个角 —— 首尾各有一个点落在直边端点上。"""
        for steps in (1, 4, 6, 12):
            with self.subTest(steps=steps):
                self.assertEqual(len(rounded_rect_points(0, 0, 100, 50, 8, steps)), 4 * (steps + 1) * 2)

    def test_every_point_is_inside_the_box(self) -> None:
        """所有顶点都必须落在矩形里。

        圆弧是往**里**鼓的,所以正常情况下本来就在框内;这条断言真正防的是
        "圆心算错方向",那会让四个角往外翘出去、多边形自交。
        """
        for radius in (0, 3, 12, 25):
            with self.subTest(radius=radius):
                for x, y in self.points(0, 0, 200, 100, radius):
                    self.assertGreaterEqual(x, -1e-9)
                    self.assertLessEqual(x, 200 + 1e-9)
                    self.assertGreaterEqual(y, -1e-9)
                    self.assertLessEqual(y, 100 + 1e-9)

    def test_radius_is_clamped_to_half_the_short_side(self) -> None:
        """半径给太大时必须钳住。

        不钳的话四个角的圆心会跑到矩形外面,画出来是个自交的蝴蝶结 ——
        而且这在最小窗口上真的会发生(窗口高 220,圆角参数却可能很大)。
        """
        for x, y in self.points(0, 0, 200, 40, 999):
            self.assertLessEqual(y, 40 + 1e-9)
            self.assertGreaterEqual(y, -1e-9)
            self.assertLessEqual(x, 200 + 1e-9)

    def test_zero_radius_gives_a_plain_rectangle(self) -> None:
        """半径为 0 时四段圆弧各自塌成一个点,整体退化成四个直角。"""
        corners = set(self.points(0, 0, 100, 20, 0))
        self.assertEqual(corners, {(0.0, 0.0), (100.0, 0.0), (100.0, 20.0), (0.0, 20.0)})

    def test_starts_at_the_top_left_edge_end(self) -> None:
        """第一个点是左边那条直边的上端点 —— 这样和最后一个点接起来才是闭合的。"""
        first = self.points(0, 0, 100, 50, 10)[0]
        self.assertAlmostEqual(first[0], 0.0)
        self.assertAlmostEqual(first[1], 10.0)

    def test_rejects_degenerate_rect(self) -> None:
        for box in ((0, 0, 0, 50), (0, 0, 100, 0), (10, 0, 5, 50)):
            with self.subTest(box=box):
                with self.assertRaises(ValueError):
                    rounded_rect_points(*box, 4)

    def test_rejects_zero_steps(self) -> None:
        """steps 是除数,给 0 会 ZeroDivisionError —— 必须先挡住。"""
        for steps in (0, -3):
            with self.subTest(steps=steps):
                with self.assertRaises(ValueError):
                    rounded_rect_points(0, 0, 100, 50, 8, steps)


class ResizeZoneTest(unittest.TestCase):
    """点落在哪个缩放区。判错的表现是"拖边变成了拖窗口",用户会觉得窗口不听话。"""

    W, H = 520, 400

    def zone(self, x: float, y: float, w: int | None = None, h: int | None = None):
        return resize_zone(x, y, w or self.W, h or self.H)

    def test_all_eight_zones(self) -> None:
        cases = {
            "n": (self.W // 2, 0),
            "s": (self.W // 2, self.H - 1),
            "w": (0, self.H // 2),
            "e": (self.W - 1, self.H // 2),
            "nw": (0, 0),
            "ne": (self.W - 1, 0),
            "sw": (0, self.H - 1),
            "se": (self.W - 1, self.H - 1),
        }
        for expected, (x, y) in cases.items():
            with self.subTest(zone=expected):
                self.assertEqual(self.zone(x, y), expected)

    def test_interior_is_none(self) -> None:
        """None 是"这儿不是边缘,按下就是拖窗口"—— 用户要的整块可拖就靠它。"""
        for x, y in ((self.W // 2, self.H // 2), (60, 60), (self.W - 60, self.H - 60)):
            with self.subTest(point=(x, y)):
                self.assertIsNone(self.zone(x, y))

    def test_corner_beats_edge(self) -> None:
        """角区比边带宽,所以贴着角的那个点必须判成角,不然拉不出"两个方向同时变"。"""
        self.assertEqual(self.zone(2, 2), "nw")
        self.assertEqual(self.zone(2, self.H - 2), "sw")
        self.assertEqual(self.zone(self.W - 2, 2), "ne")
        self.assertEqual(self.zone(self.W - 2, self.H - 2), "se")

    def test_edge_band_boundary_is_exclusive(self) -> None:
        """恰好落在 band 上的点**不**算边缘 —— 边界只归属一侧,不能两边都算。"""
        self.assertEqual(self.zone(self.W // 2, RESIZE_BORDER_PX - 1), "n")
        self.assertIsNone(self.zone(self.W // 2, RESIZE_BORDER_PX))

    def test_corner_box_boundary_is_exclusive(self) -> None:
        self.assertEqual(self.zone(RESIZE_CORNER_PX - 1, RESIZE_CORNER_PX - 1), "nw")
        self.assertIsNone(self.zone(RESIZE_CORNER_PX, RESIZE_CORNER_PX))

    def test_every_zone_is_reachable_at_the_minimum_window_size(self) -> None:
        """最小窗口上八个区都得能点到。

        这是"角区不能比窗口的一半还大"的实证:真按 100px 的角区去算,
        最小窗口(380x220)会整块都是角,连拖窗口的地方都没有了。
        """
        w, h = MIN_WINDOW_W, MIN_WINDOW_H
        cases = {
            "nw": (0, 0),
            "ne": (w - 1, 0),
            "sw": (0, h - 1),
            "se": (w - 1, h - 1),
            "n": (w // 2, 0),
            "s": (w // 2, h - 1),
            "w": (0, h // 2),
            "e": (w - 1, h // 2),
        }
        for expected, (x, y) in cases.items():
            with self.subTest(zone=expected):
                self.assertEqual(resize_zone(x, y, w, h), expected)
        self.assertIsNone(resize_zone(w // 2, h // 2, w, h))

    def test_opposite_corner_zones_do_not_meet_at_the_minimum_size(self) -> None:
        """左右两排角区中间必须留得下一条能拖窗口的缝。"""
        self.assertLess(RESIZE_CORNER_PX * 2, MIN_WINDOW_W)
        self.assertLess(RESIZE_CORNER_PX * 2, MIN_WINDOW_H)

    def test_degenerate_size_is_not_a_zone(self) -> None:
        """窗口还没映射出来、尺寸是 0 的时候不该误判出边缘。"""
        for w, h in ((0, 400), (400, 0), (-1, -1)):
            with self.subTest(size=(w, h)):
                self.assertIsNone(resize_zone(0, 0, w, h))


class ResizeRectTest(unittest.TestCase):
    """拖动量 -> 新的 (x, y, w, h)。

    这里最容易错的是"拖左边时光改宽不改 x",症状是窗口整个往右跑、像在躲鼠标。
    """

    START = (100, 60, 520, 400)   # x, y, w, h

    def resized(self, zone: str, dx: int, dy: int) -> tuple[int, int, int, int]:
        x, y, w, h = self.START
        return resize_rect(zone, (w, h), (x, y), dx, dy)

    def test_dragging_east_only_changes_width(self) -> None:
        self.assertEqual(self.resized("e", 40, 0), (100, 60, 560, 400))

    def test_dragging_south_only_changes_height(self) -> None:
        self.assertEqual(self.resized("s", 0, 30), (100, 60, 520, 430))

    def test_dragging_west_moves_x_and_keeps_the_right_edge(self) -> None:
        """往左拖 50 像素 = 左边那条边往左伸,右边不动。"""
        self.assertEqual(self.resized("w", -50, 0), (50, 60, 570, 400))
        self.assertEqual(50 + 570, 100 + 520)

    def test_dragging_north_moves_y_and_keeps_the_bottom_edge(self) -> None:
        self.assertEqual(self.resized("n", 0, -30), (100, 30, 520, 430))
        self.assertEqual(30 + 430, 60 + 400)

    def test_dragging_west_shrinks_from_the_left(self) -> None:
        """往右拖(正的 dx)是**缩小**:左边那条边往右收,右边还是不动。"""
        self.assertEqual(self.resized("w", 60, 0), (160, 60, 460, 400))

    def test_corners_change_both_directions(self) -> None:
        self.assertEqual(self.resized("se", 20, 10), (100, 60, 540, 410))
        self.assertEqual(self.resized("nw", -20, -10), (80, 50, 540, 410))

    def test_east_and_south_never_move_the_origin(self) -> None:
        """拖右/下边时窗口左上角必须纹丝不动 —— 动了就是拖动和缩放串了。"""
        for zone in ("e", "s", "se"):
            with self.subTest(zone=zone):
                for dx, dy in ((10, 10), (-10, -10), (0, 25), (-40, 0)):
                    x, y, _, _ = self.resized(zone, dx, dy)
                    self.assertEqual((x, y), (100, 60))

    def test_shrinking_from_the_left_widens_towards_the_maximum(self) -> None:
        """反方向也一样会被钳住:左边往左拖太多,宽度撞上限,右边缘照样不动。"""
        for dx in (-9000,):
            with self.subTest(dx=dx):
                x, y, w, h = self.resized("w", dx, 0)
                self.assertEqual(w, MAX_WINDOW_W)
                self.assertEqual(x + w, 100 + 520)
                self.assertEqual((y, h), (60, 400))

    def test_clamping_to_the_minimum_anchors_the_opposite_edge(self) -> None:
        """缩到最小尺寸之后继续拖,窗口不能漂走。

        这就是拖左边时"用对面那条边反推 x"的意义:一旦宽度被钳在 380,
        再往右拖多少,右边缘都得停在 620 上。不这么算的话,被钳住之后
        每多拖一像素窗口就会整体挪一像素 —— 看起来像在躲鼠标。
        """
        for dx in (500, 900, 5000):
            with self.subTest(dx=dx):
                x, y, w, h = self.resized("w", dx, 0)
                self.assertEqual((w, h), (MIN_WINDOW_W, 400))
                self.assertEqual(x + w, 100 + 520)
                self.assertEqual(y, 60)

    def test_clamping_the_north_edge_keeps_the_bottom(self) -> None:
        for dy in (500, 5000):
            with self.subTest(dy=dy):
                x, y, w, h = self.resized("n", 0, dy)
                self.assertEqual((w, h), (520, MIN_WINDOW_H))
                self.assertEqual(y + h, 60 + 400)
                self.assertEqual(x, 100)

    def test_clamping_the_maximum_also_anchors(self) -> None:
        x, y, w, h = self.resized("nw", -99999, -99999)
        self.assertEqual((w, h), (MAX_WINDOW_W, MAX_WINDOW_H))
        self.assertEqual(x + w, 100 + 520)
        self.assertEqual(y + h, 60 + 400)

    def test_opposite_edge_is_fixed_for_every_direction(self) -> None:
        """把上面几条揉成一条性质:贴着哪条边,对面那条边就一动不动。"""
        x0, y0, w0, h0 = self.START
        for zone in ("n", "s", "w", "e", "nw", "ne", "sw", "se"):
            with self.subTest(zone=zone):
                x, y, w, h = self.resized(zone, -37, 23)
                if "w" in zone:
                    self.assertEqual(x + w, x0 + w0)
                if "n" in zone:
                    self.assertEqual(y + h, y0 + h0)
                if "e" in zone:
                    self.assertEqual(x, x0)
                if "s" in zone:
                    self.assertEqual(y, y0)

    def test_zero_drag_changes_nothing(self) -> None:
        """按下去没动就松开,窗口不能有变化 —— 否则拖窗口会"自己长大一点"。"""
        for zone in ("n", "s", "w", "e", "nw", "ne", "sw", "se"):
            with self.subTest(zone=zone):
                self.assertEqual(self.resized(zone, 0, 0), self.START)


class PanelLayoutTest(unittest.TestCase):
    """部件摆位。摆错的表现是按钮叠在一起、或者压在缩放带上点不着。"""

    W, H = 520, 400
    # 实测宽度大致就是这几个数:"开始"两颗字,"切换为倒计时"六颗字
    TEXT_WIDTHS = (34, 34, 104)

    def layout(self, w: int | None = None, h: int | None = None, widths=None):
        return panel_layout(w or self.W, h or self.H, widths or self.TEXT_WIDTHS)

    def test_three_buttons_in_a_row(self) -> None:
        buttons = self.layout().buttons
        self.assertEqual(len(buttons), 3)
        self.assertEqual(len({b.y for b in buttons}), 1)
        self.assertEqual(len({b.h for b in buttons}), 1)

    def test_buttons_do_not_overlap_and_are_in_order(self) -> None:
        buttons = self.layout().buttons
        for left, right in zip(buttons, buttons[1:]):
            with self.subTest(left=left, right=right):
                self.assertLessEqual(left.right, right.x)
                self.assertGreaterEqual(right.x - left.right, 1)  # 至少留一点缝

    def test_buttons_are_centred_in_the_panel(self) -> None:
        buttons = self.layout().buttons
        left_gap = buttons[0].x
        right_gap = self.W - buttons[-1].right
        self.assertLessEqual(abs(left_gap - right_gap), 1)

    def test_wider_text_gets_a_wider_button(self) -> None:
        """按钮宽度跟着文字走,不是均分 —— 参考图里三颗本来就宽窄不一。"""
        buttons = self.layout().buttons
        self.assertGreater(buttons[2].w, buttons[0].w)

    def test_narrow_text_still_gets_a_clickable_button(self) -> None:
        buttons = self.layout(widths=(4, 4, 4)).buttons
        for button in buttons:
            with self.subTest(button=button):
                self.assertGreaterEqual(button.w, BTN_MIN_W)

    def test_buttons_are_inside_the_panel(self) -> None:
        for width in (MIN_WINDOW_W, 520, 900):
            with self.subTest(width=width):
                buttons = self.layout(w=width).buttons
                self.assertGreaterEqual(buttons[0].x, PANEL_PAD)
                self.assertLessEqual(buttons[-1].right, width - PANEL_PAD)

    def test_a_very_long_label_still_fits(self) -> None:
        """兜底:文字长到一行放不下时整体压窄,但绝不能画到面板外面去。"""
        buttons = self.layout(widths=(400, 400, 900)).buttons
        self.assertGreaterEqual(buttons[0].x, 0)
        self.assertLessEqual(buttons[-1].right, self.W)

    def test_digit_area_is_above_the_buttons(self) -> None:
        layout = self.layout()
        self.assertLessEqual(layout.digits.bottom, layout.buttons[0].y)

    def test_digit_area_is_never_empty(self) -> None:
        """最小窗口上数字区也得剩下正的高度,否则数字没地方画。"""
        self.assertGreater(self.layout(h=MIN_WINDOW_H).digits.h, 0)

    def test_close_button_sits_in_the_top_right(self) -> None:
        close = self.layout().close
        self.assertGreaterEqual(close.x, self.W // 2)
        self.assertEqual(close.right + CLOSE_INSET, self.W)
        self.assertEqual(close.y, CLOSE_INSET)

    def test_close_button_escapes_the_corner_resize_zone(self) -> None:
        """× 必须整个落在右上角缩放区之外。

        不躲开的话,"想关窗口"会变成"想拉大窗口" —— 而 × 又小又靠角,
        这是最容易撞上的一个。
        """
        for width, height in ((MIN_WINDOW_W, MIN_WINDOW_H), (520, 400), (900, 700)):
            with self.subTest(size=(width, height)):
                close = panel_layout(width, height, self.TEXT_WIDTHS).close
                for x, y in (
                    (close.x, close.y),
                    (close.right - 1, close.y),
                    (close.x, close.bottom - 1),
                    (close.right - 1, close.bottom - 1),
                ):
                    self.assertIsNone(resize_zone(x, y, width, height))

    def test_button_row_escapes_the_bottom_resize_zone(self) -> None:
        """按钮行不能压在底边的缩放带上,不然点按钮会变成拉窗口。"""
        for width, height in ((MIN_WINDOW_W, MIN_WINDOW_H), (520, 400), (900, 700)):
            with self.subTest(size=(width, height)):
                for button in panel_layout(width, height, self.TEXT_WIDTHS).buttons:
                    for y in (button.y, button.bottom - 1):
                        self.assertIsNone(resize_zone(button.centre[0], y, width, height))

    def test_PANEL_PAD_clears_the_side_resize_bands(self) -> None:
        """左右留白必须比侧边缩放带宽,否则第一颗按钮的一角会被缩放抢走。"""
        self.assertGreater(PANEL_PAD, RESIZE_BORDER_PX)

    def test_bottom_margin_clears_the_bottom_band(self) -> None:
        """按钮行距下边缘的留白同样要比缩放带宽。"""
        layout = self.layout()
        self.assertGreaterEqual(self.H - layout.buttons[0].bottom, RESIZE_BORDER_PX)


if __name__ == "__main__":
    unittest.main()
