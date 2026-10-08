"""사진 여러 장을 세로로 이어 붙여 한 장으로 만든다 (카톡 붙여넣기용).

2026-10-08 요청 - "여기있는 전체 복사 기능을 가지고 와서 넣으면 되지 않을까?": 사이트 항차 입력 화면의
'전체 복사'(voyages/air-entry.php copyAllModalPhotos)는 "카카오톡은 붙여넣기 한 번에 이미지 한 장만
받는다"는 이유로 사진들을 세로로 이어 붙인 한 장을 클립보드에 넣는다. 발송 도우미도 같은 방식으로
보낸다(사진 파일 여러 개를 붙여넣던 방식은 카톡에서 사진이 안 가는 경우가 있었다).
"""
from __future__ import annotations

from pathlib import Path

GAP = 14          # 사진 사이 흰 여백 (사이트 '전체 복사'와 같음)
MAX_WIDTH = 1280  # 너무 크면 카톡이 많이 줄여 버리므로 폭을 맞춘다
PER_IMAGE = 4     # 한 장에 너무 많이 붙이면 세로로 길어져 알아보기 어려워 4장씩 나눈다

_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")


def is_image(path: str | Path) -> bool:
    """파일 내용이 사진(jpg/png/gif/webp)인지. 이름이 .jpg 여도 내용이 웹페이지면 False."""
    try:
        head = Path(path).read_bytes()[:12]
    except OSError:
        return False
    return head.startswith(_MAGIC) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")


def merge_photos(paths: list[str], out_dir: Path, stem: str = "사진") -> list[Path]:
    """사진들을 PER_IMAGE 장씩 세로로 이어 붙인 jpg 파일 목록. Pillow 가 없으면 ImportError."""
    from PIL import Image, ImageOps

    out_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for n, start in enumerate(range(0, len(paths), PER_IMAGE), start=1):
        imgs = []
        for p in paths[start:start + PER_IMAGE]:
            im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")  # 휴대폰 사진 회전 정보 반영
            if im.width > MAX_WIDTH:
                im = im.resize((MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)))
            imgs.append(im)
        width = max(im.width for im in imgs)
        height = sum(im.height for im in imgs) + GAP * (len(imgs) - 1)
        canvas = Image.new("RGB", (width, height), "white")
        y = 0
        for im in imgs:
            canvas.paste(im, ((width - im.width) // 2, y))
            y += im.height + GAP
        path = out_dir / f"{stem}_{n}.jpg"
        canvas.save(path, "JPEG", quality=88)
        out.append(path)
    return out
