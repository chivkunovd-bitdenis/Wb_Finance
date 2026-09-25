import io

from PIL import Image

from app.services.image_preview import PREVIEW_MAX_SIDE, make_preview_jpeg


def test_preview_is_small_jpeg_with_bounded_side():
    src = io.BytesIO()
    Image.new("RGB", (1024, 1536), (120, 60, 30)).save(src, "PNG")
    out = make_preview_jpeg(src.getvalue())
    assert out is not None
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG"
    assert max(img.size) == PREVIEW_MAX_SIDE
    assert len(out) < len(src.getvalue())


def test_preview_returns_none_for_garbage():
    assert make_preview_jpeg(b"not an image") is None
