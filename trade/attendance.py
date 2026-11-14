"""البصمة: صورة لايف من كاميرا الموبايل + اللوكيشن + التأكد إن الوش ظاهر.

- الصورة بتتاخد من الكاميرا جوه الصفحة (مفيش اختيار ملف من الموبايل)، ومعاها كود مؤقت
  بيتعمل لما الصفحة تفتح ويتستخدم مرة واحدة خلال 3 دقايق، فمينفعش يتبعت طلب قديم أو متجهز.
- السيرفر بيتأكد بنفسه إن فيه وش واضح في الصورة (OpenCV) قبل ما يسجل البصمة.
- البعد عن أقرب مكان شغل بيتحسب، ولو برا المسافة المسموحة البصمة بتتسجل "برا المكان" وبيجيلك تنبيه.
"""
import math
import secrets
import time
from decimal import Decimal

TOKEN_KEY = "attendance_token"
TOKEN_TTL = 180          # ثانية
MAX_ACCURACY_M = 300     # لو دقة اللوكيشن أوحش من كده بنطلب منه يشغّل GPS
MAX_PHOTO_BYTES = 4 * 1024 * 1024
MIN_FACE_RATIO = 0.18    # الوش لازم يكون على الأقل 18% من عرض الصورة (سيلفي مش صورة من بعيد)
REPEAT_SECONDS = 120     # بصمتين ورا بعض في أقل من دقيقتين = غلط


class PunchError(Exception):
    pass


# ---------------------------------------------------------------- الكود المؤقت
def issue_token(session):
    token = secrets.token_urlsafe(16)
    session[TOKEN_KEY] = {"t": token, "at": time.time()}
    return token


def use_token(session, token):
    data = session.pop(TOKEN_KEY, None)
    if not data or not token or not secrets.compare_digest(str(data.get("t")), str(token)):
        raise PunchError("الصفحة دي قديمة. افتح صفحة البصمة من جديد")
    if time.time() - data.get("at", 0) > TOKEN_TTL:
        raise PunchError("الوقت خلص. افتح صفحة البصمة من جديد وصوّر على طول")


# ---------------------------------------------------------------- اللوكيشن
def distance_m(lat1, lng1, lat2, lng2):
    """المسافة بالمتر بين نقطتين (Haversine)"""
    r = 6371000
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(round(2 * r * math.asin(math.sqrt(a))))


def parse_location(lat, lng, accuracy):
    try:
        lat, lng = Decimal(str(lat)), Decimal(str(lng))
        accuracy = float(accuracy or 0)
    except Exception:
        raise PunchError("مقدرناش نجيب مكانك. اسمح للموقع باستخدام اللوكيشن")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        raise PunchError("اللوكيشن مش صحيح")
    if accuracy > MAX_ACCURACY_M:
        raise PunchError(f"اللوكيشن مش دقيق (±{int(accuracy)} متر). شغّل الـ GPS واستنى ثواني وجرّب تاني")
    return lat.quantize(Decimal("0.000001")), lng.quantize(Decimal("0.000001")), int(accuracy)


def nearest_workplace(lat, lng):
    from .models import Workplace
    best = None
    for w in Workplace.objects.filter(is_active=True):
        d = distance_m(lat, lng, w.latitude, w.longitude)
        if best is None or d < best[1]:
            best = (w, d)
    return best  # (workplace, distance) أو None


# ---------------------------------------------------------------- الصورة والوش
_cascade = None


def _detector():
    global _cascade
    if _cascade is None:
        import cv2
        _cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _cascade


def check_photo(data):
    """بيرجع JPEG متصغّر (من غير بيانات EXIF) لو فيه وش واضح، وإلا PunchError"""
    import cv2
    import numpy as np
    if not data:
        raise PunchError("مفيش صورة. صوّر نفسك الأول")
    if len(data) > MAX_PHOTO_BYTES:
        raise PunchError("الصورة كبيرة أوي")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise PunchError("الصورة مش سليمة. صوّر تاني")
    h, w = img.shape[:2]
    scale = min(1.0, 960 / max(h, w))
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    side = int(min(h, w) * MIN_FACE_RATIO)
    faces = _detector().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(side, side))
    if len(faces) == 0:
        raise PunchError("وشك مش ظاهر في الصورة. قرّب الموبايل وخلّي وشك في النص وفي إضاءة كويسة")
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise PunchError("حصلت مشكلة في الصورة. جرّب تاني")
    return buf.tobytes()


# ---------------------------------------------------------------- البصمة
def next_kind(user):
    """لو آخر بصمة النهارده حضور، اللي بعدها انصراف، وإلا حضور"""
    from django.utils import timezone

    from .models import Attendance
    last = (Attendance.objects.filter(user=user, at__date=timezone.localdate())
            .order_by("-at", "-id").first())
    return Attendance.OUT if last and last.kind == Attendance.IN else Attendance.IN


def day_summary(punches):
    """punches لموظف في يوم: أول حضور، آخر انصراف، ساعات الشغل (مجموع الفترات حضور→انصراف)"""
    first_in = next((p for p in punches if p.kind == "in"), None)
    last_out = next((p for p in reversed(punches) if p.kind == "out"), None)
    seconds, opened = 0, None
    for p in punches:
        if p.kind == "in" and opened is None:
            opened = p.at
        elif p.kind == "out" and opened is not None:
            seconds += (p.at - opened).total_seconds()
            opened = None
    return {"first_in": first_in, "last_out": last_out, "hours": round(seconds / 3600, 2),
            "open": opened is not None, "outside": sum(1 for p in punches if not p.inside),
            "count": len(punches)}
