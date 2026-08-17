import io
from typing import Optional

from PIL import Image, ImageOps

import db

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
AVATAR_SIZE = 512


class InvalidAvatarError(Exception):
    pass


def save_avatar(user_id: int, data: bytes) -> None:
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidAvatarError("File too large")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as e:
        raise InvalidAvatarError("Not a valid image") from e

    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")
    side = min(image.width, image.height)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    image = image.resize((AVATAR_SIZE, AVATAR_SIZE))

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    with db.get_identity_pool().connection() as conn:
        conn.execute("UPDATE users SET avatar_image = %s WHERE id = %s", [buf.getvalue(), user_id])
        conn.commit()


def get_avatar(user_id: int) -> Optional[bytes]:
    with db.get_identity_pool().connection() as conn:
        row = conn.execute("SELECT avatar_image FROM users WHERE id = %s", [user_id]).fetchone()
    return bytes(row["avatar_image"]) if row and row["avatar_image"] is not None else None


def delete_avatar(user_id: int) -> None:
    with db.get_identity_pool().connection() as conn:
        conn.execute("UPDATE users SET avatar_image = NULL WHERE id = %s", [user_id])
        conn.commit()
