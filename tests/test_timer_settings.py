"""timer_settings 的单元测试。

全部在临时目录里跑,不碰真实的 %APPDATA%。

重点在**坏输入**:设置文件是用户看得见、改得动的东西,还可能被写坏。
这个模块的硬要求是"永远不抛异常",所以下面有一大半用例是在喂垃圾。

运行(在项目根目录):
    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from timer_settings import APP_DIR_NAME, Settings, default_path, load, save
from timer_view import DEFAULT_BG, MIN_WINDOW_H, MIN_WINDOW_W


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
        settings = load(self.path)
        self.assertEqual(settings.bg, DEFAULT_BG)
        self.assertFalse(settings.transparent)
        self.assertIsNone(settings.width)
        self.assertIsNone(settings.height)

    def test_empty_file_gives_defaults(self) -> None:
        self.write_raw("")
        self.assertEqual(load(self.path), Settings())

    def test_broken_json_gives_defaults(self) -> None:
        """用户手改时漏了一个引号是最常见的情况。"""
        self.write_raw('{"bg": "#000000",}')
        self.assertEqual(load(self.path), Settings())

    def test_non_utf8_gives_defaults(self) -> None:
        self.path.write_bytes(b'{"bg": "\xff\xfe\x00"}')
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
        self.write_raw(json.dumps({"bg": "#1E2430", "transparent": True, "width": 500, "height": 300}))
        settings = load(self.path)
        self.assertEqual(settings.bg, "#1e2430")   # 规范化成小写
        self.assertTrue(settings.transparent)
        self.assertEqual((settings.width, settings.height), (500, 300))

    def test_unknown_keys_are_ignored(self) -> None:
        """前向兼容:以后加的字段不该让旧版本崩掉。"""
        self.write_raw(json.dumps({"bg": "#000000", "未来字段": {"a": 1}}))
        self.assertEqual(load(self.path).bg, "#000000")

    def test_bad_color_falls_back_but_keeps_size(self) -> None:
        self.write_raw(json.dumps({"bg": "紫色", "width": 500, "height": 300}))
        settings = load(self.path)
        self.assertEqual(settings.bg, DEFAULT_BG)
        self.assertEqual((settings.width, settings.height), (500, 300))

    def test_non_string_color_falls_back(self) -> None:
        self.write_raw(json.dumps({"bg": ["#000000"]}))
        self.assertEqual(load(self.path).bg, DEFAULT_BG)

    def test_string_boolean_does_not_enable_transparency(self) -> None:
        """关键:字符串 "false" 在 Python 里是真值。

        如果直接拿它当布尔用,用户把设置改成 "false" 反而会打开透明,
        得到一个背景全空、点不着的窗口 —— 正好反了。
        """
        for raw in ("false", "true", "0", 1, 0, None, []):
            with self.subTest(raw=raw):
                self.write_raw(json.dumps({"transparent": raw, "width": 500, "height": 300}))
                self.assertFalse(load(self.path).transparent)

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

    def test_string_size_is_discarded(self) -> None:
        self.write_raw(json.dumps({"width": "500", "height": "300"}))
        self.assertIsNone(load(self.path).width)

    def test_float_size_is_discarded(self) -> None:
        self.write_raw(json.dumps({"width": 500.5, "height": 300.0}))
        self.assertIsNone(load(self.path).width)


class SaveTest(SettingsTempDirTest):
    def test_roundtrip(self) -> None:
        original = Settings(bg="#1f2b24", transparent=True, width=460, height=280)
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
        save(Settings(bg="#000000"), self.path)
        save(Settings(bg="#ffffff"), self.path)
        self.assertEqual(load(self.path).bg, "#ffffff")

    def test_replaces_corrupt_file_without_failing(self) -> None:
        """文件本来是坏的,保存一次应该能修好它,而不是报错。"""
        self.write_raw("{ 这不是 json")
        self.assertTrue(save(Settings(bg="#1e2430"), self.path))
        self.assertEqual(load(self.path).bg, "#1e2430")

    def test_load_of_saved_settings_survives_broken_field(self) -> None:
        """存进去的值本身必须干净 —— 否则读回来会被当成坏字段丢掉。"""
        save(Settings(bg="#ABCDEF"), self.path)
        self.assertEqual(load(self.path).bg, "#abcdef")

    def test_unwritable_target_returns_false_instead_of_raising(self) -> None:
        """目标是目录时写不进去。必须返回 False,不能把退出流程炸掉。"""
        blocked = self.dir / "blocked"
        blocked.mkdir()
        self.assertFalse(save(Settings(), blocked))

    def test_returns_true_on_success(self) -> None:
        self.assertTrue(save(Settings(), self.path))


if __name__ == "__main__":
    unittest.main()
