"""카톡 '전송 실패'(↻ ✕) 표시 찾기 테스트 - 2026-10-12 박대희 건."""
from PIL import Image, ImageDraw

from receivables.kakao_pc import has_fail_marks


def chat_bg():
    return Image.new("RGB", (380, 300), (186, 206, 224))  # 카톡 채팅방 하늘색 바탕


def test_detects_retry_cancel_pill():
    img = chat_bg()
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 150, 110, 175), 6, fill=(255, 255, 255))  # 흰 상자
    d.ellipse((66, 156, 78, 168), outline=(90, 90, 90), width=2)     # ↻ 회색
    d.line((90, 157, 100, 167), fill=(240, 80, 50), width=2)         # ✕ 빨강
    d.line((100, 157, 90, 167), fill=(240, 80, 50), width=2)
    d.rounded_rectangle((120, 40, 360, 175), 8, fill=(255, 235, 0))  # 노란 말풍선
    assert has_fail_marks(img)


def test_ignores_red_emoji_in_yellow_bubble():
    img = chat_bg()
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((120, 40, 360, 175), 8, fill=(255, 235, 0))
    d.ellipse((140, 60, 152, 72), fill=(230, 40, 40))  # 🔴 이모지
    d.text((160, 60), "OR", fill=(60, 60, 60))
    assert not has_fail_marks(img)
