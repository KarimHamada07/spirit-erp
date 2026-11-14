"""تطبيق على الموبايل (PWA): ملف التعريف + Service Worker + صفحة بدون نت"""
from django.http import HttpResponse, JsonResponse
from django.templatetags.static import static
from django.views.decorators.cache import cache_control

SW_VERSION = "spirit-v1"

SERVICE_WORKER = r"""
const CACHE = "%(version)s";
const OFFLINE = "/offline/";
const PRECACHE = [OFFLINE, "%(icon)s", "%(icon192)s"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;               // البيع والحفظ لازم يروحوا للسيرفر على طول
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  // الصفحات: من السيرفر دايماً (بيانات حية)، ولو مفيش نت تظهر صفحة "مفيش اتصال"
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
    return;
  }
  // الملفات الثابتة (CSS/JS/صور): من الكاش وبتتحدث في الخلفية
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(caches.open(CACHE).then((c) => c.match(req).then((hit) => {
      const net = fetch(req).then((res) => { if (res.ok) c.put(req, res.clone()); return res; });
      return hit || net;
    })));
  }
});
"""

OFFLINE_HTML = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SPIRIT — مفيش اتصال</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font-family:Cairo,Tahoma,sans-serif;background:#000;color:#fff;text-align:center;padding:16px}
.b{max-width:420px}img{width:96px;height:96px;border-radius:20px}h1{color:#fe0000;font-size:24px}
button{background:#fe0000;color:#fff;border:0;border-radius:10px;padding:12px 24px;font-size:16px;font-weight:700}
</style></head><body><div class="b"><img src="%(icon)s" alt=""><h1>مفيش اتصال بالإنترنت</h1>
<p>النظام محتاج نت عشان البيانات تفضل مظبوطة (المخزون والخزنة). اتأكد من الاتصال وجرّب تاني.</p>
<button onclick="location.reload()">جرّب تاني</button></div></body></html>"""


@cache_control(max_age=86400, public=True)
def manifest(request):
    return JsonResponse({
        "name": "SPIRIT ERP",
        "short_name": "SPIRIT",
        "description": "نظام SPIRIT للمبيعات والمخزون والخزينة",
        "lang": "ar", "dir": "rtl",
        "start_url": "/dashboard/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#000000",
        "theme_color": "#000000",
        "icons": [
            {"src": static("trade/img/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("trade/img/icon.png"), "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
        ],
        "shortcuts": [
            {"name": "بيع سريع", "url": "/pos/"},
            {"name": "العملاء", "url": "/customers/"},
            {"name": "المخزون", "url": "/inventory/"},
        ],
    }, content_type="application/manifest+json", json_dumps_params={"ensure_ascii": False})


@cache_control(no_cache=True)
def service_worker(request):
    body = SERVICE_WORKER % {"version": SW_VERSION, "icon": static("trade/img/icon.png"),
                             "icon192": static("trade/img/icon-192.png")}
    resp = HttpResponse(body, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    return resp


def offline(request):
    return HttpResponse(OFFLINE_HTML % {"icon": static("trade/img/icon-192.png")})
