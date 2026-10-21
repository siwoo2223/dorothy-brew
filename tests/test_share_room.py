from receivables.kakao_pc import pick_share_room


def test_partial_ocr_room_row():
    texts = ["람다스마 1회 1777", "사진 15장을 보냈습니다.", "화이", "0"]
    assert pick_share_room("매일 실랑다스마 1회", texts) == 0


def test_two_rooms_is_ambiguous():
    assert pick_share_room("매일 실랑다스마 1회", ["람다스마 1회 1777", "다스마 2회 300"]) is None


def test_unrelated_room():
    assert pick_share_room("매일 실랑다스마 1회", ["MANILA OFFICE 5"]) is None
