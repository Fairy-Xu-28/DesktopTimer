"""视图计算 —— 颜色、主题、字号、窗口几何。纯 Python,不 import tkinter。

这是 v1.1.0 新增的一层,理由和当初把计时逻辑抽成 timer_core 一样:
**把「界面长什么样」里能算的部分拿出来**,于是它们可以用假数据直接断言,
不必真的开一个窗口肉眼看。

timer_ui 只负责把这里算出来的值写进控件,自己不做任何计算。

分层后的依赖方向:
    timer.py -> timer_ui.py -> {timer_view.py, timer_settings.py} -> timer_core.py
"""

from __future__ import annotations

from dataclasses import dataclass

from timer_core import State

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
    "PRESET_COLORS",
    "DEFAULT_BG",
    "MIN_BTN_BG_DISTANCE",
    "MIN_WINDOW_W",
    "MIN_WINDOW_H",
    "MIN_DIGIT_PX",
    "MAX_DIGIT_PX",
]

# ---------------- 配色常量 ----------------

DEFAULT_BG = "#3b3b3b"
DARK_TEXT = "#1c1c1c"
LIGHT_TEXT = "#ffffff"

# 用户可选的预设底色,只做深色系。
PRESET_COLORS: tuple[tuple[str, str], ...] = (
    ("深灰", "#3b3b3b"),
    ("纯黑", "#000000"),
    ("深蓝", "#1e2430"),
    ("墨绿", "#1f2b24"),
)

# 从一个底色往「远离底色」的方向混色的比例。
# 这几个数是照着 v1.0.0 手工调出来的调色板反推的,所以默认主题看起来和以前一样
# (tests/test_timer_view.py 里有一条断言把这个性质钉住了)。
BTN_MIX = 0.09          # #3b3b3b -> #4d4d4d,正是原来的按钮底色
BTN_ACTIVE_MIX = 0.18   # #3b3b3b -> #5e5e5e,原来的按下态
BTN_FG_MIX = 0.88       # #3b3b3b -> #e8e8e8,原来的按钮文字
DISABLED_MIX = 0.34     # #3b3b3b -> #7e7e7e,原来的禁用文字(原值 #7d7d7d)
HINT_MIX = 0.48         # #3b3b3b -> #999999,原来的提示文字(原值 #9a9a9a)

# WCAG 用来判断「该配白字还是黑字」的相对亮度分界线。
LUMINANCE_THRESHOLD = 0.179

# 浅色底上要把语义色压暗,否则绿字黄字在白底上几乎看不清。
SEMANTIC_DARKEN = 0.35

# 语义色(状态提示)。这些是「意思」不是「配色」,所以不参与主题派生,
# 只在底色偏亮时压暗一点保证可读。
SEMANTIC_STATUS = {
    State.RUNNING: "#7fd47f",
    State.PAUSED: "#e8c46a",
}

# 按钮底色与窗口底色至少要差这么多(单通道)。这条是为透明功能兜底的:
# 背景透明靠的是「把某个颜色整片抹掉」,如果按钮底色和窗口底色太接近,
# 按钮就可能被一起抹掉,变成点都点不到。
# 目前四个预设的实际距离是 18 / 23 / 20 / 20,都过得去。
MIN_BTN_BG_DISTANCE = 16

# ---------------- 几何常量 ----------------

MIN_WINDOW_W = 380
MIN_WINDOW_H = 220
MAX_WINDOW_W = 4000   # 防止还原出一块比屏幕还大的窗口
MAX_WINDOW_H = 4000

MIN_DIGIT_PX = 20     # 再小就没法读
MAX_DIGIT_PX = 160    # 再大就撑破布局
DIGIT_PAD_X = 112     # 数字两侧留给 padding 的像素(对应 Label 的 padx=28 两侧)
DIGIT_HEIGHT_FRACTION = 0.44   # 数字最多占窗口高度的比例

WINDOW_VERTICAL_DIVISOR = 3    # 纵向放在屏幕上 1/3 处而非正中,视线更舒服

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


# ---------------- 主题 ----------------

@dataclass(frozen=True)
class Theme:
    """一套完整的配色。由底色派生,界面层直接拿去写控件。"""

    bg: str
    fg: str
    hint_fg: str
    btn_bg: str
    btn_fg: str
    btn_active: str
    btn_disabled_fg: str
    status_fg: dict[State, str]

    def status_color(self, state: State) -> str:
        return self.status_fg[state]


def _semantic_color(base: str, bg_luminance: float) -> str:
    """语义色(绿/黄)在浅色底上压暗,否则白底上根本看不清。"""
    rgb = hex_to_rgb(base)
    if bg_luminance > LUMINANCE_THRESHOLD:
        rgb = mix_rgb(rgb, (0.0, 0.0, 0.0), SEMANTIC_DARKEN)  # type: ignore[assignment]
    return rgb_to_hex(rgb)


def build_theme(bg: str) -> Theme:
    """从一个底色派生出一整套配色。

    派生方向跟着文字色走:深底往白里混(控件更亮 = 更凸出),
    浅底往黑里混。所以同一个函数同时支持深色和浅色主题。

    默认底色 ``#3b3b3b`` 派生出来的结果与 v1.0.0 手工调的那套几乎逐位相同 ——
    这不是巧合,几个 MIX 常量就是照着旧调色板反推出来的。
    """
    bg = normalize_color(bg)
    bg_rgb = hex_to_rgb(bg)
    luminance = relative_luminance(bg)
    fg = readable_foreground(bg)
    # 深底往白走,浅底往黑走
    away = (255.0, 255.0, 255.0) if fg == LIGHT_TEXT else (0.0, 0.0, 0.0)
    toward = tuple(away)  # type: ignore[assignment]

    status_fg = {
        State.IDLE: rgb_to_hex(mix_rgb(bg_rgb, toward, HINT_MIX)),
        State.RUNNING: _semantic_color(SEMANTIC_STATUS[State.RUNNING], luminance),
        State.PAUSED: _semantic_color(SEMANTIC_STATUS[State.PAUSED], luminance),
    }

    return Theme(
        bg=bg,
        fg=fg,
        hint_fg=rgb_to_hex(mix_rgb(bg_rgb, toward, HINT_MIX)),
        btn_bg=rgb_to_hex(mix_rgb(bg_rgb, toward, BTN_MIX)),
        btn_fg=rgb_to_hex(mix_rgb(bg_rgb, toward, BTN_FG_MIX)),
        btn_active=rgb_to_hex(mix_rgb(bg_rgb, toward, BTN_ACTIVE_MIX)),
        btn_disabled_fg=rgb_to_hex(mix_rgb(bg_rgb, toward, DISABLED_MIX)),
        status_fg=status_fg,
    )


# ---------------- 字号 ----------------

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def digit_font_px(width: int, height: int, text_ratio: float, line_ratio: float) -> int:
    """按窗口尺寸算大号数字该用多大字号(像素)。

    ``text_ratio`` 是「整串 00:00:00 的宽度 / 字号」,``line_ratio`` 是行高比。
    这两个比例由调用方用 tkinter.font 实测后传进来,而不是写死 ——
    写死就得猜 Consolas 的字符宽度,而且换台机器字体被替换就全错。

    宽高各算一个上限,取小的那个,再钳到 [MIN_DIGIT_PX, MAX_DIGIT_PX]。
    """
    if text_ratio <= 0 or line_ratio <= 0:
        raise ValueError("字体比例必须为正数")
    available_w = width - DIGIT_PAD_X
    by_width = available_w / text_ratio
    by_height = (height * DIGIT_HEIGHT_FRACTION) / line_ratio
    return int(_clamp(min(by_width, by_height), MIN_DIGIT_PX, MAX_DIGIT_PX))


def scaled_font_px(base_px: int, factor: float, low: int, high: int) -> int:
    """次要文字(按钮/状态/提示)按数字字号等比缩放,并各自钳位。

    不让它们自由缩放是因为:按钮文字太小点不着看着难受,太大又会把按钮撑破。
    """
    return int(_clamp(base_px * factor, low, high))


def clamp_window_size(width: int, height: int) -> tuple[int, int]:
    """把窗口尺寸钳到合理范围。

    下限对应 minsize,防止字号触底;上限防止从设置文件里还原出一块
    比屏幕还大的窗口(比如换了小显示器)。
    """
    return (
        int(_clamp(width, MIN_WINDOW_W, MAX_WINDOW_W)),
        int(_clamp(height, MIN_WINDOW_H, MAX_WINDOW_H)),
    )
