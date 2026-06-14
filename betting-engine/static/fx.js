/* ChapaFut — microinterações premium (JS puro, sem dependências).
   Respeita prefers-reduced-motion e dispositivos sem mouse. */
(() => {
  "use strict";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const hasHover = matchMedia("(hover: hover) and (pointer: fine)").matches;

  /* ------------- overlay de carregamento (busca demorada) ----------- */
  const overlay = document.getElementById("loading-overlay");
  if (overlay) {
    const TIPS = [
      "Buscando os últimos jogos de cada time (casa e fora)…",
      "Calculando gols, escanteios e cartões esperados…",
      "Consultando a média de cartões do árbitro…",
      "Cruzando o modelo Poisson com as odds da casa…",
      "Na primeira vez do dia demora um pouco; depois fica rápido.",
    ];
    let tipTimer = null;
    document.querySelectorAll("form[data-loading]").forEach((form) => {
      form.addEventListener("submit", () => {
        document.getElementById("load-title").textContent =
          form.getAttribute("data-loading") || "Carregando…";
        const tipEl = document.getElementById("load-tip");
        let i = 0;
        tipEl.textContent = TIPS[0];
        tipTimer = setInterval(() => {
          i = (i + 1) % TIPS.length;
          tipEl.style.opacity = 0;
          setTimeout(() => { tipEl.textContent = TIPS[i];
                             tipEl.style.opacity = 1; }, 250);
        }, 2600);
        overlay.classList.add("on");
      });
    });
    // se o usuário voltar pela seta do navegador, esconde o overlay
    addEventListener("pageshow", () => {
      overlay.classList.remove("on");
      if (tipTimer) clearInterval(tipTimer);
    });
  }

  /* ------------- marca a aba atual no menu (estado premium) ---------- */
  const path = location.pathname;
  document.querySelectorAll(".topbar nav a").forEach((a) => {
    const href = a.getAttribute("href");
    const active = href === "/" ? path === "/" : path.startsWith(href);
    if (active) a.classList.add("active");
  });

  /* ---------------- revelação no scroll (fade + movimento) ----------- */
  if (!reduced && "IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          e.target.classList.add("in");
          io.unobserve(e.target);
        }
      }
    }, { threshold: 0.08, rootMargin: "0px 0px -40px 0px" });
    document.querySelectorAll(".card, .hero, .xray").forEach((el, i) => {
      el.classList.add("reveal");
      el.style.transitionDelay = `${Math.min(i * 60, 240)}ms`;
      io.observe(el);
    });
  }

  /* ------- parallax do cenário + barra de progresso de leitura ------- */
  const scene = document.querySelector(".bg-scene");
  const progress = document.getElementById("progress");
  if (!reduced && (scene || progress)) {
    let ticking = false;
    addEventListener("scroll", () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        if (scene) scene.style.transform = `translateY(${scrollY * -0.06}px)`;
        if (progress) {
          const max = document.documentElement.scrollHeight - innerHeight;
          progress.style.width = `${max > 0 ? (scrollY / max) * 100 : 0}%`;
        }
        ticking = false;
      });
    }, { passive: true });
  }

  /* ------- tilt 3D leve + holofote que segue o cursor nos cards ------ */
  if (!reduced && hasHover) {
    const MAX_TILT = 2.4; // graus — sutil de propósito
    document.querySelectorAll(".card").forEach((card) => {
      let raf = null;
      card.addEventListener("mousemove", (ev) => {
        if (raf) return;
        raf = requestAnimationFrame(() => {
          const r = card.getBoundingClientRect();
          const px = (ev.clientX - r.left) / r.width;   // 0..1
          const py = (ev.clientY - r.top) / r.height;
          card.style.setProperty("--mx", `${px * 100}%`);
          card.style.setProperty("--my", `${py * 100}%`);
          // só inclina cartões de tamanho razoável (tabelas grandes não)
          if (r.height < 560) {
            const rx = (0.5 - py) * MAX_TILT;
            const ry = (px - 0.5) * MAX_TILT;
            card.style.transform =
              `perspective(900px) rotateX(${rx}deg) rotateY(${ry}deg)`;
          }
          raf = null;
        });
      });
      card.addEventListener("mouseleave", () => {
        card.style.transform = "";
      });
    });
  }
})();
