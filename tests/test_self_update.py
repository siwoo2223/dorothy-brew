"""발송 도우미 자동 업데이트(self_update) 테스트: 실제 GitHub 대신 만든 zip 으로."""
import io
import zipfile

from receivables import self_update


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr("dorothy-brew-branch/" + name, data)
    return buf.getvalue()


def test_apply_zip_overwrites_changed_files_and_keeps_local_ones(tmp_path):
    (tmp_path / "agent.py").write_text("old")
    (tmp_path / "same.txt").write_text("same")
    (tmp_path / ".env").write_text("KF_AGENT_TOKEN=secret")
    (tmp_path / "발송도우미.bat").write_text("running bat")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "agent.log").write_text("log")
    data = _zip({"agent.py": "new", "same.txt": "same", ".env": "KF_AGENT_TOKEN=", "발송도우미.bat": "new bat",
                 "logs/agent.log": "x", "receivables/new_mod.py": "print(1)"})
    changed = self_update.apply_zip(data, tmp_path)
    assert sorted(changed) == ["agent.py", "receivables/new_mod.py"]
    assert (tmp_path / "agent.py").read_text() == "new"
    assert (tmp_path / ".env").read_text() == "KF_AGENT_TOKEN=secret"  # 연결 키는 그대로
    assert (tmp_path / "발송도우미.bat").read_text() == "running bat"  # 실행 중인 배치 파일은 그대로
    assert (tmp_path / "logs" / "agent.log").read_text() == "log"


def test_update_skips_when_version_same(tmp_path, monkeypatch):
    (tmp_path / ".version").write_text("abc1234def")
    monkeypatch.setattr(self_update.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("받으면 안 됨")))
    assert self_update.update(tmp_path, lambda s: None, latest="abc1234def") is False


def test_update_downloads_and_records_version(tmp_path, monkeypatch):
    (tmp_path / "agent.py").write_text("old")
    (tmp_path / "requirements.txt").write_text("requests")

    class R:
        content = _zip({"agent.py": "new", "requirements.txt": "requests"})

        def raise_for_status(self):
            pass

    monkeypatch.setattr(self_update.requests, "get", lambda *a, **k: R())
    logs = []
    assert self_update.update(tmp_path, logs.append, latest="feedbeef12") is True
    assert (tmp_path / ".version").read_text() == "feedbeef12"
    assert (tmp_path / "agent.py").read_text() == "new"
