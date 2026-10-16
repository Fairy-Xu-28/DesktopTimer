"""设置的读写 —— 记住配色、透明开关、窗口大小。纯 Python,不依赖 tkinter。

设计上只有一条硬要求:**这个模块永远不能抛异常**。

设置文件是用户看得见、改得动的东西,还可能被别的程序占用、被磁盘写满打断。
一旦它出问题就崩,用户看到的是"计时器打不开了",而且完全不知道跟那个
json 文件有关。所以:

- ``load()`` 遇到任何异常都静默退回默认值 —— 顶多是设置没了,程序照常能用
- ``save()`` 遇到任何异常都静默放弃 —— 顶多是这次设置没记住,绝不能让退出流程崩掉

``load()`` 只对**出问题的那个字段**退回默认值,其余字段照常保留。比如把手改坏
了颜色但窗口大小是对的,那窗口大小应该还能记住。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from timer_view import DEFAULT_BG, clamp_window_size, normalize_color

__all__ = ["Settings", "load", "save", "default_path", "APP_DIR_NAME"]

APP_DIR_NAME = "DesktopTimer"
FILE_NAME = "settings.json"


@dataclass(frozen=True)
class Settings:
    """一次运行需要记住的全部设置。"""

    bg: str = DEFAULT_BG
    transparent: bool = False
    width: int | None = None    # None 表示"没存过",交给界面用自然尺寸
    height: int | None = None


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

def _coerce_bg(raw: Any) -> str:
    try:
        return normalize_color(raw)
    except (ValueError, TypeError):
        return DEFAULT_BG


def _coerce_bool(raw: Any) -> bool:
    # 只认真正的布尔值。字符串 "false" 在 Python 里是非空字符串(真值),
    # 如果拿它当布尔用,用户把设置改成 "false" 反而会打开透明。
    return raw if isinstance(raw, bool) else False


def _coerce_int(raw: Any) -> int | None:
    # bool 是 int 的子类,必须先排除,否则 True 会被当成尺寸 1
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _coerce_size(data: dict[str, Any]) -> tuple[int | None, int | None]:
    """宽高要么都有、要么都没有 —— 只有一边没意义,而且会让窗口比例很奇怪。"""
    width = _coerce_int(data.get("width"))
    height = _coerce_int(data.get("height"))
    if width is None or height is None:
        return None, None
    return clamp_window_size(width, height)


def _from_dict(data: dict[str, Any]) -> Settings:
    width, height = _coerce_size(data)
    return Settings(
        bg=_coerce_bg(data.get("bg")),
        transparent=_coerce_bool(data.get("transparent")),
        width=width,
        height=height,
    )


def _to_dict(settings: Settings) -> dict[str, Any]:
    return {
        "bg": settings.bg,
        "transparent": settings.transparent,
        "width": settings.width,
        "height": settings.height,
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
