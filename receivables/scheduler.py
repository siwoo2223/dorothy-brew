"""예약 발송: 발송 설정을 저장하고 Windows 작업 스케줄러에 등록한다.

schedules/<예약ID>/job.json   설정(문구·열 지정·발송 방식·예약 시간)
schedules/<예약ID>/원장.xlsx   (선택) 예약할 때 올린 엑셀 사본
schedules/<예약ID>/실행기록.txt 예약이 실행될 때마다 결과를 덧붙임

예약 시간이 되면 Windows 가 `python run_schedule.py <예약ID>` 를 실행한다.
화면(브라우저)이 꺼져 있어도 되지만, PC 는 켜져 있고 로그인돼 있어야 한다.
"""
from __future__ import annotations

import datetime as dt
import json
import secrets
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .runner import Job

ROOT = Path(__file__).resolve().parent.parent
SCHEDULE_DIR = ROOT / "schedules"
TASK_FOLDER = "\\KFLogistics"
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
REPEATS = ("once", "daily", "weekly", "monthly")


@dataclass
class Schedule:
    repeat: str = "once"  # once | daily | weekly | monthly
    at: str = ""  # 처음 실행 일시 "YYYY-MM-DDTHH:MM" (once 는 이 시각 한 번, 나머지는 이 시각의 '시:분' 사용)
    weekdays: list[int] = field(default_factory=list)  # weekly: 0=월 … 6=일
    day: int = 1  # monthly: 매월 며칠

    def describe(self) -> str:
        when = dt.datetime.fromisoformat(self.at)
        hm = when.strftime("%H:%M")
        if self.repeat == "once":
            return when.strftime("%Y-%m-%d %H:%M") + " 한 번"
        if self.repeat == "daily":
            return f"매일 {hm}"
        if self.repeat == "weekly":
            return "매주 " + ",".join(WEEKDAYS[d] for d in sorted(self.weekdays)) + f" {hm}"
        return f"매월 {self.day}일 {hm}"

    def validate(self, now: dt.datetime | None = None) -> None:
        if self.repeat not in REPEATS:
            raise ValueError("반복 방식이 올바르지 않습니다.")
        when = dt.datetime.fromisoformat(self.at)
        if self.repeat == "once" and when <= (now or dt.datetime.now()):
            raise ValueError("예약 시간이 이미 지났습니다. 앞으로의 시간을 골라 주세요.")
        if self.repeat == "weekly" and not self.weekdays:
            raise ValueError("요일을 하나 이상 골라 주세요.")
        if self.repeat == "monthly" and not 1 <= self.day <= 31:
            raise ValueError("날짜는 1~31 사이로 골라 주세요.")


@dataclass
class Entry:
    id: str
    name: str
    schedule: Schedule
    job: Job
    created: str = ""

    @property
    def folder(self) -> Path:
        return SCHEDULE_DIR / self.id

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "created": self.created,
                "schedule": asdict(self.schedule), "job": self.job.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        return cls(d["id"], d.get("name", ""), Schedule(**d["schedule"]), Job.from_dict(d["job"]), d.get("created", ""))


# ───────────────────────── 저장 / 불러오기 ─────────────────────────
def new_id(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


def save(entry: Entry, ledger_bytes: bytes | None = None, ledger_name: str = "원장.xlsx",
         base: Path | None = None) -> Entry:
    """설정을 저장한다. ledger_bytes 가 있으면 엑셀 사본을 저장해 그것을 쓴다. 첨부도 사본을 둔다."""
    folder = (base or SCHEDULE_DIR) / entry.id
    folder.mkdir(parents=True, exist_ok=True)
    if ledger_bytes is not None:
        path = folder / ("원장" + (Path(ledger_name).suffix or ".xlsx"))
        path.write_bytes(ledger_bytes)
        entry.job.ledger = str(path)
    copied = []
    for a in entry.job.attachments:
        src = Path(a)
        if src.is_file() and folder not in src.parents:
            dst = folder / "첨부" / src.name
            dst.parent.mkdir(exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(str(dst))
        else:
            copied.append(a)
    entry.job.attachments = copied
    entry.created = entry.created or dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    (folder / "job.json").write_text(json.dumps(entry.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def load(entry_id: str, base: Path | None = None) -> Entry:
    path = (base or SCHEDULE_DIR) / entry_id / "job.json"
    return Entry.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_entries(base: Path | None = None) -> list[Entry]:
    folder = base or SCHEDULE_DIR
    out = []
    for p in sorted(folder.glob("*/job.json")):
        try:
            out.append(Entry.from_dict(json.loads(p.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def remove(entry_id: str, base: Path | None = None) -> None:
    try:
        unregister(entry_id)
    except Exception:
        pass
    shutil.rmtree((base or SCHEDULE_DIR) / entry_id, ignore_errors=True)


def append_run_log(entry_id: str, text: str, base: Path | None = None) -> None:
    path = (base or SCHEDULE_DIR) / entry_id / "실행기록.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")


# ───────────────────────── Windows 작업 스케줄러 ─────────────────────────
def trigger_spec(s: Schedule, now: dt.datetime | None = None) -> dict:
    """작업 스케줄러 트리거 값 (테스트하기 쉽게 따로 계산)."""
    now = now or dt.datetime.now()
    when = dt.datetime.fromisoformat(s.at)
    if s.repeat != "once":  # 반복은 '오늘의 그 시각'부터 시작
        when = now.replace(hour=when.hour, minute=when.minute, second=0, microsecond=0)
    spec = {"start": when.strftime("%Y-%m-%dT%H:%M:%S")}
    if s.repeat == "once":
        spec["type"] = 1  # TASK_TRIGGER_TIME
    elif s.repeat == "daily":
        spec.update(type=2, days_interval=1)
    elif s.repeat == "weekly":
        # 작업 스케줄러 요일 비트: 일=1, 월=2, 화=4, … 토=64
        spec.update(type=3, weeks_interval=1, days_of_week=sum(1 << ((d + 1) % 7) for d in set(s.weekdays)))
    else:
        spec.update(type=4, days_of_month=1 << (s.day - 1), months_of_year=0xFFF)
    return spec


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("예약 발송 등록은 Windows 에서만 됩니다.")


def _service():
    import win32com.client

    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    return svc


def _folder(svc, create: bool = True):
    root = svc.GetFolder("\\")
    try:
        return root.GetFolder(TASK_FOLDER)
    except Exception:
        if not create:
            raise
        return root.CreateFolder(TASK_FOLDER)


def register(entry: Entry) -> None:
    """Windows 작업 스케줄러에 등록(같은 ID 면 덮어씀)."""
    _require_windows()
    spec = trigger_spec(entry.schedule)
    svc = _service()
    td = svc.NewTask(0)
    td.RegistrationInfo.Description = f"KF로지스틱 예약 발송: {entry.name} ({entry.schedule.describe()})"
    td.Settings.Enabled = True
    td.Settings.StartWhenAvailable = False  # 꺼져 있던 시간의 발송을 나중에 몰아서 하지 않음
    td.Settings.DisallowStartIfOnBatteries = False
    td.Settings.StopIfGoingOnBatteries = False
    td.Settings.ExecutionTimeLimit = "PT8H"
    td.Principal.LogonType = 3  # 로그인한 사용자 화면에서 실행 (카카오톡 조작에 필요)

    trig = td.Triggers.Create(spec["type"])
    trig.StartBoundary = spec["start"]
    if spec["type"] == 2:
        trig.DaysInterval = spec["days_interval"]
    elif spec["type"] == 3:
        trig.WeeksInterval = spec["weeks_interval"]
        trig.DaysOfWeek = spec["days_of_week"]
    elif spec["type"] == 4:
        trig.DaysOfMonth = spec["days_of_month"]
        trig.MonthsOfYear = spec["months_of_year"]

    action = td.Actions.Create(0)  # 프로그램 실행
    action.Path = str(Path(sys.executable).with_name("python.exe"))
    action.Arguments = f'"{ROOT / "run_schedule.py"}" {entry.id}'
    action.WorkingDirectory = str(ROOT)
    _folder(svc).RegisterTaskDefinition(entry.id, td, 6, None, None, 3)  # 6=만들기/고치기, 3=로그인 사용자


def unregister(entry_id: str) -> None:
    _require_windows()
    _folder(_service(), create=False).DeleteTask(entry_id, 0)


def task_status(entry_id: str) -> dict:
    """다음 실행·마지막 실행 시각. 등록이 안 돼 있으면 빈 값."""
    try:
        _require_windows()
        task = _folder(_service(), create=False).GetTask(entry_id)
    except Exception:
        return {}

    def fmt(v) -> str:
        try:
            t = dt.datetime(v.year, v.month, v.day, v.hour, v.minute)
            return "" if t.year < 2000 else t.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    return {"next": fmt(task.NextRunTime), "last": fmt(task.LastRunTime), "enabled": bool(task.Enabled)}
