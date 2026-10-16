"""设置的读写 —— 记住窗口大小、位置、上次用的是哪种计时器、倒计时设了多久。

纯 Python,不依赖 tkinter。

设计上只有一条硬要求:**这个模块永远不能抛异常**。

设置文件是用户看得见、改得动的东西,还可能被别的程序占用、被磁盘写满打断。
一旦它出问题就崩,用户看到的是"计时器打不开了",而且完全不知道跟那个
json 文件有关。所以:

- ``load()`` 遇到任何异常都静默退回默认值 —— 顶多是设置没了,程序照常能用
- ``save()`` 遇到任何异常都静默放弃 —— 顶多是这次设置没记住,绝不能让退出流程崩掉

``load()`` 只对**出问题的那个字段**退回默认值,其余字段照常保留。比如把手改坏
了倒计时时长但窗口大小是对的,那窗口大小应该还能记住。

v2.0.0 起不再记配色和透明开关(用户不再能改),改记 ``mode`` 和 ``target``。
**老设置文件里多出来的键会被直接忽略** —— 这是刻意的,所以不需要写迁移代码。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from timer_core import (
    DEFAULT_COUNTDOWN_SECONDS,
    MAX_COUNTDOWN_SECONDS,
    MIN_COUNTDOWN_SECONDS,
)
from timer_view import clamp_window_size

__all__ = [
    "Settings",
    "load",
    "save",
    "default_path",
    "APP_DIR_NAME",
    "MODE_WATCH",
    "MODE_COUNTDOWN",
    "MODES",
]

APP_DIR_NAME = "DesktopTimer"
FILE_NAME = "settings.json"

# 两种计时模式。用字符串而不是枚举,是因为它要直接写进 json ——
# 写 "watch" 比写 0 或者 "State.WATCH" 都更好懂,用户打开文件也看得明白。
MODE_WATCH = "watch"
MODE_COUNTDOWN = "countdown"
MODES = (MODE_WATCH, MODE_COUNTDOWN)


@dataclass(frozen=True)
class Settings:
    """一次运行需要记住的全部设置。"""

    # 上次用的是哪种计时器。下次打开接着上次的模式,不用每次重新切。
    mode: str = MODE_WATCH
    # 上次设的倒计时时长(秒)。
    target: int = DEFAULT_COUNTDOWN_SECONDS
    width: int | None = None    # None 表示"没存过",交给界面用默认尺寸
    height: int | None = None
    # 窗口位置。无边框窗口没有标题栏、也不进任务栏,位置记不住的话
    # 用户下次只能在屏幕正中重新找它 —— 所以这一项是跟着无边框一起来的。
    x: int | None = None
    y: int | None = None


def default_path() -> Path:
    """设置文件的位置。

    **刻意不放在程序旁边**,两个原因:

    1. exe 是 PyInstaller ``--onefile`` 打的,``__file__`` 运行时指向解压出来的
       临时目录 —— 每次启动都是一个新的、退出就被删掉,写在那里等于写进垃圾桶。
    2. 放在 exe 旁边的话,用户把 exe 放进 ``C:\\Program Files\\`` 就会因为权限
       写不进去。

    ``%APPDATA%`` 两个问题都没有。代价是路径不好找,所以 README 里写了完整路径。
    """
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home()
    return root / APP_DIR_NAME / FILE_NAME


# ---------------- 字段校验 ----------------
# 每一个都保证:拿到任何垃圾输入都返回一个能用的值,不抛异常。

def _coerce_mode(raw: Any) -> str:
    return raw if raw in MODES else MODE_WATCH


def _coerce_target(raw: Any) -> int:
    """倒计时时长。

    越界不是退回默认值而是**钳到边界** —— 用户把 "target" 手改成 3 小时,
    看到的是"时长变成了最长能设的那个值",比"莫名其妙变回 5 分钟"好懂。
    类型不对(字符串、小数、布尔)才退回默认值。
    """
    # bool 是 int 的子类,不先排除的话 True 会被当成 1 秒
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULT_COUNTDOWN_SECONDS
    return max(MIN_COUNTDOWN_SECONDS, min(MAX_COUNTDOWN_SECONDS, raw))


def _coerce_int(raw: Any) -> int | None:
    # bool 是 int 的子类,必须先排除,否则 True 会被当成尺寸 1
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _coerce_pair(
    data: dict[str, Any], first: str, second: str
) -> tuple[int | None, int | None]:
    """成对读两个整数,要么都有、要么都没有。

    尺寸和位置都是这种"成对才有意义"的东西:只有一个宽度没意义,
    只有一个 x 也一样。缺一半就当整对没存过。
    """
    one = _coerce_int(data.get(first))
    other = _coerce_int(data.get(second))
    if one is None or other is None:
        return None, None
    return one, other


def _from_dict(data: dict[str, Any]) -> Settings:
    width, height = _coerce_pair(data, "width", "height")
    if width is not None and height is not None:
        width, height = clamp_window_size(width, height)
    x, y = _coerce_pair(data, "x", "y")
    return Settings(
        mode=_coerce_mode(data.get("mode")),
        target=_coerce_target(data.get("target")),
        width=width,
        height=height,
        x=x,
        y=y,
    )


def _to_dict(settings: Settings) -> dict[str, Any]:
    return {
        "mode": settings.mode,
        "target": settings.target,
        "width": settings.width,
        "height": settings.height,
        "x": settings.x,
        "y": settings.y,
    }


# ---------------- 读写 ----------------

def load(path: Path | None = None) -> Settings:
    """读设置。文件不存在、内容坏了、字段非法 —— 一律退回默认值,绝不抛异常。"""
    target = path if path is not None else default_path()
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        # OSError: 文件不存在 / 是目录 / 没权限
        # ValueError: JSON 语法错误 / 编码不是 UTF-8(UnicodeDecodeError 也是它的子类)
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    return _from_dict(data)


def save(settings: Settings, path: Path | None = None) -> bool:
    """写设置,返回是否写成功。

    **原子写**:先写同目录下的临时文件,再 ``os.replace`` 覆盖目标。
    直接往目标文件写的话,万一写到一半进程被杀(比如用户强退),会留下一个
    半截的 json —— 下次启动解析失败,设置就整个没了。``os.replace`` 在
    Windows 上也是原子的。

    任何失败都吞掉并返回 False:磁盘满、目录只读、文件被别的程序占用,
    都不该让计时器的退出流程崩掉。
    """
    target = path if path is not None else default_path()
    temp = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(
            json.dumps(_to_dict(settings), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, target)
    except OSError:
        # 顺手清掉可能残留的临时文件,免得在用户目录里留垃圾
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True
