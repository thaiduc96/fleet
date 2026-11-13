from fleetd.models import Battery, SleepHolder
from fleetd.system import parse_assertions, parse_battery, process_label

PMSET_AC = ("Now drawing from 'AC Power'\n"
            " -InternalBattery-0 (id=123)\t80%; AC attached; not charging present: true\n")
PMSET_BATT = ("Now drawing from 'Battery Power'\n"
              " -InternalBattery-0 (id=123)\t55%; discharging; 4:10 remaining present: true\n")

# Shape of `pmset -g assertions` on macOS (captured on the home Mac, 2026-11-13).
ASSERTIONS = """\
Listed by owning process:
   pid 351(powerd): [0x0001467b000194b0] 01:36:43 PreventUserIdleSystemSleep named: "Powerd - Prevent sleep while display is on"
   pid 412(runningboardd): [0x00015d2700018123] 00:00:00 PreventUserIdleSystemSleep named: "osservice<com.apple.nsurlsessiond(501)>"
   pid 74565(caffeinate): [0x00015d0e0001811a] 00:00:31 PreventUserIdleSystemSleep named: "caffeinate command-line tool"
	Details: caffeinate asserting for 300 secs
   pid 54023(caffeinate): [0x00014919000196f0] 01:25:41 PreventUserIdleSystemSleep named: "caffeinate command-line tool"
	Details: caffeinate asserting on behalf of Process ID 54015
   pid 54023(caffeinate): [0x00014919000796f1] 01:25:41 PreventSystemSleep named: "caffeinate command-line tool"
	Details: caffeinate asserting on behalf of Process ID 54015
   pid 900(backupd): [0x0001] 00:12:00 PreventUserIdleSystemSleep named: "Time Machine"
   pid 901(Music): [0x0002] 00:12:00 PreventUserIdleDisplaySleep named: "playing"
"""
OWNERS = {54015: "homecam.supervisor"}


def test_parse_battery_on_ac() -> None:
    result = parse_battery(PMSET_AC)

    assert result == Battery(percent=80, state="AC attached", on_ac=True)


def test_parse_battery_on_battery() -> None:
    result = parse_battery(PMSET_BATT)

    assert result == Battery(percent=55, state="discharging", on_ac=False)


def test_parse_battery_desktop_without_battery() -> None:
    result = parse_battery("Now drawing from 'AC Power'\n")

    assert result == Battery(percent=None, state=None, on_ac=True)


def test_parse_assertions_keeps_real_holders_and_drops_display_and_short_tasks() -> None:
    result = parse_assertions(ASSERTIONS, OWNERS.get, {74565: "claude"}.get)

    assert result == [
        SleepHolder(pid=74565, process="caffeinate", kind="PreventUserIdleSystemSleep", owner="claude"),
        SleepHolder(pid=54023, process="caffeinate", kind="PreventUserIdleSystemSleep",
                    owner="homecam.supervisor"),
        SleepHolder(pid=54023, process="caffeinate", kind="PreventSystemSleep",
                    owner="homecam.supervisor"),
        SleepHolder(pid=900, process="backupd", kind="PreventUserIdleSystemSleep", owner=None),
    ]


def test_parse_assertions_with_only_display_on_is_empty() -> None:
    only_display = ASSERTIONS.splitlines()[1] + "\n"

    result = parse_assertions(only_display, OWNERS.get)

    assert result == []


def test_parse_assertions_of_empty_output_is_empty() -> None:
    result = parse_assertions("", OWNERS.get)

    assert result == []


def test_process_label_uses_python_module_name() -> None:
    result = process_label("/Users/me/homecam/.venv/bin/python -m homecam.supervisor")

    assert result == "homecam.supervisor"


def test_process_label_uses_binary_basename() -> None:
    result = process_label("/Applications/Foo.app/Contents/MacOS/Foo --flag")

    assert result == "Foo"
