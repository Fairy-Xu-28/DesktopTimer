"""我的桌面计时器 —— 程序入口。

代码分两层,这里只负责把两层装到一起并启动:

- ``timer_core.py``  **后端**:纯计时逻辑,不依赖任何界面库
- ``timer_ui.py``    **前端**:Tkinter 界面,通过 Stopwatch 读写计时状态

运行: python timer.py
按键: 空格 = 开始/暂停, R = 重置, T = 窗口置顶, Esc = 退出
"""

from __future__ import annotations

import tkinter as tk

from timer_ui import TimerApp


def main() -> None:
    root = tk.Tk()
    app = TimerApp(root)
    # 交给应用处理关闭:计时中会先弹二次确认
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
