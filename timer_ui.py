"""计时器的界面层 —— Tkinter 相关代码全部在这里。

这是"前后端"里的**前端**:只负责把计时器的状态画到窗口上,以及把鼠标、
按键转成对计时器对象的调用。它自己不做任何时间计算,读数和格式化都交给
timer_core,"东西摆在哪儿""点在这儿算不算边缘"则交给 timer_view。

v2.0.0 起整个界面是**画在一块 Canvas 上的**:圆角面板、大号数字、三颗圆角
按钮、右上角的 ×,全是用坐标算出来、再用多边形和文字画上去的,不再用
pack/grid 摆控件。这么做有三个理由:

1. 参考图里的按钮是**圆角**的,``tk.Button`` 画不出来。
2. 只剩下一个控件,也就只剩一个 ``cursor`` 属性 —— "鼠标移到边上变成双向
   箭头"就只是改一个值,不用去追指针底下压着哪个控件。
3. 谁在哪儿全由 timer_view 的纯函数算出来,能脱离显示器断言。

窗口这件事里有三个坑,都在下面标了注释:

1. **重排的反馈环** —— 靠"显式尺寸 + 整数哨兵"切断,见 _on_configure。
   现在只有一块 Canvas、尺寸也是显式设的,这个环比 v1 更难形成,但那套
   防御继续留着 —— 它是花了一整轮才调稳的。
2. **透明和"抓得住"是矛盾的** —— v1 用颜色键把整片背景抹掉,结果透明处
   鼠标直接穿透到桌面,窗口一半抓不住(用户报过"不能移动这个窗口")。
   v2 换成整窗 ``-alpha``:半透明但仍然属于窗口,鼠标点得到,于是"整块都能拖"。
3. **无边框和键盘焦点** —— 无边框窗口点一下不会自动获得键盘焦点,
   ``focus_force()`` 不能省,否则空格/R/T/Esc 全部失灵。

颜色键只剩一个用途:把面板**四个圆角**挖掉。做法是让 Canvas 自己的底色
等于 CORNER_KEY,再把圆角矩形面板画在上面 —— 于是圆角外面那点像素正好是
键色,被整片抹掉,看起来就是圆角。代价是"整套配色里不能有任何颜色等于
CORNER_KEY",这条有测试钉着。
"""

from __future__ import annotations

import tkinter as tk
from contextlib import contextmanager
from pathlib import Path
from tkinter import font as tkfont, messagebox, simpledialog
from typing import Callable, Iterator

from timer_core import Countdown, State, Stopwatch, Timer, format_hms, parse_hms
from timer_settings import (
    MODE_COUNTDOWN,
    MODE_WATCH,
    Settings,
    load as load_settings,
    save as save_settings,
)
from timer_view import (
    CORNER_KEY,
    DEFAULT_BG,
    WINDOW_ALPHA,
    WINDOW_VERTICAL_DIVISOR,
    PanelLayout,
    build_theme,
    clamp_window_position,
    clamp_window_size,
    digit_area,
    digit_font_px,
    panel_layout,
    panel_radius,
    resize_rect,
    resize_zone,
    rounded_rect_points,
    scaled_font_px,
    stroke_color,
    stroke_offsets,
)

# ---------------- 行为常量 ----------------

WINDOW_TITLE = "计时器"
TICK_INTERVAL_MS = 100          # 刷新间隔。显示精度只到整秒,100ms 足够跟手又不费 CPU
SETTINGS_SAVE_DELAY_MS = 1000   # 尺寸/位置变化后的存盘去抖:拖动中别每次都写盘
DEFAULT_WINDOW_SIZE = (440, 280)  # 没有存过尺寸时的首启尺寸

# 拖动窗口前要先挪够这么多像素,才算"在拖"而不是"在点"。
# 有这个阈值,点按钮时手稍微抖一下不会被当成拖动。
DRAG_THRESHOLD = 4

DIGIT_FAMILY = "Consolas"
UI_FAMILY = "Microsoft YaHei"
DIGIT_SAMPLE = "00:00:00"
REFERENCE_PX = 200   # 标定字体比例用的参考字号(取大一点,测出的比例更准)

# 按钮文字相对数字字号的缩放比例和上下限。
# 不让它自由缩放是因为:太小点不着看着难受,太大又会把按钮撑破。
BTN_FONT_FACTOR, BTN_FONT_RANGE = 0.26, (10, 22)
BTN_RADIUS = 8       # 按钮圆角半径(像素)。按钮高 34,取 8 看着像"圆角"而不是"胶囊"

# 圆角按钮按下/悬停的闪烁节奏。
FLASH_TIMES = 6
FLASH_INTERVAL_MS = 130

# 「按钮 → 光标」和「缩放区 → 光标」两张表。
# 这些名字是这台机器上实测**存在**的 Tk 光标名;看起来更自然的 size_nwse /
# size_nesw / size_all 反而是不存在的,写上去会在设光标那一刻抛 TclError。
RESIZE_CURSORS = {
    "n": "size_ns", "s": "size_ns",
    "e": "size_we", "w": "size_we",
    "nw": "size_nw_se", "se": "size_nw_se",
    "ne": "size_ne_sw", "sw": "size_ne_sw",
}
HAND_CURSOR = "hand2"   # 能点的地方
MOVE_CURSOR = "fleur"   # 能拖的地方

DURATION_PROMPT = (
    "输入时长,例如 05:00(5 分钟)、1:30:00(1 小时 30 分),"
    "或者直接写 90(90 秒):"
)


def primary_label(state: State) -> str:
    """第一颗按钮写什么。计时中它是"暂停",其余时候是"开始"。

    参考图里就是这个行为:按下去开始,同一个按钮变成暂停。
    """
    return "暂停" if state is State.RUNNING else "开始"


def mode_label(mode: str) -> str:
    """第三颗按钮写什么 —— 它显示的是"切过去之后是什么",不是"现在是什么"。"""
    return "切换为秒表" if mode == MODE_COUNTDOWN else "切换为倒计时"


class TimerApp:
    """把计时器渲染到 Tkinter 窗口上。"""

    def __init__(
        self,
        root: tk.Tk,
        watch: Stopwatch | None = None,
        settings_path: Path | None = None,
    ) -> None:
        self.root = root
        # 设置文件的路径可以注入 —— 冒烟测试必须指到临时目录去,
        # 否则一次测试跑下来会把用户真实的窗口大小和位置覆盖掉。
        # 必须在建控件之前读:记住的尺寸和模式要先套上去。
        self._settings_path = settings_path
        self.settings = load_settings(settings_path)
        self.mode: str = self.settings.mode
        # 依赖注入:默认自建;测试时传入用假时钟驱动的实例
        self._timers: dict[str, Timer] = {
            MODE_WATCH: watch if watch is not None else Stopwatch(),
            MODE_COUNTDOWN: Countdown(target=self.settings.target),
        }
        # v2.0.0 起配色不可选,主题是常量派生的。
        self.theme = build_theme(DEFAULT_BG)

        self._job: str | None = None            # 刷新循环的 after id
        self._relayout_job: str | None = None   # 缩放重排的 after id(和上面是两个,别混用)
        self._save_job: str | None = None       # 存盘去抖的 after id
        self._flash_job: str | None = None      # 到点闪烁的 after id
        self._last_text: str = ""               # 上次渲染的读数,用于去重
        self._last_state: State | None = None
        self._last_size: tuple[int, int] = (0, 0)
        self._digit_px: int = 0                 # 当前数字字号
        self._layout: PanelLayout | None = None  # 当前这一版布局,画和点都用它
        self._text_ratio: float = 4.4           # 标定前的兜底值,启动后会被实测值覆盖
        self._line_ratio: float = 1.17
        self._ui_font_obj: tkfont.Font | None = None
        self._ui_font_px: int = 0

        # 拖动窗口用。_drag_from 是按下时鼠标的屏幕坐标,None 表示没在拖。
        self._drag_from: tuple[int, int] | None = None
        self._window_from: tuple[int, int] = (0, 0)
        self._dragged = False
        # 拖边/角改尺寸用,和拖动窗口各走各的。
        self._resize_zone: str | None = None
        self._resize_from: tuple[int, int] | None = None
        self._resize_size: tuple[int, int] = (0, 0)
        self._resize_pos: tuple[int, int] = (0, 0)

        # 鼠标交互的瞬时状态:悬停/按在哪颗按钮上,-1 表示没有。
        self._hover: int = -1
        self._press: int = -1
        self._close_hover = False
        self._close_press = False
        self._cursor: str = MOVE_CURSOR
        # 倒计时"已经提醒过"标记,保证只响一次。
        self._alerted = False

        # 默认置顶。放不下开关(用户说了设置窗不要),所以只由 T 键切换。
        self.topmost_var = tk.BooleanVar(value=True)

        self._build_ui()
        self._calibrate_font_ratios()
        self._setup_geometry()
        self._apply_topmost()
        self._bind_keys()
        self._tick()  # 启动刷新循环(内部会先渲染一次)

    # ---------------- 当前计时器 ----------------

    @property
    def watch(self) -> Timer:
        """当前驱动界面的那个计时器。

        秒表和倒计时接口完全一样(见 timer_core.Timer),所以界面里除了
        "点数字设时长"那一处,再没有别的地方需要判断现在是哪种模式。
        """
        return self._timers[self.mode]

    def switch_mode(self) -> None:
        """在秒表和倒计时之间切换。

        两边各自留着自己的状态,所以切回来还是刚才那样(秒表停在 01:23、
        倒计时停在 04:10),不是从头开始。
        """
        new_mode = MODE_WATCH if self.mode == MODE_COUNTDOWN else MODE_COUNTDOWN
        if self.watch.is_running() and not self._confirm_switch():
            return
        # 确认框里说的是"会丢掉当前计时",所以切走之前把当前这个停掉
        self.watch.reset()
        self.mode = new_mode
        self._alerted = False
        self._sync_all()
        self._schedule_save()

    # ---------------- 搭建界面 ----------------

    def _build_ui(self) -> None:
        self.root.title(WINDOW_TITLE)
        # **无边框**。这不只是为了好看 —— 带系统边框的窗口一旦开了透明,
        # 整窗都收不到鼠标点击(最小复现验证过,跟我们的代码无关)。
        # 所以"隐去那条白色标题栏"和"界面点得动"是同一件事。
        self.root.overrideredirect(True)
        self.root.configure(bg=CORNER_KEY)

        # 整窗半透明:能看出底下的软件,但依然**属于窗口** —— 鼠标点得到。
        # 这正是"整块都能拖"能成立的原因,也是和 v1 那套颜色键方案的分水岭。
        self.root.attributes("-transparentcolor", CORNER_KEY)
        self.root.attributes("-alpha", WINDOW_ALPHA)

        # 唯一的一块 Canvas,铺满整个客户区。
        #
        # 底色**故意设成 CORNER_KEY**:圆角面板画在它上面,于是四个角剩下那点
        # 像素正好是键色,被颜色键整片抹掉 —— 圆角就是这么来的,不需要 ctypes,
        # 也不需要在每次改尺寸时重设 Windows 区域。
        #
        # width/height 压到 1 是另一半的原因:Canvas 默认会按内容**请求**尺寸,
        # 而数字是照着窗口大小反算出来的,于是它的请求尺寸能涨到好几百像素。
        # pack 分配空间时先满足请求尺寸,结果别的控件分不到地方(实测过)。
        # 现在只有一个控件、且用 fill+expand,但这条防御继续留着。
        self.canvas = tk.Canvas(
            self.root, width=1, height=1, highlightthickness=0, bd=0,
            bg=CORNER_KEY, cursor=MOVE_CURSOR,
        )
        self.canvas.pack(fill="both", expand=True)

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

    def _ui_font(self) -> tkfont.Font:
        """量按钮文字宽度用的字体对象。字号变了就重建一个。"""
        px = scaled_font_px(self._digit_px, BTN_FONT_FACTOR, *BTN_FONT_RANGE)
        if self._ui_font_obj is None or self._ui_font_px != px:
            self._ui_font_obj = tkfont.Font(root=self.root, family=UI_FAMILY, size=-px)
            self._ui_font_px = px
        return self._ui_font_obj

    def _button_labels(self) -> tuple[str, str, str]:
        return (
            primary_label(self.watch.state),
            "重置",
            mode_label(self.mode),
        )

    # ---------------- 几何 ----------------

    def _setup_geometry(self) -> None:
        """按记住的尺寸(没有就用默认尺寸)给窗口定位。

        **这里必须设显式尺寸,它是缩放功能的保险丝。**
        实测:不给窗口设尺寸时,改字体/重画内容会把顶层窗口撑大,
        于是"改字号 -> 内容变大 -> 窗口变大 -> 再触发重排"就形成了环;
        而给了显式尺寸之后,顶层尺寸只由我们自己改,环从结构上就不存在。
        """
        width, height = clamp_window_size(
            self.settings.width or DEFAULT_WINDOW_SIZE[0],
            self.settings.height or DEFAULT_WINDOW_SIZE[1],
        )
        self._place_window(width, height)
        self._last_size = (width, height)

    def _place_window(self, width: int, height: int) -> None:
        """放窗口。存过位置就用存过的,没存过就居中偏上。

        存过的位置要先过一遍 clamp_window_position:上次可能是在另一块
        显示器上拖的,现在那块屏没了。窗口是无边框、不进任务栏的,
        整个跑到屏幕外就再也点不回来 —— 这一步不能省。
        """
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if self.settings.x is not None and self.settings.y is not None:
            x, y = clamp_window_position(
                self.settings.x, self.settings.y, width, height, screen_w, screen_h
            )
        else:
            x = (screen_w - width) // 2
            y = (screen_h - height) // WINDOW_VERTICAL_DIVISOR
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _window_size(self) -> tuple[int, int]:
        return self.root.winfo_width(), self.root.winfo_height()

    def _on_configure(self, event: tk.Event) -> None:
        """窗口尺寸变化 -> 重排字号和布局。

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
        # **"尺寸没变"不足以跳过,还得确认布局真的已经算过了。**
        # 窗口映射出来的尺寸如果正好等于 _setup_geometry 写下的那个尺寸,
        # 这里就会一次都不重排 —— 界面上一个字都没有(老版本"打开时数字被
        # 盖住"就是这条路径)。加上 _layout is not None 之后,第一次真正的
        # Configure 一定能把界面画出来。
        if size == self._last_size and self._layout is not None:
            return
        self._last_size = size
        if self._relayout_job is None:
            self._relayout_job = self.root.after_idle(self._relayout)
        self._schedule_save()

    def _relayout(self) -> None:
        self._relayout_job = None
        self._relayout_now()

    def _relayout_now(self) -> None:
        """按当前窗口尺寸重算布局和字号,然后把整块画一遍。

        顺序不能乱:数字区的尺寸只由窗口高矮决定(见 timer_view.digit_area),
        所以先拿它算字号,再用字号去量按钮文字、算按钮宽度 —— 反过来就绕成
        一个圈了。
        """
        width, height = self._window_size()
        if width < 2 or height < 2:
            return  # 窗口还没映射出来时的 1x1,忽略;由 _refresh 兜底重试
        area = digit_area(width, height)
        self._digit_px = digit_font_px(area.w, area.h, self._text_ratio, self._line_ratio)
        widths = tuple(round(self._ui_font().measure(t)) for t in self._button_labels())
        self._layout = panel_layout(width, height, widths)
        self._last_size = (width, height)
        self._redraw_panel()
        self._redraw_buttons()
        self._redraw_close()
        self._redraw_digits()

    def _ensure_layout(self) -> None:
        """布局没跟上窗口尺寸就补一次。

        这是"一打开数字被盖住"那个老 bug 的兜底:窗口刚建好时尺寸还是 1x1,
        ``_relayout_now`` 会跳过,真正画出数字得靠这里在下一次 tick 时补上。
        只留 after_idle 一条路是不够的 —— 那一次 <Configure> 有可能就是
        在 1x1 的时候来的。
        """
        if self._layout is None or (self._layout.panel.w, self._layout.panel.h) != self._window_size():
            self._relayout_now()

    # ---------------- 画 ----------------

    def _redraw_panel(self) -> None:
        """画圆角面板。它盖住整块 Canvas,只把四个角留给键色。"""
        self.canvas.delete("panel")
        width, height = self._layout.panel.w, self._layout.panel.h
        self.canvas.create_polygon(
            *rounded_rect_points(0, 0, width, height, panel_radius(height)),
            fill=self.theme.bg, outline="", tags="panel",
        )

    def _redraw_digits(self) -> None:
        """画大号数字。

        用 Canvas 画而不是 Label,是为了给字描一圈边:面板是半透明的,
        数字底下多少会透出一点用户的桌面(14%),加上一圈暗描边之后,
        不管底下是浅色还是深色的软件都读得清。整块删掉重来 ——
        一共十几个图元,比逐个挪坐标简单也更不容易错。
        """
        self.canvas.delete("digit")
        if self._layout is None:
            return
        centre_x, centre_y = self._layout.digits.centre
        text = self.watch.formatted()
        font = (DIGIT_FAMILY, -self._digit_px, "bold")
        stroke = stroke_color(self.theme.fg, self.theme.bg)
        for dx, dy in stroke_offsets():
            self.canvas.create_text(
                centre_x + dx, centre_y + dy, text=text, font=font,
                fill=stroke, tags="digit",
            )
        # 正色的字最后画,盖在描边上面
        self.canvas.create_text(
            centre_x, centre_y, text=text, font=font,
            fill=self.theme.fg, tags="digit",
        )

    def _redraw_buttons(self) -> None:
        """画三颗圆角按钮。悬停、按下都只是换个填充色重画一遍。"""
        self.canvas.delete("button")
        if self._layout is None:
            return
        labels = self._button_labels()
        font = (UI_FAMILY, -self._ui_font_px)
        for index, rect in enumerate(self._layout.buttons):
            if index == self._press:
                fill = self.theme.btn_pressed
            elif index == self._hover:
                fill = self.theme.btn_active
            else:
                fill = self.theme.btn_bg
            self.canvas.create_polygon(
                *rounded_rect_points(
                    rect.x, rect.y, rect.right, rect.bottom,
                    min(BTN_RADIUS, rect.h // 2),
                ),
                fill=fill, outline="", tags="button",
            )
            self.canvas.create_text(
                *rect.centre, text=labels[index], font=font,
                fill=self.theme.btn_fg, tags="button",
            )

    def _redraw_close(self) -> None:
        """画右上角那个 ×。无边框就没有系统关闭按钮了,得自己给一个。"""
        self.canvas.delete("close")
        if self._layout is None:
            return
        rect = self._layout.close
        inset = rect.w * 0.3
        width = max(1, round(rect.w * 0.1))
        colour = self.theme.btn_fg if self._close_hover else self.theme.hint_fg
        for x0, y0, x1, y1 in (
            (rect.x + inset, rect.y + inset, rect.right - inset, rect.bottom - inset),
            (rect.right - inset, rect.y + inset, rect.x + inset, rect.bottom - inset),
        ):
            self.canvas.create_line(
                x0, y0, x1, y1, fill=colour, width=width,
                capstyle="round", tags="close",
            )

    # ---------------- 鼠标:命中判定 ----------------

    def _button_at(self, x: float, y: float) -> int | None:
        """点 (x, y) 压在哪颗按钮上。返回下标,都不在就返回 None。"""
        if self._layout is None:
            return None
        for index, rect in enumerate(self._layout.buttons):
            if rect.contains(x, y):
                return index
        return None

    def _zone_at(self, x: float, y: float) -> str | None:
        """点 (x, y) 落在哪个缩放区,不在边缘就返回 None。"""
        if self._layout is None:
            return None
        return resize_zone(x, y, self._layout.panel.w, self._layout.panel.h)

    def _cursor_for(self, x: float, y: float) -> str:
        if self._button_at(x, y) is not None or self._close_hover_at(x, y):
            return HAND_CURSOR
        zone = self._zone_at(x, y)
        if zone is not None:
            return RESIZE_CURSORS[zone]
        if self.mode == MODE_COUNTDOWN and self._digits_hit(x, y):
            # 倒计时模式下点数字能设时长,所以给手型而不是"可移动"
            return HAND_CURSOR
        return MOVE_CURSOR

    def _close_hover_at(self, x: float, y: float) -> bool:
        return self._layout is not None and self._layout.close.contains(x, y)

    def _digits_hit(self, x: float, y: float) -> bool:
        return self._layout is not None and self._layout.digits.contains(x, y)

    # ---------------- 鼠标:按下 / 拖动 / 松开 ----------------

    def _on_press(self, event: tk.Event) -> None:
        """按下左键:先判断落在哪,再决定这一次是"点""拖"还是"拉"。

        **控件优先于边缘带**这条规则是关键:右上角的 × 和底部的按钮行都压在
        边缘带上,靠它才不会被缩放抢走。同理,布局时也让 × 往内挪了一点、
        按钮行留出了底边距(见 timer_view.panel_layout),双保险。

        **抢焦点这一步不能省**。无边框窗口在 Windows 上是 WS_POPUP,
        实测点它不会自动获得焦点,而没有焦点就收不到键盘事件 ——
        空格开始/暂停、R 重置、T 置顶、Esc 退出会全部失灵。
        """
        self.root.focus_force()
        self._dragged = False

        index = self._button_at(event.x, event.y)
        if index is not None:
            self._press = index
            self._redraw_buttons()
            self._begin_move(event)   # 从按钮上按下去照样能拖窗口,按钮不是死区
            return

        if self._close_hover_at(event.x, event.y):
            self._close_press = True
            self._begin_move(event)
            return

        zone = self._zone_at(event.x, event.y)
        if zone is not None:
            self._resize_zone = zone
            self._resize_from = (event.x_root, event.y_root)
            self._resize_size = self._layout.panel.w, self._layout.panel.h
            self._resize_pos = (self.root.winfo_x(), self.root.winfo_y())
            return

        self._begin_move(event)

    def _begin_move(self, event: tk.Event) -> None:
        self._drag_from = (event.x_root, event.y_root)
        self._window_from = (self.root.winfo_x(), self.root.winfo_y())

    def _on_motion(self, event: tk.Event) -> None:
        if self._resize_zone is not None:
            self._do_resize(event)
            return
        if self._drag_from is not None:
            self._do_move(event)
            return
        self._update_pointer(event.x, event.y)

    def _do_resize(self, event: tk.Event) -> None:
        """拉边/拉角改尺寸。

        拖动的基准点是**按下那一刻**的鼠标位置和窗口大小,不是上一帧的 ——
        累加式写法会把每一帧的取整误差攒起来,慢慢拖十秒窗口就跑偏了。
        """
        dx = event.x_root - self._resize_from[0]
        dy = event.y_root - self._resize_from[1]
        self._dragged = True
        x, y, width, height = resize_rect(
            self._resize_zone, self._resize_size, self._resize_pos, dx, dy
        )
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _do_move(self, event: tk.Event) -> None:
        """拖窗口。挪够 DRAG_THRESHOLD 才真的开始动,免得点按钮时手抖一下把窗口挪歪。"""
        dx = event.x_root - self._drag_from[0]
        dy = event.y_root - self._drag_from[1]
        if not self._dragged and abs(dx) < DRAG_THRESHOLD and abs(dy) < DRAG_THRESHOLD:
            return
        self._dragged = True
        self.root.geometry(f"+{self._window_from[0] + dx}+{self._window_from[1] + dy}")

    def _on_release(self, event: tk.Event) -> None:
        """松开左键:没拖过才真的算"点了一下"。"""
        index, self._press = self._press, -1
        close_pressed, self._close_press = self._close_press, False
        resized = self._resize_zone is not None
        dragged = self._dragged
        self._redraw_buttons()

        self._drag_from = None
        self._resize_zone = None
        self._resize_from = None
        self._dragged = False

        if dragged or resized:
            # 窗口动过 -> 位置/尺寸记一下。拖窗口和拉边都要,别只写一个分支。
            self._schedule_save()
            return

        if index is not None and self._button_at(event.x, event.y) == index:
            self._on_button(index)
            return
        if close_pressed and self._close_hover_at(event.x, event.y):
            self.on_close()
            return
        if self.mode == MODE_COUNTDOWN and self._digits_hit(event.x, event.y):
            self.prompt_duration()

    def _update_pointer(self, x: float, y: float) -> None:
        """鼠标没按着的时候:更新悬停态和光标形状。"""
        hover = self._button_at(x, y)
        if hover != self._hover:
            self._hover = hover
            self._redraw_buttons()
        close_hover = self._close_hover_at(x, y)
        if close_hover != self._close_hover:
            self._close_hover = close_hover
            self._redraw_close()
        cursor = self._cursor_for(x, y)
        if cursor != self._cursor:
            self._cursor = cursor
            self.canvas.configure(cursor=cursor)

    def _on_leave(self, _event: tk.Event) -> None:
        """鼠标离开窗口:清掉悬停态,否则按钮会一直亮着。"""
        if self._hover != -1:
            self._hover = -1
            self._redraw_buttons()
        if self._close_hover:
            self._close_hover = False
            self._redraw_close()

    # ---------------- 动作 ----------------

    def _on_button(self, index: int) -> None:
        if index == 0:
            self._act(self.watch.toggle)
        elif index == 1:
            self._act(self.watch.reset)
        else:
            self.switch_mode()

    def _act(self, operation: Callable[[], bool]) -> None:
        """所有动作的统一入口:执行后立刻刷新界面,不等下一次 tick。"""
        operation()
        self._sync_all()

    def _sync_all(self) -> None:
        """清掉去重标记,强制按当前状态整块重画一遍。

        切模式、改时长之后必须走这条路 —— 读数可能看着没变(秒表切过去
        正好是 00:00:00),但按钮上的字和可点的区域全变了。
        """
        self._last_text = ""
        self._last_state = None
        self._refresh()

    def _on_space(self, _event: tk.Event) -> None:
        """空格开始/暂停。

        v1 在这里踩过一个坑:``tk.Button`` 的 Tk 类绑定里自带 ``<Key-space>``
        (而且内部是 ``after 100`` 才执行),事件又是「控件 -> 类 -> 顶层」派发,
        于是焦点在按钮上时按空格会先激活按钮、再触发全局绑定 ——
        表现是"按空格秒表不动"。v1 的修法是给每个按钮补一份 ``break`` 截断派发。
        v2 里主窗口**一个 tk.Button 都不剩**,那个类绑定根本不存在,
        这类冲突从结构上消失了 —— 所以这里不再需要任何补丁。
        """
        self._act(self.watch.toggle)

    def _toggle_topmost_by_key(self) -> None:
        """T 键切置顶。界面上放不下这个开关(用户不要设置窗),所以只有键盘入口。"""
        self.topmost_var.set(not self.topmost_var.get())
        self._apply_topmost()

    def _apply_topmost(self) -> None:
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

    # ---------------- 绑定 ----------------

    def _bind_keys(self) -> None:
        """绑键盘和鼠标。

        鼠标**绑在 Canvas 上**而不是顶层:Canvas 铺满整个客户区,所有点击都会
        落在它身上,于是拿到的 event.x/y 天然就是窗口内坐标 —— 直接喂给
        timer_view 的那套纯函数判定就行,不用换算。

        键盘只能绑在顶层(焦点在窗口上,不在某个控件上)。
        """
        self.root.bind("<space>", self._on_space)
        self.root.bind("<r>", lambda _e: self._act(self.watch.reset))
        self.root.bind("<R>", lambda _e: self._act(self.watch.reset))
        self.root.bind("<t>", lambda _e: self._toggle_topmost_by_key())
        self.root.bind("<T>", lambda _e: self._toggle_topmost_by_key())
        # Esc 与点右上角 × 行为一致,同样会走"计时中二次确认"
        self.root.bind("<Escape>", lambda _e: self.on_close())
        self.root.bind("<Configure>", self._on_configure)

        self.canvas.bind("<Button-1>", self._on_press, add="+")
        self.canvas.bind("<B1-Motion>", self._on_motion, add="+")
        self.canvas.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.canvas.bind("<Motion>", self._on_motion, add="+")
        self.canvas.bind("<Leave>", self._on_leave, add="+")

    # ---------------- 倒计时 ----------------

    def prompt_duration(self) -> None:
        """弹个小输入框问倒计时要设多久。

        图里没有输入框,所以入口就是数字本身 —— 点一下弹框。输入不合法时
        **重问一次并说清原因**,不静默吞掉:静默吞掉的话用户会以为程序坏了。
        """
        timer = self._timers[MODE_COUNTDOWN]
        if timer.is_running():
            self._info("计时中", "先暂停或重置,再改时长。")
            return

        while True:
            text = self._ask(
                "设定时长", DURATION_PROMPT, initial=format_hms(timer.target())
            )
            if text is None:
                return  # 用户点了取消
            try:
                seconds = parse_hms(text)
            except ValueError as error:
                self._error("这个时长不行", str(error))
                continue
            timer.set_target(seconds)
            self._sync_all()
            self._schedule_save()
            return

    def _check_finished(self) -> None:
        """倒计时跑完时提醒一次。

        "只提醒一次"靠 ``_alerted`` 这个标记:它成立的条件是"已经跑完",
        而重新开始(或重置)之后 ``is_finished()`` 立刻变回假,标记也就跟着清了。
        """
        timer = self._timers[MODE_COUNTDOWN]
        if self.mode != MODE_COUNTDOWN or not timer.is_finished():
            self._alerted = False
            return
        if self._alerted:
            return
        self._alerted = True
        self._alert_finished()

    def _alert_finished(self) -> None:
        self.root.bell()
        self._flash(FLASH_TIMES)
        self._info("时间到", "倒计时结束了。")

    def _flash(self, remaining: int, bright: bool = False) -> None:
        """面板闪几下。纯粹是视觉提醒 —— 用户可能没开音箱,铃声听不见。"""
        if remaining <= 0:
            self._flash_job = None
            self.canvas.itemconfigure("panel", fill=self.theme.bg)
            return
        self.canvas.itemconfigure(
            "panel", fill=self.theme.btn_active if bright else self.theme.bg
        )
        self._flash_job = self.root.after(
            FLASH_INTERVAL_MS, self._flash, remaining - 1, not bright
        )

    # ---------------- 对话框 ----------------
    # 主窗口默认置顶,而 Windows 上对话框会被置顶的父窗口盖住(实测过),
    # 所以弹框之前先临时取消置顶,弹完再恢复。

    @contextmanager
    def _untopmost(self) -> Iterator[None]:
        was_topmost = bool(self.topmost_var.get())
        if was_topmost:
            self.root.attributes("-topmost", False)
        try:
            yield
        finally:
            if was_topmost:
                self.root.attributes("-topmost", True)

    def _info(self, title: str, message: str) -> None:
        with self._untopmost():
            messagebox.showinfo(title, message, parent=self.root)

    def _error(self, title: str, message: str) -> None:
        with self._untopmost():
            messagebox.showerror(title, message, parent=self.root)

    def _ask(self, title: str, prompt: str, initial: str = "") -> str | None:
        with self._untopmost():
            return simpledialog.askstring(
                title, prompt, initialvalue=initial, parent=self.root
            )

    def _confirm(self, title: str, message: str) -> bool:
        with self._untopmost():
            return messagebox.askokcancel(title, message, parent=self.root)

    # ---------------- 渲染 ----------------

    def _tick(self) -> None:
        """刷新循环:渲染一次,再把自己排到 TICK_INTERVAL_MS 之后。"""
        self._refresh()
        self._job = self.root.after(TICK_INTERVAL_MS, self._tick)

    def _refresh(self) -> None:
        """把逻辑层状态同步到界面。

        只在内容真的变化时才重画,避免每秒十次做同样的绘制。
        """
        self._ensure_layout()

        text = self.watch.formatted()
        if text != self._last_text:
            self._last_text = text
            self._redraw_digits()

        state = self.watch.state
        if state is not self._last_state:
            self._last_state = state
            self._redraw_buttons()

        self._check_finished()

    # ---------------- 设置存盘 ----------------

    def _current_settings(self) -> Settings:
        width, height = self._last_size
        return Settings(
            mode=self.mode,
            target=self._timers[MODE_COUNTDOWN].target(),
            width=width or None,
            height=height or None,
            x=self.root.winfo_x(),
            y=self.root.winfo_y(),
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
        return self._confirm("确认退出", "计时正在进行,确定要退出吗?")

    def _confirm_switch(self) -> bool:
        return self._confirm(
            "切换模式", "计时正在进行,切换模式会丢掉当前的计时。确定要切换吗?"
        )

    def _cancel_pending(self) -> None:
        """撤掉所有还没触发的定时任务。

        四个 after id 是独立的,少撤一个就会在窗口销毁后触发,
        报 "invalid command name"。
        """
        for attribute in ("_job", "_relayout_job", "_save_job", "_flash_job"):
            job = getattr(self, attribute)
            if job is not None:
                self.root.after_cancel(job)
                setattr(self, attribute, None)

    # ---------------- 给测试用的只读视图 ----------------

    def layout(self) -> PanelLayout | None:
        """当前这一版布局。测试用它断言"按钮在哪儿""点在按钮上派发给了谁"。"""
        return self._layout
