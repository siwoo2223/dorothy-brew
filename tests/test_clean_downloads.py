import os
import time

from receivables.site_agent import clean_downloads


def test_removes_sent_and_old_folders(tmp_path):
    sent = tmp_path / "101"
    old = tmp_path / "55"
    fresh = tmp_path / "102"
    for d in (sent, old, fresh):
        d.mkdir()
        (d / "01.jpg").write_bytes(b"x")
    past = time.time() - 3 * 3600
    os.utime(old, (past, past))
    clean_downloads(tmp_path, sent)
    assert not sent.exists() and not old.exists() and fresh.exists()


def test_missing_dir_is_fine(tmp_path):
    assert clean_downloads(tmp_path / "none", tmp_path / "none" / "1") == 0
