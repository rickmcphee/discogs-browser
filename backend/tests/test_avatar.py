import io

import pytest
from PIL import Image

import avatar
import db


@pytest.fixture
def user_id(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=42, discogs_username="alice")
        conn.commit()
    yield user["id"]
    with db.get_admin_pool().connection() as conn:
        conn.execute("TRUNCATE users CASCADE")
        conn.commit()


def _png_bytes(size=(800, 400), color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def test_save_avatar_writes_square_png(user_id):
    avatar.save_avatar(user_id, _png_bytes())
    data = avatar.get_avatar(user_id)
    assert data is not None
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "PNG"
        assert img.size == (avatar.AVATAR_SIZE, avatar.AVATAR_SIZE)


def test_save_avatar_overwrites_existing_file(user_id):
    avatar.save_avatar(user_id, _png_bytes(color=(255, 0, 0)))
    avatar.save_avatar(user_id, _png_bytes(color=(0, 255, 0)))
    data = avatar.get_avatar(user_id)
    with Image.open(io.BytesIO(data)) as img:
        assert img.getpixel((0, 0)) == (0, 255, 0)


def test_save_avatar_rejects_oversized_file(user_id):
    oversized = b"\x00" * (avatar.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(avatar.InvalidAvatarError, match="too large"):
        avatar.save_avatar(user_id, oversized)
    assert avatar.get_avatar(user_id) is None


def test_save_avatar_rejects_non_image_bytes(user_id):
    with pytest.raises(avatar.InvalidAvatarError, match="valid image"):
        avatar.save_avatar(user_id, b"not an image")
    assert avatar.get_avatar(user_id) is None


def test_delete_avatar_removes_file(user_id):
    avatar.save_avatar(user_id, _png_bytes())
    avatar.delete_avatar(user_id)
    assert avatar.get_avatar(user_id) is None


def test_delete_avatar_is_noop_when_missing(user_id):
    avatar.delete_avatar(user_id)
    assert avatar.get_avatar(user_id) is None


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


def test_save_avatar_applies_exif_orientation(user_id):
    avatar.save_avatar(user_id, _sideways_png_bytes())
    data = avatar.get_avatar(user_id)
    with Image.open(io.BytesIO(data)) as img:
        rgb = img.convert("RGB")
        assert rgb.getpixel((256, 20)) == (255, 0, 0)
        assert rgb.getpixel((256, 490)) == (0, 0, 255)
