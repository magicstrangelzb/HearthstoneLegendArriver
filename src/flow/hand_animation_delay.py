"""Delay hand clicks while newly entered cards finish animating."""

import time


class HandAnimationDelay:
    def __init__(self, seconds_per_card=1.0, clock=time.monotonic):
        self.seconds_per_card = seconds_per_card
        self.clock = clock
        self._last_entry_count = None
        self._ready_at = 0.0

    def observe(self, entry_count):
        if self._last_entry_count is None:
            self._last_entry_count = entry_count
            return 0
        if entry_count < self._last_entry_count:
            self._last_entry_count = entry_count
            self._ready_at = 0.0
            return 0

        new_count = entry_count - self._last_entry_count
        self._last_entry_count = entry_count
        if new_count:
            self._ready_at = (
                max(self.clock(), self._ready_at)
                + self.seconds_per_card * new_count)
        return new_count

    def remaining(self):
        return max(0.0, self._ready_at - self.clock())
