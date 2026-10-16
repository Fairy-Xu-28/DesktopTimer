"""我的桌面计时器 —— 一个基于 Tkinter 的简易秒表。

运行: python timer.py
按键: 空格 = 开始/暂停, R = 重置, Esc = 退出
"""

import time
import tkinter as tk

# ---------------------------- 外观主题 ----------------------------
BG = "#3b3b3b"          # 窗口背景
FG = "#ffffff"          # 数字颜色
BTN_BG = "#4d4d4d"      # 按钮底色
BTN_FG = "#e8e8e8"      # 按钮文字
BTN_ACTIVE = "#5e5e5e"  # 按钮按下时的底色
HINT_FG = "#9a9a9a"     # 底部提示文字

DIGIT_FONT = ("Consolas", 46, "bold")
BTN_FONT = ("Microsoft YaHei", 12)
HINT_FONT = ("Microsoft YaHei", 9)


class TimerApp:
    """秒表：显示已用时间,支持 开始 / 暂停 / 重置。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.elapsed = 0.0       # 已累计的秒数
        self.start_time = None   # 本段计时的起始时刻(time.monotonic)
        self.job = None          # after() 返回的定时器 id

        self._build_ui()
        self._bind_keys()
        self._tick()             # 先渲染一次 00:00:00

    # ---------------------------- 界面 ----------------------------
    def _build_ui(self) -> None:
        self.root.title("我的桌面计时器")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        # 大号数字显示区
        self.display = tk.Label(
            self.root,
            text="00:00:00",
            font=DIGIT_FONT,
            bg=BG,
            fg=FG,
            padx=28,
            pady=20,
        )
        self.display.pack()

        # 按钮行
        row = tk.Frame(self.root, bg=BG)
        row.pack(pady=(0, 6))

        self.start_btn = self._make_button(row, "开始", self.start)
        self.pause_btn = self._make_button(row, "暂停", self.pause)
        self.reset_btn = self._make_button(row, "重置", self.reset)

        for i, btn in enumerate((self.start_btn, self.pause_btn, self.reset_btn)):
            btn.grid(row=0, column=i, padx=4)

        tk.Label(
            self.root,
            text="空格 开始/暂停   ·   R 重置   ·   Esc 退出",
            font=HINT_FONT,
            bg=BG,
            fg=HINT_FG,
        ).pack(pady=(0, 10))

        self._center_window()

    def _make_button(self, parent: tk.Widget, text: str, command) -> tk.Button:
        btn = tk.Button(
            parent,
            text=text,
            font=BTN_FONT,
            bg=BTN_BG,
            fg=BTN_FG,
            activebackground=BTN_ACTIVE,
            activeforeground=FG,
            relief="flat",
            bd=0,
            width=8,
            padx=10,
            pady=8,
            cursor="hand2",
            command=command,
        )
        return btn

    def _center_window(self) -> None:
        """把窗口放到屏幕中央。"""
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 3
        self.root.geometry(f"+{x}+{y}")

    def _bind_keys(self) -> None:
        self.root.bind("<space>", lambda _e: self.pause() if self.is_running() else self.start())
        self.root.bind("<r>", lambda _e: self.reset())
        self.root.bind("<R>", lambda _e: self.reset())
        self.root.bind("<Escape>", lambda _e: self.root.destroy())

    # ---------------------------- 逻辑 ----------------------------
    def is_running(self) -> bool:
        return self.start_time is not None

    def total_seconds(self) -> float:
        """当前总用时(含正在进行的这一段)。"""
        if self.is_running():
            return self.elapsed + (time.monotonic() - self.start_time)
        return self.elapsed

    def start(self) -> None:
        """开始计时;已在计时中则忽略。"""
        if self.is_running():
            return
        self.start_time = time.monotonic()
        self._render(self.total_seconds())

    def pause(self) -> None:
        """暂停计时,保留已用时间。"""
        if not self.is_running():
            return
        self.elapsed = self.total_seconds()
        self.start_time = None
        self._render(self.elapsed)

    def reset(self) -> None:
        """归零并停止。"""
        self.elapsed = 0.0
        self.start_time = None
        self._render(0.0)

    # ---------------------------- 循环 ----------------------------
    def _tick(self) -> None:
        """每 100ms 刷新一次显示。"""
        if self.is_running():
            self._render(self.total_seconds())
        self.job = self.root.after(100, self._tick)

    def _render(self, seconds: float) -> None:
        total = int(seconds)
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        self.display.config(text=f"{hours:02d}:{minutes:02d}:{secs:02d}")

    def on_close(self) -> None:
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = TimerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
