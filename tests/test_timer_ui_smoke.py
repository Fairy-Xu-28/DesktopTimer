"""timer_ui 的冒烟测试 —— 真的开一个窗口,但只断言"结构",不看长相。

界面层有一大类错误是**光看代码看不出来**的:绑定漏了一个、命中判定算反了、
某个尺寸下部件叠在一起。它们都不会让程序崩,只会让功能悄悄失效 ——
所以必须变成会失败的测试。

v2.0.0 起整个界面画在**一块 Canvas** 上,于是"点在哪儿"不再由 Tk 的控件树
决定,而是由 timer_view 的纯函数算出来。这个文件负责的是**接线**:
算出来的矩形有没有真的绑到鼠标事件上、按下去到底走了哪条路。

**测试用的设置文件指向临时目录**,绝不碰 %APPDATA% 里用户真实的那份。

跑测试时窗口会真的闪一下,这是正常的。没有显示器时会自动跳过。

运行(在项目根目录):
    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from timer_core import Countdown, Stopwatch
from timer_settings import MODE_COUNTDOWN, MODE_WATCH, Settings, load as load_settings, save as save_settings
from timer_view import (
    CORNER_KEY,
    MIN_WINDOW_H,
    MIN_WINDOW_W,
    WINDOW_ALPHA,
    resize_zone,
)

try:
    import tkinter as tk

    import timer_ui
    from timer_ui import TimerApp
except ImportError:  # pragma: no cover - Tk 缺失的 Python 才走这里
    tk = None  # type: ignore[assignment]


class _FakeEvent:
    """一个只有 x / y / x_root / y_root 的假事件。

    鼠标的处理函数只读这四个坐标。直接调处理器而不是真去动鼠标,是为了让
    断言确定 —— 真实鼠标那条路另有 _shots 系列的实测验证。
    """

    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y
        # 窗口内坐标 + 窗口左上角 = 屏幕坐标。测试里窗口位置是随便定的,
        # 所以这一层换算必须有,否则"拖了多少像素"会全错。
        self.x_root = x
        self.y_root = y

    def moved_by(self, dx: float, dy: float, origin: tuple[float, float]) -> "_FakeEvent":
        """从 origin(按下时的屏幕坐标)挪 (dx, dy) 之后的事件。"""
        event = _FakeEvent(self.x + dx, self.y + dy)
        event.x_root = origin[0] + (self.x - origin[2]) + dx
        event.y_root = origin[1] + (self.y - origin[3]) + dy
        return event


@unittest.skipIf(tk is None, "没有 tkinter")
class TimerAppSmokeTest(unittest.TestCase):
    """每个用例开一个真窗口,用完立刻销毁。"""

    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("没有可用显示器(无头环境)")

        def cleanup_root() -> None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass  # 用例自己已经销毁过了

        self.addCleanup(cleanup_root)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "settings.json"

        self.watch = Stopwatch()
        self.app = TimerApp(self.root, watch=self.watch, settings_path=self.path)
        self.addCleanup(self._stop_timers, self.app)
        self.root.update()

        # 默认把四个对话框全换成"敢弹就报错"的替身。
        # **不换的话用例会挂住而不是失败** —— 它们都是模态框,弹出来之后 Tk
        # 进嵌套事件循环等人去点按钮,自动化跑的时候就是无限等,CI 上表现为卡死。
        # 需要弹框的用例自己再覆盖一次(它的 cleanup 后注册、先执行,
        # 还原成这里的替身,层次正好对)。
        self._stub_dialogs()

    def _stub_dialogs(self, *, info=None, error=None, confirm=None, ask=None) -> None:
        """换掉 interface 层用到的四个对话框。

        只传想要的那个,其余的仍然保持"敢弹就报错"。
        """

        def refuse(name: str):
            def _refuse(*_args, **_kwargs):
                raise AssertionError(f"这个用例不该弹「{name}」—— 要么补上替身,要么是逻辑跑偏了")

            return _refuse

        for module, attribute, replacement in (
            (timer_ui.messagebox, "showinfo", info or refuse("信息框")),
            (timer_ui.messagebox, "showerror", error or refuse("错误框")),
            (timer_ui.messagebox, "askokcancel", confirm or refuse("确认框")),
            (timer_ui.simpledialog, "askstring", ask or refuse("输入框")),
        ):
            original = getattr(module, attribute)
            setattr(module, attribute, replacement)
            self.addCleanup(setattr, module, attribute, original)

    @staticmethod
    def _stop_timers(app) -> None:
        """销毁窗口前先撤掉待触发的 after。

        **destroy() 不会取消已排队的 after**,这是实测出来的:定时器照样
        会在稍后触发,那时回调已经被删掉,于是往 stderr 打一串
        ``invalid command name "..."``。程序里由 on_close 调用 _cancel_pending
        兜住了,测试里得自己来。

        注册顺序上这个 cleanup 排在 destroy 之后注册,会**先于**它执行
        (addCleanup 是后进先出)。
        """
        try:
            app._cancel_pending()
            # 再跑一遍 idle 队列:Tk 自己也可能排了 C 层的 idle 回调
            # (比如收到 WM_THEMECHANGED 后排下的 ttk::ThemeChanged)。
            # after info 看不见这些,但它们跟 after 一样不随 destroy 消失,
            # 等窗口没了再跑就会往 stderr 打 "application has been destroyed"。
            # 在这儿先消化掉,窗口还活着,回调正常执行。
            app.root.update_idletasks()
        except tk.TclError:
            pass  # 窗口已经销毁了

    # ---------------- 小工具 ----------------

    def _rect(self) -> tuple[int, int, int, int]:
        """窗口的 (x, y, w, h)。"""
        return (
            self.root.winfo_x(), self.root.winfo_y(),
            self.root.winfo_width(), self.root.winfo_height(),
        )

    def _layout(self):
        self.assertIsNotNone(self.app.layout(), "布局还没算出来")
        return self.app.layout()

    def _press(self, x: float, y: float) -> tuple[float, float]:
        """在窗口内 (x, y) 按下,返回按下时的屏幕坐标。"""
        origin = (self.root.winfo_rootx() + x, self.root.winfo_rooty() + y)
        event = _FakeEvent(x, y)
        event.x_root, event.y_root = origin
        self.app._on_press(event)
        return origin

    def _move_to(self, origin: tuple[float, float], dx: float, dy: float) -> None:
        event = _FakeEvent(origin[0] + dx, origin[1] + dy)
        event.x_root, event.y_root = origin[0] + dx, origin[1] + dy
        self.app._on_motion(event)

    def _release(self, x: float, y: float) -> None:
        event = _FakeEvent(x, y)
        event.x_root = self.root.winfo_rootx() + x
        event.y_root = self.root.winfo_rooty() + y
        self.app._on_release(event)
        try:
            self.root.update()
        except tk.TclError:
            pass  # 用例点的正是 ×,窗口已经被销毁了

    def _drag(self, start: tuple[float, float], dx: float, dy: float) -> tuple[int, int, int, int]:
        """从 start 按下,横竖各拖 (dx, dy),松开。返回拖完之后的窗口矩形。"""
        origin = self._press(*start)
        self._move_to(origin, dx, dy)
        self._release(start[0] + dx, start[1] + dy)
        return self._rect()

    def _click(self, x: float, y: float) -> None:
        """在窗口内 (x, y) 处完整地按一下再松开(中间没挪动)。"""
        self._press(x, y)
        self._release(x, y)

    def _digit_bbox(self) -> tuple[int, int, int, int]:
        box = self.app.canvas.bbox("digit")
        self.assertIsNotNone(box, "画布上一个数字都没有")
        return box

    # ---------------- 窗口本身 ----------------

    def test_the_window_is_borderless(self) -> None:
        """主窗口必须是无边框的。

        这不只是长相问题 —— 带系统边框的窗口一开透明就整窗收不到点击
        (最小复现验证过),无边框是"界面点得动"的前提。所以这条要钉住。
        """
        self.assertTrue(self.root.overrideredirect())

    def test_the_window_is_translucent(self) -> None:
        """整窗半透明 —— 用户要的"深灰但看得出底下是什么软件"。

        而且用的是 **alpha** 而不是把背景整片抹掉:透明处依然属于窗口,
        鼠标点得到,这正是"整块都能拖"能成立的原因(老方案是颜色键,
        透明处会穿透到桌面,于是窗口一半抓不住)。
        """
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), WINDOW_ALPHA)

    def test_only_the_four_corners_are_keyed_out(self) -> None:
        """颜色键只剩"挖掉圆角"这一个用途,而且键色绝不能等于面板底色。

        等于的话整块面板都会被抹掉 —— 那就不只是圆角了,整个窗口都会消失,
        连点都点不到。
        """
        self.assertEqual(str(self.root.attributes("-transparentcolor")).lower(), CORNER_KEY)
        self.assertEqual(str(self.app.canvas.cget("bg")).lower(), CORNER_KEY)
        self.assertNotEqual(self.app.theme.bg, CORNER_KEY)

    def test_the_canvas_is_the_only_widget(self) -> None:
        """窗口里只有一块 Canvas。

        这条是"整块都能拖"的结构性保证:没有第二个控件来抢鼠标事件,
        也就不会出现"这块地方按下去没反应"。曾经的状态条、齿轮、缩放手柄
        都会各自吃掉一块点击区域。
        """
        self.assertEqual(self.root.winfo_children(), [self.app.canvas])

    # ---------------- 布局 ----------------

    def test_the_panel_fills_the_whole_window(self) -> None:
        self.assertEqual(
            (self._layout().panel.w, self._layout().panel.h), self._rect()[2:]
        )

    def test_digits_always_fit_inside_the_panel(self) -> None:
        """任何尺寸下,整串数字都得完整落在面板里。

        回归的是"一打开数字就被盖住"那个 bug:构造期间窗口还是 1x1,这时算出来的
        数字中心是 (1,1),整串只剩右下角一点点露在画布边缘。原来只有"窗口尺寸
        变了"才会重排,所以窗口一开就开在记住的尺寸上时,这个错位永远没人纠正。
        """
        for size in ((MIN_WINDOW_W, MIN_WINDOW_H), (440, 280), (519, 324), (900, 600)):
            with self.subTest(size=size):
                self.root.geometry(f"{size[0]}x{size[1]}")
                self.root.update()
                self.app._relayout()
                self.app._refresh()
                x1, y1, x2, y2 = self._digit_bbox()
                panel = self._layout().panel
                self.assertGreaterEqual(x1, panel.x, f"数字左边出了面板 {panel.x - x1}px")
                self.assertGreaterEqual(y1, panel.y, f"数字上边出了面板 {panel.y - y1}px")
                self.assertLessEqual(x2, panel.right, f"数字右边出了面板 {x2 - panel.right}px")
                self.assertLessEqual(y2, panel.bottom, f"数字下边出了面板 {y2 - panel.bottom}px")

    def test_digits_never_overlap_the_button_row(self) -> None:
        """数字和按钮行不能叠在一起 —— 叠上了按钮就点不着,数字也读不出来。"""
        for size in ((MIN_WINDOW_W, MIN_WINDOW_H), (440, 280), (900, 600)):
            with self.subTest(size=size):
                self.root.geometry(f"{size[0]}x{size[1]}")
                self.root.update()
                self.app._relayout()
                self.assertLess(
                    self._digit_bbox()[3], self._layout().buttons[0].y,
                    "数字下沿压到了按钮行",
                )

    def test_every_button_is_inside_the_panel(self) -> None:
        for width, height in ((MIN_WINDOW_W, MIN_WINDOW_H), (440, 280), (1100, 700)):
            with self.subTest(size=(width, height)):
                self.root.geometry(f"{width}x{height}")
                self.root.update()
                self.app._relayout()
                panel = self._layout().panel
                for button in self._layout().buttons:
                    self.assertGreaterEqual(button.x, panel.x)
                    self.assertGreaterEqual(button.y, panel.y)
                    self.assertLessEqual(button.right, panel.right)
                    self.assertLessEqual(button.bottom, panel.bottom)

    def test_opening_at_a_saved_size_still_places_the_digits_correctly(self) -> None:
        """窗口"开在记住的尺寸上"时,数字照样得摆正。

        这是原 bug 唯一的触发条件,而且很窄:窗口映射出来的尺寸如果**正好等于**
        _setup_geometry 写进 _last_size 的那个尺寸,<Configure> 的哨兵就会认为
        "尺寸没变"直接返回,整场都不会有人重排。所以这条必须拿一份**存了尺寸**的
        设置真的重开一次窗口 —— 用 setUp 里那个默认尺寸的 app 是复现不出来的。
        """
        save_settings(
            Settings(mode=MODE_WATCH, width=519, height=324, x=60, y=60), self.path
        )

        root = tk.Tk()
        app = TimerApp(root, watch=Stopwatch(), settings_path=self.path)
        try:
            for _ in range(4):
                root.update()
            self.assertEqual((root.winfo_width(), root.winfo_height()), (519, 324))
            # 平时这活儿是 100ms 的刷新循环在干,测试里直接叫它跑一趟
            app._refresh()
            self.assertIsNotNone(app.layout(), "开在记住的尺寸上时布局没算出来")
            box = app.canvas.bbox("digit")
            self.assertIsNotNone(box, "开在记住的尺寸上时数字没画出来")
            panel = app.layout().panel
            self.assertGreaterEqual(box[0], panel.x)
            self.assertGreaterEqual(box[1], panel.y)
            self.assertLessEqual(box[2], panel.right)
            self.assertLessEqual(box[3], panel.bottom)
        finally:
            app._cancel_pending()
            try:
                root.update_idletasks()
                root.destroy()
            except tk.TclError:
                pass

    def test_growing_the_window_grows_the_digits(self) -> None:
        before = self.app._digit_px
        self.root.geometry("900x600")
        self.root.update()
        self.app._relayout()
        self.assertGreater(self.app._digit_px, before)

    def test_shrinking_the_window_shrinks_the_digits(self) -> None:
        self.root.geometry("900x600")
        self.root.update()
        self.app._relayout()
        big = self.app._digit_px
        self.root.geometry(f"{MIN_WINDOW_W}x{MIN_WINDOW_H}")
        self.root.update()
        self.app._relayout()
        self.assertLess(self.app._digit_px, big)

    # ---------------- 四边缩放 ----------------

    def test_the_edges_are_not_covered_by_the_buttons_or_the_close_box(self) -> None:
        """× 和按钮行都不许压在缩放带上。

        压在带上的话"想点按钮"会变成"想拉窗口",而这两处又小又靠边,
        是最容易撞上的地方。
        """
        for width, height in ((MIN_WINDOW_W, MIN_WINDOW_H), (440, 280), (900, 600)):
            with self.subTest(size=(width, height)):
                self.root.geometry(f"{width}x{height}")
                self.root.update()
                self.app._relayout()
                layout = self._layout()
                for x, y in (
                    (layout.close.x, layout.close.y),
                    (layout.close.right - 1, layout.close.bottom - 1),
                ):
                    self.assertIsNone(resize_zone(x, y, width, height), f"× 压在缩放带上 {x},{y}")
                for button in layout.buttons:
                    for y in (button.y, button.bottom - 1):
                        self.assertIsNone(
                            resize_zone(button.centre[0], y, width, height),
                            f"按钮压在缩放带上 {button}",
                        )

    def test_dragging_the_right_edge_only_changes_the_width(self) -> None:
        before = self._rect()
        after = self._drag((before[2] - 1, before[3] // 2), 60, 0)
        self.assertEqual(after[:2], before[:2], "拖右边不该让窗口挪位置")
        self.assertEqual(after[3], before[3])
        self.assertEqual(after[2], before[2] + 60)

    def test_dragging_the_left_edge_keeps_the_right_edge_still(self) -> None:
        """拖左边时 x 和宽**都要**变。

        只改宽不改 x 的症状是"拖左边,窗口整个往右跑" —— 看起来像窗口在躲鼠标。
        """
        before = self._rect()
        after = self._drag((0, before[3] // 2), -50, 0)
        self.assertEqual(after[1], before[1])
        self.assertEqual(after[3], before[3])
        self.assertEqual(after[0] + after[2], before[0] + before[2], "右边缘动了")
        self.assertEqual(after[0], before[0] - 50)

    def test_dragging_the_top_edge_keeps_the_bottom_still(self) -> None:
        before = self._rect()
        after = self._drag((before[2] // 2, 0), 0, -40)
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[2], before[2])
        self.assertEqual(after[1] + after[3], before[1] + before[3], "下边缘动了")

    def test_dragging_a_corner_changes_both_directions(self) -> None:
        before = self._rect()
        after = self._drag((0, 0), -30, -20)
        self.assertEqual(after[0], before[0] - 30)
        self.assertEqual(after[1], before[1] - 20)
        self.assertEqual(after[2], before[2] + 30)
        self.assertEqual(after[3], before[3] + 20)

    def test_resizing_stops_at_the_minimum_size_without_drifting(self) -> None:
        """无边框下没有系统缩放边框,尺寸下限只剩 resize_rect 这一道闸。

        而且被钳住之后 x 不能继续漂:拖左边一直拖到最小尺寸,右边缘必须
        停在原地(不然窗口会一路往右滑走)。
        """
        before = self._rect()
        after = self._drag((0, before[3] // 2), 5000, 0)
        self.assertEqual(after[2], MIN_WINDOW_W)
        self.assertEqual(after[3], before[3])
        self.assertEqual(after[0] + after[2], before[0] + before[2])

    def test_dragging_the_blank_area_moves_the_window(self) -> None:
        """面板空白处(数字上方那一带)按下去必须是拖窗口。

        老方案用颜色键做透明,透明处的鼠标会穿透到桌面,这块地方根本抓不住 ——
        用户报的"不能移动这个窗口"就是这个。换成整窗 alpha 之后,
        窗口上每一处都能拖,这条把它钉住。
        """
        before = self._rect()
        after = self._drag((30, 40), 70, 50)
        self.assertEqual((after[0], after[1]), (before[0] + 70, before[1] + 50))
        self.assertEqual((after[2], after[3]), (before[2], before[3]))

    def test_dragging_from_on_top_of_the_digits_moves_the_window(self) -> None:
        before = self._rect()
        centre = self._layout().digits.centre
        after = self._drag(centre, 40, 30)
        self.assertEqual((after[0], after[1]), (before[0] + 40, before[1] + 30))

    def test_dragging_from_a_button_moves_the_window_instead_of_pressing_it(self) -> None:
        """从按钮上拖走 = 拖窗口,松开时那次点击要被吞掉。

        不吞的话,想把窗口从按钮上拖开的人会顺手把秒表也给开了。
        """
        before = self._rect()
        button = self._layout().buttons[0]
        after = self._drag(button.centre, 50, 40)
        self.assertEqual((after[0], after[1]), (before[0] + 50, before[1] + 40))
        self.assertFalse(self.watch.is_running(), "拖窗口不该顺手把秒表开起来")

    def test_a_tiny_shake_while_clicking_does_not_move_the_window(self) -> None:
        """点按钮时手抖一两个像素,不该把窗口挪歪。"""
        before = self._rect()
        button = self._layout().buttons[0]
        origin = self._press(*button.centre)
        self._move_to(origin, 2, 2)   # 小于 DRAG_THRESHOLD
        self._release(button.centre[0] + 2, button.centre[1] + 2)
        self.assertEqual(self._rect()[:2], before[:2])
        self.assertTrue(self.watch.is_running(), "抖了一下按钮就不灵了")

    # ---------------- 按键 ----------------

    def test_space_toggles_the_watch(self) -> None:
        """空格开始/暂停。

        先 focus_force 是模拟真实情况:无边框窗口点一下才会拿到键盘焦点
        (见 _on_press)。测试进程里前面若销毁过窗口,新开的这个默认是没有焦点的,
        不抢一下按键根本送不到 —— 那是测试环境的产物,不是程序的问题。
        """
        self.root.focus_force()
        self.root.update()
        self.root.event_generate("<space>")
        self.root.update()
        self.assertTrue(self.watch.is_running())
        self.root.event_generate("<space>")
        self.root.update()
        self.assertFalse(self.watch.is_running())

    def test_closing_leaves_no_pending_timers(self) -> None:
        """关窗必须把所有定时器都撤干净。

        漏掉任何一个,它都会在窗口销毁后触发 —— 轻则在控制台打红字,
        重则去操作一个已经不存在的控件。四个 after id 要一个不落。
        """
        self.app.on_close()
        self.assertIsNone(self.app._job)
        self.assertIsNone(self.app._relayout_job)
        self.assertIsNone(self.app._save_job)
        self.assertIsNone(self.app._flash_job)

    # ---------------- 防抖 ----------------

    def test_resizing_does_not_run_away(self) -> None:
        """连续改尺寸不能引发重排风暴。

        v1.0.0 的隐患是:改字号 -> 控件请求变大 -> 顶层变大 -> 再触发重排。
        现在顶层尺寸是显式设定的,这个环从结构上就不存在;
        这条断言把它钉住 —— 真出现了环,事件数会炸到几百上千。
        """
        events: list[object] = []
        # add="+" 是必须的:不带的话会把 app 自己那条 <Configure> 覆盖掉,
        # 那就变成"把要测的东西删了再测它"
        self.root.bind("<Configure>", lambda e: events.append(e), add="+")

        sizes = [(520, 300), (640, 380), (760, 460), (880, 540), (600, 340)]
        for width, height in sizes:
            self.root.geometry(f"{width}x{height}")
            self.root.update()

        self.assertLess(
            len(events), 15 * len(sizes),
            f"{len(sizes)} 次改尺寸触发了 {len(events)} 个 <Configure>,疑似重排反馈环",
        )

    def test_explicit_size_survives_font_changes(self) -> None:
        """改字号不能改变顶层窗口尺寸 —— 这正是切断反馈环的那一条。"""
        for width, height in ((500, 300), (900, 600), (700, 420)):
            with self.subTest(size=(width, height)):
                self.root.geometry(f"{width}x{height}")
                self.root.update()
                self.app._relayout()
                self.root.update()
                self.assertEqual(
                    (self.root.winfo_width(), self.root.winfo_height()), (width, height)
                )

    # ---------------- 按钮 ----------------

    def test_the_primary_button_starts_the_watch(self) -> None:
        self._click(*self._layout().buttons[0].centre)
        self.assertTrue(self.watch.is_running())

    def test_the_primary_button_reads_pause_while_running(self) -> None:
        """开始之后同一个按钮变成暂停 —— 参考图里就是这么做的。"""
        self.assertEqual(self.app._button_labels()[0], "开始")
        self._click(*self._layout().buttons[0].centre)
        self.assertEqual(self.app._button_labels()[0], "暂停")
        self._click(*self._layout().buttons[0].centre)
        self.assertFalse(self.watch.is_running())
        self.assertEqual(self.app._button_labels()[0], "开始")

    def test_the_reset_button_clears_the_watch(self) -> None:
        self._click(*self._layout().buttons[0].centre)
        self._click(*self._layout().buttons[1].centre)
        self.assertFalse(self.watch.is_running())
        self.assertEqual(self.watch.formatted(), "00:00:00")

    def test_the_third_button_switches_the_mode(self) -> None:
        self._click(*self._layout().buttons[2].centre)
        self.assertEqual(self.app.mode, MODE_COUNTDOWN)
        self._click(*self._layout().buttons[2].centre)
        self.assertEqual(self.app.mode, MODE_WATCH)

    def test_the_third_button_label_follows_the_mode(self) -> None:
        """按钮上写的是"切过去之后是什么",不是"现在是什么"。"""
        self.assertEqual(self.app._button_labels()[2], "切换为倒计时")
        self._click(*self._layout().buttons[2].centre)
        self.assertEqual(self.app._button_labels()[2], "切换为秒表")

    def test_clicking_a_button_does_not_move_the_window(self) -> None:
        before = self._rect()
        self._click(*self._layout().buttons[1].centre)
        self.assertEqual(self._rect()[:2], before[:2])

    def test_the_close_box_closes_the_window(self) -> None:
        close = self._layout().close
        self._press(*close.centre)
        self._release(*close.centre)
        self.assertIsNone(self.app._job, "关掉之后刷新循环该停了")
        with self.assertRaises(tk.TclError):
            # root.destroy() 会把整个 Tcl 解释器拆掉,所以"窗口没了"在这里
            # 表现成"连 winfo 都调不了" —— 这就是证据。
            self.root.winfo_exists()

    def test_the_close_box_does_not_act_when_the_click_starts_elsewhere(self) -> None:
        """在空白处按下、拖到 × 上才松开,不算点 ×。

        否则想拖窗口的人会在拖到右上角时把程序关掉。
        """
        close = self._layout().close
        self._press(close.centre[0] - 60, close.centre[1] + 40)
        self._release(*close.centre)
        self.assertTrue(self.root.winfo_exists())

    def test_the_buttons_are_wired_to_the_mouse(self) -> None:
        """真正走一遍 Tk 的事件派发,证明画布上的绑定没接错。

        上面那些用例是直接调处理器的;万一 bind 那一行写错了,
        它们一条都发现不了 —— 这条是唯一能发现的。
        """
        button = self._layout().buttons[0]
        self.app.canvas.event_generate("<Button-1>", x=button.centre[0], y=button.centre[1])
        self.app.canvas.event_generate(
            "<ButtonRelease-1>", x=button.centre[0], y=button.centre[1]
        )
        self.root.update()
        self.assertTrue(self.watch.is_running())

    # ---------------- 光标 ----------------

    def test_the_cursor_follows_the_edges(self) -> None:
        """鼠标挪到边上要变成双向箭头 —— 无边框窗口没有系统边框,

        不自己给这个提示,用户根本不知道四个边都能拉。
        """
        for x, y, expected in (
            (1, self._rect()[3] // 2, "size_we"),
            (self._rect()[2] // 2, 1, "size_ns"),
            (1, 1, "size_nw_se"),
            (self._rect()[2] - 1, self._rect()[3] - 1, "size_nw_se"),
            (self._rect()[2] - 1, 1, "size_ne_sw"),
            (1, self._rect()[3] - 1, "size_ne_sw"),
        ):
            with self.subTest(point=(x, y)):
                self.app._update_pointer(x, y)
                self.assertEqual(self.app.canvas.cget("cursor"), expected)

    def test_the_cursor_is_a_move_arrow_in_the_middle_and_a_hand_on_the_buttons(self) -> None:
        self.app._update_pointer(*self._layout().digits.centre)
        self.assertEqual(self.app.canvas.cget("cursor"), "fleur")
        self.app._update_pointer(*self._layout().buttons[0].centre)
        self.assertEqual(self.app.canvas.cget("cursor"), "hand2")
        self.app._update_pointer(*self._layout().close.centre)
        self.assertEqual(self.app.canvas.cget("cursor"), "hand2")

    def test_the_cursor_becomes_a_hand_over_the_digits_in_countdown_mode(self) -> None:
        """倒计时模式下点数字能设时长,所以那儿也得是手型。"""
        self._click(*self._layout().buttons[2].centre)
        self.app._update_pointer(*self._layout().digits.centre)
        self.assertEqual(self.app.canvas.cget("cursor"), "hand2")

    def test_the_hover_highlight_clears_when_the_mouse_leaves(self) -> None:
        """鼠标离开窗口后按钮不能一直亮着。"""
        self.app._update_pointer(*self._layout().buttons[0].centre)
        self.assertEqual(self.app._hover, 0)
        self.app._on_leave(None)
        self.assertEqual(self.app._hover, -1)

    # ---------------- 倒计时 ----------------

    def _switch_to_countdown(self) -> None:
        self._click(*self._layout().buttons[2].centre)
        self.assertEqual(self.app.mode, MODE_COUNTDOWN)

    def test_countdown_mode_shows_the_target_time(self) -> None:
        # 秒表在跑的时候切模式会先问一句,这里替身直接答应。
        self._stub_dialogs(confirm=lambda *a, **k: True)
        self.root.event_generate("<space>")   # 先让秒表跑起来,确认切模式是干净的
        self.root.update()
        self._switch_to_countdown()
        self.assertEqual(self.app.watch.formatted(), "00:05:00")

    def test_clicking_the_digits_asks_for_a_duration(self) -> None:
        """图里没有输入框,设时长的入口就是数字本身。"""
        self._switch_to_countdown()
        asked: list[str] = []
        original = timer_ui.simpledialog.askstring
        timer_ui.simpledialog.askstring = (
            lambda title, prompt, initialvalue="", parent=None:
            (asked.append(initialvalue), "2:30")[1]
        )
        self.addCleanup(setattr, timer_ui.simpledialog, "askstring", original)

        self._click(*self._layout().digits.centre)

        self.assertEqual(asked, ["00:05:00"], "弹框里该预填当前时长")
        self.assertEqual(self.app.watch.formatted(), "00:02:30")

    def test_cancelling_the_duration_box_changes_nothing(self) -> None:
        self._switch_to_countdown()
        original = timer_ui.simpledialog.askstring
        timer_ui.simpledialog.askstring = lambda *a, **k: None
        self.addCleanup(setattr, timer_ui.simpledialog, "askstring", original)

        self._click(*self._layout().digits.centre)
        self.assertEqual(self.app.watch.formatted(), "00:05:00")

    def test_a_bad_duration_is_reported_and_asked_again(self) -> None:
        """输入不合法要说清原因并重问,不能静默吞掉。

        静默吞掉的话用户会以为程序坏了 —— 他明明输了字,界面却什么都没发生。
        """
        self._switch_to_countdown()
        answers = iter(["哈哈", "0", "90"])
        errors: list[str] = []
        original_ask = timer_ui.simpledialog.askstring
        original_error = timer_ui.messagebox.showerror
        timer_ui.simpledialog.askstring = lambda *a, **k: next(answers)
        timer_ui.messagebox.showerror = lambda t, m, parent=None: errors.append(m)
        self.addCleanup(setattr, timer_ui.simpledialog, "askstring", original_ask)
        self.addCleanup(setattr, timer_ui.messagebox, "showerror", original_error)

        self._click(*self._layout().digits.centre)

        self.assertEqual(len(errors), 2, "两次非法输入各该报一次")
        self.assertIn("不是数字", errors[0])
        self.assertIn("大于 0", errors[1])
        self.assertEqual(self.app.watch.formatted(), "00:01:30", "最后那个 90 秒该被采纳")

    def test_clicking_the_digits_does_nothing_in_stopwatch_mode(self) -> None:
        """秒表模式下点数字只是拖窗口,不该弹框。"""
        original = timer_ui.simpledialog.askstring
        timer_ui.simpledialog.askstring = lambda *a, **k: self.fail("秒表模式不该问时长")
        self.addCleanup(setattr, timer_ui.simpledialog, "askstring", original)
        self._click(*self._layout().digits.centre)

    def test_the_countdown_alerts_exactly_once_when_it_finishes(self) -> None:
        """到点提醒必须只响一次。

        靠 _alerted 那个标记;漏掉它的话每 100 毫秒刷新一次就弹一个框,
        屏幕上会叠出一串对话框,关都关不完。
        """
        self._switch_to_countdown()
        bells: list[int] = []
        info: list[str] = []
        self.root.bell = lambda: bells.append(1)
        self.addCleanup(setattr, self.root, "bell", tk.Tk.bell.__get__(self.root))
        original_info = timer_ui.messagebox.showinfo
        timer_ui.messagebox.showinfo = lambda t, m, parent=None: info.append(m)
        self.addCleanup(setattr, timer_ui.messagebox, "showinfo", original_info)

        timer = self.app._timers[MODE_COUNTDOWN]
        timer.set_target(1)
        # **假时钟要在 start() 之前装上**:start() 会把"当前时刻"记成这一段的
        # 起点,事后换时钟的话起点和读数就不在同一把尺子上了。
        # 而且一次 _refresh 会读好几次时钟(读数、状态、是否跑完),所以假时钟
        # 得是"某个时刻"而不是"下一个时刻" —— 迭代器第二次读就 StopIteration。
        now = [1000.0]
        timer._clock = lambda: now[0]
        timer.start()
        now[0] = 2000.0   # 一步跨过那 1 秒

        self.app._refresh()
        self.app._refresh()
        self.app._refresh()

        self.assertEqual(bells, [1], "铃只该响一次")
        self.assertEqual(len(info), 1, "框只该弹一次")

    def test_a_finished_countdown_starts_over_when_you_press_start(self) -> None:
        """数到 0 之后再点开始要重新计时。

        不这么做的话,用户在 00:00:00 上点开始会什么都不发生 ——
        在用户眼里就是"程序坏了"。
        """
        # 归零那一下会弹"时间到",这里不弹,只关心"再点开始"的行为。
        self._stub_dialogs(info=lambda *a, **k: None)
        self._switch_to_countdown()
        timer = self.app._timers[MODE_COUNTDOWN]
        timer.set_target(5)
        now = [1000.0]
        timer._clock = lambda: now[0]   # 必须赶在 start() 之前,理由见上一条
        timer.start()
        now[0] = 2000.0   # 一步跨过那 5 秒
        self.app._refresh()
        self.assertTrue(timer.is_finished())

        now[0] = 3000.0
        self._click(*self._layout().buttons[0].centre)

        self.assertTrue(timer.is_running())
        self.assertEqual(timer.formatted(), "00:00:05")

    def test_the_reset_button_returns_a_countdown_to_its_target(self) -> None:
        """倒计时里"重置"是"重新来一遍",所以回到设定时长而不是归零。"""
        self._switch_to_countdown()
        self.app.watch.reset()
        self.assertEqual(self.app.watch.formatted(), "00:05:00")

    def test_switching_mode_while_running_asks_first(self) -> None:
        """计时中切模式会丢掉当前计时,所以要先问一句。"""
        asked: list[str] = []
        original = timer_ui.messagebox.askokcancel
        timer_ui.messagebox.askokcancel = (
            lambda t, m, parent=None: (asked.append(m), False)[1]
        )
        self.addCleanup(setattr, timer_ui.messagebox, "askokcancel", original)

        self._click(*self._layout().buttons[0].centre)
        self._click(*self._layout().buttons[2].centre)

        self.assertEqual(len(asked), 1)
        self.assertEqual(self.app.mode, MODE_WATCH, "用户点了取消,模式不能变")
        self.assertTrue(self.watch.is_running(), "取消了就不该把计时也停掉")

    def test_each_mode_remembers_its_own_state(self) -> None:
        """两边各自留着自己的状态,切回来还是刚才那样,不是从头开始。"""
        self._switch_to_countdown()
        self.app._timers[MODE_COUNTDOWN].set_target(300)
        self.app._sync_all()
        self._click(*self._layout().buttons[2].centre)   # 回秒表
        self.assertEqual(self.app.watch.formatted(), "00:00:00")
        self._click(*self._layout().buttons[2].centre)   # 再切回倒计时
        self.assertEqual(self.app.watch.formatted(), "00:05:00")

    # ---------------- 设置往返 ----------------

    def test_settings_are_persisted_on_close(self) -> None:
        self.root.geometry("560x340+120+90")
        self.root.update()
        self._click(*self._layout().buttons[2].centre)   # 切到倒计时
        self.app._timers[MODE_COUNTDOWN].set_target(450)

        self.app.on_close()

        saved = load_settings(self.path)
        self.assertEqual(saved.mode, MODE_COUNTDOWN)
        self.assertEqual(saved.target, 450)
        self.assertEqual((saved.width, saved.height), (560, 340))

    def test_the_saved_mode_and_target_come_back_on_the_next_launch(self) -> None:
        save_settings(
            Settings(mode=MODE_COUNTDOWN, target=75, width=460, height=300), self.path
        )

        root = tk.Tk()
        app = TimerApp(root, watch=Stopwatch(), settings_path=self.path)
        try:
            root.update()
            self.assertEqual(app.mode, MODE_COUNTDOWN)
            self.assertEqual(app.watch.formatted(), "00:01:15")
            self.assertEqual(app._button_labels()[2], "切换为秒表")
        finally:
            app._cancel_pending()
            try:
                root.update_idletasks()
                root.destroy()
            except tk.TclError:
                pass

    def test_corrupt_settings_file_still_starts(self) -> None:
        """设置文件被手改坏了,程序必须照常打开(退回默认),而不是报错退出。"""
        self.path.write_text("{ 这不是 json", encoding="utf-8")
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = TimerApp(root, settings_path=self.path)
        self.addCleanup(self._stop_timers, app)
        root.update()
        self.assertEqual(app.mode, MODE_WATCH)
        self.assertIsNotNone(app.layout(), "设置坏了也得把界面画出来")

    def test_an_old_settings_file_with_deleted_keys_still_starts(self) -> None:
        """老版本留下的设置文件(带 bg / transparent)必须不影响启动。

        v2.0.0 把这两项删了,但不写迁移代码 —— 多出来的键直接忽略,
        其余的照样生效。这条钉住的就是"忽略"这件事真的发生了。
        """
        save_settings(
            Settings(mode=MODE_WATCH, width=500, height=320), self.path
        )
        raw = self.path.read_text(encoding="utf-8")
        assert '"mode"' in raw
        self.path.write_text(
            raw.replace('"mode"', '"bg": "#3b3b3b", "transparent": true, "mode"'),
            encoding="utf-8",
        )

        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = TimerApp(root, settings_path=self.path)
        self.addCleanup(self._stop_timers, app)
        root.update()
        self.assertEqual((root.winfo_width(), root.winfo_height()), (500, 320))
        self.assertIsNotNone(app.layout())

    def test_saving_drops_the_deleted_keys(self) -> None:
        """存盘时不能再写回 bg / transparent —— 否则设置文件里会永远留着垃圾。"""
        self.app.on_close()
        saved = load_settings(self.path)
        self.assertFalse(hasattr(saved, "bg"))
        self.assertFalse(hasattr(saved, "transparent"))
        self.assertNotIn("transparent", self.path.read_text(encoding="utf-8"))


class CountdownWiringTest(unittest.TestCase):
    """不涉及窗口的那部分接线:两种计时器必须能互换。"""

    def test_both_timers_satisfy_the_same_interface(self) -> None:
        """界面层只依赖 timer_core.Timer 这几个成员,所以切模式时不用改逻辑。

        这条断言把它钉住:以后再加一种计时器,照着实现这几个成员就能直接插进来,
        漏掉任何一个都会在这里挂掉,而不是等到运行时某个按钮点了没反应。
        """
        from timer_core import Timer

        for timer in (Stopwatch(), Countdown()):
            with self.subTest(timer=type(timer).__name__):
                self.assertIsInstance(timer, Timer)
                for name in ("state", "is_running", "formatted", "start", "pause", "toggle", "reset"):
                    self.assertTrue(
                        hasattr(timer, name), f"{type(timer).__name__} 少了 {name}"
                    )


if __name__ == "__main__":
    unittest.main()
