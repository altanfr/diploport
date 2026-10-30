(function () {
  "use strict";

  var CATS = { burs: "Burs", staj: "Staj", is: "İş", gonulluluk: "Gönüllülük", egitim: "Eğitim", yarisma: "Yarışma", hibe: "Hibe", diger: "Fırsat" };
  var ORDER = ["burs", "staj", "is", "gonulluluk", "egitim", "yarisma", "hibe", "diger"];
  var MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
  var SHORT = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];
  var RAIL_MAX = 12;
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var $ = function (id) { return document.getElementById(id); };
  var posts = [], cat = "all", query = "";

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function fold(s) {
    return String(s || "").toLocaleLowerCase("tr").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/ı/g, "i");
  }
  function parts(iso) { var a = iso.split("-").map(Number); return { y: a[0], m: a[1], d: a[2] }; }
  function daysLeft(iso) {
    var t = new Date(); t.setHours(0, 0, 0, 0);
    var p = parts(iso);
    return Math.round((new Date(p.y, p.m - 1, p.d) - t) / 86400000);
  }
  function longDate(iso) { var p = parts(iso); return p.d + " " + MONTHS[p.m - 1] + " " + p.y; }
  function leftText(n) { return n === 0 ? "Bugün son gün" : n === 1 ? "Yarın son gün" : n + " gün kaldı"; }
  function safeUrl(u) { return /^https?:\/\//i.test(u || "") ? u : ""; }
  function smooth() { return reduceMotion ? "auto" : "smooth"; }

  /* ---------- Liste ---------- */

  function chevron() {
    var ns = "http://www.w3.org/2000/svg";
    var s = document.createElementNS(ns, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("class", "v");
    s.setAttribute("aria-hidden", "true");
    var p = document.createElementNS(ns, "path");
    p.setAttribute("d", "M6 9l6 6 6-6");
    s.appendChild(p);
    return s;
  }

  function dateBlock(p) {
    var box = el("div", "d");
    if (p.deadline) {
      var pr = parts(p.deadline);
      box.appendChild(el("b", "", String(pr.d)));
      box.appendChild(el("span", "", SHORT[pr.m - 1]));
      var left = daysLeft(p.deadline);
      if (left <= 7) box.classList.add("soon7");
      box.setAttribute("role", "img");
      box.setAttribute("aria-label", "Son başvuru " + longDate(p.deadline));
    } else {
      box.classList.add("open");
      box.appendChild(el("b", "", "Açık"));
      box.setAttribute("role", "img");
      box.setAttribute("aria-label", "Son başvuru tarihi belirtilmemiş");
    }
    return box;
  }

  function panel(p) {
    var box = el("div", "more");
    box.appendChild(el("p", "sum", p.summary_tr || ""));
    if (p.highlights_tr && p.highlights_tr.length) {
      var ul = el("ul");
      p.highlights_tr.forEach(function (h) { ul.appendChild(el("li", "", h)); });
      box.appendChild(ul);
    }
    var dl = el("dl", "facts");
    function fact(k, v) { dl.appendChild(el("dt", "", k)); dl.appendChild(el("dd", "", v)); }
    fact("Son başvuru", p.deadline ? longDate(p.deadline) : "Belirtilmemiş, açık başvuru");
    if (p.funding_tr && !/^belirtilmemi/i.test(p.funding_tr)) fact("Destek", p.funding_tr);
    if (p.fee_required) fact("Ücret", "Başvuru için ücret var");
    box.appendChild(dl);
    var url = safeUrl(p.apply_url);
    if (url) {
      var a = el("a", "go", "Resmî sayfada başvur");
      a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
      box.appendChild(a);
    }
    if (p.source) box.appendChild(el("p", "src", "Kaynak: " + p.source));
    return box;
  }

  function item(p) {
    var li = el("li", "item");
    li.id = p.id;
    var row = el("button", "row");
    row.type = "button";
    row.setAttribute("aria-expanded", "false");
    var pid = "more-" + p.id;
    row.setAttribute("aria-controls", pid);

    var t = el("div", "t");
    t.appendChild(el("h3", "", p.title_tr));
    if (p.organization) t.appendChild(el("p", "", p.organization));

    var m = el("div", "m");
    if (p.country_tr) m.appendChild(el("span", "c", p.country_tr));
    m.appendChild(el("span", "k", CATS[p.category] || CATS.diger));
    if (p.deadline) {
      var left = daysLeft(p.deadline);
      m.appendChild(el("span", left <= 7 ? "l left" : "l", leftText(left)));
    }
    row.append(dateBlock(p), t, m, chevron());
    li.appendChild(row);

    var more = panel(p);
    more.id = pid;
    more.hidden = true;
    li.appendChild(more);
    row.addEventListener("click", function () { toggle(li, row.getAttribute("aria-expanded") !== "true"); });
    return li;
  }

  function toggle(li, open) {
    var list = $("list");
    list.querySelectorAll('.row[aria-expanded="true"]').forEach(function (r) {
      if (r.parentNode !== li) {
        r.setAttribute("aria-expanded", "false");
        r.parentNode.querySelector(".more").hidden = true;
      }
    });
    li.querySelector(".row").setAttribute("aria-expanded", String(open));
    li.querySelector(".more").hidden = !open;
  }

  function matches(p) {
    if (cat !== "all" && p.category !== cat) return false;
    if (!query) return true;
    var hay = fold([p.title_tr, p.summary_tr, p.country_tr, p.organization, CATS[p.category], (p.highlights_tr || []).join(" ")].join(" "));
    return query.split(/\s+/).every(function (w) { return hay.indexOf(w) !== -1; });
  }

  function render() {
    var list = $("list");
    list.replaceChildren();
    var shown = posts.filter(matches);
    shown.forEach(function (p) { list.appendChild(item(p)); });
    $("count").textContent = shown.length ? shown.length + " ilan" : "";
    $("none").hidden = shown.length > 0;
    list.hidden = shown.length === 0;
  }

  function tabs() {
    var present = ["all"].concat(ORDER.filter(function (k) { return posts.some(function (p) { return p.category === k; }); }));
    $("tabs").replaceChildren.apply($("tabs"), present.map(function (k) {
      var b = el("button", "tab", k === "all" ? "Tümü" : CATS[k]);
      b.type = "button";
      b.setAttribute("aria-pressed", String(k === cat));
      b.addEventListener("click", function () {
        cat = k; tabs(); render();
        var nav = $("tabsNav");
        if (nav.getBoundingClientRect().top <= 1) $("count").scrollIntoView({ block: "start" });
      });
      return b;
    }));
    var sel = $("tabs").querySelector('[aria-pressed="true"]');
    if (sel && sel.scrollIntoView) { var box = $("tabs"); box.scrollLeft = Math.max(0, sel.offsetLeft - 24); }
  }

  function openPost(id, fromHash) {
    var target = document.getElementById(id);
    if (!target || !target.classList.contains("item")) {
      if (!posts.some(function (p) { return p.id === id; })) return;
      cat = "all"; query = ""; $("q").value = "";
      tabs(); render();
      target = document.getElementById(id);
      if (!target) return;
    }
    toggle(target, true);
    if (history.replaceState) history.replaceState(null, "", "#" + id);
    target.scrollIntoView({ behavior: fromHash ? "auto" : smooth(), block: "start" });
  }

  /* ---------- Kayan kartlar ---------- */

  function rail() {
    var withImg = posts.filter(function (p) { return p.thumb || p.image; }).slice(0, RAIL_MAX);
    var wrap = $("rail");
    if (!withImg.length) return;
    var track = $("railTrack"), view = $("railView"), thumb = $("railThumb"), line = thumb.parentNode;

    withImg.forEach(function (p, i) {
      var li = el("li", "slide");
      var a = el("a");
      a.href = "#" + p.id;
      a.setAttribute("aria-label", p.title_tr);
      var img = el("img");
      img.src = p.thumb || p.image;
      img.width = 540; img.height = 675;
      img.alt = "";
      img.decoding = "async";
      if (i > 2) img.loading = "lazy";
      a.appendChild(img);
      a.addEventListener("click", function (e) { e.preventDefault(); openPost(p.id); });
      li.appendChild(a);
      track.appendChild(li);
    });
    wrap.hidden = false;

    var slides = Array.prototype.slice.call(track.children);
    function maxScroll() { return view.scrollWidth - view.clientWidth; }
    function slideLeft(i) { return slides[i].offsetLeft - slides[0].offsetLeft; }
    function current() {
      var best = 0, d = Infinity;
      for (var i = 0; i < slides.length; i++) {
        var x = Math.abs(slideLeft(i) - view.scrollLeft);
        if (x < d) { d = x; best = i; }
      }
      return best;
    }
    function goto(i) {
      i = Math.max(0, Math.min(slides.length - 1, i));
      view.scrollTo({ left: Math.min(slideLeft(i), maxScroll()), behavior: smooth() });
    }

    var frame = 0;
    function paint() {
      frame = 0;
      var total = view.scrollWidth, shown = view.clientWidth;
      var ratio = total > 0 ? Math.min(1, shown / total) : 1;
      var travel = line.clientWidth * (1 - ratio);
      var p = maxScroll() > 0 ? view.scrollLeft / maxScroll() : 0;
      thumb.style.transform = "translateX(" + (travel * p).toFixed(1) + "px) scaleX(" + ratio.toFixed(4) + ")";
    }
    function queuePaint() { if (!frame) frame = requestAnimationFrame(paint); }
    view.addEventListener("scroll", queuePaint, { passive: true });
    window.addEventListener("resize", queuePaint);
    paint();

    $("prev").addEventListener("click", function () { pause(10000); goto(current() - 1); });
    $("next").addEventListener("click", function () { pause(10000); goto(current() + 1); });

    /* Kendiliğinden ilerleme: dokunma, fare üstünde, odak ve görünmeyen sekmede durur. */
    var holdUntil = 0, hovering = false, focused = false, visible = true;
    function pause(ms) { holdUntil = Date.now() + ms; }
    ["pointerdown", "touchstart", "wheel", "keydown"].forEach(function (ev) {
      view.addEventListener(ev, function () { pause(9000); }, { passive: true });
    });
    wrap.addEventListener("mouseenter", function () { hovering = true; });
    wrap.addEventListener("mouseleave", function () { hovering = false; });
    wrap.addEventListener("focusin", function () { focused = true; });
    wrap.addEventListener("focusout", function () { focused = false; });
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (es) { visible = es[0].isIntersecting; }, { threshold: 0.4 }).observe(wrap);
    }
    if (!reduceMotion && slides.length > 1) {
      setInterval(function () {
        if (document.hidden || hovering || focused || !visible || Date.now() < holdUntil) return;
        if (maxScroll() < 4) return;
        if (view.scrollLeft >= maxScroll() - 4) view.scrollTo({ left: 0, behavior: "smooth" });
        else goto(current() + 1);
      }, 4200);
    }
  }

  /* ---------- Başlangıç ---------- */

  function links(cfg) {
    var url = cfg && cfg.instagram;
    ["ig", "ig2", "soonIg"].forEach(function (id) {
      var a = $(id);
      if (!a) return;
      if (url) a.href = url;
    });
  }

  function start(data) {
    var today = new Date().toISOString().slice(0, 10);
    posts = (Array.isArray(data) ? data : []).filter(function (p) { return p && p.id && (!p.deadline || p.deadline >= today); });
    if (!posts.length) { $("soon").hidden = false; $("liste").hidden = true; return; }
    $("searchBox").hidden = false;
    $("tabsNav").hidden = false;
    tabs(); render(); rail();
    if (location.hash.length > 1) openPost(decodeURIComponent(location.hash.slice(1)), true);
  }

  $("q").addEventListener("input", function () { query = fold(this.value).trim(); render(); });
  $("clear").addEventListener("click", function () { cat = "all"; query = ""; $("q").value = ""; tabs(); render(); });

  fetch("site.json").then(function (r) { return r.ok ? r.json() : null; }).then(links).catch(function () {});
  fetch("posts.json", { cache: "no-cache" })
    .then(function (r) { return r.ok ? r.json() : []; })
    .catch(function () { return []; })
    .then(start);
})();
