"""timer_ui 的冒烟测试 —— 真的开一个窗口,但只断言"结构",不看长相。

这个文件存在的理由:界面层有两个错误是**光看代码看不出来**的。
一个是"加了新控件但忘了登记进主题注册表"(换色时露一块旧颜色),
另一个是"空格键绑定名单漏了新控件"(按空格秒表不动,v1.0.0 修过)。
这两个都不会让程序崩,只会让功能悄悄失效,所以必须变成会失败的测试。

**测试用的设置文件指向临时目录**,绝不碰 %APPDATA% 里用户真实的那份。

跑测试时窗口会真的闪一下,这是正常的。没有显示器时会自动跳过。

运行(在项目根目录):
    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from timer_core import State, Stopwatch
from timer_settings import load as load_settings
from timer_view import MIN_WINDOW_H, MIN_WINDOW_W

try:
    import tkinter as tk

    from timer_ui import SPACE_SENSITIVE, TimerApp
except ImportError:  # pragma: no cover - Tk 缺失的 Python 才走这里
    tk = None  # type: ignore[assignment]


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
        except tk.TclError:
            pass  # 窗口已经销毁了

    # ---------------- 主题注册表 ----------------

    def test_no_widget_is_left_unregistered(self) -> None:
        """控件树里不允许有没登记进主题注册表的控件。

        漏登记的症状是"换个配色,那一块还是旧颜色",肉眼很容易漏看,
        所以交给这条断言去发现。
        """
        leftovers = [str(w) for w in self.app._untracked()]
        self.assertEqual(leftovers, [], f"这些控件没登记进主题注册表: {leftovers}")

    def test_theme_change_reaches_every_widget(self) -> None:
        """换底色后,主窗口和显示区的底色必须真的跟着变。"""
        for name, bg in (("纯黑", "#000000"), ("深蓝", "#1e2430")):
            with self.subTest(preset=name):
                self.app._choose_color(bg)
                self.assertEqual(self.app.theme.bg, bg)
                self.assertEqual(self.root.cget("bg"), bg)
                self.assertEqual(self.app.display.cget("bg"), bg)

    def test_status_color_survives_theme_change(self) -> None:
        """状态文字色由状态决定,不能被统一换肤覆盖掉。

        这是主题机制里最容易踩的一个坑:那个 Label 和别的控件一样登记在册,
        统一 configure 时如果连 fg 一起写,绿/黄状态色就没了。
        """
        self.app._act(self.watch.start)
        self.app._choose_color("#000000")
        expected = self.app.theme.status_color(State.RUNNING)
        self.assertEqual(self.app.status.cget("fg"), expected)

    # ---------------- 空格键 ----------------

    def test_every_space_sensitive_widget_is_bound(self) -> None:
        """所有自带 <space> 类绑定的控件,都必须额外绑一份控件级的。

        不绑的话,焦点在那个控件上时按空格会先激活控件、再触发全局绑定 ——
        表现就是"按空格秒表不动"。名单必须从注册表推导,不能写死。
        """
        sensitive = [w for w, _role in self.app._themed if isinstance(w, SPACE_SENSITIVE)]
        self.assertTrue(sensitive, "一个敏感控件都没有,这条断言等于没测")

        self.assertTrue(self.root.bind("<space>"), "顶层没有绑全局空格键")
        for widget in sensitive:
            with self.subTest(widget=str(widget)):
                self.assertTrue(widget.bind("<space>"), f"{widget} 没有控件级空格绑定")

    def test_space_handler_truncates_dispatch(self) -> None:
        """处理函数必须返回 "break",否则事件还会继续往上派发、触发两次。"""
        self.assertEqual(self.app._on_space(None), "break")

    def test_space_toggles_the_watch(self) -> None:
        self.app._on_space(None)
        self.assertTrue(self.watch.is_running())
        self.app._on_space(None)
        self.assertEqual(self.watch.state, State.PAUSED)

    # ---------------- 透明 ----------------

    def test_transparency_key_is_the_current_background(self) -> None:
        """键色必须正好等于当前底色,否则会在窗口上打出一个按旧颜色留的洞。"""
        self.app.transparent_var.set(True)
        self.app._apply_transparency()
        self.assertEqual(self.root.attributes("-transparentcolor"), self.app.theme.bg)

    def test_transparency_follows_color_change(self) -> None:
        """开着透明时换配色,键色要跟着换。"""
        self.app.transparent_var.set(True)
        self.app._choose_color("#1e2430")
        self.assertEqual(self.root.attributes("-transparentcolor"), "#1e2430")

    def test_status_line_keeps_a_solid_background_when_transparent(self) -> None:
        """透明时状态那行小字必须自带底色,不能被键色抹掉。

        它是绿/黄的小字,浮在桌面上等于没写 —— 而桌面是什么颜色我们不知道。
        这条断言的是"它的底色不等于键色",也就是"它一定还看得见"。
        """
        self.app.transparent_var.set(True)
        self.app._apply_transparency()
        self.assertNotEqual(self.app.status.cget("bg"), self.app.theme.bg)

    def test_status_line_has_no_chip_when_opaque(self) -> None:
        """不透明时它必须和窗口同色,否则界面上会多出一块色块。"""
        self.app.transparent_var.set(False)
        self.app._apply_transparency()
        self.assertEqual(self.app.status.cget("bg"), self.app.theme.bg)

    def test_status_colour_survives_a_transparency_round_trip(self) -> None:
        """透明开->关->开之后,状态色还得是对的(v1.0.0 的回归项之一)。"""
        self.app._act(self.watch.start)
        for transparent in (True, False, True):
            with self.subTest(transparent=transparent):
                self.app.transparent_var.set(transparent)
                self.app._apply_transparency()
                self.app._sync_controls(self.watch.state)
                self.assertEqual(
                    self.app.status.cget("fg"),
                    self.app.theme.status_color(State.RUNNING),
                )

    def test_transparency_off_clears_the_key(self) -> None:
        self.app.transparent_var.set(True)
        self.app._apply_transparency()
        self.app.transparent_var.set(False)
        self.app._apply_transparency()
        self.assertFalse(str(self.root.attributes("-transparentcolor")).strip())

    # ---------------- 缩放 ----------------

    def test_window_is_resizable_with_a_minimum(self) -> None:
        self.assertTrue(self.root.resizable())
        self.assertEqual(
            (self.root.minsize()), (MIN_WINDOW_W, MIN_WINDOW_H)
        )

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
        self.root.geometry("700x420")
        self.root.update()
        for width, height in ((500, 300), (900, 600), (700, 420)):
            with self.subTest(size=(width, height)):
                self.root.geometry(f"{width}x{height}")
                self.root.update()
                self.app._relayout()
                self.root.update()
                self.assertEqual(
                    (self.root.winfo_width(), self.root.winfo_height()), (width, height)
                )

    # ---------------- 设置小窗 ----------------

    def test_settings_window_has_its_own_registry(self) -> None:
        """设置小窗的控件不能混进主窗口那份注册表。

        混进去的话,_bind_keys 会给它们也绑上全局空格键 ——
        在设置窗里按空格就会去动秒表,而勾选框该被勾上。
        """
        self.app.open_settings()
        self.root.update()
        self.addCleanup(self.app.close_settings)

        self.assertTrue(self.app._settings_themed, "设置窗里一个控件都没登记")
        main = {str(w) for w, _role in self.app._themed}
        for widget, _role in self.app._settings_themed:
            self.assertNotIn(str(widget), main, f"{widget} 同时进了两份名单")
        self.assertEqual(self.app._untracked(), [], "设置窗被算进了主窗口的自检")

    def test_settings_window_follows_theme_change(self) -> None:
        self.app.open_settings()
        self.root.update()
        self.addCleanup(self.app.close_settings)

        self.app._choose_color("#1e2430")
        self.root.update()
        window = self.app._settings_window
        self.assertEqual(window.cget("bg"), "#1e2430")
        for widget, role in self.app._settings_themed:
            if role == "root":
                self.assertEqual(widget.cget("bg"), "#1e2430")

    def test_settings_window_can_be_closed_twice(self) -> None:
        """点了 × 之后再点"关闭"不能炸。

        destroy 一个已经销毁的控件会抛 TclError,而用户完全可能这么操作。
        """
        self.app.open_settings()
        self.root.update()
        self.app._settings_window.destroy()
        self.app.close_settings()   # 不该抛异常

    # ---------------- 设置往返 ----------------

    def test_settings_are_persisted_on_close(self) -> None:
        self.app._choose_color("#1f2b24")
        self.app.transparent_var.set(True)
        self.root.geometry("560x340")
        self.root.update()

        self.app.on_close()

        saved = load_settings(self.path)
        self.assertEqual(saved.bg, "#1f2b24")
        self.assertTrue(saved.transparent)
        self.assertEqual((saved.width, saved.height), (560, 340))

    def test_corrupt_settings_file_still_starts(self) -> None:
        """设置文件被手改坏了,程序必须照常打开(退回默认),而不是报错退出。"""
        self.path.write_text("{ 这不是 json", encoding="utf-8")
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = TimerApp(root, settings_path=self.path)
        self.addCleanup(self._stop_timers, app)
        self.assertEqual(app.theme.bg, "#3b3b3b")
        self.assertFalse(app.transparent_var.get())

    def test_closing_leaves_no_pending_timers(self) -> None:
        """关窗必须把所有定时器都撤干净。

        漏掉任何一个,它都会在窗口销毁后触发 —— 轻则在控制台打红字,
        重则去操作一个已经不存在的控件。三个 after id 要一个不落。
        """
        self.app.on_close()
        self.assertIsNone(self.app._job)
        self.assertIsNone(self.app._relayout_job)
        self.assertIsNone(self.app._save_job)


if __name__ == "__main__":
    unittest.main()
