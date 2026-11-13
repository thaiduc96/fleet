"""fleetd exits when Fleet.app (its parent) is gone: the waiting loop."""

from fleetd.main import wait_for_parent_exit


class FakeParent:
    """getppid() that reports `parent` for `alive_polls` calls, then launchd (1)."""

    def __init__(self, parent: int, alive_polls: int) -> None:
        self.parent = parent
        self.left = alive_polls
        self.sleeps: list[float] = []

    def getppid(self) -> int:
        if self.left > 0:
            self.left -= 1
            return self.parent
        return 1

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def test_returns_once_parent_is_gone() -> None:
    fake = FakeParent(4242, alive_polls=3)

    wait_for_parent_exit(4242, getppid=fake.getppid, sleep=fake.sleep, interval=2.0)

    assert fake.sleeps == [2.0, 2.0, 2.0]


def test_returns_immediately_when_started_under_another_parent() -> None:
    fake = FakeParent(4242, alive_polls=0)

    wait_for_parent_exit(4242, getppid=fake.getppid, sleep=fake.sleep)

    assert fake.sleeps == []
