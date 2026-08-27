"""저장소가 클론했을 때 실제로 동작하는지.

세션 내내 테스트는 통과하는데 저장소는 망가져 있었다. 이유는 단순하다.
테스트는 **디스크의 파일**을 보고, 저장소는 **git이 추적하는 파일**만 담는다.
둘이 어긋나면 로컬에서는 전부 초록불인데 클론하면 임포트조차 안 된다.

실제로 .gitignore의 `data/` 한 줄이 src/dorothy/data/ 패키지를 통째로
삼켰다. 앞에 /가 없으면 모든 깊이의 같은 이름 디렉터리에 걸린다.
클론하면 ModuleNotFoundError: No module named 'dorothy.data'였다.

이 테스트는 그 간극을 막는다.
"""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def is_git_repo() -> bool:
    try:
        git("rev-parse", "--git-dir")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


@unittest.skipUnless(is_git_repo(), "git 저장소가 아닙니다")
class TrackedSourceTests(unittest.TestCase):
    def tracked(self) -> set[Path]:
        return {ROOT / line for line in git("ls-files").splitlines() if line}

    def on_disk(self, folder: str) -> set[Path]:
        return {
            p for p in (ROOT / folder).rglob("*.py")
            if "__pycache__" not in p.parts
        }

    def test_every_source_file_is_tracked(self):
        """src/ 아래 .py가 하나라도 빠지면 클론한 사람은 임포트조차 못 한다."""
        missing = sorted(self.on_disk("src") - self.tracked())
        self.assertFalse(
            missing,
            "git이 추적하지 않는 소스 파일이 있습니다. .gitignore 규칙이"
            " 삼키고 있지 않은지 확인하세요:\n  "
            + "\n  ".join(str(p.relative_to(ROOT)) for p in missing),
        )

    def test_every_test_file_is_tracked(self):
        missing = sorted(self.on_disk("tests") - self.tracked())
        self.assertFalse(
            missing,
            "추적되지 않는 테스트 파일:\n  "
            + "\n  ".join(str(p.relative_to(ROOT)) for p in missing),
        )

    def test_every_package_has_an_init(self):
        """__init__.py가 빠진 디렉터리는 설치 시 패키지로 잡히지 않는다."""
        for path in (ROOT / "src").rglob("*"):
            if not path.is_dir() or "__pycache__" in path.parts:
                continue
            if not any(p.suffix == ".py" for p in path.iterdir() if p.is_file()):
                continue
            self.assertTrue(
                (path / "__init__.py").exists(),
                f"{path.relative_to(ROOT)}에 __init__.py가 없습니다",
            )

    def test_runtime_ignore_rules_are_anchored(self):
        """`data/`처럼 앞에 /가 없는 규칙은 모든 깊이에 걸린다.

        런타임 산출물용 규칙은 반드시 /로 시작해야 한다.
        이 한 줄 때문에 소스 패키지가 통째로 사라진 적이 있다.
        """
        risky = {"data", "logs", "output", "build", "dist", "tmp", "cache"}
        offenders = []
        for line in (ROOT / ".gitignore").read_text().splitlines():
            rule = line.split("#")[0].strip()
            if not rule or rule.startswith("/") or rule.startswith("!"):
                continue
            if rule.rstrip("/") in risky:
                offenders.append(rule)
        self.assertFalse(
            offenders,
            f"루트에만 걸려야 할 규칙에 /가 없습니다: {offenders}."
            " 이런 규칙은 src/dorothy/data/ 같은 하위 디렉터리도 삼킵니다.",
        )

    def test_secrets_stay_ignored(self):
        """고치다가 반대로 비밀값이 새면 훨씬 나쁘다."""
        for path in (".env", "config/config.yaml", "data/dorothy.db"):
            result = subprocess.run(
                ["git", "check-ignore", "-q", path],
                cwd=ROOT, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, f"{path}가 더 이상 무시되지 않습니다")


if __name__ == "__main__":
    unittest.main()
