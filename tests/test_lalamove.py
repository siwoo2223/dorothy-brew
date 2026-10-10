"""라라무브 배송 완료 확인 테스트: 실제 화면 글자(2026-10-10 캡처) 기준."""
from receivables.lalamove import page_state
from receivables.site_agent import Agent

DONE = """Earn exclusive rewards. Sign Up Now!
Drop-off Point
Completed Yesterday, 4:02 PM
Order has been paid.
How was Leonard Cortez's service?
Completed Yesterday, 4:02 PM"""

ONGOING = """Earn exclusive rewards. Sign Up Now!
English
Drop-off Point
Heading to 7 Cario Drive
Order has been paid.
Rate the driver after delivery is completed.
Eric Rio
97***E · Motorcycle"""


def test_page_state_reads_completed_and_ongoing():
    assert page_state(DONE) == "completed"
    assert page_state(ONGOING) == "ongoing"  # 'delivery is completed.' 문구에 속지 않음
    assert page_state("Drop-off Point\nCancelled") == "cancelled"
    assert page_state("Link expired") == "unknown"


class FakeAPI:
    def __init__(self):
        self.done = []

    def lalamove_list(self):
        return [{"id": 1, "tracking_no": "DRY29-001", "link": "L1"}, {"id": 2, "tracking_no": "DRY29-002", "link": "L2"},
                {"id": 3, "tracking_no": "DRY29-003", "link": "L3"}]

    def lalamove_done(self, sid, link):
        self.done.append((sid, link))
        return True


def test_check_lalamove_marks_only_completed(tmp_path):
    api = FakeAPI()
    agent = Agent(api, lambda s: None, log=lambda s: None, sleep=lambda s: None, download_dir=tmp_path)
    n = agent.check_lalamove(lambda links: {"L1": "completed", "L2": "ongoing", "L3": "unknown"})
    assert n == 1 and api.done == [(1, "L1")]
