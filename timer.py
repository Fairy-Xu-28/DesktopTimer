"""计时器 —— 程序入口。

代码分四层,这里只负责把它们装到一起并启动:

- ``timer_core.py``      **后端**:纯计时逻辑,不依赖任何界面库
- ``timer_view.py``      **算法**:颜色、字号、窗口尺寸的计算,同样不依赖界面库
- ``timer_settings.py``  **记忆**:把设置存到磁盘再读回来
- ``timer_ui.py``        **前端**:Tkinter 界面,把上面几层的结果画出来

依赖方向是单向的:timer.py -> timer_ui -> {timer_view, timer_settings} -> timer_core

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
