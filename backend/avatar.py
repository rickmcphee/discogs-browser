import io

from PIL import Image, ImageOps

import config

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
AVATAR_SIZE = 512
AVATAR_FILE = config.CONFIG_DIR / "avatar.png"


class InvalidAvatarError(Exception):
    pass


def save_avatar(data: bytes) -> None:
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
    AVATAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    image.save(AVATAR_FILE, format="PNG")


def delete_avatar() -> None:
    AVATAR_FILE.unlink(missing_ok=True)
