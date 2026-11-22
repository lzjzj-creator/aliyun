"""Bounded, byte-preserving validation for optional, client-rasterized photos."""
import base64
import binascii
import hashlib
import warnings
from io import BytesIO

from PIL import Image

PHOTO_PREFIX = "data:image/jpeg;base64,"
MAX_PHOTO_BYTES = 30000
MAX_PHOTO_WIDTH = 240
MAX_PHOTO_HEIGHT = 320
# Preserve only Chromium's exact public canvas sRGB profile, never source metadata.
_CANVAS_SRGB_APP2_SHA256 = "c3bb12de30d7357252ec3a5ec781bd2f8a6dd8c69dd7d3de97bbac262d9e1fd4"


class PhotoInputError(ValueError):
    def __init__(self, reason):
        self.issue = {"field_path": "_meta.photo", "label": "照片", "reason": reason}
        super().__init__(f"_meta.photo（照片）：{reason}")


def _check_jpeg_segments(raw):
    # Pillow's applist misses metadata between progressive scans and after image data.
    malformed = "照片 JPEG 数据不完整或损坏，请重新上传"
    if not raw.startswith(b"\xff\xd8"):
        raise PhotoInputError(malformed)
    pos, in_scan, seen_scan = 2, False, False
    while pos < len(raw):
        if in_scan:
            pos = raw.find(b"\xff", pos)
            if pos < 0:
                break
        elif raw[pos] != 0xFF:
            break
        while pos < len(raw) and raw[pos] == 0xFF:
            pos += 1
        if pos >= len(raw):
            break
        marker = raw[pos]
        pos += 1
        if in_scan and (marker == 0 or 0xD0 <= marker <= 0xD7):
            continue  # Entropy byte stuffing / restart markers.
        in_scan = False
        if marker == 0xD9:
            if seen_scan and pos == len(raw):
                return
            break
        if marker in (0, 1, 0xD8) or 0xD0 <= marker <= 0xD7:
            break
        length = int.from_bytes(raw[pos:pos + 2], "big")
        if length < 2 or pos + length > len(raw):
            break
        body = raw[pos + 2:pos + length]
        if 0xE0 <= marker <= 0xEF or marker == 0xFE:
            jfif = marker == 0xE0 and len(body) == 14 and body.startswith(b"JFIF\0") and body[-2:] == b"\0\0"
            adobe = marker == 0xEE and len(body) == 12 and body.startswith(b"Adobe")
            canvas_srgb = marker == 0xE2 and hashlib.sha256(body).hexdigest() == _CANVAS_SRGB_APP2_SHA256
            if not (jfif or adobe or canvas_srgb):
                raise PhotoInputError("照片含 EXIF 或其他元数据，请重新上传由编辑器处理的照片")
        pos += length
        if marker == 0xDA:
            in_scan = seen_scan = True
    raise PhotoInputError(malformed)


def validate_photo(value):
    # Preserve validated bytes to avoid changing orientation or color from the preview.
    if not isinstance(value, str):
        raise PhotoInputError("照片须为 JPEG 数据字符串；不需要照片时请删除该字段")
    if not value.startswith(PHOTO_PREFIX):
        raise PhotoInputError("照片须为 data:image/jpeg;base64 格式，请重新上传 JPEG 或 PNG 照片")
    # Bound allocation before base64 decoding, not just before pixel decoding.
    if len(value) - len(PHOTO_PREFIX) > 4 * ((MAX_PHOTO_BYTES + 2) // 3):
        raise PhotoInputError("照片不能超过 30000 字节，请压缩后重新上传")
    encoded = value[len(PHOTO_PREFIX):]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise PhotoInputError("照片 base64 数据无效，请重新上传") from None
    if not raw or base64.b64encode(raw).decode("ascii") != encoded:
        raise PhotoInputError("照片 base64 数据须为规范编码，请重新上传")
    if len(raw) > MAX_PHOTO_BYTES:
        raise PhotoInputError("照片不能超过 30000 字节，请压缩后重新上传")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw), formats=["JPEG"]) as image:
                width, height = image.size
                if not (1 <= width <= MAX_PHOTO_WIDTH and 1 <= height <= MAX_PHOTO_HEIGHT):
                    raise PhotoInputError("照片尺寸须为宽 1–240、高 1–320 像素，请缩小后重新上传")
                _check_jpeg_segments(raw)
                image.verify()
            # JPEG verify() alone does not decode the entropy stream.
            with Image.open(BytesIO(raw), formats=["JPEG"]) as image:
                image.load()
    except PhotoInputError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise PhotoInputError("照片不是完整有效的 JPEG，请重新上传") from None
    return value


def photo_from_fields(fields):
    """Validate only the optional photo; leave all other metadata untouched."""
    meta = fields.get("_meta")
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise PhotoInputError("照片所在的 _meta 须为对象，请重新保存简历")
    return validate_photo(meta["photo"]) if "photo" in meta else None
