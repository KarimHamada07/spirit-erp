/* SPIRIT — حركة وتفاعل المتجر: ظهور العناصر، الهيدر، السلايدر، السلة الجانبية، المعاينة السريعة،
   المفضلة، آخر حاجات اتفرجت عليها، معرض الصور، ونقل الصورة للسلة. كله من غير مكتبات. */
(function () {
  "use strict";
  var doc = document, body = doc.body, U = window.SPIRIT || {};
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var csrf = (doc.cookie.match(/csrftoken=([^;]+)/) || [])[1] || (doc.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
  function $(s, r) { return (r || doc).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || doc).querySelectorAll(s)); }
  function store(k, v) {
    try { if (v === undefined) return JSON.parse(localStorage.getItem(k) || "[]"); localStorage.setItem(k, JSON.stringify(v)); } catch (e) { return []; }
  }

  /* ---------- رسايل صغيرة ---------- */
  var toasts = $(".toasts");
  function toast(text, icon) {
    if (!toasts) return;
    var t = doc.createElement("div");
    t.className = "toast"; t.setAttribute("role", "status");
    t.innerHTML = "<i>" + (icon || "✓") + "</i><span></span>";
    t.querySelector("span").textContent = text;
    toasts.appendChild(t);
    setTimeout(function () { t.classList.add("out"); setTimeout(function () { t.remove(); }, 300); }, 2600);
  }
  U.toast = toast;

  /* ---------- ظهور العناصر وانت نازل ---------- */
  function reveal(root) {
    var items = $$(".rv:not(.in)", root);
    if (reduce || !("IntersectionObserver" in window)) { items.forEach(function (e) { e.classList.add("in"); }); return; }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, {rootMargin: "0px 0px -8% 0px", threshold: .08});
    items.forEach(function (e) { io.observe(e); });
  }
  U.reveal = reveal;

  /* ---------- الهيدر: زجاج بعد أول نزلة، وبيستخبى وانت نازل ---------- */
  var top = $(".top"), lastY = 0;
  function onScroll() {
    var y = window.scrollY;
    if (top) {
      top.classList.toggle("scrolled", y > 20);
      top.classList.toggle("hide", y > 300 && y > lastY + 4 && !body.classList.contains("lock"));
      if (y < lastY - 4) top.classList.remove("hide");
    }
    var sb = $(".sticky-buy");
    if (sb) { var anchor = $("#buy-form .buy"); sb.classList.toggle("show", !!anchor && anchor.getBoundingClientRect().bottom < 0); }
    lastY = y;
  }
  window.addEventListener("scroll", onScroll, {passive: true});
  onScroll();

  /* ---------- انتقال ناعم بين الصفحات ---------- */
  doc.addEventListener("click", function (e) {
    var a = e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || a.target || a.hasAttribute("download")) return;
    var href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#" || /^(https?:|tel:|mailto:)/.test(href) && a.host !== location.host) return;
    if (a.host !== location.host) return;
    body.classList.add("leaving");
  });
  window.addEventListener("pageshow", function () { body.classList.remove("leaving"); });

  /* ---------- البحث ---------- */
  var sbox = $(".searchbox");
  function openSearch(on) {
    if (!sbox) return;
    sbox.classList.toggle("show", on);
    body.classList.toggle("lock", on);
    if (on) setTimeout(function () { sbox.querySelector("input").focus(); }, 50);
  }
  $$("[data-search]").forEach(function (b) { b.addEventListener("click", function () { openSearch(true); }); });
  if (sbox) sbox.addEventListener("click", function (e) { if (e.target === sbox) openSearch(false); });
  doc.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { openSearch(false); closeModals(); drawer(false); }
    if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) { e.preventDefault(); openSearch(true); }
  });

  /* ---------- السلايدر الكبير ---------- */
  $$(".hero").forEach(function (hero) {
    var slides = $$(".slide", hero), dots = $$(".dots button", hero), i = 0, timer = null, dur = 6500;
    if (slides.length < 2) { if (slides[0]) slides[0].classList.add("on"); return; }
    hero.style.setProperty("--dur", dur + "ms");
    function go(n) {
      slides[i].classList.remove("on"); if (dots[i]) dots[i].classList.remove("on");
      i = (n + slides.length) % slides.length;
      slides[i].classList.add("on");
      if (dots[i]) { dots[i].classList.remove("on"); void dots[i].offsetWidth; dots[i].classList.add("on"); }
      clearTimeout(timer); if (!reduce) timer = setTimeout(function () { go(i + 1); }, dur);
    }
    dots.forEach(function (d, n) { d.addEventListener("click", function () { go(n); }); });
    var sx = null;
    hero.addEventListener("touchstart", function (e) { sx = e.touches[0].clientX; }, {passive: true});
    hero.addEventListener("touchend", function (e) {
      if (sx === null) return; var dx = e.changedTouches[0].clientX - sx; sx = null;
      if (Math.abs(dx) > 50) go(i + (dx > 0 ? 1 : -1));   // عربي: السحب لليمين = اللي بعده
    });
    go(0);
  });

  /* ---------- أسهم السلايدر الأفقي ---------- */
  $$("[data-rail]").forEach(function (b) {
    b.addEventListener("click", function () {
      var rail = doc.getElementById(b.getAttribute("data-rail"));
      if (rail) rail.scrollBy({left: (b.getAttribute("data-dir") === "next" ? -1 : 1) * rail.clientWidth * .8, behavior: "smooth"});
    });
  });

  /* ---------- السلة الجانبية ---------- */
  var dr = $(".drawer");
  function setCount(n) {
    $$("[data-cart-count]").forEach(function (el) {
      el.textContent = n; el.hidden = !n; el.classList.remove("bump"); void el.offsetWidth; el.classList.add("bump");
    });
  }
  function drawer(on) {
    if (!dr) return;
    if (on) {
      var b = $(".body", dr);
      b.innerHTML = '<div class="skel" style="height:96px;margin:14px 0"></div><div class="skel" style="height:96px"></div>';
      fetch(U.drawerUrl, {credentials: "same-origin"}).then(function (r) { return r.text(); }).then(function (h) {
        var tmp = doc.createElement("div"); tmp.innerHTML = h;
        $(".body", dr).innerHTML = ""; $(".body", dr).appendChild(tmp.querySelector(".d-body") || tmp);
        var foot = tmp.querySelector(".d-foot"), fr = tmp.querySelector(".freebar");
        $(".dfoot", dr).innerHTML = foot ? foot.innerHTML : "";
        var fb = $(".fb-slot", dr); fb.innerHTML = fr ? fr.outerHTML : "";
        setTimeout(function () { var i = $(".freebar .bar i", dr); if (i) i.style.width = i.getAttribute("data-w") + "%"; }, 60);
      });
    }
    dr.classList.toggle("show", on); body.classList.toggle("lock", on);
  }
  U.drawer = drawer;
  $$("[data-open-cart]").forEach(function (b) { b.addEventListener("click", function (e) { e.preventDefault(); drawer(true); }); });
  if (dr) dr.addEventListener("click", function (e) { if (e.target.closest("[data-close]")) drawer(false); });

  /* الصورة بتطير لحد أيقونة السلة */
  function fly(img) {
    var cart = $(".top [data-open-cart]") || $(".tabbar [data-open-cart]");
    if (!img || !cart || reduce) return;
    var a = img.getBoundingClientRect(), b = cart.getBoundingClientRect();
    if (!a.width) return;
    var f = img.cloneNode(); f.className = "flyer";
    f.style.cssText = "left:" + a.left + "px;top:" + a.top + "px;width:" + a.width + "px;height:" + a.height + "px";
    body.appendChild(f);
    requestAnimationFrame(function () {
      f.style.transform = "translate(" + (b.left + b.width / 2 - a.left - a.width / 2) + "px," + (b.top + b.height / 2 - a.top - a.height / 2) + "px) scale(.08)";
      f.style.opacity = ".4"; f.style.borderRadius = "50%";
    });
    setTimeout(function () { f.remove(); }, 850);
  }

  /* أي فورم إضافة للسلة: من غير ما الصفحة تتغير */
  doc.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches("form[data-ajax-cart]")) return;
    var btn = e.submitter || form.querySelector("button[type=submit],button:not([type])");
    var data = new FormData(form);
    if (btn && btn.name) data.append(btn.name, btn.value);
    e.preventDefault();
    if (btn) { btn.disabled = true; btn._html = btn.innerHTML; btn.innerHTML = '<span class="spin"></span>'; }
    fetch(form.action, {method: "POST", body: data, credentials: "same-origin",
                        headers: {"X-Requested-With": "fetch", "X-CSRFToken": csrf}})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.go) { location.href = d.go; return; }
        if (!d.ok) { toast(d.error || "حصلت مشكلة", "!"); return; }
        var host = form.closest(".card, .qv-grid, .pd"); fly(host && host.querySelector("img"));
        setCount(d.count); toast(d.msg);
        closeModals();
        setTimeout(function () { drawer(true); }, reduce ? 0 : 650);
      })
      .catch(function () { form.submit(); })
      .finally(function () { if (btn) { btn.disabled = false; btn.innerHTML = btn._html; } });
  });

  /* ---------- النوافذ ---------- */
  function closeModals() { $$(".modal.show").forEach(function (m) { m.classList.remove("show"); }); if (!(dr && dr.classList.contains("show"))) body.classList.remove("lock"); }
  function openModal(id) { var m = doc.getElementById(id); if (m) { m.classList.add("show"); body.classList.add("lock"); } return m; }
  U.openModal = openModal;
  doc.addEventListener("click", function (e) {
    var m = e.target.closest(".modal");
    if (m && (e.target === m || e.target.closest("[data-close]"))) closeModals();
    var g = e.target.closest("[data-modal]");
    if (g) { e.preventDefault(); openModal(g.getAttribute("data-modal")); }
  });

  /* المعاينة السريعة */
  doc.addEventListener("click", function (e) {
    var b = e.target.closest("[data-quick]");
    if (!b) return;
    e.preventDefault();
    var m = openModal("quick"), box = $(".content", m);
    box.innerHTML = '<div class="qv-grid"><div class="skel" style="aspect-ratio:4/5;border-radius:0"></div><div class="in"><div class="skel" style="height:30px;width:70%"></div><div class="skel" style="height:22px;width:40%;margin-top:14px"></div><div class="skel" style="height:52px;margin-top:30px"></div></div></div>';
    fetch(b.getAttribute("data-quick"), {credentials: "same-origin"}).then(function (r) { return r.text(); }).then(function (h) {
      box.innerHTML = h; gallery(box); bindQuick(box);
    });
  });
  function bindQuick(box) {
    var groups = $$("[data-color-group]", box), sws = $$(".swatches button", box);
    sws.forEach(function (s, n) {
      s.addEventListener("click", function () {
        sws.forEach(function (x) { x.classList.toggle("on", x === s); });
        groups.forEach(function (g, k) { g.hidden = k !== n; });
        $(".qv-color", box).textContent = s.getAttribute("data-name");
      });
    });
  }

  /* ---------- المفضلة (على الجهاز) ---------- */
  function favs() { return store("spirit-favs"); }
  function paintFavs() {
    var f = favs();
    $$(".fav[data-code]").forEach(function (b) {
      var on = f.indexOf(b.getAttribute("data-code")) !== -1;
      b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    $$("[data-fav-count]").forEach(function (el) { el.textContent = f.length; el.hidden = !f.length; });
  }
  doc.addEventListener("click", function (e) {
    var b = e.target.closest(".fav[data-code]");
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    var f = favs(), c = b.getAttribute("data-code"), i = f.indexOf(c);
    if (i === -1) { f.unshift(c); toast("اتضاف للمفضلة", "♥"); } else { f.splice(i, 1); toast("اتشال من المفضلة", "♡"); }
    store("spirit-favs", f.slice(0, 40));
    b.classList.remove("pop"); void b.offsetWidth; b.classList.add("pop");
    paintFavs();
  });
  U.paintFavs = paintFavs;

  /* كروت بالأكواد (المفضلة / آخر حاجات اتفرجت عليها) */
  $$("[data-cards-from]").forEach(function (box) {
    var codes = store(box.getAttribute("data-cards-from")).filter(function (c) { return c !== box.getAttribute("data-skip"); }).slice(0, 12);
    var wrap = box.closest("[data-hide-empty]");
    if (!codes.length) { if (wrap) wrap.hidden = true; var em = box.parentNode.querySelector("[data-empty]"); if (em) em.hidden = false; return; }
    fetch(U.cardsUrl + "?codes=" + encodeURIComponent(codes.join(",")), {credentials: "same-origin"})
      .then(function (r) { return r.text(); }).then(function (h) {
        box.innerHTML = h;
        if (!box.querySelector(".card")) { if (wrap) wrap.hidden = true; var em = box.parentNode.querySelector("[data-empty]"); if (em) em.hidden = false; return; }
        if (wrap) wrap.hidden = false;
        reveal(box); paintFavs();
      });
  });
  if (U.viewed) {   // صفحة منتج: يتسجل في آخر حاجات اتفرجت عليها
    var v = store("spirit-viewed").filter(function (c) { return c !== U.viewed; }); v.unshift(U.viewed); store("spirit-viewed", v.slice(0, 16));
  }

  /* ---------- معرض الصور (سحب بالصباع + نقط + صور صغيرة + تكبير) ---------- */
  function gallery(root) {
    $$(".gallery", root).forEach(function (g) {
      var track = $(".gal-track", g); if (!track || track._ok) return; track._ok = 1;
      var figs = $$("figure", track), dots = $(".gal-dots", g), thumbs = $$(".gal-thumbs button", g);
      if (dots) dots.innerHTML = figs.length > 1 ? figs.map(function () { return "<i></i>"; }).join("") : "";
      function cur() { return Math.round(Math.abs(track.scrollLeft) / (track.clientWidth || 1)); }
      function paint() {
        var n = cur();
        if (dots) $$("i", dots).forEach(function (d, k) { d.classList.toggle("on", k === n); });
        thumbs.forEach(function (t, k) { t.classList.toggle("on", k === n); });
      }
      function to(n) {   // عربي (RTL): scrollLeft بالسالب، فبنحسبها من عرض الصورة
        n = Math.max(0, Math.min(n, figs.length - 1));
        var rtl = getComputedStyle(track).direction === "rtl";
        track.scrollTo({left: (rtl ? -1 : 1) * n * track.clientWidth, behavior: "smooth"});
      }
      g._to = to;
      track.addEventListener("scroll", function () { window.requestAnimationFrame(paint); }, {passive: true});
      thumbs.forEach(function (t, k) { t.addEventListener("click", function () { to(k); }); });
      $$(".gal-nav button", g).forEach(function (b) { b.addEventListener("click", function () { to(cur() + (b.getAttribute("data-dir") === "next" ? 1 : -1)); }); });
      figs.forEach(function (f) {
        f.addEventListener("click", function () {
          var lb = doc.getElementById("lightbox"); if (!lb || f.closest(".modal")) return;
          $("img", lb).src = f.querySelector("img").getAttribute("data-full") || f.querySelector("img").src;
          openModal("lightbox");
        });
      });
      paint();
    });
  }
  U.gallery = gallery;

  /* ---------- قصاصات ورق بعد الطلب ---------- */
  if ($(".done") && !reduce) {
    var c = doc.createElement("div"); c.className = "confetti";
    var cols = ["#fe0000", "#ff7a1a", "#ffffff", "#ff2d55", "#ffd23f"];
    for (var k = 0; k < 70; k++) {
      var p = doc.createElement("i");
      p.style.left = Math.random() * 100 + "%"; p.style.background = cols[k % cols.length];
      p.style.animationDelay = Math.random() * .8 + "s"; p.style.animationDuration = 2 + Math.random() * 1.6 + "s";
      c.appendChild(p);
    }
    body.appendChild(c); setTimeout(function () { c.remove(); }, 4500);
  }

  reveal(); gallery(); paintFavs();
})();
