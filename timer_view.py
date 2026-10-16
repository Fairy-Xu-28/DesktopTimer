"""视图计算 —— 颜色、主题、字号、窗口几何。纯 Python,不 import tkinter。

这是 v1.1.0 新增的一层,理由和当初把计时逻辑抽成 timer_core 一样:
**把「界面长什么样」里能算的部分拿出来**,于是它们可以用假数据直接断言,
不必真的开一个窗口肉眼看。

timer_ui 只负责把这里算出来的值写进控件,自己不做任何计算。

分层后的依赖方向:
    timer.py -> timer_ui.py -> {timer_view.py, timer_settings.py} -> timer_core.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

__all__ = [
    "Theme",
    "build_theme",
    "normalize_color",
    "hex_to_rgb",
    "rgb_to_hex",
    "mix_rgb",
    "relative_luminance",
    "readable_foreground",
    "channel_distance",
    "digit_font_px",
    "scaled_font_px",
    "clamp_window_size",
    "clamp_window_position",
    "stroke_offsets",
    "stroke_color",
    "Rect",
    "PanelLayout",
    "panel_layout",
    "panel_radius",
    "digit_area",
    "button_row_y",
    "rounded_rect_points",
    "resize_zone",
    "resize_rect",
    "DEFAULT_BG",
    "MIN_BTN_BG_DISTANCE",
    "MIN_WINDOW_W",
    "MIN_WINDOW_H",
    "MIN_DIGIT_PX",
    "MAX_DIGIT_PX",
    "CORNER_KEY",
    "WINDOW_ALPHA",
    "RESIZE_BORDER_PX",
    "RESIZE_CORNER_PX",
    "PANEL_RADIUS_MIN",
    "PANEL_RADIUS_MAX",
    "PANEL_PAD",
    "BTN_MIN_W",
    "BTN_HEIGHT",
    "BTN_GAP",
    "CLOSE_SIZE",
    "CLOSE_INSET",
]

# ---------------- 配色常量 ----------------

# v2.0.0 起配色不再由用户选择,整套界面就这一套。
# 底色取的是参考图里那个深灰:要足够暗,才能压住底下透出来的东西,
# 白字才始终读得清(面板是半透明的,数字底下是用户的桌面)。
DEFAULT_BG = "#2b2b2b"
DARK_TEXT = "#1c1c1c"
LIGHT_TEXT = "#ffffff"

# 从一个底色往「远离底色」的方向混色的比例。
BTN_MIX = 0.09          # #2b2b2b -> #3e3e3e,按钮的常态底色
BTN_ACTIVE_MIX = 0.18   # #2b2b2b -> #4f4f4f,鼠标悬停在按钮上
BTN_FG_MIX = 0.88       # #2b2b2b -> #e5e5e5,按钮文字
HINT_MIX = 0.48         # #2b2b2b -> #8f8f8f,不抢眼的次要前景(右上角那个 ×)

# 按下态要比常态**更暗**,不能比悬停还亮 —— 否则"按住"看起来像"更亮了",
# 和"按下去"的手感正好相反。所以它是往黑里混,方向和其他几个相反。
BTN_PRESSED_MIX = 0.35

# WCAG 用来判断「该配白字还是黑字」的相对亮度分界线。
LUMINANCE_THRESHOLD = 0.179

# 按钮底色与面板底色至少要差这么多(单通道)。差得太近按钮就"陷"进面板里,
# 看不出那是颗按钮。
MIN_BTN_BG_DISTANCE = 16

# ---------------- 几何常量 ----------------

MIN_WINDOW_W = 380
MIN_WINDOW_H = 220
MAX_WINDOW_W = 4000   # 防止还原出一块比屏幕还大的窗口
MAX_WINDOW_H = 4000

MIN_DIGIT_PX = 20     # 再小就没法读
MAX_DIGIT_PX = 220    # 再大就撑破布局
# 下面两个是相对**数字区**(见 digit_area)算的,不是相对整个窗口 ——
# v2.0.0 起数字区只占面板的上半部分,底下留给按钮行。
# 数字最多占数字区宽度的比例。用比例而不是固定的左右留白(两版之前是固定值):
# 固定留白在宽窗口上会让数字一直涨到贴着面板边缘,比例才不会。
DIGIT_WIDTH_FRACTION = 0.78
DIGIT_HEIGHT_FRACTION = 0.62   # 数字最多占数字区高度的比例

WINDOW_VERTICAL_DIVISOR = 3    # 纵向放在屏幕上 1/3 处而非正中,视线更舒服

# 窗口至少要有这么多像素留在屏幕里。
# v1.2.0 起窗口是无边框的、也不在任务栏上,一旦整个跑到屏幕外就再也点不到了 ——
# 所以还原上次位置时必须把它拽回来一部分。
MIN_VISIBLE_PX = 90

# ---------------- 颜色 ----------------

def normalize_color(text: str) -> str:
    """把用户给的颜色规范成 ``#rrggbb`` 小写形式;认不出就抛 ValueError。

    只接受十六进制写法 —— 因为用户在界面上是点色块或调取色器,不会手打颜色名,
    而颜色名(如 "red"、"gray20")需要 Tk 的颜色表,本模块刻意不依赖 Tk。
    """
    if not isinstance(text, str):
        raise ValueError(f"颜色必须是字符串,收到 {type(text).__name__}")
    body = text.strip()
    if body.startswith("#"):
        body = body[1:]
    if len(body) == 3:
        body = "".join(ch * 2 for ch in body)   # #abc -> #aabbcc
    if len(body) != 6:
        raise ValueError(f"颜色格式不对: {text!r}(需要 #rgb 或 #rrggbb)")
    try:
        int(body, 16)
    except ValueError:
        raise ValueError(f"颜色里有非十六进制字符: {text!r}") from None
    return "#" + body.lower()


def hex_to_rgb(text: str) -> tuple[int, int, int]:
    """``#rrggbb`` -> ``(r, g, b)``。输入会先被规范化。"""
    body = normalize_color(text)[1:]
    return int(body[0:2], 16), int(body[2:4], 16), int(body[4:6], 16)


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    """``(r, g, b)`` -> ``#rrggbb``。每通道钳到 0-255 并四舍五入。"""
    channels = (max(0, min(255, round(value))) for value in rgb)
    return "#{:02x}{:02x}{:02x}".format(*channels)


def mix_rgb(
    base: tuple[float, float, float],
    target: tuple[float, float, float],
    ratio: float,
) -> tuple[float, float, float]:
    """按比例从 base 向 target 混色。ratio=0 是 base,1 是 target。"""
    return tuple(b + (t - b) * ratio for b, t in zip(base, target))  # type: ignore[return-value]


def channel_distance(a: str, b: str) -> int:
    """两个颜色在单通道上的最大差值,0 表示完全相同。

    用「最大单通道差」而不是欧氏距离,是因为判断「按钮会不会被透明键吃掉」
    时关心的是每个通道是否都拉开了 —— 只要有一个通道完全相同,视觉上就可能糊在一起。
    """
    return max(abs(x - y) for x, y in zip(hex_to_rgb(a), hex_to_rgb(b)))


def _linearize(channel: int) -> float:
    """sRGB 通道值 -> 线性光强度(WCAG 定义的那一步)。"""
    value = channel / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    """WCAG 相对亮度,范围 0(黑)到 1(白)。

    注意它**不是**简单的通道平均值 —— 人眼对绿色最敏感、对蓝色最不敏感,
    所以必须做 gamma 校正再加权,否则「该配白字还是黑字」会判断错。
    """
    red, green, blue = hex_to_rgb(color)
    return (
        0.2126 * _linearize(red)
        + 0.7152 * _linearize(green)
        + 0.0722 * _linearize(blue)
    )


def readable_foreground(bg: str) -> str:
    """给一个底色挑一个看得清的文字色。"""
    if relative_luminance(bg) > LUMINANCE_THRESHOLD:
        return DARK_TEXT
    return LIGHT_TEXT


# 描边(数字外面那圈衬托色)用的两个近似黑/白。不用纯黑纯白,是因为纯色在
# 浅底/深底上都显得生硬,稍微收一点更像"阴影"而不是"描边"。
STROKE_DARK = "#101010"
STROKE_LIGHT = "#f2f2f2"
STROKE_SHIFT = 0x30   # 描边色不幸和键色撞上时,把每个通道挪这么远


def stroke_color(fg: str, bg: str) -> str:
    """给数字挑一个描边色 —— 就是衬在文字外面、让它任何背景上都看得清的那一圈。

    为什么需要它:开背景透明之后,数字底下压的是**用户的桌面**,而我们不知道
    桌面是什么颜色。白字浮在白桌面上等于没写(实测过,正是用户报上来的现象)。
    给字加一圈反色的描边,浅桌面深桌面就都读得出来了。

    描边色**绝对不能等于键色**(也就是窗口底色)。等于的话那一圈像素会被透明
    一起抹掉,描边白加 —— 所以撞上了就往远离底色的方向挪一档。
    """
    candidate = (
        STROKE_DARK if relative_luminance(fg) > LUMINANCE_THRESHOLD else STROKE_LIGHT
    )
    if normalize_color(candidate) == normalize_color(bg):
        direction = -STROKE_SHIFT if relative_luminance(bg) > 0.5 else STROKE_SHIFT
        candidate = rgb_to_hex(tuple(c + direction for c in hex_to_rgb(candidate)))
    return candidate


# ---------------- 主题 ----------------

@dataclass(frozen=True)
class Theme:
    """一套完整的配色。由面板底色派生,界面层直接拿去画。

    v2.0.0 起只有一套配色(用户不再能换),这个类保留下来是因为
    "底色 -> 按钮色 / 文字色 / 悬停色"的派生关系仍然要算,
    而且算出来的对比度有测试盯着。
    """

    bg: str          # 面板底色
    fg: str          # 大号数字
    hint_fg: str     # 不抢眼的次要前景(右上角那个 ×)
    btn_bg: str      # 按钮常态
    btn_fg: str      # 按钮文字
    btn_active: str  # 鼠标悬停在按钮上
    btn_pressed: str # 按住按钮


def build_theme(bg: str) -> Theme:
    """从一个底色派生出一整套配色。

    派生方向跟着文字色走:深底往白里混(控件更亮 = 更凸出),
    浅底往黑里混。所以同一个函数同时支持深色和浅色主题 ——
    虽然 v2.0.0 只用深色那一套,但保留这个性质不花什么代价。

    按下态是**唯一的例外**:它固定往黑里混。按钮亮起来表示"轮到你点了",
    按下去则应该暗下去 —— 两者方向相反,手感才对。
    """
    bg = normalize_color(bg)
    bg_rgb = hex_to_rgb(bg)
    fg = readable_foreground(bg)
    # 深底往白走,浅底往黑走
    toward: tuple[float, float, float] = (
        (255.0, 255.0, 255.0) if fg == LIGHT_TEXT else (0.0, 0.0, 0.0)
    )

    return Theme(
        bg=bg,
        fg=fg,
        hint_fg=rgb_to_hex(mix_rgb(bg_rgb, toward, HINT_MIX)),
        btn_bg=rgb_to_hex(mix_rgb(bg_rgb, toward, BTN_MIX)),
        btn_fg=rgb_to_hex(mix_rgb(bg_rgb, toward, BTN_FG_MIX)),
        btn_active=rgb_to_hex(mix_rgb(bg_rgb, toward, BTN_ACTIVE_MIX)),
        btn_pressed=rgb_to_hex(mix_rgb(bg_rgb, (0.0, 0.0, 0.0), BTN_PRESSED_MIX)),
    )


# ---------------- 字号 ----------------

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def digit_font_px(width: int, height: int, text_ratio: float, line_ratio: float) -> int:
    """按**数字区**的尺寸算大号数字该用多大字号(像素)。

    传进来的是数字区(``digit_area``)的宽高,不是整个窗口 —— 窗口下半部分
    是按钮行,数字不该按那部分来算大小。

    ``text_ratio`` 是「整串 00:00:00 的宽度 / 字号」,``line_ratio`` 是行高比。
    这两个比例由调用方用 tkinter.font 实测后传进来,而不是写死 ——
    写死就得猜 Consolas 的字符宽度,而且换台机器字体被替换就全错。

    宽高各算一个上限,取小的那个,再钳到 [MIN_DIGIT_PX, MAX_DIGIT_PX]。
    """
    if text_ratio <= 0 or line_ratio <= 0:
        raise ValueError("字体比例必须为正数")
    by_width = (width * DIGIT_WIDTH_FRACTION) / text_ratio
    by_height = (height * DIGIT_HEIGHT_FRACTION) / line_ratio
    return int(_clamp(min(by_width, by_height), MIN_DIGIT_PX, MAX_DIGIT_PX))


def scaled_font_px(base_px: int, factor: float, low: int, high: int) -> int:
    """次要文字(按钮/状态/提示)按数字字号等比缩放,并各自钳位。

    不让它们自由缩放是因为:按钮文字太小点不着看着难受,太大又会把按钮撑破。
    """
    return int(_clamp(base_px * factor, low, high))


def clamp_window_size(width: int, height: int) -> tuple[int, int]:
    """把窗口尺寸钳到合理范围。

    下限防止字号触底;上限防止从设置文件里还原出一块比屏幕还大的窗口
    (比如换了小显示器)。

    v1.2.0 起窗口是无边框的、没有系统缩放边框,所以这个函数不再只是"兜底",
    它是**唯一的**尺寸下限来源 —— 拖右下角那个手柄时靠它挡着。
    """
    return (
        int(_clamp(width, MIN_WINDOW_W, MAX_WINDOW_W)),
        int(_clamp(height, MIN_WINDOW_H, MAX_WINDOW_H)),
    )


def clamp_window_position(
    x: int,
    y: int,
    width: int,
    height: int,
    screen_w: int,
    screen_h: int,
) -> tuple[int, int]:
    """把窗口位置拽回到"至少还有一块露在屏幕里"的范围。

    为什么要管这件事:无边框窗口既没有标题栏、也不出现在任务栏上,
    一旦整个跑到屏幕外,用户就再也点不到它了 —— 连关都关不掉。
    换显示器、改分辨率、或者上一次拖到屏幕边缘,都可能造成这种局面。

    纵向只保证"顶边不跑到屏幕上方"(上面有 × 按钮和拖动区),
    横向允许越出去一部分,因为窗口本来就可能一半贴边。
    """
    return (
        int(_clamp(x, MIN_VISIBLE_PX - width, screen_w - MIN_VISIBLE_PX)),
        int(_clamp(y, 0, screen_h - MIN_VISIBLE_PX)),
    )


# ---------------- 描边 ----------------
# 数字的描边要画在 Canvas 上。把偏移量算法放在这里(而不是界面层),
# 是为了它能用普通断言测。


def stroke_offsets(radius: int = 1) -> tuple[tuple[int, int], ...]:
    """文字描边要复制的 8 个偏移量(不含中心)。

    做法是把同一串数字在 8 个方向上各画一遍、画在底色为描边色的那层,
    最后把正色的字盖在中间 —— 看起来就是一圈描边。
    取 8 个方向而不是 4 个,是为了斜角也不漏,圆润的字形转角处不会缺口。
    """
    if radius <= 0:
        raise ValueError(f"描边半径必须为正数,收到 {radius}")
    return tuple(
        (dx, dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if (dx, dy) != (0, 0)
    )


# ---------------- 面板几何 ----------------
# v2.0.0 起整个界面画在**一块** Canvas 上(面板、数字、三颗按钮、右上角的 ×),
# 不再用 pack/grid 摆控件。于是"谁在哪儿"全是算出来的,也就全都能放进这个模块
# —— 布局对不对、点在哪颗按钮上、按在哪个边算是缩放,都可以脱离显示器断言。
#
# 顺带解决一个麻烦:一块 Canvas 只有一个 cursor 属性,所以"鼠标移到边上变成
# 双向箭头"就只是每次鼠标移动时改这一个值,不用去追指针底下压着哪个控件。

PANEL_RADIUS_MIN = 10
PANEL_RADIUS_MAX = 18
PANEL_RADIUS_FRACTION = 0.06   # 圆角半径占窗口高度的比例

# 四条边可以拉的宽度,以及四个角可以拉的正方形边长。
# 角一定比边宽,否则在角上永远命中"边",拉不出两个方向同时变的效果。
RESIZE_BORDER_PX = 6
RESIZE_CORNER_PX = 14

# 圆角处要挖掉的颜色。Windows 的颜色键透明会把"正好等于这个颜色"的像素整片
# 抹掉,于是窗口的四个角就真的不属于窗口了 —— 看不见,也点不到。
#
# 这个值是刻意挑的**魔数**:它必须不等于界面里任何一处画出来的颜色
# (面板底色、按钮的常态/悬停/按下、文字、描边),否则那个地方会被顺带挖个洞。
# 挑了个几乎不可能被用到的深色,并且有测试钉住"整套配色里没有任何颜色等于它"。
CORNER_KEY = "#010203"

# 整窗的不透明度。要能透出底下的软件(用户明确要的效果),又要压得住颜色,
# 让白字在任何桌面背景上都读得清 —— 再低数字就糊了。
WINDOW_ALPHA = 0.86

PANEL_PAD = 14                 # 按钮行距面板左右边缘的留白
CLOSE_SIZE = 22                # 右上角 × 的边长
CLOSE_INSET = RESIZE_CORNER_PX + 2   # × 距上边缘/右边缘的距离,见 panel_layout
BTN_HEIGHT = 34
BTN_PAD_X = 14                 # 按钮文字左右各留多少
BTN_MIN_W = 60                 # 按钮再窄就不好点了
BTN_GAP = 10                   # 按钮之间的间距
BTN_BOTTOM_MARGIN = 16         # 按钮行距下边缘。要大于 RESIZE_BORDER_PX,
                               # 否则按钮会压在底边的缩放带上


def panel_radius(height: int) -> int:
    """面板圆角半径。跟着窗口高度走,钳在上下限内。

    固定值不行:窗口缩到最小(220 高)时 18px 的圆角看着像颗胶囊;
    拉到大窗口时 10px 又几乎看不出来。
    """
    return int(_clamp(height * PANEL_RADIUS_FRACTION, PANEL_RADIUS_MIN, PANEL_RADIUS_MAX))


def rounded_rect_points(
    x0: float, y0: float, x1: float, y1: float, radius: float, steps: int = 6
) -> list[float]:
    """圆角矩形的轮廓顶点,扁平的 ``[x1, y1, x2, y2, ...]``,喂给 create_polygon。

    Tk 的 Canvas 没有圆角矩形图元,只能拿多边形逼近:四条直边照画,四个角各用
    ``steps + 1`` 个点沿圆弧走一遍。分成四段独立圆弧而不是一条连续曲线,
    是为了圆心好算 —— 每段的圆心都在距该角点 radius 的位置上。

    半径超过半个短边时会被钳住,否则两段圆弧会互相穿过、形状翻过来。
    返回的是**闭合**轮廓:最后一个点是左下角那段圆弧的终点,
    create_polygon 会自动把它连回第一个点。
    """
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"矩形要有正的宽高,收到 ({x0}, {y0})-({x1}, {y1})")
    if steps < 1:
        raise ValueError(f"每段至少要走 1 步,收到 {steps}")
    radius = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))

    # (圆心 x, 圆心 y, 起始角, 结束角)。按屏幕坐标走一圈:左上 -> 右上 -> 右下 -> 左下。
    # 屏幕的 y 朝下,所以 sin 为负表示"在上方",各段的角度区间就是按这个选的。
    corners = (
        (x0 + radius, y0 + radius, 180.0, 270.0),
        (x1 - radius, y0 + radius, 270.0, 360.0),
        (x1 - radius, y1 - radius, 0.0, 90.0),
        (x0 + radius, y1 - radius, 90.0, 180.0),
    )
    points: list[float] = []
    for cx, cy, start, end in corners:
        for step in range(steps + 1):
            angle = math.radians(start + (end - start) * step / steps)
            points.append(cx + radius * math.cos(angle))
            points.append(cy + radius * math.sin(angle))
    return points


@dataclass(frozen=True)
class Rect:
    """一个矩形,用左上角 + 宽高表示 —— 和 Canvas 的坐标习惯一致。"""

    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def centre(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom


@dataclass(frozen=True)
class PanelLayout:
    """一次布局的完整结果:面板、右上角的 ×、三颗按钮、数字区各在哪儿。"""

    panel: Rect
    close: Rect
    buttons: tuple[Rect, ...]
    digits: Rect


def button_row_y(height: int) -> int:
    """按钮行的上边缘距面板顶部的距离。

    单独拿出来是因为它是**算数字区大小的唯一依据** —— 而算字号之前
    还不知道按钮有多宽(宽度要量文字),所以不能等 panel_layout 算完再回头
    问数字区在哪儿,会绕成一个圈。
    """
    return height - BTN_BOTTOM_MARGIN - BTN_HEIGHT


def digit_area(width: int, height: int) -> Rect:
    """数字区:按钮行以上的整块空间。数字在这块里居中。"""
    return Rect(0, 0, width, max(1, button_row_y(height)))


def panel_layout(
    width: int, height: int, button_text_widths: Sequence[int]
) -> PanelLayout:
    """按面板尺寸和按钮文字的实测宽度,算出各个部件的位置。

    按钮宽度是**跟着文字量出来的**(界面层用 tkfont 量完传进来),不是均分 ——
    "切换为倒计时"比"开始"宽得多,均分的话它会被挤得要换行,而"开始"会空一大块。
    参考图里三颗按钮本来就是不一样宽的。
    """
    panel = Rect(0, 0, width, height)

    # 右上角的 ×。往内挪了 CLOSE_INSET,好让整个按钮落在右上角的缩放区之外 ——
    # 不挪的话"想点 × 却拉起了窗口大小"。
    close = Rect(width - CLOSE_INSET - CLOSE_SIZE, CLOSE_INSET, CLOSE_SIZE, CLOSE_SIZE)

    widths = [max(BTN_MIN_W, int(w) + 2 * BTN_PAD_X) for w in button_text_widths]
    gaps = BTN_GAP * max(0, len(widths) - 1)
    total = sum(widths) + gaps

    # 兜底:万一字体大到一行放不下,整体等比压窄。
    # 正常尺寸下走不到这里(最小窗口配最大字号时也还有富余),但布局函数
    # 不该因为"参数超出预期"就画到面板外面去。
    available = width - 2 * PANEL_PAD
    if total > available > 0:
        scale = available / total
        widths = [max(1, int(w * scale)) for w in widths]
        total = sum(widths) + gaps

    row_y = button_row_y(height)
    x = (width - total) // 2
    buttons: list[Rect] = []
    for button_w in widths:
        buttons.append(Rect(x, row_y, button_w, BTN_HEIGHT))
        x += button_w + BTN_GAP

    return PanelLayout(
        panel=panel, close=close, buttons=tuple(buttons), digits=digit_area(width, height)
    )


def resize_zone(
    x: float,
    y: float,
    width: int,
    height: int,
    band: int = RESIZE_BORDER_PX,
    corner: int = RESIZE_CORNER_PX,
) -> str | None:
    """点 ``(x, y)``(窗口内坐标)落在哪个缩放区。

    返回 ``'n'`` / ``'s'`` / ``'e'`` / ``'w'`` / ``'nw'`` / ``'ne'`` / ``'sw'``
    / ``'se'``;都不在就返回 ``None`` —— ``None`` 的意思是"这儿是拖窗口的地方"。

    **角优先于边**:在角上要能两个方向同时缩放,所以先判角。四个角各是一个
    ``corner × corner`` 的小方块,比边带宽 —— 不然在角上永远只命中一条边。
    """
    if width <= 0 or height <= 0:
        return None

    vertical = None
    if y < corner:
        vertical = "n"
    elif y > height - corner:
        vertical = "s"
    horizontal = None
    if x < corner:
        horizontal = "w"
    elif x > width - corner:
        horizontal = "e"

    if vertical is not None and horizontal is not None:
        return vertical + horizontal

    # 四个角都没命中,再看是不是贴着某一条边
    if y < band:
        return "n"
    if y > height - band:
        return "s"
    if x < band:
        return "w"
    if x > width - band:
        return "e"
    return None


def resize_rect(
    zone: str,
    start_size: tuple[int, int],
    start_pos: tuple[int, int],
    dx: int,
    dy: int,
) -> tuple[int, int, int, int]:
    """按拖动量算出新的 ``(x, y, w, h)``。

    **x/y 也得一起算**,不能只改宽高:拖左边或上边的时候,右边/下边必须保持不动,
    所以窗口的左上角坐标要跟着变。漏掉这一步的症状是"拖左边,窗口整个往右跑"
    —— 看起来像窗口在躲着鼠标。

    尺寸照旧过 ``clamp_window_size``。钳住之后 x/y 仍然按"对面那条边不动"算,
    所以拖到最小尺寸之后继续拖,窗口不会慢慢漂走。
    """
    width, height = start_size
    x, y = start_pos

    new_w, new_h = width, height
    if "e" in zone:
        new_w = width + dx
    elif "w" in zone:
        new_w = width - dx
    if "s" in zone:
        new_h = height + dy
    elif "n" in zone:
        new_h = height - dy
    new_w, new_h = clamp_window_size(new_w, new_h)

    # 贴哪条边就固定对面那条:拖左/上边时窗口要挪位置,拖右/下边时不用
    new_x = (x + width) - new_w if "w" in zone else x
    new_y = (y + height) - new_h if "n" in zone else y
    return new_x, new_y, new_w, new_h
