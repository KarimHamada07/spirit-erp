"""تجهيز صور الموقع وقت الرفع: بتتلف صح (EXIF)، وبتتصغر لصورة كبيرة (1600) ومصغرة (600) JPEG، عشان
الموقع يفتح بسرعة على موبايل وإنترنت ضعيف، ومن غير ما الموظف يحتاج يصغّر الصور بنفسه."""
import io
import os
import uuid

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

MAX_UPLOAD = 15 * 1024 * 1024


def _jpeg(img, side):
    from PIL import Image
    im = img.copy()
    im.thumbnail((side, side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=84, optimize=True, progressive=True)
    return buf.getvalue()


def process_image(field_file, max_side=1600, thumb_side=600):
    from PIL import Image, ImageOps, UnidentifiedImageError
    f = field_file.file if hasattr(field_file, "file") else field_file
    if getattr(f, "size", 0) and f.size > MAX_UPLOAD:
        raise ValidationError("الصورة كبيرة قوي (أكتر من 15 ميجا)")
    try:
        f.seek(0)
        img = Image.open(f)
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError):
        raise ValidationError("الملف ده مش صورة")
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    name = uuid.uuid4().hex[:16]
    return (ContentFile(_jpeg(img, max_side), name=f"{name}.jpg"),
            ContentFile(_jpeg(img, thumb_side), name=f"{name}-t.jpg"))


def delete_files(*fields):
    for f in fields:
        try:
            if f and f.name and os.path.exists(f.path):
                os.remove(f.path)
        except (ValueError, OSError):
            pass
