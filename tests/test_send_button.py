from receivables.kakao_names import OcrLine
from receivables.kakao_pc import find_send_button


def _ln(text, y):
    return OcrLine(text=text, x=100, y=y, w=40, h=14)


def test_picks_counted_bottom_button_not_title():
    lines = [_ln("파일 전송", 30), _ln("전송", 30), _ln("01.jpg", 80), _ln("15개 전송", 320)]
    assert find_send_button(lines, 0, 360).text == "15개 전송"


def test_skips_title_when_only_plain_words():
    lines = [_ln("전송", 30), _ln("01.jpg", 80), _ln("전송", 320)]
    assert find_send_button(lines, 0, 360).y == 320


def test_none_when_only_title():
    assert find_send_button([_ln("전송", 30)], 0, 360) is None
