"""timer_settings 的单元测试。

全部在临时目录里跑,不碰真实的 %APPDATA%。

重点在**坏输入**:设置文件是用户看得见、改得动的东西,还可能被写坏。
这个模块的硬要求是"永远不抛异常",所以下面有一大半用例是在喂垃圾。

v2.0.0 起不再记配色和透明开关(用户不再能改),改记 mode 和 target。
老文件里多出来的 bg/transparent 会被直接忽略 —— 有几条用例专门钉住这一点。

运行(在项目根目录):
    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from timer_core import (
    DEFAULT_COUNTDOWN_SECONDS,
    MAX_COUNTDOWN_SECONDS,
    MIN_COUNTDOWN_SECONDS,
)
from timer_settings import (
    APP_DIR_NAME,
    MODE_COUNTDOWN,
    MODE_WATCH,
    Settings,
    default_path,
    load,
    save,
)
from timer_view import MIN_WINDOW_H, MIN_WINDOW_W


class SettingsTempDirTest(unittest.TestCase):
    """给每个用例一个干净的临时目录,用完自动清掉。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "settings.json"

    def write_raw(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")


class DefaultPathTest(unittest.TestCase):
    def test_uses_appdata(self) -> None:
        """必须落在 %APPDATA% 下,不能是程序所在目录。

        程序如果是 --onefile 打包的 exe,程序目录在运行时是解压出来的临时目录,
        写在那里等于每次启动都从头开始。
        """
        path = default_path()
        appdata = os.environ.get("APPDATA")
        if appdata:
            self.assertEqual(path.parent.parent, Path(appdata))
        self.assertEqual(path.parent.name, APP_DIR_NAME)
        self.assertEqual(path.name, "settings.json")

    def test_is_independent_of_working_directory(self) -> None:
        original = Path.cwd()
        try:
            os.chdir(tempfile.gettempdir())
            self.assertEqual(default_path(), default_path())
        finally:
            os.chdir(original)


class LoadDefaultsTest(SettingsTempDirTest):
    """文件不存在或读不了时,必须静默退回默认值。"""

    def test_missing_file_gives_defaults(self) -> None:
        """头一次打开:秒表模式 + 默认倒计时时长,尺寸和位置都还没有。"""
        settings = load(self.path)
        self.assertEqual(settings.mode, MODE_WATCH)
        self.assertEqual(settings.target, DEFAULT_COUNTDOWN_SECONDS)
        self.assertIsNone(settings.width)
        self.assertIsNone(settings.height)
        self.assertIsNone(settings.x)
        self.assertIsNone(settings.y)

    def test_empty_file_gives_defaults(self) -> None:
        self.write_raw("")
        self.assertEqual(load(self.path), Settings())

    def test_broken_json_gives_defaults(self) -> None:
        """用户手改时漏了一个引号是最常见的情况。"""
        self.write_raw('{"mode": "countdown",}')
        self.assertEqual(load(self.path), Settings())

    def test_non_utf8_gives_defaults(self) -> None:
        self.path.write_bytes(b'{"mode": "\xff\xfe\x00"}')
        self.assertEqual(load(self.path), Settings())

    def test_json_array_gives_defaults(self) -> None:
        """合法 JSON 但不是对象。"""
        self.write_raw("[1, 2, 3]")
        self.assertEqual(load(self.path), Settings())

    def test_json_scalar_gives_defaults(self) -> None:
        self.write_raw("42")
        self.assertEqual(load(self.path), Settings())

    def test_directory_instead_of_file_gives_defaults(self) -> None:
        self.path.mkdir()
        self.assertEqual(load(self.path), Settings())


class LoadFieldTest(SettingsTempDirTest):
    """坏字段只影响它自己,不能连累别的字段。"""

    def test_reads_valid_values(self) -> None:
        self.write_raw(
            json.dumps(
                {
                    "mode": MODE_COUNTDOWN,
                    "target": 300,
                    "width": 500,
                    "height": 300,
                    "x": 120,
                    "y": 80,
                }
            )
        )
        settings = load(self.path)
        self.assertEqual(settings.mode, MODE_COUNTDOWN)
        self.assertEqual(settings.target, 300)
        self.assertEqual((settings.width, settings.height), (500, 300))
        self.assertEqual((settings.x, settings.y), (120, 80))

    def test_unknown_keys_are_ignored(self) -> None:
        """前向兼容:以后加的字段不该让旧版本崩掉。"""
        self.write_raw(json.dumps({"target": 90, "未来字段": {"a": 1}}))
        self.assertEqual(load(self.path).target, 90)

    def test_deleted_color_keys_are_ignored(self) -> None:
        """老设置文件里的 bg/transparent 直接忽略,不需要迁移代码。

        用户从 v1 升级上来时文件里就是有这两个键的,程序必须照常启动。
        """
        self.write_raw(
            json.dumps(
                {
                    "bg": "#1e2430",
                    "transparent": True,
                    "mode": MODE_COUNTDOWN,
                    "width": 500,
                    "height": 300,
                }
            )
        )
        settings = load(self.path)
        self.assertEqual(settings.mode, MODE_COUNTDOWN)
        self.assertEqual((settings.width, settings.height), (500, 300))
        self.assertFalse(hasattr(settings, "bg"))
        self.assertFalse(hasattr(settings, "transparent"))

    def test_saving_drops_deleted_color_keys(self) -> None:
        """存一次就把旧键抹掉 —— 文件会自己瘦下来。"""
        self.write_raw(json.dumps({"bg": "#1e2430", "transparent": False}))
        self.assertTrue(save(load(self.path), self.path))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("bg", raw)
        self.assertNotIn("transparent", raw)

    def test_bad_mode_falls_back_to_watch(self) -> None:
        """模式写错了就当秒表 —— 千万别猜,猜错了用户会以为程序记不住。"""
        for raw in ("COUNTDOWN", "倒计时", "", 0, 1, None, [], {}, True):
            with self.subTest(raw=raw):
                self.write_raw(json.dumps({"mode": raw, "width": 500, "height": 300}))
                self.assertEqual(load(self.path).mode, MODE_WATCH)

    def test_both_valid_modes_survive(self) -> None:
        for raw in (MODE_WATCH, MODE_COUNTDOWN):
            with self.subTest(raw=raw):
                self.write_raw(json.dumps({"mode": raw}))
                self.assertEqual(load(self.path).mode, raw)

    def test_bad_mode_does_not_take_the_size_down_with_it(self) -> None:
        self.write_raw(json.dumps({"mode": 123, "width": 500, "height": 300}))
        settings = load(self.path)
        self.assertEqual(settings.mode, MODE_WATCH)
        self.assertEqual((settings.width, settings.height), (500, 300))

    def test_target_is_clamped_not_discarded(self) -> None:
        """越界钳到边界,比"莫名其妙变回 5 分钟"好懂。"""
        for raw, expected in (
            (0, MIN_COUNTDOWN_SECONDS),
            (-5, MIN_COUNTDOWN_SECONDS),
            (MAX_COUNTDOWN_SECONDS + 1, MAX_COUNTDOWN_SECONDS),
            (10**9, MAX_COUNTDOWN_SECONDS),
            (1, MIN_COUNTDOWN_SECONDS),
            (MAX_COUNTDOWN_SECONDS, MAX_COUNTDOWN_SECONDS),
        ):
            with self.subTest(raw=raw):
                self.write_raw(json.dumps({"target": raw}))
                self.assertEqual(load(self.path).target, expected)

    def test_non_integer_target_falls_back_to_the_default(self) -> None:
        """类型不对才退回默认值 —— 字符串 "300" 不认,别猜用户想说什么。"""
        for raw in ("300", "5:00", 300.5, 300.0, None, [], {}, True, False):
            with self.subTest(raw=raw):
                self.write_raw(json.dumps({"target": raw}))
                self.assertEqual(load(self.path).target, DEFAULT_COUNTDOWN_SECONDS)

    def test_bad_target_does_not_take_the_mode_down_with_it(self) -> None:
        self.write_raw(json.dumps({"mode": MODE_COUNTDOWN, "target": "哈哈"}))
        settings = load(self.path)
        self.assertEqual(settings.mode, MODE_COUNTDOWN)
        self.assertEqual(settings.target, DEFAULT_COUNTDOWN_SECONDS)

    def test_bool_is_not_accepted_as_size(self) -> None:
        """bool 是 int 的子类。不排除的话 True 会被当成尺寸 1。"""
        self.write_raw(json.dumps({"width": True, "height": True}))
        settings = load(self.path)
        self.assertIsNone(settings.width)
        self.assertIsNone(settings.height)

    def test_negative_size_is_clamped_not_discarded(self) -> None:
        """负数/过小是坏数据,但钳到下限比整个丢掉更友好。"""
        self.write_raw(json.dumps({"width": -100, "height": 1}))
        settings = load(self.path)
        self.assertEqual((settings.width, settings.height), (MIN_WINDOW_W, MIN_WINDOW_H))

    def test_huge_size_is_clamped(self) -> None:
        """换到小显示器后,别还原出一块比屏幕还大的窗口。"""
        self.write_raw(json.dumps({"width": 99999, "height": 99999}))
        settings = load(self.path)
        self.assertEqual((settings.width, settings.height), (4000, 4000))

    def test_half_a_size_is_discarded(self) -> None:
        """只有宽度没有高度没意义。"""
        self.write_raw(json.dumps({"width": 500}))
        self.assertIsNone(load(self.path).width)

    def test_half_a_position_is_discarded(self) -> None:
        """位置同理:只有 x 的话放哪儿都说不清。"""
        self.write_raw(json.dumps({"x": 300}))
        settings = load(self.path)
        self.assertIsNone(settings.x)
        self.assertIsNone(settings.y)

    def test_string_size_is_discarded(self) -> None:
        self.write_raw(json.dumps({"width": "500", "height": "300"}))
        self.assertIsNone(load(self.path).width)

    def test_float_size_is_discarded(self) -> None:
        self.write_raw(json.dumps({"width": 500.5, "height": 300.0}))
        self.assertIsNone(load(self.path).width)

    def test_position_may_be_negative(self) -> None:
        """副屏在主屏左边时 x 就是负的 —— 这里不能钳。"""
        self.write_raw(json.dumps({"x": -800, "y": 40}))
        settings = load(self.path)
        self.assertEqual((settings.x, settings.y), (-800, 40))


class SaveTest(SettingsTempDirTest):
    def test_roundtrip(self) -> None:
        original = Settings(
            mode=MODE_COUNTDOWN,
            target=90,
            width=460,
            height=280,
            x=200,
            y=150,
        )
        self.assertTrue(save(original, self.path))
        self.assertEqual(load(self.path), original)

    def test_creates_missing_directories(self) -> None:
        """首次运行时 %APPDATA%\\DesktopTimer 还不存在。"""
        nested = self.dir / "a" / "b" / "settings.json"
        self.assertTrue(save(Settings(), nested))
        self.assertTrue(nested.exists())

    def test_leaves_no_temp_file_behind(self) -> None:
        save(Settings(), self.path)
        leftovers = [p.name for p in self.dir.iterdir() if p.name != "settings.json"]
        self.assertEqual(leftovers, [])

    def test_overwrites_previous_content(self) -> None:
        save(Settings(mode=MODE_WATCH), self.path)
        save(Settings(mode=MODE_COUNTDOWN), self.path)
        self.assertEqual(load(self.path).mode, MODE_COUNTDOWN)

    def test_replaces_corrupt_file_without_failing(self) -> None:
        """文件本来是坏的,保存一次应该能修好它,而不是报错。"""
        self.write_raw("{ 这不是 json")
        self.assertTrue(save(Settings(target=120), self.path))
        self.assertEqual(load(self.path).target, 120)

    def test_load_of_saved_settings_survives_broken_field(self) -> None:
        """存进去的值本身必须干净 —— 否则读回来会被当成坏字段丢掉。"""
        save(Settings(mode=MODE_COUNTDOWN, target=42), self.path)
        settings = load(self.path)
        self.assertEqual(settings.mode, MODE_COUNTDOWN)
        self.assertEqual(settings.target, 42)

    def test_unwritable_target_returns_false_instead_of_raising(self) -> None:
        """目标是目录时写不进去。必须返回 False,不能把退出流程炸掉。"""
        blocked = self.dir / "blocked"
        blocked.mkdir()
        self.assertFalse(save(Settings(), blocked))

    def test_returns_true_on_success(self) -> None:
        self.assertTrue(save(Settings(), self.path))


if __name__ == "__main__":
    unittest.main()
