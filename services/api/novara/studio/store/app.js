/* The store: products from the organization's catalog, a cart kept in the page, and the order sent to the
   organization's sales team (it waits in Sales until someone takes it). Prices always come from the catalog
   on the server; the page only says which products and how many. */
(function () {
  "use strict";
  var cfg = window.NOVARA_STORE || {};
  var api = String(cfg.api || "").replace(/\/$/, "");
  var $ = function (id) { return document.getElementById(id); };
  var status = $("status"), grid = $("products"), dialog = $("cart"), form = $("order-form");
  var products = [], currency = "", cart = {};

  function money(value) {
    try { return new Intl.NumberFormat("ar", { style: "currency", currency: currency }).format(Number(value)); }
    catch (e) { return value + " " + currency; }
  }
  function headers(json) {
    var h = { "X-Novara-Key": cfg.key };
    if (json) h["Content-Type"] = "application/json";
    return h;
  }
  function el(tag, text, cls) {
    var node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  function renderProducts() {
    grid.textContent = "";
    if (!products.length) { status.textContent = "لا منتجات معروضة بعد."; return; }
    status.textContent = "";
    products.forEach(function (p, i) {
      var card = el("li", undefined, "product");
      var thumb = el("div", Array.from(p.name.trim())[0] || "", "thumb thumb-" + (i % 6));
      thumb.setAttribute("aria-hidden", "true");
      card.appendChild(thumb);
      card.appendChild(el("h2", p.name));
      if (p.description) card.appendChild(el("p", p.description));
      var price = el("div", money(p.price_with_tax), "price");
      price.appendChild(el("small", " شامل الضريبة"));
      card.appendChild(price);
      var add = el("button", "أضف إلى السلة");
      add.type = "button";
      add.setAttribute("aria-label", "أضف " + p.name + " إلى السلة");
      add.addEventListener("click", function () { cart[p.id] = (cart[p.id] || 0) + 1; renderCart(); });
      card.appendChild(add);
      grid.appendChild(card);
    });
  }

  function renderCart() {
    var lines = $("cart-lines"), count = 0, total = 0;
    lines.textContent = "";
    products.forEach(function (p) {
      var qty = cart[p.id];
      if (!qty) return;
      count += qty;
      total += qty * Number(p.price_with_tax);
      var li = el("li");
      li.appendChild(el("span", p.name));
      var box = el("span", undefined, "qty");
      var less = el("button", "−"); less.type = "button"; less.setAttribute("aria-label", "أنقص " + p.name);
      less.addEventListener("click", function () { cart[p.id] = qty - 1; if (!cart[p.id]) delete cart[p.id]; renderCart(); });
      var more = el("button", "+"); more.type = "button"; more.setAttribute("aria-label", "زد " + p.name);
      more.addEventListener("click", function () { cart[p.id] = qty + 1; renderCart(); });
      box.appendChild(less); box.appendChild(el("span", String(qty))); box.appendChild(more);
      li.appendChild(box);
      lines.appendChild(li);
    });
    if (!count) lines.appendChild(el("li", "السلة فارغة."));
    $("cart-count").textContent = String(count);
    $("cart-total").textContent = money(total.toFixed(2));
    $("send").disabled = !count;
  }

  function say(text, kind) {
    var out = $("order-status");
    out.textContent = text;
    out.className = "order-status" + (kind ? " " + kind : "");
  }

  $("cart-open").addEventListener("click", function () { renderCart(); say(""); dialog.showModal(); });
  $("cart-close").addEventListener("click", function () { dialog.close(); });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var data = new FormData(form);
    var name = String(data.get("name") || "").trim();
    var phone = String(data.get("phone") || "").trim();
    var email = String(data.get("email") || "").trim();
    var lines = Object.keys(cart).map(function (id) { return { product_id: id, quantity: cart[id] }; });
    if (!name) { say("اكتب اسمك من فضلك.", "error"); return; }
    if (!phone && !email) { say("نحتاج رقم الجوال أو البريد لنتواصل معك.", "error"); return; }
    if (!lines.length) { say("السلة فارغة.", "error"); return; }
    $("send").disabled = true;
    say("جارٍ إرسال الطلب…");
    window.novaraProof(api, cfg.key)
      .then(function (proof) {
        return fetch(api + "/api/v1/public/orders", {
          method: "POST", headers: headers(true),
          body: JSON.stringify({ name: name, phone: phone, email: email || null, note: String(data.get("note") || ""),
                                 website: String(data.get("website") || ""), lines: lines, proof: proof }),
        });
      })
      .then(function (r) { return r.json().then(function (body) { return { ok: r.ok, body: body }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.body && res.body.detail ? res.body.detail : "refused");
        cart = {};
        renderCart();
        form.reset();
        say("وصلنا طلبك رقم " + res.body.reference + "، وسنتواصل معك قريباً. شكراً لك!", "done");
      })
      .catch(function () {
        $("send").disabled = false;
        say("تعذّر إرسال الطلب. حاول مرة أخرى بعد قليل.", "error");
      });
  });

  if (!cfg.key || !api) {
    status.textContent = "المتجر غير مربوط ببيانات المنشأة بعد.";
    return;
  }
  fetch(api + "/api/v1/public/catalog", { headers: headers(false) })
    .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
    .then(function (data) { currency = data.currency; products = data.products || []; renderProducts(); renderCart(); })
    .catch(function () { status.textContent = "تعذّر تحميل المنتجات. حاول لاحقاً."; });
})();
