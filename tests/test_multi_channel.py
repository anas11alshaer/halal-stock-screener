"""Tests for multi-channel supervision in the service entry point."""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from bot import run_channels


class BlockingChannel:
    def __init__(self):
        self.release = threading.Event()

    def run(self):
        self.release.wait(timeout=5)


class CrashingChannel:
    def run(self):
        raise RuntimeError("boom")


class ReturningChannel:
    def run(self):
        return None


def test_run_channels_exits_nonzero_when_a_channel_crashes():
    healthy = BlockingChannel()
    with pytest.raises(SystemExit) as excinfo:
        run_channels([healthy, CrashingChannel()])
    healthy.release.set()
    assert excinfo.value.code == 1


def test_run_channels_exits_nonzero_when_a_channel_returns():
    healthy = BlockingChannel()
    with pytest.raises(SystemExit) as excinfo:
        run_channels([healthy, ReturningChannel()])
    healthy.release.set()
    assert excinfo.value.code == 1
