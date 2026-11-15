"""يحوّل ملفات الترجمة locale/ar/LC_MESSAGES/*.po لـ .mo (من غير ما نحتاج gettext على السيرفر)"""
import ast
import struct
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


def parse_po(text):
    entries, key, buf, cur = {}, None, {}, None
    for raw in text.splitlines() + [""]:
        line = raw.strip()
        if not line or line.startswith("#"):
            if "msgid" in buf and "msgstr" in buf:
                entries[buf["msgid"]] = buf["msgstr"]
            buf, cur = {}, None
            continue
        if line.startswith("msgid "):
            if "msgid" in buf and "msgstr" in buf:
                entries[buf["msgid"]] = buf["msgstr"]
                buf = {}
            cur, line = "msgid", line[6:]
        elif line.startswith("msgstr "):
            cur, line = "msgstr", line[7:]
        buf[cur] = buf.get(cur, "") + ast.literal_eval(line)
    return entries


def write_mo(entries, path):
    keys = sorted(entries)
    ids = b"".join(k.encode() + b"\0" for k in keys)
    strs = b"".join(entries[k].encode() + b"\0" for k in keys)
    n = len(keys)
    start = 7 * 4 + 16 * n
    offsets, o_ids, o_strs = [], 0, 0
    for k in keys:
        offsets.append((len(k.encode()), start + o_ids))
        o_ids += len(k.encode()) + 1
    for k in keys:
        offsets.append((len(entries[k].encode()), start + len(ids) + o_strs))
        o_strs += len(entries[k].encode()) + 1
    out = struct.pack("Iiiiiii", 0x950412de, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0)
    for length, off in offsets:
        out += struct.pack("ii", length, off)
    Path(path).write_bytes(out + ids + strs)


class Command(BaseCommand):
    help = "يعمل ملف الترجمة العربي django.mo من django.po"

    def handle(self, *args, **opts):
        folder = Path(settings.BASE_DIR) / "locale" / "ar" / "LC_MESSAGES"
        for po in sorted(folder.glob("*.po")):   # django.po و djangojs.po
            entries = parse_po(po.read_text(encoding="utf-8"))
            tmp = po.with_suffix(".mo.tmp")
            write_mo(entries, tmp)
            tmp.replace(po.with_suffix(".mo"))
            self.stdout.write(self.style.SUCCESS(f"تمام: {po.name} — {len(entries) - 1} ترجمة"))
