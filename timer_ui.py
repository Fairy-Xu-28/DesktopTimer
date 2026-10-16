"""秒表的界面层 —— Tkinter 相关代码全部在这里。

这是"前后端"里的**前端**:只负责把 Stopwatch 的状态画到窗口上,
以及把按钮、按键转成对 Stopwatch 的调用。它自己不做任何时间计算,
读数和格式化都交给 timer_core。

界面层从构造函数接收 Stopwatch(不传则自建一个),这样测试时可以注入
一个用假时钟驱动的实例,不必依赖真实时间。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Callable

from timer_core import State, Stopwatch

# ---------------- 主题 ----------------
BG = "#3b3b3b"
FG = "#ffffff"
BTN_BG = "#4d4d4d"
BTN_FG = "#e8e8e8"
BTN_ACTIVE = "#5e5e5e"
BTN_DISABLED_FG = "#7d7d7d"  # 深色底上的禁用文字;不显式指定的话默认灰字几乎看不见
HINT_FG = "#9a9a9a"
STATUS_FG = {
    State.IDLE: "#9a9a9a",
    State.RUNNING: "#7fd47f",
    State.PAUSED: "#e8c46a",
}

DIGIT_FONT = ("Consolas", 46, "bold")
BTN_FONT = ("Microsoft YaHei", 12)
STATUS_FONT = ("Microsoft YaHei", 10, "bold")
SMALL_FONT = ("Microsoft YaHei", 9)

# ---------------- 行为常量 ----------------
TICK_INTERVAL_MS = 100  # 刷新间隔。显示精度只到整秒,100ms 足够跟手又不费 CPU
WINDOW_TITLE = "我的桌面计时器"
WINDOW_VERTICAL_DIVISOR = 3  # 窗口纵向放在屏幕上 1/3 处而非正中,视线更舒服
STATUS_TEXT = {
    State.IDLE: "就绪",
    State.RUNNING: "● 计时中",
    State.PAUSED: "‖ 已暂停",
}


class TimerApp:
    """把 Stopwatch 渲染到 Tkinter 窗口上。"""

    def __init__(self, root: tk.Tk, watch: Stopwatch | None = None) -> None:
        self.root = root
        # 依赖注入:默认自建;测试时传入用假时钟驱动的实例
        self.watch = watch if watch is not None else Stopwatch()

        self._job: str | None = None          # after() 定时器 id
        self._last_text: str = ""             # 上次渲染的读数,用于去重
        self._last_state: State | None = None

        self.topmost_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._center_window()
        self._bind_keys()
        self._tick()  # 启动刷新循环(内部会先渲染一次)

    # ---------------- 搭建界面 ----------------

    def _build_ui(self) -> None:
        self.root.title(WINDOW_TITLE)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        # 大号数字显示区
        self.display = tk.Label(
            self.root, text="00:00:00", font=DIGIT_FONT, bg=BG, fg=FG, padx=28, pady=20
        )
        self.display.pack()

        # 状态提示
        self.status = tk.Label(
            self.root, text="", font=STATUS_FONT, bg=BG, fg=STATUS_FG[State.IDLE]
        )
        self.status.pack()

        # 按钮行
        row = tk.Frame(self.root, bg=BG)
        row.pack(pady=(10, 6))

        self.start_btn = self._make_button(row, "开始", self._on_start)
        self.pause_btn = self._make_button(row, "暂停", self._on_pause)
        self.reset_btn = self._make_button(row, "重置", self._on_reset)
        for column, btn in enumerate((self.start_btn, self.pause_btn, self.reset_btn)):
            btn.grid(row=0, column=column, padx=4)

        # 置顶开关
        self.topmost_box = tk.Checkbutton(
            self.root,
            text="窗口置顶",
            variable=self.topmost_var,
            command=self._apply_topmost,
            font=SMALL_FONT,
            bg=BG,
            fg=HINT_FG,
            activebackground=BG,
            activeforeground=FG,
            selectcolor=BTN_BG,  # 勾选框内部底色,默认是白色,会破坏深色主题
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.topmost_box.pack(pady=(0, 4))

        tk.Label(
            self.root,
            text="空格 开始/暂停   ·   R 重置   ·   T 置顶   ·   Esc 退出",
            font=SMALL_FONT,
            bg=BG,
            fg=HINT_FG,
        ).pack(pady=(0, 10))

    def _make_button(self, parent: tk.Widget, text: str, command: Callable[[], None]) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            font=BTN_FONT,
            bg=BTN_BG,
            fg=BTN_FG,
            activebackground=BTN_ACTIVE,
            activeforeground=FG,
            disabledforeground=BTN_DISABLED_FG,
            relief="flat",
            bd=0,
            width=8,
            padx=10,
            pady=8,
            cursor="hand2",
            command=command,
        )

    def _center_window(self) -> None:
        """把窗口横向居中,纵向放在屏幕上 1/3 处。"""
        self.root.update_idletasks()
        width, height = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - height) // WINDOW_VERTICAL_DIVISOR
        self.root.geometry(f"+{x}+{y}")

    # ---------------- 按键 ----------------

    def _bind_keys(self) -> None:
        """绑定快捷键。

        关于空格键:Button 和 Checkbutton 的 Tk 类绑定里自带 <Key-space>
        (Button 的是 ``tk::ButtonInvoke``),而事件按「控件 -> 类 -> 顶层」的顺序
        派发。所以焦点落在按钮上时按空格,会先激活那个按钮、再触发这里的全局
        绑定 —— 表现为"按空格秒表不动"(开始完立刻被暂停)。
        在这些控件上也绑一份控件级 <space> 并返回 "break",即可截断后续派发,
        从根上避免重复触发。这样空格在窗口里就只有一种含义:开始/暂停。
        """
        self.root.bind("<space>", self._on_space)
        for widget in (self.start_btn, self.pause_btn, self.reset_btn, self.topmost_box):
            widget.bind("<space>", self._on_space)

        self.root.bind("<r>", lambda _e: self._act(self.watch.reset))
        self.root.bind("<R>", lambda _e: self._act(self.watch.reset))
        self.root.bind("<t>", lambda _e: self._toggle_topmost_by_key())
        self.root.bind("<T>", lambda _e: self._toggle_topmost_by_key())
        # Esc 与点右上角 × 行为一致,同样会走"计时中二次确认"
        self.root.bind("<Escape>", lambda _e: self.on_close())

    def _on_space(self, _event: tk.Event) -> str:
        self._act(self.watch.toggle)
        return "break"  # 截断派发,不让按钮的类绑定再跑一次

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

    def _apply_topmost(self) -> None:
        """把复选框的状态套用到窗口上。

        注意:点击复选框时 Tk 会先改好变量再调用本方法,所以这里只读取、
        不再翻转 —— 否则会和 Tk 的翻转动作抵消,勾选框看起来没反应。
        """
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

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
        self.status.config(text=STATUS_TEXT[state], fg=STATUS_FG[state])
        self.start_btn.config(state=tk.DISABLED if state is State.RUNNING else tk.NORMAL)
        self.pause_btn.config(state=tk.NORMAL if state is State.RUNNING else tk.DISABLED)
        self.reset_btn.config(state=tk.DISABLED if state is State.IDLE else tk.NORMAL)

    # ---------------- 退出 ----------------

    def on_close(self) -> None:
        """关窗:正在计时就先确认一次,再撤掉定时器并销毁窗口。"""
        if self.watch.is_running() and not self._confirm_quit():
            return
        self._cancel_tick()
        self.root.destroy()

    def _confirm_quit(self) -> bool:
        """计时中关窗的二次确认。

        窗口置顶时先临时取消置顶,否则对话框可能被主窗口盖住(Windows 上实测会)。
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

    def _cancel_tick(self) -> None:
        if self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None
