from .notify import _current_ip, _current_user, client_ip

# بيتحط في <head>: تعريف التطبيق (PWA) عشان يتثبت على الموبايل كأنه أبلكيشن
HEAD = """<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#000000">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="SPIRIT">
"""

THEME = r"""
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap" rel="stylesheet">
<style id="erp-theme">
:root{--erp-red:#fe0000;--erp-red-d:#cc0000;--erp-black:#000000;--erp-silver:#b4b4b4;
  --erp-card:#fff;--erp-ink:#111;--erp-line:#e2e2e4;--erp-soft:#f1f1f2;--erp-muted:#5a5a5e;
  --bs-body-font-family:Cairo,"Segoe UI",Tahoma,sans-serif;
  --bs-font-sans-serif:Cairo,"Segoe UI",Tahoma,sans-serif}
html,body,button,input,select,textarea{font-family:var(--bs-body-font-family)}
body{font-synthesis-weight:none}
input[type=checkbox],input[type=radio]{accent-color:var(--erp-red)}

/* ===== الوضع الفاتح والداكن (نفس مفتاح Jazzmin: jazzmin-theme-mode) ===== */
[data-bs-theme=light]{--bs-primary:#fe0000;--bs-primary-rgb:215,8,18;--bs-link-color:#fe0000;
  --bs-link-color-rgb:215,8,18;--bs-link-hover-color:#cc0000;--bs-link-hover-color-rgb:168,6,16;
  --bs-body-bg:#fff;--bs-tertiary-bg:#fff;--bs-tertiary-bg-rgb:255,255,255;--bs-body-bg-rgb:255,255,255;--bs-border-color:#e2e2e4}
[data-bs-theme=dark]{--erp-card:#1b1b1d;--erp-ink:#f2f2f2;--erp-line:#34343a;--erp-soft:#26262a;--erp-muted:#a8a8b0;
  --bs-body-bg:#121214;--bs-tertiary-bg:#121214;--bs-tertiary-bg-rgb:18,18,20;--bs-body-bg-rgb:18,18,20;--bs-body-color:#f2f2f2;--bs-border-color:#34343a;
  --bs-link-color:#ff5a63;--bs-link-color-rgb:255,90,99;--bs-link-hover-color:#ff8a91;--bs-link-hover-color-rgb:255,138,145}
.card{background:var(--erp-card);color:var(--erp-ink);border:1px solid var(--erp-line);box-shadow:none;border-radius:12px}
.card-header,.card-footer{background:var(--erp-card);border-color:var(--erp-line)}
.card-outline.card-primary,.card-outline.card-warning,.card-outline.card-success{border-top:3px solid var(--erp-red)}
.app-header{background:var(--erp-card)!important;border-bottom:1px solid var(--erp-line)!important}
.app-header .nav-link{color:var(--erp-ink)}
.app-footer{background:var(--erp-card)!important;color:var(--erp-muted)!important;border-color:var(--erp-line)!important}
.table{--bs-table-bg:transparent;--bs-table-color:var(--erp-ink);--bs-table-border-color:var(--erp-line)}
.form-control,.form-select{background-color:var(--erp-card);color:var(--erp-ink);border-color:var(--erp-line)}
.form-control:focus,.form-select:focus{border-color:var(--erp-red);box-shadow:0 0 0 .2rem rgba(215,8,18,.15)}
.text-muted{color:var(--erp-muted)!important}

/* ===== القائمة الجانبية: أسود والنشط أحمر ===== */
#jazzy-sidebar{background-color:var(--erp-black)!important;--lte-sidebar-hover-bg:rgba(255,255,255,.1);
  --lte-sidebar-menu-active-bg:var(--erp-red);--lte-sidebar-menu-active-color:#fff}
#jazzy-sidebar .nav-link.active{background-color:var(--erp-red)!important;color:#fff!important}
#jazzy-sidebar .sidebar-brand{background:var(--erp-black);border-bottom:1px solid #2a2a2c}
#jazzy-sidebar .brand-image{opacity:1!important;max-height:44px;width:44px;object-fit:contain}
/* اسم المحل الإنجليزي: اتجاه صحيح (يسار لليمين) وينزل سطرين بدل ما يطلع برا القايمة */
#jazzy-sidebar .brand-text{direction:ltr!important;text-align:left;unicode-bidi:isolate;font-size:1rem!important;
  line-height:1.15;white-space:normal!important;overflow-wrap:anywhere}

/* الأرقام (ومنها السالب) بتتعرض صح في الصفحات العربي: -1,250.00 مش 1,250.00- */
.kpi .value,a.stat .n,.total-box{direction:ltr;unicode-bidi:isolate}
.kpi .value{text-align:right}

/* ===== أزرار ===== */
.btn-primary,.btn-danger{--bs-btn-bg:var(--erp-red);--bs-btn-border-color:var(--erp-red);--bs-btn-color:#fff;
  --bs-btn-hover-bg:var(--erp-red-d);--bs-btn-hover-border-color:var(--erp-red-d);--bs-btn-hover-color:#fff;
  --bs-btn-active-bg:var(--erp-red-d);--bs-btn-active-border-color:var(--erp-red-d);--bs-btn-active-color:#fff;
  --bs-btn-disabled-bg:var(--erp-red);--bs-btn-disabled-border-color:var(--erp-red)}
.btn-success,.btn-dark{--bs-btn-bg:var(--erp-black);--bs-btn-border-color:#3a3a3d;--bs-btn-color:#fff;
  --bs-btn-hover-bg:#2a2a2c;--bs-btn-hover-border-color:#3a3a3d;--bs-btn-hover-color:#fff;
  --bs-btn-active-bg:#2a2a2c;--bs-btn-active-border-color:#3a3a3d;--bs-btn-active-color:#fff;
  --bs-btn-disabled-bg:var(--erp-black);--bs-btn-disabled-border-color:#3a3a3d}
.btn-info,.btn-warning{--bs-btn-bg:var(--erp-card);--bs-btn-border-color:var(--erp-ink);--bs-btn-color:var(--erp-ink);
  --bs-btn-hover-bg:var(--erp-ink);--bs-btn-hover-border-color:var(--erp-ink);--bs-btn-hover-color:var(--erp-card);
  --bs-btn-active-bg:var(--erp-ink);--bs-btn-active-border-color:var(--erp-ink);--bs-btn-active-color:var(--erp-card)}
.btn-outline-primary{--bs-btn-color:var(--erp-red);--bs-btn-border-color:var(--erp-red);
  --bs-btn-hover-bg:var(--erp-red);--bs-btn-hover-border-color:var(--erp-red);--bs-btn-hover-color:#fff;
  --bs-btn-active-bg:var(--erp-red);--bs-btn-active-border-color:var(--erp-red);--bs-btn-active-color:#fff}
/* توافق مع أسماء Bootstrap 4 المستخدمة في الصفحات */
.badge-primary,.badge-dark{background:var(--erp-black);color:#fff}
.badge-secondary,.badge-info{background:var(--erp-silver);color:#111}
.badge-success{background:var(--erp-card);color:var(--erp-ink);border:1px solid var(--erp-ink)}
.badge-warning{background:var(--erp-card);color:var(--erp-red);border:1px solid var(--erp-red)}
.badge-danger{background:var(--erp-red);color:#fff}
.btn-block{display:block;width:100%}
.text-right{text-align:right!important}
.text-left{text-align:left!important}

/* ===== إصلاح الأفقي: عنصر مخفي للقارئات بيتحط على -10000px فبيطلع شريط تمرير أفقي ضخم ===== */
.live-region{left:0!important;right:auto!important;width:1px!important;height:1px!important;
  overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important}

/* ===== العربي (RTL): إخفاء القايمة على الكمبيوتر بالهامش الأيمن بدل الأيسر ===== */
[dir=rtl] .sidebar-collapse:not(.sidebar-mini) .app-sidebar{margin-left:0;margin-right:calc(var(--lte-sidebar-width) * -1)}

/* ===== الموبايل: القايمة ثابتة فوق المحتوى (fixed) مع خلفية معتمة وحركة ناعمة ===== */
.erp-sidebar-close{display:none;width:100%;padding:14px;border:0;background:var(--erp-red);color:#fff;font-weight:700;font-size:16px}
#erp-backdrop{display:none}
@media (max-width:991.98px){
  #jazzy-sidebar{position:fixed!important;top:0;bottom:0;right:0;left:auto;margin:0!important;
    width:min(82vw,300px)!important;max-height:100vh;z-index:1045;overflow-y:auto;
    transform:translateX(100%);visibility:hidden;
    transition:transform .28s ease,visibility 0s linear .28s}
  [dir=ltr] #jazzy-sidebar{right:auto;left:0;transform:translateX(-100%)}
  body.sidebar-open #jazzy-sidebar{transform:none;visibility:visible;transition:transform .28s ease,visibility 0s}
  .sidebar-overlay{display:none!important}
  #erp-backdrop{display:block;position:fixed;top:0;right:0;bottom:0;left:0;z-index:1040;background:rgba(0,0,0,.55);
    opacity:0;visibility:hidden;transition:opacity .28s ease,visibility 0s linear .28s}
  body.sidebar-open #erp-backdrop{opacity:1;visibility:visible;transition:opacity .28s ease,visibility 0s}
  body.sidebar-open{overflow:hidden}
  .erp-sidebar-close{display:block}
  .app-header [data-lte-toggle=sidebar]{font-size:22px;padding:10px 14px}
}

/* ===== بحث لحظي في الهيدر ===== */
.erp-search{position:relative;flex:1 1 240px;max-width:360px;margin:0 12px}
.erp-search input{width:100%}
.erp-results{display:none;position:absolute;top:calc(100% + 6px);right:0;left:0;z-index:1050;max-height:70vh;overflow:auto;
  background:var(--erp-card);color:var(--erp-ink);border:1px solid var(--erp-line);border-radius:10px;
  box-shadow:0 10px 28px rgba(0,0,0,.22)}
.erp-group{padding:6px 12px;font-size:12px;font-weight:700;background:var(--erp-soft);color:var(--erp-muted)}
.erp-item{display:flex;flex-direction:column;padding:8px 12px;color:var(--erp-ink)!important;text-decoration:none;border-top:1px solid var(--erp-line)}
.erp-item small{color:var(--erp-muted)}
.erp-item:hover,.erp-item.on{background:var(--erp-soft)}
.erp-empty{padding:12px;color:var(--erp-muted)}
@media (max-width:991.98px){
  .app-header .container-fluid{flex-wrap:wrap}
  .erp-search{order:5;flex:1 1 100%;max-width:none;margin:6px 0}
}

/* ===== نافذة تأكيد الحذف ===== */
#erp-confirm{display:none;position:fixed;top:0;right:0;bottom:0;left:0;z-index:3000;align-items:center;justify-content:center;
  background:rgba(0,0,0,.6);padding:16px}
#erp-confirm.show{display:flex}
#erp-confirm .box{width:100%;max-width:420px;background:var(--erp-card);color:var(--erp-ink);border-radius:14px;
  border-top:5px solid var(--erp-red);padding:22px;box-shadow:0 20px 50px rgba(0,0,0,.4)}
#erp-confirm h3{margin:0 0 8px;font-size:20px;color:var(--erp-red)}
#erp-confirm p{margin:0 0 18px;line-height:1.7}
#erp-confirm .btns{display:flex;gap:10px;justify-content:flex-end}
#erp-confirm button{padding:10px 20px;border-radius:8px;border:1px solid var(--erp-ink);background:var(--erp-card);color:var(--erp-ink);font-size:15px;cursor:pointer}
#erp-confirm button.yes{background:var(--erp-red);border-color:var(--erp-red);color:#fff;font-weight:700}

/* ===== القايمة المقسمة ===== */
#jazzy-sidebar .erp-nav-header{color:#8d8d95!important;font-size:12px;font-weight:700;letter-spacing:0;
  padding:14px 16px 4px!important;text-transform:none}
#jazzy-navigation{padding:0!important;margin:0}
#jazzy-sidebar .nav-item{padding:0 10px}
#jazzy-sidebar .nav-link{border-radius:8px;margin:1px 0;padding:8px 10px;width:100%}
#jazzy-sidebar .nav-link p{font-size:14.5px}
#jazzy-sidebar .nav-icon{width:22px;text-align:center;opacity:.85}

/* ===== شريط الموبايل اللي تحت ===== */
#erp-bottom{display:none}
@media (max-width:991.98px){
  #erp-bottom{display:flex;position:fixed;right:0;left:0;bottom:0;z-index:1035;background:var(--erp-card);
    border-top:1px solid var(--erp-line);box-shadow:0 -4px 16px rgba(0,0,0,.08);
    padding:4px 4px calc(4px + env(safe-area-inset-bottom))}
  #erp-bottom a,#erp-bottom button{flex:1 1 0;display:flex;flex-direction:column;align-items:center;gap:2px;
    padding:6px 2px;border:0;background:none;color:var(--erp-muted);font-size:11.5px;font-weight:600;
    text-decoration:none;border-radius:10px;min-height:52px;justify-content:center}
  #erp-bottom i{font-size:19px}
  #erp-bottom a.on{color:var(--erp-red)}
  #erp-bottom a:active,#erp-bottom button:active{background:var(--erp-soft)}
  body:has(#erp-bottom) .app-main{padding-bottom:76px}
  /* الهيدر على الموبايل: أصغر */
  .app-header .container-fluid{padding-top:4px;padding-bottom:4px}
  .app-content-header h3{font-size:1.45rem}
}

/* ===== الطباعة: إخفاء القوائم والأزرار ===== */
@media print{
  #erp-bottom,#jazzy-sidebar,.app-header,.app-footer,#erp-backdrop,.erp-sidebar-close,.erp-search,#erp-confirm,.no-print{display:none!important}
  .app-wrapper,.app-main{display:block!important;margin:0!important;padding:0!important}
  body{background:#fff!important;color:#000!important}
}
</style>
<script>
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () { navigator.serviceWorker.register("/sw.js").catch(function () {}); });
}
</script>
<script id="erp-theme-js">
(function () {
  var T = {confirmTitle: "تأكيد", yes: "نعم، متأكد", no: "إلغاء", close: "\u2715 إغلاق القائمة",
           placeholder: "بحث سريع: موديل، عميل، فاتورة، أوردر...", none: "لا نتائج", fail: "تعذر البحث",
           mode: "الوضع الداكن / الفاتح"};
  function mobile() { return window.innerWidth < 992; }
  var body = document.body;

  /* ---------- القايمة الجانبية على الموبايل ---------- */
  function closeSb() { body.classList.remove("sidebar-open"); }
  if (mobile()) closeSb();                       // القالب بيكتب sidebar-open ثابت فكانت بتبدأ مفتوحة
  var sb = document.getElementById("jazzy-sidebar");
  if (sb && !sb.querySelector(".erp-sidebar-close")) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "erp-sidebar-close"; b.textContent = T.close;
    b.addEventListener("click", closeSb);
    sb.insertBefore(b, sb.firstChild);
    sb.addEventListener("click", function (e) {
      var a = e.target.closest("a.nav-link"), h = a && a.getAttribute("href");
      if (mobile() && h && h !== "#" && h.indexOf("javascript") !== 0) closeSb();
    });
  }
  if (!document.getElementById("erp-backdrop")) {
    var bd = document.createElement("div");
    bd.id = "erp-backdrop";
    bd.addEventListener("click", closeSb);
    body.appendChild(bd);
  }
  window.addEventListener("load", function () { if (mobile()) closeSb(); });
  document.addEventListener("click", function (e) {
    if (e.target.closest("#erp-bottom-menu")) { body.classList.add("sidebar-open"); }
  });
  /* القايمة: انزل للرابط المفتوح لو تحت */
  var act = sb && sb.querySelector(".nav-link.active");
  if (act && act.scrollIntoView) { act.scrollIntoView({block: "nearest"}); }

  /* ---------- زر الوضع الداكن/الفاتح ---------- */
  var header = document.querySelector(".app-header .container-fluid");
  var right = header && header.querySelector("ul.navbar-nav.ms-auto");
  function applyMode(m) {
    document.documentElement.setAttribute("data-bs-theme", m);
    try { localStorage.setItem("jazzmin-theme-mode", m); } catch (e) {}
    var i = document.querySelector("#erp-mode-toggle i");
    if (i) i.className = m === "dark" ? "fas fa-sun" : "fas fa-moon";
  }
  if (right && !document.getElementById("erp-mode-toggle")) {
    var li = document.createElement("li"); li.className = "nav-item";
    li.innerHTML = '<a href="#" class="nav-link" id="erp-mode-toggle" role="button"><i class="fas fa-moon"></i></a>';
    var a = li.firstChild; a.setAttribute("aria-label", T.mode); a.setAttribute("title", T.mode);
    a.addEventListener("click", function (e) {
      e.preventDefault();
      applyMode(document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark");
    });
    right.insertBefore(li, right.firstChild);
    applyMode(document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "light");
  }

  /* ---------- بحث لحظي (AJAX) ---------- */
  if (header && !header.querySelector(".erp-search")) {
    var form = document.createElement("form");
    form.className = "erp-search"; form.setAttribute("role", "search"); form.setAttribute("autocomplete", "off");
    var input = document.createElement("input");
    input.type = "search"; input.className = "form-control form-control-sm";
    input.placeholder = T.placeholder; input.setAttribute("aria-label", T.placeholder);
    var box = document.createElement("div"); box.className = "erp-results";
    form.appendChild(input); form.appendChild(box);
    header.insertBefore(form, right || null);
    var timer = null, ctrl = null, links = [], cur = -1;
    function mark(n) {
      links.forEach(function (l, i) { l.classList.toggle("on", i === n); });
      cur = n; if (links[n]) links[n].scrollIntoView({block: "nearest"});
    }
    function render(data) {
      box.textContent = ""; links = []; cur = -1;
      if (!data.groups || !data.groups.length) {
        var d = document.createElement("div"); d.className = "erp-empty"; d.textContent = T.none; box.appendChild(d);
      }
      (data.groups || []).forEach(function (g) {
        var t = document.createElement("div"); t.className = "erp-group"; t.textContent = g.title; box.appendChild(t);
        g.items.forEach(function (it) {
          var l = document.createElement("a"); l.className = "erp-item"; l.href = it.url;
          var s1 = document.createElement("b"); s1.textContent = it.label;
          var s2 = document.createElement("small"); s2.textContent = it.sub || "";
          l.appendChild(s1); l.appendChild(s2); box.appendChild(l); links.push(l);
        });
      });
      box.style.display = "block";
    }
    function run() {
      var q = input.value.trim();
      if (!q) { box.style.display = "none"; return; }
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      fetch("/search/api/?q=" + encodeURIComponent(q), {credentials: "same-origin", signal: ctrl.signal,
            headers: {"X-Requested-With": "XMLHttpRequest"}})
        .then(function (r) { return r.ok ? r.json() : {groups: []}; })
        .then(render)
        .catch(function (e) { if (e.name !== "AbortError") { box.textContent = T.fail; box.style.display = "block"; } });
    }
    input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(run, 220); });
    input.addEventListener("focus", function () { if (links.length) box.style.display = "block"; });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); mark(Math.min(cur + 1, links.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); mark(Math.max(cur - 1, 0)); }
      else if (e.key === "Enter") { e.preventDefault(); if (links[cur]) location.href = links[cur].href; }
      else if (e.key === "Escape") { box.style.display = "none"; }
    });
    form.addEventListener("submit", function (e) { e.preventDefault(); });
    document.addEventListener("click", function (e) { if (!form.contains(e.target)) box.style.display = "none"; });
  }

  /* ---------- نافذة تأكيد الحذف: أي form أو رابط عليه data-confirm ---------- */
  var modal = null, onYes = null;
  function build() {
    modal = document.createElement("div"); modal.id = "erp-confirm";
    modal.setAttribute("role", "dialog"); modal.setAttribute("aria-modal", "true");
    modal.innerHTML = '<div class="box"><h3></h3><p></p><div class="btns"><button type="button" class="no"></button>' +
                      '<button type="button" class="yes"></button></div></div>';
    modal.querySelector(".no").textContent = T.no; modal.querySelector(".yes").textContent = T.yes;
    modal.querySelector(".no").addEventListener("click", hide);
    modal.querySelector(".yes").addEventListener("click", function () { var f = onYes; hide(); if (f) f(); });
    modal.addEventListener("click", function (e) { if (e.target === modal) hide(); });
    body.appendChild(modal);
  }
  function hide() { modal.classList.remove("show"); onYes = null; }
  function ask(msg, title, yes) {
    if (!modal) build();
    modal.querySelector("h3").textContent = title || T.confirmTitle;
    modal.querySelector("p").textContent = msg;
    onYes = yes; modal.classList.add("show"); modal.querySelector(".no").focus();
  }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && modal && modal.classList.contains("show")) hide(); });
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (f.matches && f.matches("form[data-confirm]") && !f._confirmed) {
      e.preventDefault();
      ask(f.getAttribute("data-confirm"), f.getAttribute("data-confirm-title"), function () { f._confirmed = true; f.submit(); });
    }
  }, true);
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[data-confirm]");
    if (a) { e.preventDefault(); ask(a.getAttribute("data-confirm"), a.getAttribute("data-confirm-title"), function () { location.href = a.href; }); }
  }, true);
})();
</script>
"""


def _swap_menu(content, request):
    """يحط القايمة المقسمة مكان قايمة Jazzmin، ويزود شريط الموبايل اللي تحت"""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return content
    start = content.find(b'<ul class="nav sidebar-menu')
    end = content.find(b"</nav>", start)
    if start == -1 or end == -1:
        return content  # القالب اتغير: سيب القايمة الأصلية
    from .menu import render_bottom_bar, render_sidebar
    content = (content[:start] + render_sidebar(request).encode("utf-8") + content[end:])
    return content.replace(b"</body>", render_bottom_bar(request).encode("utf-8") + b"</body>", 1)


class MobileSidebarMiddleware:
    """يحقن ثيم النظام (ألوان الشعار، الخط، الوضع الداكن، البحث اللحظي، نافذة التأكيد، إصلاح القايمة)
    ويحفظ المستخدم والـ IP للطلب الحالي عشان سجل المراجعة."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.snippet = THEME.encode("utf-8")
        self.head = HEAD.encode("utf-8")

    def __call__(self, request):
        user_token = _current_user.set(getattr(request, "user", None))
        ip_token = _current_ip.set(client_ip(request))
        try:
            response = self.get_response(request)
        finally:
            _current_user.reset(user_token)
            _current_ip.reset(ip_token)
        if (
            "text/html" in response.get("Content-Type", "")
            and not response.streaming
            and response.status_code == 200
            and b"</body>" in response.content
            and b"vendor/adminlte/css/adminlte" in response.content   # صفحات القالب فقط
        ):
            content = response.content.replace(b"</body>", self.snippet + b"</body>", 1)
            content = content.replace(b"</head>", self.head + b"</head>", 1)
            response.content = _swap_menu(content, request)
            if response.has_header("Content-Length"):
                response["Content-Length"] = str(len(response.content))
        return response
