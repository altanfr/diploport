(function () {
  const CATS = { burs: "Burs", staj: "Staj", is: "İş", gonulluluk: "Gönüllülük", egitim: "Eğitim", yarisma: "Yarışma", hibe: "Hibe", diger: "Fırsat" };
  const MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
  const $ = (id) => document.getElementById(id);
  let posts = [], cat = "all";

  function el(tag, props, children) {
    const n = document.createElement(tag);
    Object.assign(n, props || {});
    (children || []).forEach((c) => n.append(c));
    return n;
  }
  function fmt(iso) { const [y, m, d] = iso.split("-").map(Number); return d + " " + MONTHS[m - 1] + " " + y; }
  function daysLeft(iso) {
    const t = new Date(); t.setHours(0, 0, 0, 0);
    const [y, m, d] = iso.split("-").map(Number);
    return Math.round((new Date(y, m - 1, d) - t) / 86400000);
  }

  function card(p) {
    const left = p.deadline ? daysLeft(p.deadline) : null;
    const item = el("article", { className: "item", id: p.id });
    item.style.setProperty("--c", "var(--" + (CATS[p.category] ? p.category : "diger") + ")");
    const meta = el("div", { className: "meta" }, [el("span", { className: "pill", textContent: CATS[p.category] || "Fırsat" })]);
    if (p.country_tr) meta.append(el("span", { textContent: p.country_tr }));
    if (p.organization) meta.append(el("span", { textContent: p.organization }));
    item.append(meta, el("h2", { textContent: p.title_tr }), el("p", { textContent: p.summary_tr }));
    if (p.highlights_tr && p.highlights_tr.length) {
      item.append(el("ul", {}, p.highlights_tr.map((h) => el("li", { textContent: h }))));
    }
    const dl = el("p", {});
    if (p.deadline) {
      const soon = left <= 7;
      dl.append("Son başvuru: ", el("span", { className: "deadline" + (soon ? " soon" : ""), textContent: fmt(p.deadline) + (left >= 0 ? " (" + (left === 0 ? "bugün" : left + " gün kaldı") + ")" : "") }));
    } else {
      dl.append("Son başvuru: ", el("span", { className: "deadline", textContent: "belirtilmemiş / açık başvuru" }));
    }
    item.append(dl);
    const a = el("a", { className: "apply", href: p.apply_url, target: "_blank", rel: "noopener noreferrer", textContent: "Resmî sayfada başvur" });
    item.append(a, el("span", { className: "src", textContent: "Kaynak: " + p.source }));
    return item;
  }

  function render() {
    const q = $("q").value.trim().toLocaleLowerCase("tr");
    const list = $("list");
    list.replaceChildren();
    const shown = posts.filter((p) => {
      if (cat !== "all" && p.category !== cat) return false;
      if (!q) return true;
      return [p.title_tr, p.summary_tr, p.country_tr, p.organization].join(" ").toLocaleLowerCase("tr").includes(q);
    });
    shown.forEach((p) => list.append(card(p)));
    $("count").textContent = shown.length + " fırsat";
    $("empty").hidden = posts.length > 0;
    if (location.hash) { const t = document.getElementById(location.hash.slice(1)); if (t) t.scrollIntoView(); }
  }

  function chips() {
    const present = ["all"].concat(Object.keys(CATS).filter((k) => posts.some((p) => p.category === k)));
    $("chips").replaceChildren(...present.map((k) => {
      const b = el("button", { className: "chip", type: "button", textContent: k === "all" ? "Tümü" : CATS[k] });
      b.setAttribute("aria-pressed", String(k === cat));
      b.addEventListener("click", () => { cat = k; chips(); render(); });
      return b;
    }));
  }

  fetch("posts.json", { cache: "no-cache" })
    .then((r) => (r.ok ? r.json() : []))
    .catch(() => [])
    .then((data) => {
      const today = new Date().toISOString().slice(0, 10);
      // Süresi geçenleri gizle
      posts = data.filter((p) => !p.deadline || p.deadline >= today);
      chips(); render();
    });
  $("q").addEventListener("input", render);
  fetch("site.json").then((r) => (r.ok ? r.json() : null)).then((c) => {
    if (c && c.instagram) $("ig").href = c.instagram; else $("ig").hidden = true;
  }).catch(() => { $("ig").hidden = true; });
})();
