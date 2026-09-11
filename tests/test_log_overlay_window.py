"""日志浮窗刷新逻辑回归测试：超过 _MAX_LINES 行后不得停刷。

历史 bug：_refresh 用“已插入计数”当下标增量渲染，而 push 把 _LINES 裁成
滑动窗口（满 _MAX_LINES 后每进一行丢一行、长度恒为 _MAX_LINES），一旦超过
500 行，新行永远落在已渲染区间内且重建条件永不触发 → 浮窗停刷。
修复改为按“窗口内容指纹”整体重建。本测试验证指纹在每个滑动步都变化，
从而基于指纹的重建不会漏掉任何新行。
"""

import unittest
from collections import deque

import log_overlay


class FakeDeque(deque):
    """复刻 push 的滑动窗口：满 _MAX_LINES 后从头部丢最旧行。"""

    def __init__(self, max_lines):
        super().__init__(maxlen=max_lines * 2)
        self._max_lines = max_lines

    def push_line(self, line):
        self.append((line, False))
        while len(self) > self._max_lines:
            self.popleft()


class WindowFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.max_lines = log_overlay._MAX_LINES

    def test_fingerprint_changes_every_step_past_the_cap(self):
        # 压过 _MAX_LINES：每一步指纹都必须变化，否则基于指纹的重建会漏行。
        window = FakeDeque(self.max_lines)
        fp = log_overlay._window_fingerprint(list(window))
        for i in range(self.max_lines * 2):
            window.push_line(f"日志行 {i}")
            new_fp = log_overlay._window_fingerprint(list(window))
            self.assertNotEqual(
                fp, new_fp,
                f"第 {i} 行入队后指纹未变，浮窗会漏掉该行")
            fp = new_fp

    def test_cap_holds_deque_at_max_lines(self):
        window = FakeDeque(self.max_lines)
        for i in range(self.max_lines * 2):
            window.push_line(f"日志行 {i}")
        self.assertEqual(self.max_lines, len(window))
        # 滑动窗口保留最新行：头 = 第 500 行(最旧幸存)，尾 = 最新推入行。
        self.assertEqual(f"日志行 {self.max_lines}", window[0][0])
        self.assertEqual(
            f"日志行 {self.max_lines * 2 - 1}", window[-1][0])

    def test_fingerprint_identical_for_same_content(self):
        lines = [("a", False), ("b", False)]
        self.assertEqual(
            log_overlay._window_fingerprint(lines),
            log_overlay._window_fingerprint(list(lines)))


if __name__ == "__main__":
    unittest.main()
