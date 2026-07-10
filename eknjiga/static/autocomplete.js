// Autocomplete partnera (dobavljač/kupac): tipkanjem pretražuje šifrarnik
// preko data-api endpointa, odabir sprema id u skriveno polje.
// Inicijalizira sve blokove .autocomplete[data-api] na stranici.
(function () {
  document.querySelectorAll(".autocomplete[data-api]").forEach(function (blok) {
    const api = blok.dataset.api;
    const input = blok.querySelector(".ac-input");
    const hiddenId = blok.querySelector(".ac-id");
    const hiddenPrikaz = blok.querySelector(".ac-prikaz");
    const lista = blok.querySelector(".autocomplete-lista");
    if (!input || !lista) return;

    let stavke = [];
    let aktivan = -1;
    let timer = null;

    function zatvori() { lista.hidden = true; aktivan = -1; }

    function odaberi(d) {
      input.value = d.naziv + (d.oib ? " (OIB: " + d.oib + ")" : "");
      hiddenId.value = d.id;
      hiddenPrikaz.value = input.value;
      zatvori();
    }

    function prikazi(rezultati) {
      stavke = rezultati;
      lista.innerHTML = "";
      if (!rezultati.length) { zatvori(); return; }
      rezultati.forEach(function (d) {
        const li = document.createElement("li");
        li.textContent = d.naziv + (d.oib ? " — OIB: " + d.oib : "");
        if (d.adresa) {
          const span = document.createElement("span");
          span.className = "ac-adresa";
          span.textContent = d.adresa;
          li.appendChild(span);
        }
        li.addEventListener("mousedown", function (e) { e.preventDefault(); odaberi(d); });
        lista.appendChild(li);
      });
      lista.hidden = false;
      aktivan = -1;
    }

    input.addEventListener("input", function () {
      hiddenId.value = "";           // ručna promjena teksta poništava odabir
      hiddenPrikaz.value = input.value;
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 1) { zatvori(); return; }
      timer = setTimeout(function () {
        fetch(api + "?q=" + encodeURIComponent(q))
          .then(function (r) { return r.json(); })
          .then(prikazi)
          .catch(zatvori);
      }, 150);
    });

    input.addEventListener("keydown", function (e) {
      if (lista.hidden) return;
      const liEls = lista.querySelectorAll("li");
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        aktivan += e.key === "ArrowDown" ? 1 : -1;
        aktivan = Math.max(0, Math.min(liEls.length - 1, aktivan));
        liEls.forEach(function (li, i) { li.classList.toggle("aktivan", i === aktivan); });
      } else if (e.key === "Enter" && aktivan >= 0) {
        e.preventDefault();
        odaberi(stavke[aktivan]);
      } else if (e.key === "Escape") {
        zatvori();
      }
    });

    input.addEventListener("blur", function () { setTimeout(zatvori, 150); });
  });
})();
