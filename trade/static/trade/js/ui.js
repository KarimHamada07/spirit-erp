/* SPIRIT: تلميحات لكل زرار + حركات الواجهة + صندوق شرح الصفحة.
   التلميح بييجي من: data-tip ← title ← قاموس بنص الزرار ← قاموس بالأيقونة ← قواعد عامة. */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduce) root.classList.add("erp-anim");

  /* ================= قاموس التلميحات ================= */
  var EXACT = {
    "احفظ": "يحفظ التعديلات ويرجعك للقايمة",
    "حفظ": "يحفظ البيانات",
    "احفظ وأضف آخر": "يحفظ ويفتح فورم فاضي تضيف واحد كمان",
    "احفظ واستمر بالتعديل": "يحفظ ويسيبك في نفس الصفحة تكمّل تعديل",
    "حذف": "يمسح العنصر ده (هيسألك الأول)",
    "احذف": "يمسح العنصر ده (هيسألك الأول)",
    "بحث": "يدوّر بالكلام اللي كتبته",
    "مسح": "يشيل البحث والفلاتر ويرجع كل حاجة",
    "عرض": "يعرض النتايج للفترة اللي اخترتها",
    "تحديث": "يعيد الحساب بالأرقام اللي كتبتها",
    "تفريغ": "يفضّي الفاتورة الحالية ويبدأ من الأول",
    "إتمام البيع": "يسجل الفاتورة ويخصم المخزون ويسجل الفلوس في الخزنة",
    "+ طريقة دفع": "يقسم الدفع على أكتر من خزنة (مثلاً جزء كاش وجزء إنستاباي)",
    "ج.م": "الخصم بالمبلغ (جنيه)",
    "%": "الخصم بالنسبة المئوية من مجموع الفاتورة",
    "Excel": "ينزّل البيانات ملف Excel",
    "PDF": "ينزّل ملف PDF تطبعه أو تبعته",
    "طباعة": "يفتح نسخة للطباعة",
    "اطبع": "يفتح نسخة للطباعة",
    "+ عميل جديد": "يضيف عميل جديد ويربطه ببائع",
    "+ مورد جديد": "يضيف مورد جديد",
    "سداد لمورد": "يسجل فلوس اتدفعت لمورد من خزنة",
    "استيراد من Excel": "يرفع بيانات كتير مرة واحدة من ملف Excel",
    "تأكيد استلام الكمية": "يأكد إن البضاعة وصلت ويزوّد المخزن",
    "مراجعة / تعديل": "يفتح فاتورة التوريد تراجعها أو تعدّلها",
    "بيع جديد": "يفتح نقطة البيع",
    "أوردر أونلاين": "يسجل أوردر أونلاين جديد",
    "تحصيل من عميل": "يفتح العملاء اللي عليهم فلوس عشان تسجل تحصيل",
    "توريد جديد": "يسجل بضاعة جاية من مورد",
    "تسليم عهدة لشخص": "يسلّم فلوس لموظف أو حد من بره وتتسجل على عهدته",
    "تسجيل التسليم": "يسجل الفلوس اللي اتسلمت على عهدة الشخص",
    "إضافة على العهدة": "يضيف فلوس على العهدة العامة (زي رصيد قبل النظام)",
    "إضافة": "يضيف البيانات اللي كتبتها",
    "راجع الطلبات": "يفتح طلبات تصفية العهد اللي مستنية موافقتك",
    "موافقة": "يوافق على الطلب ويخصمه من العهدة",
    "رفض": "يرفض الطلب، ومش هيتخصم حاجة",
    "ابعت للموافقة": "يبعت طلب التصفية لمدير الحسابات يوافق عليه",
    "طباعة سند التسليم": "يطبع ورقة بتسليم العهدة يمضي عليها المستلم",
    "نزّل الفورم الفاضي": "ينزّل ملف Excel فاضي بالأعمدة المطلوبة تملاه",
    "صدّر البيانات الحالية": "ينزّل الموجود في السيستم بنفس شكل الفورم عشان تعدّل فيه",
    "ارفع وراجع": "يرفع الملف ويوريك معاينة قبل ما يتحفظ حاجة",
    "تأكيد الاستيراد": "يحفظ كل الصفوف في السيستم مرة واحدة",
    "رجوع / رفع ملف تاني": "يرجعك لصفحة الاستيراد",
    "صوّر تاني": "يرجّع الكاميرا تصوّر من جديد",
    "ضيف من مكاني الحالي": "يسجل مكانك الحالي كمكان شغل للبصمة",
    "اتصال": "يتصل بالرقم من الموبايل",
    "غيّر البائع": "يغيّر البائع المرتبط بالعميل",
    "حذف العميل": "يمسح العميل (لو مالوش فواتير)",
    "الكل": "يعرض كل الحاجات من غير فلتر",
    "رجوع": "يرجعك للصفحة اللي قبلها",
    "إلغاء": "يقفل من غير ما يعمل حاجة",
    "نعم، متأكد": "ينفذ العملية",
    "تسجيل خروج": "يخرجك من السيستم",
    "تغيير كلمة المرور": "يغيّر كلمة السر بتاعتك",
    "بياناتي": "يفتح بيانات حسابك",
    "عرض على الموقع": "يفتح الصفحة على الموقع",
    "التاريخ": "اختار التاريخ",
    "اليوم": "يحط تاريخ النهارده",
    "الآن": "يحط الوقت دلوقتي",
    "اختر": "يختار من القايمة",
    "نفذ": "ينفذ الإجراء اللي اخترته على السطور المعلّمة",
    "تنفيذ": "ينفذ الإجراء اللي اخترته على السطور المعلّمة",
    "سجل": "يفتح سجل التعديلات على العنصر ده",
    "تاريخ": "يفتح سجل التعديلات على العنصر ده"
  };
  var ICONS = [
    ["fa-moon", "الوضع الداكن"], ["fa-sun", "الوضع الفاتح"],
    ["fa-bars", "يفتح أو يقفل القايمة"], ["fa-camera", "امسح الباركود بكاميرا الموبايل"],
    ["fa-user", "حسابك: بياناتك وتغيير كلمة السر والخروج"], ["fa-trash", "حذف"],
    ["fa-file-pdf", "ينزّل PDF"], ["fa-file-excel", "ينزّل Excel"], ["fa-print", "طباعة"],
    ["fa-pen", "تعديل"], ["fa-plus", "إضافة"], ["fa-search", "بحث"], ["fa-magnifying-glass", "بحث"],
    ["fa-download", "تنزيل"], ["fa-upload", "رفع"], ["fa-rotate-right", "إعادة"],
    ["fa-location-crosshairs", "يحدد مكانك الحالي"], ["fa-circle-info", "شرح الصفحة"]
  ];
  var RULES = [
    [/^\+|^أضف|^إضافة/, function (t) { return "يضيف " + t.replace(/^\+\s*|^أضف\s*|^إضافة\s*/, ""); }],
    [/^تأكيد/, function (t) { return t + " (العملية دي بتغيّر المخزون أو الفلوس)"; }],
    [/^حذف|^احذف/, function () { return "يمسح (هيسألك الأول)"; }],
    [/^احفظ/, function () { return "يحفظ التعديلات"; }],
    [/^تصدير|^صدّر|^Excel/i, function () { return "ينزّل البيانات ملف Excel"; }],
    [/PDF/, function () { return "ينزّل ملف PDF"; }],
    [/^طباعة|^اطبع/, function () { return "يفتح نسخة للطباعة"; }],
    [/^(تم|اتـ)/, function (t) { return "يغيّر الحالة لـ: " + t; }],
    [/^الفاتورة \(/, function () { return "ينزل للفاتورة الحالية"; }]
  ];

  function norm(t) { return (t || "").replace(/\s+/g, " ").trim(); }

  function tipFor(el) {
    if (el.hasAttribute("data-tip")) return el.getAttribute("data-tip");
    var title = el.getAttribute("title");
    if (title) { el.setAttribute("aria-label", el.getAttribute("aria-label") || title); el.removeAttribute("title"); return title; }
    var text = norm(el.tagName === "INPUT" ? el.value : el.textContent);
    if (EXACT[text]) return EXACT[text];
    for (var i = 0; i < RULES.length; i++) if (RULES[i][0].test(text)) return RULES[i][1](text);
    var icon = el.querySelector && el.querySelector("i[class*='fa-']");
    var cls = (icon ? icon.className : "") + " " + (el.className || "");
    for (var j = 0; j < ICONS.length; j++) if (cls.indexOf(ICONS[j][0]) !== -1) return text ? text + " — " + ICONS[j][1] : ICONS[j][1];
    var aria = el.getAttribute("aria-label");
    if (aria && aria !== text) return aria;
    if (el.tagName === "A" && text && !el.classList.contains("nav-link")) return "يفتح: " + text;
    return text && text.length <= 40 ? text : "";
  }

  var SEL = "button, a.btn, .btn, input[type=submit], input[type=button], [title], [data-tip], " +
            ".app-header .nav-link, summary, label.erp-file, .object-tools a, .erp-help-toggle, " +
            ".chips a, .cat-chips a, .kpi[href], a.stat, a.kpi, #erp-bottom a";

  function prepare(scope) {
    (scope || doc).querySelectorAll(SEL).forEach(function (el) {
      if (el.__tipReady) return;
      el.__tipReady = true;
      var t = tipFor(el);
      if (t) el.setAttribute("data-tip", t);
    });
  }

  /* ================= فقاعة التلميح ================= */
  var bubble = doc.createElement("div");
  bubble.id = "erp-tip"; bubble.setAttribute("role", "tooltip");
  var showTimer = null, current = null;

  function place(el) {
    var r = el.getBoundingClientRect(), b = bubble.getBoundingClientRect();
    var side = el.getAttribute("data-tip-side");
    var top, left, vw = root.clientWidth;
    if (side === "left" && r.left - b.width - 12 > 4) {
      top = r.top + r.height / 2 - b.height / 2; left = r.left - b.width - 10;
      bubble.setAttribute("data-side", "left");
    } else {
      top = r.top - b.height - 10; left = r.left + r.width / 2 - b.width / 2;
      bubble.setAttribute("data-side", "top");
      if (top < 6) { top = r.bottom + 10; bubble.setAttribute("data-side", "bottom"); }
    }
    left = Math.max(6, Math.min(left, vw - b.width - 6));
    bubble.style.top = Math.round(top) + "px"; bubble.style.left = Math.round(left) + "px";
  }
  function show(el) {
    var t = el.getAttribute("data-tip");
    if (!t) return;
    current = el;
    if (!bubble.parentNode) doc.body.appendChild(bubble);
    bubble.textContent = t;
    bubble.classList.remove("on");
    bubble.style.top = "-999px"; bubble.style.left = "-999px";
    requestAnimationFrame(function () { if (current === el) { place(el); bubble.classList.add("on"); } });
  }
  function hide() { clearTimeout(showTimer); current = null; bubble.classList.remove("on"); }

  doc.addEventListener("mouseover", function (e) {
    var el = e.target.closest && e.target.closest("[data-tip]");
    if (!el || el === current) return;
    clearTimeout(showTimer);
    showTimer = setTimeout(function () { show(el); }, 280);
  });
  doc.addEventListener("mouseout", function (e) {
    var el = e.target.closest && e.target.closest("[data-tip]");
    if (el && (!e.relatedTarget || !el.contains(e.relatedTarget))) hide();
  });
  doc.addEventListener("focusin", function (e) {
    var el = e.target.closest && e.target.closest("[data-tip]");
    if (el && el.matches(":focus-visible")) show(el);
  });
  doc.addEventListener("focusout", hide);
  window.addEventListener("scroll", hide, true);
  doc.addEventListener("click", hide, true);
  /* الموبايل: دوسة طويلة تظهر الشرح */
  var press = null;
  doc.addEventListener("touchstart", function (e) {
    var el = e.target.closest && e.target.closest("[data-tip]");
    if (!el) return;
    press = setTimeout(function () { show(el); setTimeout(hide, 2200); }, 550);
  }, {passive: true});
  ["touchend", "touchmove", "touchcancel"].forEach(function (n) {
    doc.addEventListener(n, function () { clearTimeout(press); }, {passive: true});
  });

  /* ================= صندوق شرح الصفحة ================= */
  function helpBox() {
    var box = doc.querySelector(".erp-help");
    if (!box) return;
    var key = "erp-help-hidden:" + box.getAttribute("data-key");
    var btn = box.querySelector(".erp-help-toggle");
    function set(hidden) {
      box.classList.toggle("collapsed", hidden);
      btn.setAttribute("aria-expanded", hidden ? "false" : "true");
    }
    try { set(localStorage.getItem(key) === "1"); } catch (e) {}
    btn.addEventListener("click", function () {
      var hidden = !box.classList.contains("collapsed");
      set(hidden);
      try { localStorage.setItem(key, hidden ? "1" : "0"); } catch (e) {}
    });
  }

  /* ================= حركات ================= */
  function countUp() {
    if (reduce) return;
    doc.querySelectorAll(".kpi .value, a.stat .n, .tot .n, .total-box").forEach(function (el) {
      if (el.__counted || el.children.length) return;
      var raw = el.textContent.trim(), m = raw.match(/^(-?)([\d,]+)(\.(\d+))?(.*)$/);
      if (!m) return;
      var target = parseFloat((m[1] + m[2].replace(/,/g, "") + (m[3] || ""))), dec = m[4] ? m[4].length : 0;
      if (!isFinite(target) || target === 0) return;
      el.__counted = true;
      var start = null, dur = 750;
      function fmt(v) { return (v < 0 ? "-" : "") + Math.abs(v).toLocaleString("en-US", {minimumFractionDigits: dec, maximumFractionDigits: dec}) + m[5]; }
      function step(ts) {
        if (!start) start = ts;
        var p = Math.min(1, (ts - start) / dur), e = 1 - Math.pow(1 - p, 3);
        el.textContent = fmt(target * e);
        if (p < 1) requestAnimationFrame(step); else el.textContent = raw;
      }
      requestAnimationFrame(step);
    });
  }

  function reveal() {
    var items = doc.querySelectorAll(".app-content .card, .kpi, a.stat, .quick a, .dcard, .sup, .day, .req, " +
                                      ".tot, .inv-card, .erp-help, .statement-box, .formcard");
    if (reduce || !("IntersectionObserver" in window)) { items.forEach(function (el) { el.classList.add("erp-in"); }); return; }
    // احتياطي: أي حاجة ما ظهرتش بعد ثانية ونص تظهر (عشان ما يفضلش حاجة مخفية)
    setTimeout(function () { doc.querySelectorAll(".erp-rv:not(.erp-in)").forEach(function (el) {
      var r = el.getBoundingClientRect(); if (r.top < innerHeight) el.classList.add("erp-in"); }); }, 1500);
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("erp-in"); io.unobserve(en.target); }
      });
    }, {rootMargin: "0px 0px -20px 0px"});
    var i = 0;
    items.forEach(function (el) {
      if (el.closest("details, .modal, [hidden]")) return;   // المقفول يظهر عادي لما يتفتح
      el.classList.add("erp-rv");
      el.style.setProperty("--d", Math.min(i++ * 35, 420) + "ms");
      io.observe(el);
    });
  }

  function ripple() {
    doc.addEventListener("pointerdown", function (e) {
      var b = e.target.closest && e.target.closest(".btn, .quick a, #erp-bottom a, #erp-bottom button, .shoot");
      if (!b || reduce) return;
      var r = b.getBoundingClientRect(), s = Math.max(r.width, r.height);
      var w = doc.createElement("span");
      w.className = "erp-ripple";
      w.style.width = w.style.height = s + "px";
      w.style.left = (e.clientX - r.left - s / 2) + "px"; w.style.top = (e.clientY - r.top - s / 2) + "px";
      if (getComputedStyle(b).position === "static") b.style.position = "relative";
      b.style.overflow = "hidden";
      b.appendChild(w);
      setTimeout(function () { w.remove(); }, 600);
    });
  }

  /* شريط تحميل فوق لما تفتح صفحة */
  function progress() {
    var bar = doc.createElement("div"); bar.id = "erp-progress"; doc.body.appendChild(bar);
    doc.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest("a[href]");
      if (!a || e.defaultPrevented || e.ctrlKey || e.metaKey || e.shiftKey || a.target === "_blank" ||
          a.hasAttribute("download") || a.getAttribute("href").charAt(0) === "#" ||
          a.origin !== location.origin || /\.(xlsx|pdf)|export=|\/pdf\/|statement\//.test(a.href)) return;
      bar.className = "go";
    });
    doc.addEventListener("submit", function (e) {
      if (!e.defaultPrevented && !e.target.target) bar.className = "go";
    });
    window.addEventListener("pageshow", function () { bar.className = ""; });
  }

  /* AdminLTE بيعلّم مربع الاختيار المزدوج إنه غلط أول ما الصفحة تفتح: نشيل العلامة */
  function fixSelectors() {
    doc.querySelectorAll(".selector select.is-invalid").forEach(function (s) { s.classList.remove("is-invalid"); });
  }

  /* القايمة: "صفحات أكتر" بتتفتح وتتقفل، والنجمة بتثبّت الصفحة فوق (محفوظة على الجهاز ده) */
  function sidebarExtras() {
    var nav = doc.getElementById("jazzy-navigation");
    if (!nav) return;
    var more = nav.querySelector(".erp-more");
    if (more) {
      var btn = more.querySelector(".erp-more-btn");
      try { if (localStorage.getItem("erp-more-open") === "1") { more.classList.add("open"); btn.setAttribute("aria-expanded", "true"); } } catch (e) {}
      btn.addEventListener("click", function () {
        var open = more.classList.toggle("open");
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        try { localStorage.setItem("erp-more-open", open ? "1" : "0"); } catch (e) {}
      });
    }
    var KEY = "erp-pins", pins = [];
    try { pins = JSON.parse(localStorage.getItem(KEY) || "[]") || []; } catch (e) { pins = []; }
    var box = nav.querySelector(".erp-pins"), head = nav.querySelector(".erp-pins-head");
    if (!box) return;
    var list = box.querySelector("ul");
    var links = Array.prototype.filter.call(nav.querySelectorAll(".nav-item > a.nav-link"), function (a) {
      return !a.closest(".erp-pins");
    });
    function draw() {
      list.innerHTML = "";
      pins.forEach(function (href) {
        var a = links.filter(function (x) { return x.getAttribute("href") === href; })[0];
        if (!a) return;
        var li = a.parentNode.cloneNode(true);
        var pin = li.querySelector(".erp-pin"); if (pin) pin.remove();
        list.appendChild(li);
      });
      var any = list.children.length > 0;
      box.hidden = !any; head.hidden = !any;
      links.forEach(function (a) {
        var p = a.parentNode.querySelector(".erp-pin"), on = pins.indexOf(a.getAttribute("href")) !== -1;
        if (p) { p.classList.toggle("on", on); p.setAttribute("aria-pressed", on ? "true" : "false");
                 p.setAttribute("data-tip", on ? "شيلها من المثبتة" : "ثبّتها فوق في القايمة"); }
      });
    }
    links.forEach(function (a) {
      var b = doc.createElement("button");
      b.type = "button"; b.className = "erp-pin";
      b.setAttribute("aria-label", "تثبيت " + a.textContent.trim());
      b.innerHTML = '<i class="fas fa-star" aria-hidden="true"></i>';
      b.addEventListener("click", function (e) {
        e.preventDefault(); e.stopPropagation();
        var href = a.getAttribute("href"), i = pins.indexOf(href);
        if (i === -1) pins.push(href); else pins.splice(i, 1);
        try { localStorage.setItem(KEY, JSON.stringify(pins)); } catch (err) {}
        draw();
      });
      a.parentNode.appendChild(b);
    });
    draw();
  }

  /* زرار + العائم على الموبايل */
  function fab() {
    var b = doc.getElementById("erp-fab"), sheet = doc.getElementById("erp-fab-sheet");
    if (!b || !sheet) return;
    function set(open) {
      sheet.classList.toggle("show", open); b.setAttribute("aria-expanded", open ? "true" : "false");
      doc.body.classList.toggle("erp-fab-open", open);
    }
    b.addEventListener("click", function () { set(!sheet.classList.contains("show")); });
    doc.addEventListener("click", function (e) { if (!e.target.closest("#erp-fab, #erp-fab-sheet")) set(false); });
    doc.addEventListener("keydown", function (e) { if (e.key === "Escape") set(false); });
  }

  function init() {
    sidebarExtras();
    fab();
    fixSelectors(); setTimeout(fixSelectors, 400); setTimeout(fixSelectors, 1500);
    prepare();
    helpBox();
    reveal();
    countUp();
    ripple();
    progress();
    root.classList.add("erp-ready");
    /* أي حاجة بتتضاف بعدين (صفوف جديدة، نتايج بحث، فورم بتتفتح) تاخد تلميحات */
    var pending = null;
    new MutationObserver(function () {
      clearTimeout(pending);
      pending = setTimeout(function () { prepare(); }, 120);
    }).observe(doc.body, {childList: true, subtree: true});
  }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init); else init();
})();
