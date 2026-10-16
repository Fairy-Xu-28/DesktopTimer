"""秒表的界面层 —— Tkinter 相关代码全部在这里。

这是"前后端"里的**前端**:只负责把 Stopwatch 的状态画到窗口上,
以及把按钮、按键转成对 Stopwatch 的调用。它自己不做任何时间计算,
读数和格式化都交给 timer_core。

v1.1.0 起多了一件事:**把"界面长什么样"里能算的部分交给了 timer_view**
(颜色派生、字号计算),把"记住设置"交给了 timer_settings。
本模块只负责把算出来的值写进控件,以及处理窗口这件麻烦事。

窗口这件事里有两个坑,都在下面标了注释:
1. 重排的反馈环 —— 靠"显式尺寸 + 整数哨兵"切断,见 _on_configure
2. 透明和配色互相影响 —— 键色就是当前底色,见 _apply_transparency
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, font as tkfont, messagebox
from typing import Callable

from timer_core import State, Stopwatch
from timer_settings import Settings, load as load_settings, save as save_settings
from timer_view import (
    MIN_WINDOW_H,
    MIN_WINDOW_W,
    PRESET_COLORS,
    WINDOW_VERTICAL_DIVISOR,
    Theme,
    build_theme,
    clamp_window_size,
    digit_font_px,
    normalize_color,
    scaled_font_px,
)

# ---------------- 行为常量 ----------------

WINDOW_TITLE = "计时器"
TICK_INTERVAL_MS = 100          # 刷新间隔。显示精度只到整秒,100ms 足够跟手又不费 CPU
SETTINGS_SAVE_DELAY_MS = 1000   # 尺寸变化后的存盘去抖:拖动中别每次都写盘
DEFAULT_WINDOW_SIZE = (420, 260)  # 没有存过尺寸时的首启尺寸

DIGIT_FAMILY = "Consolas"
UI_FAMILY = "Microsoft YaHei"
DIGIT_SAMPLE = "00:00:00"
REFERENCE_PX = 200   # 标定字体比例用的参考字号(取大一点,测出的比例更准)

# 次要文字相对数字字号的缩放比例,以及各自的上下限。
# 不让它们自由缩放是因为:按钮文字太小看着难受,太大又会把按钮撑破。
BTN_FONT_FACTOR, BTN_FONT_RANGE = 0.26, (9, 20)
STATUS_FONT_FACTOR, STATUS_FONT_RANGE = 0.22, (8, 16)
# 提示文字不参与缩放 —— 它只在设置小窗里出现,而小窗是固定尺寸的

STATUS_TEXT = {
    State.IDLE: "就绪",
    State.RUNNING: "● 计时中",
    State.PAUSED: "‖ 已暂停",
}

# Tk 里自带 <space> 类绑定、因而会和全局空格键冲突的控件类型。
# Button 的是 tk::ButtonInvoke(而且延迟 100ms 才执行),Menubutton 也有。
SPACE_SENSITIVE = (tk.Button, tk.Checkbutton, tk.Radiobutton, tk.Menubutton)

SHORTCUT_HINT = "空格 开始/暂停   ·   R 重置   ·   T 置顶   ·   Esc 退出"

# 写进设置窗和 README 的代价说明。这几条不是缺陷,是这套透明方案的固有行为,
# 写出来是为了以后不会被当成 bug。
TRANSPARENCY_CAVEATS = (
    "背景透明的代价:\n"
    "· 透明处鼠标会穿透,点在那儿等于点桌面\n"
    "· 要移动窗口请拖标题栏\n"
    "· 数字边缘会有一圈很淡的灰边,这是字体的抗锯齿,消不掉"
)


# ---------------- 角色化样式 ----------------
# 换主题时按"角色"统一写控件,而不是逐个手写 config —— 漏掉任何一个,
# 界面上就会露一块旧颜色。角色在登记控件时指定(见 _track)。

def _style_root(theme: Theme) -> dict:
    return {"bg": theme.bg}


def _style_display(theme: Theme) -> dict:
    return {"bg": theme.bg, "fg": theme.fg}


def _style_status(theme: Theme) -> dict:
    # 刻意只写 bg:文字色由"就绪/计时中/已暂停"决定,不能在这里覆盖。
    # 换主题后由 _apply_theme 强制重刷一次状态色。
    return {"bg": theme.bg}


def _style_hint(theme: Theme) -> dict:
    return {"bg": theme.bg, "fg": theme.hint_fg}


def _style_button(theme: Theme) -> dict:
    return {
        "bg": theme.btn_bg,
        "fg": theme.btn_fg,
        "activebackground": theme.btn_active,
        "activeforeground": theme.fg,
        # 必须显式指定:深色底上的禁用文字默认几乎看不见(v1.0.0 踩过这个坑)
        "disabledforeground": theme.btn_disabled_fg,
    }


def _style_check(theme: Theme) -> dict:
    return {
        "bg": theme.bg,
        "fg": theme.hint_fg,
        "activebackground": theme.bg,
        "activeforeground": theme.fg,
        "selectcolor": theme.btn_bg,   # 勾选框内部底色,默认白色会破坏深色主题
    }


def _style_title(theme: Theme) -> dict:
    return {"bg": theme.bg, "fg": theme.fg}


def _style_separator(theme: Theme) -> dict:
    return {"bg": theme.btn_bg}


ROLE_STYLES: dict[str, Callable[[Theme], dict]] = {
    "root": _style_root,
    "display": _style_display,
    "status": _style_status,
    "hint": _style_hint,
    "button": _style_button,
    "check": _style_check,
    "title": _style_title,
    "separator": _style_separator,
}


class TimerApp:
    """把 Stopwatch 渲染到 Tkinter 窗口上。"""

    def __init__(
        self,
        root: tk.Tk,
        watch: Stopwatch | None = None,
        settings_path: Path | None = None,
    ) -> None:
        self.root = root
        # 依赖注入:默认自建;测试时传入用假时钟驱动的实例
        self.watch = watch if watch is not None else Stopwatch()
        # 设置文件的路径同样可注入 —— 冒烟测试必须指到临时目录去,
        # 否则一次测试跑下来会把用户真实的配色和窗口大小覆盖掉。
        self._settings_path = settings_path
        self.settings = load_settings(settings_path)
        self.theme = build_theme(self.settings.bg)

        self._job: str | None = None            # 刷新循环的 after id
        self._relayout_job: str | None = None   # 缩放重排的 after id(和上面是两个,别混用)
        self._save_job: str | None = None       # 存盘去抖的 after id
        self._last_text: str = ""               # 上次渲染的读数,用于去重
        self._last_state: State | None = None
        self._last_size: tuple[int, int] = (0, 0)
        self._digit_px: int = 0                 # 当前数字字号,重排的哨兵
        self._text_ratio: float = 4.4           # 标定前的兜底值,启动后会被实测值覆盖
        self._line_ratio: float = 1.17
        self._themed: list[tuple[tk.Widget, str]] = []
        self._settings_window: tk.Toplevel | None = None
        # 设置小窗那份名单,和 _themed 分开存。小窗还没开时是空的。
        self._settings_themed: list[tuple[tk.Widget, str]] = []
        self._swatches: dict[str, tk.Button] = {}

        self.topmost_var = tk.BooleanVar(value=False)
        self.transparent_var = tk.BooleanVar(value=self.settings.transparent)

        self._build_ui()
        self._calibrate_font_ratios()
        self._setup_geometry()
        self._apply_theme(self.theme)
        self._apply_topmost()
        self._apply_transparency()
        self._bind_keys()
        self._tick()  # 启动刷新循环(内部会先渲染一次)

    # ---------------- 搭建界面 ----------------

    def _track(self, widget: tk.Widget, role: str) -> tk.Widget:
        """把一个控件登记进主题注册表。

        凡是不登记就换不到颜色。开发期可以用 _untracked() 检查有没有漏网的。
        """
        self._themed.append((widget, role))
        return widget

    def _build_ui(self) -> None:
        self.root.title(WINDOW_TITLE)
        self._track(self.root, "root")
        self.root.resizable(True, True)
        self.root.minsize(MIN_WINDOW_W, MIN_WINDOW_H)

        # 大号数字显示区。fill+expand 让底色铺满整个客户区,
        # 否则窗口拉大以后内容会缩在中间、四周露出一圈没被主题覆盖的底色。
        self.display = self._track(
            tk.Label(self.root, text="00:00:00", padx=28, pady=20), "display"
        )
        self.display.pack(fill="both", expand=True)

        # 状态提示。
        # 留出内边距不是为了好看 —— 开背景透明时这个 Label 会换上一块实心底
        # (见 _apply_transparency),没有内边距的话文字会顶着色块边缘。
        self.status = self._track(tk.Label(self.root, text="", padx=12, pady=3), "status")
        self.status.pack()

        # 按钮行
        row = self._track(tk.Frame(self.root), "root")
        row.pack(pady=(10, 8))

        self.start_btn = self._make_button(row, "开始", self._on_start)
        self.pause_btn = self._make_button(row, "暂停", self._on_pause)
        self.reset_btn = self._make_button(row, "重置", self._on_reset)
        for column, btn in enumerate((self.start_btn, self.pause_btn, self.reset_btn)):
            btn.grid(row=0, column=column, padx=4)

        # 设置入口。配色、置顶、透明都收在弹出的小窗里 ——
        # 背景透明时主窗口只会剩下数字和按钮,把设置项留在主窗口里
        # 会出现"控件被当成背景一起抹掉、点不着"的问题。
        self.settings_btn = self._make_button(self.root, "设置", self.open_settings, width=6)
        self.settings_btn.pack(pady=(0, 10))

    def _make_button(
        self, parent: tk.Widget, text: str, command: Callable[[], None], width: int = 8
    ) -> tk.Button:
        return self._track(
            tk.Button(
                parent,
                text=text,
                relief="flat",
                bd=0,
                width=width,
                padx=10,
                pady=8,
                cursor="hand2",
                command=command,
            ),
            "button",
        )

    def _calibrate_font_ratios(self) -> None:
        """实测"整串数字的宽度 / 字号"和"行高 / 字号"两个比例。

        不写死是因为写死就得猜 Consolas 的字符宽度,而且换台机器字体被替换
        (比如没装 Consolas 时回退到别的等宽字体)就全错。实测一次最稳。

        参考字号用**负数**表示像素。用正数(点数)的话要经过 tk scaling 换算,
        150% 缩放的屏幕上同一套公式会得出不同结果;负数直接就是像素。
        """
        try:
            reference = tkfont.Font(
                root=self.root, family=DIGIT_FAMILY, size=-REFERENCE_PX, weight="bold"
            )
            text_ratio = reference.measure(DIGIT_SAMPLE) / REFERENCE_PX
            line_ratio = reference.metrics("linespace") / REFERENCE_PX
        except tk.TclError:
            return  # 字体不可用时保留兜底比例
        if text_ratio > 0 and line_ratio > 0:
            self._text_ratio, self._line_ratio = text_ratio, line_ratio

    # ---------------- 几何 ----------------

    def _setup_geometry(self) -> None:
        """按记住的尺寸(没有就用默认尺寸)给窗口定位,并设好初始字号。

        **这里必须设显式尺寸,它是缩放功能的保险丝。**
        实测:不给窗口设尺寸时,改子控件的字体会把顶层窗口撑大
        (126x38 -> 358x100,连触发好几个事件),于是"改字号 -> 控件变大 ->
        窗口变大 -> 再触发重排"就形成了环;而给了显式尺寸之后,改字体
        完全不会改变顶层尺寸,环从结构上就不存在。

        尺寸**不能**靠 winfo_reqwidth() 现算 —— 那时字体还没套上去,
        量到的是 Tk 默认字体下的自然尺寸,和最终长相没关系。
        """
        width, height = clamp_window_size(
            self.settings.width or DEFAULT_WINDOW_SIZE[0],
            self.settings.height or DEFAULT_WINDOW_SIZE[1],
        )
        self._place_window(width, height)
        self._last_size = (width, height)
        self._digit_px = digit_font_px(width, height, self._text_ratio, self._line_ratio)
        self._apply_fonts()

    def _place_window(self, width: int, height: int) -> None:
        """横向居中,纵向放在屏幕上 1/3 处(比正中更符合视线习惯)。"""
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - height) // WINDOW_VERTICAL_DIVISOR
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _on_configure(self, event: tk.Event) -> None:
        """窗口尺寸变化 -> 重排字号。

        三个防御,缺一不可:
        1. **挡掉子控件的事件**。绑在 root 上的 <Configure> 会连所有后代的
           Configure 一起收到(实测事件里的 event.widget 会是 '.!label'),
           不挡的话一次布局要跑十几遍。
        2. **尺寸没变就返回**。单纯移动窗口也会触发 <Configure>。
        3. **after_idle 合帧**。拖动时每帧会触发多次,合并成一次。
        """
        if event.widget is not self.root:
            return
        size = (event.width, event.height)
        if size == self._last_size:
            return
        self._last_size = size
        if self._relayout_job is None:
            self._relayout_job = self.root.after_idle(self._relayout)
        self._schedule_save()

    def _relayout(self) -> None:
        """按当前窗口尺寸重算字号。"""
        self._relayout_job = None
        width, height = self.root.winfo_width(), self.root.winfo_height()
        if width < 2 or height < 2:
            return  # 窗口还没映射出来时的 1x1,忽略
        size = digit_font_px(width, height, self._text_ratio, self._line_ratio)
        if size == self._digit_px:
            return  # 哨兵:字号没变就一个字都不写
        self._digit_px = size
        self._apply_fonts()

    def _apply_fonts(self) -> None:
        size = self._digit_px
        self.display.config(font=(DIGIT_FAMILY, -size, "bold"))
        btn_px = scaled_font_px(size, BTN_FONT_FACTOR, *BTN_FONT_RANGE)
        for button in (self.start_btn, self.pause_btn, self.reset_btn, self.settings_btn):
            button.config(font=(UI_FAMILY, -btn_px))
        self.status.config(
            font=(UI_FAMILY, -scaled_font_px(size, STATUS_FONT_FACTOR, *STATUS_FONT_RANGE), "bold")
        )

    # ---------------- 主题 ----------------

    def _apply_theme(self, theme: Theme) -> None:
        """把一套配色写到所有登记过的控件上。"""
        self.theme = theme
        for widget, role in self._themed:
            widget.configure(**ROLE_STYLES[role](theme))
        # 状态文字的颜色由状态决定,上面的统一写入碰不到它,
        # 所以这里清掉去重标记、强制按当前状态重刷一次
        self._last_state = None
        self._sync_controls(self.watch.state)
        # 透明用的键色就是当前底色,换了底色必须重设一次
        self._apply_transparency()
        self._schedule_save()

    def _untracked(self) -> list[tk.Widget]:
        """返回主窗口控件树里所有**没被登记**进主题注册表的控件。

        开发期自检用:把"忘了登记 -> 换色时露一块旧颜色"这种看不出问题的疏漏,
        变成一条会失败的断言。

        只查主窗口。设置小窗是另一个窗口、另有一份名单(_settings_themed),
        把它算进来会把这份自检变成永远失败。
        """
        tracked = {widget for widget, _role in self._themed}

        def walk(parent: tk.Widget):
            for child in parent.winfo_children():
                if child is self._settings_window:
                    continue  # 另一个窗口,不归这份名单管
                if child not in tracked:
                    yield child
                yield from walk(child)

        return list(walk(self.root))

    # ---------------- 透明 ----------------

    def _apply_transparency(self) -> None:
        """背景透明。

        做法是把"等于当前底色的像素"整片抹掉(Windows 的颜色键透明)。
        所以键色必须**正好是当前底色**,换了配色就得跟着换 —— 否则会在
        窗口上留一个按旧颜色打出来的洞。

        代价(README 里也写了,免得被当成 bug):
        - 透明区域鼠标会穿透到桌面,点在那儿等于点桌面图标
        - 不能靠拖背景移动窗口,得拖标题栏(所以标题栏刻意保留)
        - 字有抗锯齿,所以每个字形周围会留一圈很淡的灰边,纯 Tk 消不掉
        """
        transparent = bool(self.transparent_var.get())
        key = self.theme.bg if transparent else ""
        self.root.attributes("-transparentcolor", key)

        # 状态那行小字透明时必须自带底色。
        # 实测出来的:它是绿/黄色的小字,直接浮在桌面上等于没写 ——
        # 而用户桌面是什么颜色我们无从知道,没法挑一个"在哪儿都看得清"的文字色。
        # 按钮之所以在透明下依然清楚,靠的正是那块实心底,状态行照抄这个做法。
        # 不透明时底色就是窗口底色,所以这条对原来观感没有任何影响。
        self.status.configure(bg=self.theme.btn_bg if transparent else self.theme.bg)

    # ---------------- 按键 ----------------

    def _bind_keys(self) -> None:
        """绑定快捷键。

        关于空格键:Button / Checkbutton / Menubutton 的 Tk 类绑定里都自带
        ``<Key-space>``(Button 的是 ``tk::ButtonInvoke``,而且内部是
        ``after 100`` 才真正执行),而事件按「控件 -> 类 -> 顶层」的顺序派发。
        所以焦点落在按钮上时按空格,会先激活那个控件、再触发这里的全局绑定 ——
        表现为"按空格秒表不动"。

        修法是在这些控件上也绑一份控件级 <space> 并返回 "break" 截断派发。
        **名单从主题注册表推导,不写死** —— v1.0.0 是硬编码四个控件的,
        这次加了新控件(设置按钮),硬编码就会漏,老毛病直接复发。
        """
        self.root.bind("<space>", self._on_space)
        for widget, _role in self._themed:
            if isinstance(widget, SPACE_SENSITIVE):
                widget.bind("<space>", self._on_space)

        self.root.bind("<r>", lambda _e: self._act(self.watch.reset))
        self.root.bind("<R>", lambda _e: self._act(self.watch.reset))
        self.root.bind("<t>", lambda _e: self._toggle_topmost_by_key())
        self.root.bind("<T>", lambda _e: self._toggle_topmost_by_key())
        # Esc 与点右上角 × 行为一致,同样会走"计时中二次确认"
        self.root.bind("<Escape>", lambda _e: self.on_close())
        self.root.bind("<Configure>", self._on_configure)

    def _on_space(self, _event: tk.Event) -> str:
        self._act(self.watch.toggle)
        return "break"  # 截断派发,不让控件的类绑定再跑一次

    # ---------------- 动作 ----------------

    def _act(self, operation: Callable[[], bool]) -> None:
        """所有动作的统一入口:执行后立刻刷新界面,不等下一次 tick。"""
        operation()
        self._refresh()

    def _on_start(self) -> None:
        self._act(self.watch.start)

    def _on_pause(self) -> None:
        self._act(self.watch.pause)

    def _on_reset(self) -> None:
        self._act(self.watch.reset)

    def _toggle_topmost_by_key(self) -> None:
        """T 键:先翻转变量,再套用(变量是唯一事实来源)。"""
        self.topmost_var.set(not self.topmost_var.get())
        self._apply_topmost()
        self._refresh_settings_window()

    def _apply_topmost(self) -> None:
        """把复选框的状态套用到窗口上。

        注意:点击复选框时 Tk 会先改好变量再调用本方法,所以这里只读取、
        不再翻转 —— 否则会和 Tk 的翻转动作抵消,勾选框看起来没反应。
        """
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

    def _toggle_transparency(self) -> None:
        self._apply_transparency()
        self._schedule_save()
        self._refresh_settings_window()

    def _choose_color(self, color: str) -> None:
        try:
            normalized = normalize_color(color)
        except ValueError:
            return  # 取色器不该给出非法值;真给了就当没发生,别弹错误框骚扰用户
        self._apply_theme(build_theme(normalized))
        self._refresh_settings_window()

    def _choose_custom_color(self) -> None:
        _rgb, hex_color = colorchooser.askcolor(color=self.theme.bg, parent=self.root)
        if hex_color:  # 用户点了取消时返回 (None, None)
            self._choose_color(hex_color)

    # ---------------- 设置小窗 ----------------

    def open_settings(self) -> None:
        """打开设置小窗。

        刻意做成**独立窗口**而不是在主窗口里塞控件:背景透明时主窗口的
        底色和文字空白处会被整片抹掉,留在那里的复选框会变得几乎点不中
        (只有恰好压在笔画上的那一两个像素能命中)。独立窗口不受主窗口的
        透明影响(实测透明度是逐窗口属性,不会传染),所以设置项永远点得着。
        """
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.lift()
            self._settings_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self._settings_window = window

        # 设置窗里的控件**单独**登记一份,不进 self._themed。
        # 原因:_themed 那个名单会被 _bind_keys 拿去绑全局空格键,而这里是另一个
        # 窗口 —— 在设置窗里按空格应该交给控件自己处理(勾选框就该被勾上),
        # 不该去动秒表。
        self._settings_themed = []

        def track(widget: tk.Widget, role: str) -> tk.Widget:
            self._settings_themed.append((widget, role))
            return widget

        window.title("设置")
        track(window, "root")
        window.resizable(False, False)
        window.transient(self.root)
        window.bind("<Escape>", lambda _e: self.close_settings())

        track(
            tk.Label(window, text="窗口配色", font=(UI_FAMILY, -12, "bold")), "title"
        ).pack(anchor="w", padx=16, pady=(14, 6))

        swatch_row = track(tk.Frame(window), "root")
        swatch_row.pack(anchor="w", padx=16)

        # 色块本身不登记角色 —— 它的底色是"它代表的那个颜色",不该跟着主题走。
        # 它只需要一圈高亮来表示"当前选中的是哪个"。
        self._swatches: dict[str, tk.Button] = {}
        for _name, color in PRESET_COLORS:
            button = tk.Button(
                swatch_row,
                text="",
                width=4,
                height=1,
                bg=color,
                activebackground=color,
                relief="flat",
                bd=0,
                cursor="hand2",
                command=lambda c=color: self._choose_color(c),
            )
            button.pack(side="left", padx=(0, 6))
            self._swatches[color] = button

        track(
            tk.Button(
                swatch_row,
                text="自定义…",
                font=(UI_FAMILY, -11),
                relief="flat",
                bd=0,
                padx=10,
                pady=4,
                cursor="hand2",
                command=self._choose_custom_color,
            ),
            "button",
        ).pack(side="left", padx=(6, 0))

        track(tk.Frame(window, height=1), "separator").pack(fill="x", padx=16, pady=12)

        for text, variable, command in (
            ("窗口置顶", self.topmost_var, self._apply_topmost),
            ("背景透明", self.transparent_var, self._toggle_transparency),
        ):
            track(
                tk.Checkbutton(
                    window,
                    text=text,
                    variable=variable,
                    command=command,
                    font=(UI_FAMILY, -11),
                    highlightthickness=0,
                    bd=0,
                    anchor="w",
                    cursor="hand2",
                ),
                "check",
            ).pack(fill="x", padx=16, pady=6)

        track(
            tk.Label(
                window,
                text=TRANSPARENCY_CAVEATS + "\n\n" + SHORTCUT_HINT,
                font=(UI_FAMILY, -10),
                justify="left",
            ),
            "hint",
        ).pack(anchor="w", padx=16, pady=(12, 6))

        track(
            tk.Button(
                window,
                text="关闭",
                font=(UI_FAMILY, -11),
                relief="flat",
                bd=0,
                padx=20,
                pady=6,
                cursor="hand2",
                command=self.close_settings,
            ),
            "button",
        ).pack(pady=(4, 14))

        self._refresh_settings_window()
        self._center_settings_window(window)

    def _refresh_settings_window(self) -> None:
        """把当前主题和选中状态同步到设置小窗(如果开着)。

        配色变化时整个设置窗都要跟着换色 —— 否则在浅色主题下打开设置,
        窗口里还是上一套深色的底。
        """
        window = self._settings_window
        if window is None or not window.winfo_exists():
            return
        for widget, role in self._settings_themed:
            widget.configure(**ROLE_STYLES[role](self.theme))
        for color, button in self._swatches.items():
            # 选中的那个用一圈亮边标出来,不然用户不知道自己在哪套配色上。
            # 用 highlightbackground 而不是改 bd,是因为改 bd 会让色块尺寸跳动。
            selected = color == self.theme.bg
            button.configure(
                highlightthickness=3,
                highlightbackground=self.theme.fg if selected else self.theme.bg,
            )

    def _center_settings_window(self, window: tk.Toplevel) -> None:
        window.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - window.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - window.winfo_height()) // 2
        # 别把设置窗推到屏幕外
        x = max(0, min(x, self.root.winfo_screenwidth() - window.winfo_width()))
        y = max(0, min(y, self.root.winfo_screenheight() - window.winfo_height()))
        window.geometry(f"+{x}+{y}")

    def close_settings(self) -> None:
        """关掉设置小窗。

        必须先查 winfo_exists:用户完全可能直接点右上角的 × 把窗口关掉,
        那样这个控件就已经销毁了,再 destroy 一次会抛 TclError。
        """
        window = self._settings_window
        self._settings_window = None
        if window is not None and window.winfo_exists():
            window.destroy()

    # ---------------- 渲染 ----------------

    def _tick(self) -> None:
        """刷新循环:渲染一次,再把自己排到 TICK_INTERVAL_MS 之后。"""
        self._refresh()
        self._job = self.root.after(TICK_INTERVAL_MS, self._tick)

    def _refresh(self) -> None:
        """把逻辑层状态同步到界面。

        只在内容真的变化时才写控件,避免每秒十次做同样的重绘。
        """
        text = self.watch.formatted()
        if text != self._last_text:
            self._last_text = text
            self.display.config(text=text)

        state = self.watch.state
        if state is not self._last_state:
            self._last_state = state
            self._sync_controls(state)

    def _sync_controls(self, state: State) -> None:
        """按当前状态更新状态标签和三个按钮的可用性。"""
        self.status.config(text=STATUS_TEXT[state], fg=self.theme.status_color(state))
        self.start_btn.config(state=tk.DISABLED if state is State.RUNNING else tk.NORMAL)
        self.pause_btn.config(state=tk.NORMAL if state is State.RUNNING else tk.DISABLED)
        self.reset_btn.config(state=tk.DISABLED if state is State.IDLE else tk.NORMAL)

    # ---------------- 设置存盘 ----------------

    def _current_settings(self) -> Settings:
        width, height = self._last_size
        return Settings(
            bg=self.theme.bg,
            transparent=bool(self.transparent_var.get()),
            width=width or None,
            height=height or None,
        )

    def _schedule_save(self) -> None:
        """延后存盘。拖动窗口时尺寸一直在变,不该每次都写盘。"""
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(SETTINGS_SAVE_DELAY_MS, self._save_now)

    def _save_now(self) -> None:
        self._save_job = None
        save_settings(self._current_settings(), self._settings_path)

    # ---------------- 退出 ----------------

    def on_close(self) -> None:
        """关窗:正在计时就先确认一次,再撤掉定时器并销毁窗口。"""
        if self.watch.is_running() and not self._confirm_quit():
            return
        self._cancel_pending()
        save_settings(self._current_settings(), self._settings_path)
        self.root.destroy()

    def _confirm_quit(self) -> bool:
        """计时中关窗的二次确认。

        窗口置顶时先临时取消置顶,否则对话框可能被主窗口盖住(Windows 上实测会)。
        透明不会影响对话框 —— 透明度是逐窗口属性,不会传染给子窗口,实测过。
        """
        was_topmost = bool(self.topmost_var.get())
        if was_topmost:
            self.root.attributes("-topmost", False)
        try:
            return messagebox.askokcancel(
                "确认退出", "计时正在进行,确定要退出吗?", parent=self.root
            )
        finally:
            if was_topmost:
                self.root.attributes("-topmost", True)

    def _cancel_pending(self) -> None:
        """撤掉所有还没触发的定时任务。

        三个 after id 是独立的,少撤一个就会在窗口销毁后触发,
        报 "invalid command name"。
        """
        for attribute in ("_job", "_relayout_job", "_save_job"):
            job = getattr(self, attribute)
            if job is not None:
                self.root.after_cancel(job)
                setattr(self, attribute, None)
