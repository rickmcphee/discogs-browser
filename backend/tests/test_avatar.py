import io

import pytest
from PIL import Image

import avatar


@pytest.fixture
def avatar_file(tmp_path, monkeypatch):
    path = tmp_path / "avatar.png"
    monkeypatch.setattr(avatar, "AVATAR_FILE", path)
    return path


def _png_bytes(size=(800, 400), color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def test_save_avatar_writes_square_png(avatar_file):
    avatar.save_avatar(_png_bytes())
    assert avatar_file.exists()
    with Image.open(avatar_file) as img:
        assert img.format == "PNG"
        assert img.size == (avatar.AVATAR_SIZE, avatar.AVATAR_SIZE)


def test_save_avatar_overwrites_existing_file(avatar_file):
    avatar.save_avatar(_png_bytes(color=(255, 0, 0)))
    avatar.save_avatar(_png_bytes(color=(0, 255, 0)))
    with Image.open(avatar_file) as img:
        assert img.getpixel((0, 0)) == (0, 255, 0)


def test_save_avatar_rejects_oversized_file(avatar_file):
    oversized = b"\x00" * (avatar.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(avatar.InvalidAvatarError, match="too large"):
        avatar.save_avatar(oversized)
    assert not avatar_file.exists()


def test_save_avatar_rejects_non_image_bytes(avatar_file):
    with pytest.raises(avatar.InvalidAvatarError, match="valid image"):
        avatar.save_avatar(b"not an image")
    assert not avatar_file.exists()


def test_delete_avatar_removes_file(avatar_file):
    avatar.save_avatar(_png_bytes())
    avatar.delete_avatar()
    assert not avatar_file.exists()


def test_delete_avatar_is_noop_when_missing(avatar_file):
    avatar.delete_avatar()
    assert not avatar_file.exists()


def _sideways_png_bytes():
    # A landscape image (100x50) that is actually a portrait photo (50x100)
    # stored rotated, with an EXIF orientation tag telling a viewer to rotate
    # it back — the way real phone-camera photos are commonly stored.
    upright = Image.new("RGB", (100, 50), color=(255, 0, 0))
    for x in range(100):
        for y in range(25, 50):
            upright.putpixel((x, y), (0, 0, 255))
    raw = upright.transpose(Image.Transpose.ROTATE_90)
    exif = raw.getexif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW to display correctly
    buf = io.BytesIO()
    raw.save(buf, format="PNG", exif=exif)
    return buf.getvalue()


def test_save_avatar_applies_exif_orientation(avatar_file):
    avatar.save_avatar(_sideways_png_bytes())
    with Image.open(avatar_file) as img:
        rgb = img.convert("RGB")
        assert rgb.getpixel((256, 20)) == (255, 0, 0)
        assert rgb.getpixel((256, 490)) == (0, 0, 255)
