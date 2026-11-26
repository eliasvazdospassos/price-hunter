/**
 * Controlador principal da interface do Price Hunter.
 * Sem inline handlers (compatível com CSP estrita) e com filtros/ordenação instantâneos.
 */

import { api } from "./api.js";
import {
  conditionLabel,
  escapeHtml,
  formatBRL,
  formatDateTime,
  maskCep,
  normalizeCepDigits,
  showToast,
} from "./utils.js";

const STORAGE_CEP_KEY = "price_hunter_cep";

const state = {
  user: null,
  favorites: [],
  favoriteUrls: new Set(),
  mode: "all",
  cep: localStorage.getItem(STORAGE_CEP_KEY) || "",
  location: null,
  providers: [],
  defaultWeights: {
    new: { competitive_price: 35, total_cost: 20, delivery_speed: 15, seller_reputation: 15, condition_warranty: 10, proximity: 5 },
    used: { competitive_price: 25, total_cost: 10, delivery_speed: 5, seller_reputation: 15, condition_warranty: 20, proximity: 25 },
  },
  customWeights: null,
  lastSearch: null,
  view: "home", // "home" | "results" | "favorites"
  filters: {
    priceMin: "",
    priceMax: "",
    store: "",
    condition: "",
    brand: "",
    category: "",
    maxDelivery: "",
    maxDistance: "",
    minRating: "",
    freeShipping: false,
    pickup: false,
    warranty: false,
    exactOnly: false,
  },
  sortBy: "opportunity",
};

const WEIGHT_LABELS = {
  competitive_price: "Preço competitivo",
  total_cost: "Custo total (com frete)",
  delivery_speed: "Velocidade de entrega",
  seller_reputation: "Confiabilidade da loja/vendedor",
  condition_warranty: "Condição e garantia",
  proximity: "Proximidade e retirada",
};

// --- Elementos DOM ---
const els = {
  hero: document.getElementById("hero-section"),
  viewHome: document.getElementById("view-home"),
  viewResults: document.getElementById("view-results"),
  viewFavorites: document.getElementById("view-favorites"),
  searchForm: document.getElementById("search-form"),
  inputQuery: document.getElementById("input-query"),
  inputCep: document.getElementById("input-cep"),
  cepStatus: document.getElementById("cep-status"),
  searchError: document.getElementById("search-error"),
  btnSubmitSearch: document.getElementById("btn-submit-search"),
  categoriesGrid: document.getElementById("categories-grid"),
  recentSearchesList: document.getElementById("recent-searches-list"),
  homeProvidersSummary: document.getElementById("home-providers-summary"),
  homeObservedBlock: document.getElementById("home-observed-block"),
  homeObservedGrid: document.getElementById("home-observed-grid"),
  loadingState: document.getElementById("loading-state"),
  resultsContent: document.getElementById("results-content"),
  intelligenceStrip: document.getElementById("intelligence-strip"),
  resultsWarnings: document.getElementById("results-warnings"),
  tradeoffCard: document.getElementById("tradeoff-card"),
  resultsHeading: document.getElementById("results-heading"),
  resultsSubcount: document.getElementById("results-subcount"),
  offersList: document.getElementById("offers-list"),
  filtersPanel: document.getElementById("filters-panel"),
  selectSort: document.getElementById("select-sort"),
  favoritesList: document.getElementById("favorites-list"),
  favoritesNotice: document.getElementById("favorites-notice"),
  favCount: document.getElementById("fav-count"),
  btnAuth: document.getElementById("btn-auth"),
  btnProfile: document.getElementById("btn-profile"),
  btnLogout: document.getElementById("btn-logout"),
  modalBackdrop: document.getElementById("modal-backdrop"),
  modalTitle: document.getElementById("modal-title"),
  modalBody: document.getElementById("modal-body"),
  modalClose: document.getElementById("modal-close"),
};

// --- Navegação de Views ---
function setView(viewName) {
  state.view = viewName;
  els.viewHome.classList.toggle("hidden", viewName !== "home");
  els.viewResults.classList.toggle("hidden", viewName !== "results");
  els.viewFavorites.classList.toggle("hidden", viewName !== "favorites");
  els.hero.classList.toggle("compact", viewName !== "home");
}

// --- Modal ---
function openModal(title, htmlContent, onMount) {
  els.modalTitle.textContent = title;
  els.modalBody.innerHTML = htmlContent;
  els.modalBackdrop.classList.remove("hidden");
  if (typeof onMount === "function") {
    onMount(els.modalBody);
  }
}

function closeModal() {
  els.modalBackdrop.classList.add("hidden");
  els.modalBody.innerHTML = "";
}

// --- CEP ---
let cepLookupTimer = null;
async function resolveCep(rawCep, silent = false) {
  const digits = normalizeCepDigits(rawCep);
  if (digits.length !== 8) {
    state.location = null;
    els.cepStatus.className = "cep-status";
    els.cepStatus.textContent = "Informe um CEP brasileiro com 8 dígitos (ex.: 01310-100).";
    return null;
  }
  els.cepStatus.className = "cep-status";
  els.cepStatus.textContent = "Consultando endereço do CEP...";
  try {
    const { location } = await api.lookupCep(digits);
    state.cep = digits;
    state.location = location;
    localStorage.setItem(STORAGE_CEP_KEY, digits);
    const parts = [location.neighborhood, `${location.city} - ${location.state}`].filter(Boolean);
    const coordTag = location.lat != null ? " • Coordenadas aproximadas ativas" : "";
    els.cepStatus.className = "cep-status ok";
    els.cepStatus.textContent = `CEP ${location.cep_formatted}: ${parts.join(", ")} (${location.address_source}${coordTag})`;
    return location;
  } catch (err) {
    state.location = null;
    els.cepStatus.className = "cep-status err";
    els.cepStatus.textContent = err.message;
    if (!silent) showToast(err.message, "error");
    return null;
  }
}

// --- Autenticação & Favoritos ---
async function refreshSession() {
  try {
    const { user } = await api.getMe();
    state.user = user;
  } catch {
    state.user = null;
  }
  updateAuthUI();
  if (state.user) {
    if (state.user.preferred_cep && !els.inputCep.value) {
      els.inputCep.value = maskCep(state.user.preferred_cep);
      resolveCep(state.user.preferred_cep, true);
    }
    if (state.user.preferences?.custom_weights) {
      state.customWeights = state.user.preferences.custom_weights;
    }
    if (state.user.preferences?.default_sort) {
      state.sortBy = state.user.preferences.default_sort;
      els.selectSort.value = state.sortBy;
    }
    await refreshFavorites();
  } else {
    state.favorites = [];
    state.favoriteUrls = new Set();
    updateFavCount();
  }
}

function updateAuthUI() {
  const logged = Boolean(state.user);
  els.btnAuth.classList.toggle("hidden", logged);
  els.btnProfile.classList.toggle("hidden", !logged);
  els.btnLogout.classList.toggle("hidden", !logged);
  if (logged) {
    els.btnProfile.textContent = state.user.name.split(" ")[0];
  }
}

function updateFavCount() {
  const count = state.favorites.length;
  els.favCount.textContent = String(count);
  els.favCount.classList.toggle("hidden", count === 0);
}

async function refreshFavorites() {
  if (!state.user) return;
  try {
    const data = await api.listFavorites();
    state.favorites = data.favorites || [];
    state.favoriteUrls = new Set(state.favorites.map((f) => f.original_url));
    els.favoritesNotice.textContent = data.monitoring_notice || "";
    updateFavCount();
    if (state.view === "favorites") renderFavorites();
  } catch {
    // ignore
  }
}

// --- Home ---
async function loadHome() {
  try {
    const data = await api.getHome();
    state.providers = data.providers || [];
    if (data.default_weights) state.defaultWeights = data.default_weights;

    els.categoriesGrid.innerHTML = (data.popular_categories || [])
      .map(
        (cat) => `
      <button type="button" class="cat-card" data-cat-query="${escapeHtml(cat.query)}" data-cat-mode="${escapeHtml(cat.mode || "all")}">
        <strong>${escapeHtml(cat.label)}</strong>
        <span>Ex.: ${escapeHtml(cat.query)}</span>
      </button>
    `
      )
      .join("");

    els.categoriesGrid.querySelectorAll(".cat-card").forEach((btn) => {
      btn.addEventListener("click", () => {
        els.inputQuery.value = btn.dataset.catQuery || "";
        setMode(btn.dataset.catMode || "all");
        if (!normalizeCepDigits(els.inputCep.value)) {
          els.inputCep.focus();
          showToast("Informe seu CEP para pesquisar com frete e proximidade.", "error");
          return;
        }
        runSearch();
      });
    });

    renderRecentSearches(data.recent_searches || []);
    renderHomeProviders(state.providers);
    renderObservedHighlights(data.observed_highlights || []);
  } catch (err) {
    showToast("Não foi possível carregar os dados iniciais.", "error");
  }
}

function renderRecentSearches(items) {
  if (!items.length) {
    els.recentSearchesList.innerHTML = `<p class="muted">Nenhuma pesquisa registrada ainda. Faça sua primeira busca acima.</p>`;
    return;
  }
  els.recentSearchesList.innerHTML = items
    .map(
      (it) => `
    <button type="button" class="recent-chip"
      data-rq="${escapeHtml(it.query)}"
      data-rcep="${escapeHtml(it.cep || "")}"
      data-rmode="${escapeHtml(it.mode || "all")}">
      <span>${escapeHtml(it.query)}</span>
      ${it.best_price ? `<strong class="muted">${formatBRL(it.best_price)}</strong>` : ""}
    </button>
  `
    )
    .join("");

  els.recentSearchesList.querySelectorAll(".recent-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      els.inputQuery.value = chip.dataset.rq || "";
      if (chip.dataset.rcep && !normalizeCepDigits(els.inputCep.value)) {
        els.inputCep.value = maskCep(chip.dataset.rcep);
      }
      setMode(chip.dataset.rmode || "all");
      runSearch();
    });
  });
}

function renderHomeProviders(providers) {
  els.homeProvidersSummary.innerHTML = providers
    .map((p) => {
      const isActive = p.status === "active";
      const label = isActive ? "Ativa" : p.status === "requires_credentials" ? "API c/ credencial" : "Sem API pública";
      return `<span class="provider-pill ${isActive ? "active" : "pending"}" title="${escapeHtml(p.limitations)}">
        ${escapeHtml(p.name)} · ${label}
      </span>`;
    })
    .join("");
}

function renderObservedHighlights(items) {
  if (!items.length) {
    els.homeObservedBlock.classList.add("hidden");
    return;
  }
  els.homeObservedBlock.classList.remove("hidden");
  els.homeObservedGrid.innerHTML = items
    .map(
      (it) => `
    <div class="card">
      <div class="muted" style="font-size:0.78rem">${escapeHtml(it.store)} • ${conditionLabel(it.condition)}</div>
      <div style="font-weight:700;margin:4px 0">${escapeHtml(it.product_key.replace(/-/g, " "))}</div>
      <div style="font-size:1.15rem;font-weight:800;color:var(--accent)">${formatBRL(it.price)}</div>
      <div class="muted" style="font-size:0.74rem;margin:4px 0 8px">Observado em ${formatDateTime(it.observed_at)}</div>
      <a class="btn btn-outline btn-sm" href="${escapeHtml(it.offer_url)}" target="_blank" rel="noopener noreferrer">
        Abrir oferta original ↗
      </a>
    </div>
  `
    )
    .join("");
}

// --- Pesquisa ---
function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-tab").forEach((t) => {
    const active = t.dataset.mode === mode;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
}

async function runSearch() {
  const q = els.inputQuery.value.trim();
  const cepDigits = normalizeCepDigits(els.inputCep.value);
  els.searchError.classList.add("hidden");

  if (q.length < 2) {
    els.searchError.textContent = "Digite pelo menos 2 caracteres para pesquisar um produto.";
    els.searchError.classList.remove("hidden");
    els.inputQuery.focus();
    return;
  }
  if (cepDigits.length !== 8) {
    els.searchError.textContent = "Informe um CEP brasileiro válido com 8 dígitos para calcular frete e proximidade.";
    els.searchError.classList.remove("hidden");
    els.inputCep.focus();
    return;
  }

  localStorage.setItem(STORAGE_CEP_KEY, cepDigits);
  setView("results");
  els.loadingState.classList.remove("hidden");
  els.resultsContent.classList.add("hidden");
  els.btnSubmitSearch.disabled = true;

  try {
    const data = await api.search({
      q,
      cep: cepDigits,
      mode: state.mode,
      weights: state.customWeights,
    });
    state.lastSearch = data;
    state.location = data.location;
    resetFilters(false);
    populateFacets(data.facets || {});
    renderSearchMeta(data);
    renderFilteredOffers();

    const parts = [data.location.neighborhood, `${data.location.city} - ${data.location.state}`].filter(Boolean);
    els.cepStatus.className = "cep-status ok";
    els.cepStatus.textContent = `CEP ${data.location.cep_formatted}: ${parts.join(", ")}`;

    els.loadingState.classList.add("hidden");
    els.resultsContent.classList.remove("hidden");
    loadHome(); // atualiza histórico de buscas em segundo plano
  } catch (err) {
    els.loadingState.classList.add("hidden");
    els.searchError.textContent = err.message || "Falha ao executar a pesquisa.";
    els.searchError.classList.remove("hidden");
    showToast(err.message || "Erro na pesquisa.", "error");
  } finally {
    els.btnSubmitSearch.disabled = false;
  }
}

function renderSearchMeta(data) {
  const loc = data.location;
  const prov = data.providers || {};
  const queriedSummary = (prov.succeeded || [])
    .map((s) => `${escapeHtml(s.name)} (${s.count})`)
    .join(", ");
  const inactiveCount = (prov.inactive || []).length;

  els.intelligenceStrip.innerHTML = `
    <div>
      <strong>CEP ${escapeHtml(loc.cep_formatted)}</strong> (${escapeHtml(loc.city)} - ${escapeHtml(loc.state)})
      &nbsp;•&nbsp;
      <span>Fontes consultadas: ${queriedSummary || "nenhuma respondeu"}</span>
      ${inactiveCount ? `&nbsp;•&nbsp;<span class="muted">${inactiveCount} fontes aguardando credenciais/API</span>` : ""}
    </div>
    <div>
      <span class="muted">${data.elapsed_ms} ms</span>
      &nbsp;·&nbsp;
      <button type="button" class="link-btn" id="btn-strip-providers">Transparência das fontes</button>
    </div>
  `;
  const stripBtn = document.getElementById("btn-strip-providers");
  if (stripBtn) stripBtn.addEventListener("click", openProvidersModal);

  // Warnings
  els.resultsWarnings.innerHTML = (data.warnings || [])
    .map((w) => `<div class="alert alert-warning" style="margin-top:0">${escapeHtml(w)}</div>`)
    .join("");

  // Tradeoff
  const tradeoff = data.scoring?.tradeoff_summary;
  if (tradeoff) {
    els.tradeoffCard.classList.remove("hidden");
    els.tradeoffCard.innerHTML = `
      <div>
        <strong>${escapeHtml(tradeoff.title)}</strong>
        <p>${escapeHtml(tradeoff.detail)}</p>
      </div>
      <span class="badge badge-best_overall">Preço vs. Entrega</span>
    `;
  } else {
    els.tradeoffCard.classList.add("hidden");
  }
}

function populateFacets(facets) {
  const fillSelect = (id, values, placeholder) => {
    const sel = document.getElementById(id);
    if (!sel) return;
    sel.innerHTML = `<option value="">${placeholder}</option>` +
      (values || []).map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  };
  fillSelect("f-store", facets.stores, "Todas as lojas");
  fillSelect("f-brand", facets.brands, "Todas as marcas");
  fillSelect("f-category", facets.categories, "Todas as categorias");
}

function resetFilters(rerender = true) {
  state.filters = {
    priceMin: "",
    priceMax: "",
    store: "",
    condition: "",
    brand: "",
    category: "",
    maxDelivery: "",
    maxDistance: "",
    minRating: "",
    freeShipping: false,
    pickup: false,
    warranty: false,
    exactOnly: false,
  };
  const ids = ["f-price-min", "f-price-max", "f-store", "f-condition", "f-brand", "f-category", "f-max-delivery", "f-max-distance", "f-min-rating"];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  ["f-free-shipping", "f-pickup", "f-warranty", "f-exact-only"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.checked = false;
  });
  if (rerender) renderFilteredOffers();
}

function applyFiltersAndSort(offers) {
  const f = state.filters;
  const filtered = offers.filter((o) => {
    const p = Number(o.price);
    if (f.priceMin !== "" && p < Number(f.priceMin)) return false;
    if (f.priceMax !== "" && p > Number(f.priceMax)) return false;
    if (f.store && o.store !== f.store) return false;
    if (f.condition && o.condition !== f.condition) return false;
    if (f.brand && o.brand !== f.brand) return false;
    if (f.category && o.category !== f.category) return false;
    if (f.maxDelivery !== "") {
      if (o.delivery_days == null || Number(o.delivery_days) > Number(f.maxDelivery)) return false;
    }
    if (f.maxDistance !== "") {
      if (o.distance_km == null || Number(o.distance_km) > Number(f.maxDistance)) return false;
    }
    if (f.minRating !== "") {
      if (o.seller_rating == null || Number(o.seller_rating) < Number(f.minRating)) return false;
    }
    if (f.freeShipping && !o.free_shipping) return false;
    if (f.pickup && !o.pickup_available) return false;
    if (f.warranty && (!o.warranty || o.warranty.toLowerCase().includes("sem garantia"))) return false;
    if (f.exactOnly && o.equivalence_status === "variant") return false;
    return true;
  });

  const sorted = [...filtered];
  const INF = 1e12;
  switch (state.sortBy) {
    case "price_asc":
      sorted.sort((a, b) => Number(a.price) - Number(b.price));
      break;
    case "final_price_asc":
      sorted.sort((a, b) => Number(a.final_price ?? a.price) - Number(b.final_price ?? b.price));
      break;
    case "delivery_asc":
      sorted.sort((a, b) => Number(a.delivery_days ?? INF) - Number(b.delivery_days ?? INF));
      break;
    case "distance_asc":
      sorted.sort((a, b) => Number(a.distance_km ?? INF) - Number(b.distance_km ?? INF));
      break;
    case "rating_desc":
      sorted.sort((a, b) => Number(b.seller_rating ?? -1) - Number(a.seller_rating ?? -1));
      break;
    case "opportunity":
    default:
      sorted.sort((a, b) => Number(b.opportunity_score) - Number(a.opportunity_score));
      break;
  }
  return sorted;
}

function renderFilteredOffers() {
  if (!state.lastSearch) return;
  const allOffers = state.lastSearch.offers || [];
  const list = applyFiltersAndSort(allOffers);
  const q = state.lastSearch.query?.raw_query || "";

  els.resultsHeading.textContent = `Oportunidades para "${q}"`;
  els.resultsSubcount.textContent = `Exibindo ${list.length} de ${allOffers.length} ofertas reais encontradas`;

  if (!list.length) {
    els.offersList.innerHTML = `
      <div class="card">
        <strong>Nenhuma oferta corresponde aos filtros aplicados.</strong>
        <p class="muted" style="margin:6px 0 0">Limpe alguns filtros ao lado ou tente uma busca mais abrangente.</p>
      </div>
    `;
    return;
  }

  els.offersList.innerHTML = list.map((o) => renderOfferCard(o)).join("");

  // Eventos dos cards
  els.offersList.querySelectorAll("[data-action='score-detail']").forEach((btn) => {
    btn.addEventListener("click", () => {
      const offer = allOffers.find((x) => x.id === btn.dataset.offerId);
      if (offer) openScoreModal(offer);
    });
  });

  els.offersList.querySelectorAll("[data-action='favorite']").forEach((btn) => {
    btn.addEventListener("click", () => {
      const offer = allOffers.find((x) => x.id === btn.dataset.offerId);
      if (offer) toggleFavorite(offer);
    });
  });
}

function renderOfferCard(o) {
  const isTop = (o.badges || []).some((b) => b.id === "best_overall");
  const isFav = state.favoriteUrls.has(o.url);

  const badgesHtml = [
    ...(o.badges || []).map(
      (b) => `<span class="badge badge-${escapeHtml(b.id)}">${escapeHtml(b.label)}</span>`
    ),
    `<span class="badge badge-cond-${escapeHtml(o.condition)}">${escapeHtml(conditionLabel(o.condition))}</span>`,
  ].join("");

  let shippingHtml = "";
  if (o.pickup_only) {
    shippingHtml = `<div class="price-shipping" style="color:var(--amber);font-weight:600">Apenas retirada presencial</div>`;
  } else if (o.free_shipping) {
    shippingHtml = `<div class="price-shipping free">Frete grátis</div>`;
  } else if (o.shipping_price != null) {
    shippingHtml = `<div class="price-shipping">Frete: ${formatBRL(o.shipping_price)}</div>`;
  } else {
    shippingHtml = `<div class="price-shipping">${escapeHtml(o.shipping_note || "Frete: consultar na loja")}</div>`;
  }

  const finalHtml =
    o.final_price != null
      ? `<div class="price-final">Total c/ frete: ${formatBRL(o.final_price)}</div>`
      : `<div class="price-shipping">Preço final depende do frete na loja</div>`;

  const variantHtml =
    o.equivalence_status === "variant" && o.model_differences?.length
      ? `<div class="variant-warning">
          <strong>Atenção à equivalência:</strong> ${escapeHtml(o.model_differences.join(" • "))}
        </div>`
      : "";

  const distText =
    o.distance_km != null
      ? `<span>Distância: <strong>~${o.distance_km} km</strong> (aprox.)</span>`
      : "";

  const ratingText =
    o.seller_rating != null
      ? `<span>Avaliação: <strong>★ ${Number(o.seller_rating).toFixed(1)}</strong> (${o.rating_count || 0})</span>`
      : `<span>Avaliação: <span class="muted">não informada</span></span>`;

  const warrantyText = o.warranty
    ? `<span>Garantia: <strong>${escapeHtml(o.warranty)}</strong></span>`
    : "";

  const locationText = o.seller_location
    ? `<span>Local: <strong>${escapeHtml(o.seller_location)}</strong></span>`
    : "";

  return `
    <article class="offer-card ${isTop ? "top-pick" : ""}">
      <div class="offer-img-box">
        ${
          o.image_url
            ? `<img src="${escapeHtml(o.image_url)}" alt="${escapeHtml(o.title)}" loading="lazy" />`
            : `<span class="img-placeholder">Sem imagem</span>`
        }
      </div>

      <div class="offer-main">
        <div class="badge-row">${badgesHtml}</div>
        <h3 class="offer-title">${escapeHtml(o.title)}</h3>
        <div class="offer-meta-line">
          <span><strong>${escapeHtml(o.store)}</strong></span>
          <span>Vendedor: ${escapeHtml(o.seller_name || o.store)}</span>
          ${o.brand ? `<span>Marca: ${escapeHtml(o.brand)}</span>` : ""}
          ${o.model ? `<span>Modelo: ${escapeHtml(o.model)}</span>` : ""}
        </div>

        <div class="offer-details-grid">
          <span>Prazo: <strong>${escapeHtml(o.delivery_label || "Consultar na loja")}</strong></span>
          ${ratingText}
          ${warrantyText}
          ${distText}
          ${locationText}
          ${o.pickup_available ? `<span style="color:var(--accent);font-weight:600">Retirada disponível</span>` : ""}
        </div>

        ${variantHtml}
      </div>

      <div class="offer-aside">
        <div class="price-block">
          ${o.list_price ? `<div class="price-old">${formatBRL(o.list_price)}</div>` : ""}
          <div class="price-main">${formatBRL(o.price)}</div>
          ${shippingHtml}
          ${finalHtml}
        </div>

        <div class="score-box">
          <div class="score-ring score-tier-${escapeHtml(o.opportunity_tier)}">${o.opportunity_score}</div>
          <div class="score-info">
            <strong>${escapeHtml(o.opportunity_label)}</strong>
            <small>${o.evaluation_limited ? "Avaliação limitada (dados parciais)" : "Opportunity Score / 100"}</small>
          </div>
        </div>

        <div class="offer-actions">
          <a class="btn btn-accent" href="${escapeHtml(o.url)}" target="_blank" rel="noopener noreferrer">
            Ir para oferta na loja ↗
          </a>
          <div class="offer-actions-row">
            <button type="button" class="btn btn-outline btn-sm" data-action="score-detail" data-offer-id="${escapeHtml(o.id)}">
              Detalhes da nota
            </button>
            <button type="button" class="btn ${isFav ? "btn-primary" : "btn-outline"} btn-sm" data-action="favorite" data-offer-id="${escapeHtml(o.id)}">
              ${isFav ? "★ Salvo" : "☆ Favoritar"}
            </button>
          </div>
        </div>
      </div>
    </article>
  `;
}

// --- Modal: Detalhes do Opportunity Score & Histórico de Preços ---
function openScoreModal(offer) {
  const factorsHtml = (offer.score_breakdown || [])
    .map(
      (f) => `
    <div class="factor-row">
      <div class="factor-head">
        <span>${escapeHtml(f.label)} (peso ${f.weight}%)</span>
        <span>${f.score}/100 (+${f.weighted_points} pts)</span>
      </div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, Math.max(0, f.score))}%"></div></div>
      <div class="muted" style="font-size:0.8rem">${escapeHtml(f.explanation)}</div>
    </div>
  `
    )
    .join("");

  const hist = offer.price_history || {};
  let histHtml = "";
  if (hist.status === "available") {
    const varSign = hist.variation_percent > 0 ? "+" : "";
    histHtml = `
      <div class="card" style="background:var(--surface-subtle)">
        <h4 style="margin:0 0 8px">Histórico real de preços (${hist.observations} registros)</h4>
        <div class="offer-details-grid">
          <span>Atual: <strong>${formatBRL(hist.current_price)}</strong></span>
          <span>Menor: <strong>${formatBRL(hist.min_price)}</strong></span>
          <span>Maior: <strong>${formatBRL(hist.max_price)}</strong></span>
          <span>Média: <strong>${formatBRL(hist.avg_price)}</strong></span>
          <span>Variação vs. média: <strong>${varSign}${hist.variation_percent}%</strong></span>
        </div>
      </div>
    `;
  } else {
    histHtml = `
      <div class="alert alert-info" style="margin-top:0">
        <strong>Histórico de preços em formação:</strong> ${escapeHtml(hist.message || "Ainda não há registros históricos suficientes para este item.")}
      </div>
    `;
  }

  openModal(
    `Price Hunter Opportunity Score — ${offer.opportunity_score}/100`,
    `
      <div style="margin-bottom:14px">
        <strong>${escapeHtml(offer.title)}</strong>
        <div class="muted" style="font-size:0.84rem">${escapeHtml(offer.store)} • ${escapeHtml(offer.opportunity_label)} • Perfil: ${offer.score_profile === "used" ? "Produto Usado" : "Produto Novo"}</div>
        <div class="alert ${offer.evaluation_limited ? "alert-warning" : "alert-info"}" style="margin-top:8px">
          ${escapeHtml(offer.evaluation_note)}
        </div>
      </div>
      <div style="margin-bottom:16px">${factorsHtml}</div>
      ${histHtml}
    `
  );
}

// --- Modal: Fontes & APIs ---
async function openProvidersModal() {
  try {
    const { providers } = await api.getProviders();
    state.providers = providers;
  } catch {
    // usa cache
  }
  const html = (state.providers || [])
    .map((p) => {
      const badge =
        p.status === "active"
          ? `<span class="badge badge-best_overall">Ativa (Real)</span>`
          : p.status === "requires_credentials"
          ? `<span class="badge badge-best_value">Aguardando credenciais</span>`
          : `<span class="badge badge-cond-used">Sem API pública</span>`;
      const envHtml = p.env_vars?.length
        ? `<p><strong>Variáveis .env:</strong> ${p.env_vars.map((v) => `<span class="code-chip">${escapeHtml(v)}</span>`).join("")}</p>`
        : "";
      return `
        <div class="provider-detail-item">
          <h4><span>${escapeHtml(p.name)}</span> ${badge}</h4>
          <p><strong>Autenticação:</strong> ${escapeHtml(p.auth_required)}</p>
          <p><strong>Recursos:</strong> ${p.supports_new ? "Novos" : ""}${p.supports_new && p.supports_used ? " • " : ""}${p.supports_used ? "Usados/OpenBox" : ""}${p.supports_cep_shipping ? " • Frete real por CEP" : ""}${p.supports_pickup ? " • Retirada" : ""}</p>
          ${envHtml}
          <p><strong>Limitações:</strong> ${escapeHtml(p.limitations)}</p>
        </div>
      `;
    })
    .join("");
  openModal("Transparência de Fontes e APIs", html);
}

// --- Modal: Personalizar Pesos do Score ---
function openWeightsModal() {
  const isUsed = state.mode === "used";
  const current = state.customWeights || state.defaultWeights[isUsed ? "used" : "new"];
  const rows = Object.keys(WEIGHT_LABELS)
    .map(
      (k) => `
    <div class="field">
      <label for="w-${k}">${WEIGHT_LABELS[k]} (<span id="wval-${k}">${current[k]}</span>)</label>
      <input type="range" id="w-${k}" min="0" max="60" value="${current[k]}" style="height:auto;padding:0" />
    </div>
  `
    )
    .join("");

  openModal(
    "Personalizar pesos do Opportunity Score",
    `
      <p class="muted" style="margin-top:0;font-size:0.86rem">
        Ajuste o peso de cada critério. Os valores são normalizados automaticamente para somar 100%.
      </p>
      <form id="weights-form" class="form-stack">
        ${rows}
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:8px">
          <button type="button" class="btn btn-outline" id="btn-reset-weights">Restaurar padrão</button>
          <button type="submit" class="btn btn-accent">Aplicar pesos</button>
        </div>
      </form>
    `,
    (body) => {
      Object.keys(WEIGHT_LABELS).forEach((k) => {
        const inp = body.querySelector(`#w-${k}`);
        const out = body.querySelector(`#wval-${k}`);
        inp.addEventListener("input", () => {
          out.textContent = inp.value;
        });
      });
      body.querySelector("#btn-reset-weights").addEventListener("click", async () => {
        state.customWeights = null;
        if (state.user) {
          try {
            await api.updateProfile({ preferences: { custom_weights: null } });
          } catch {}
        }
        closeModal();
        showToast("Pesos padrão restaurados.", "success");
        if (state.view === "results") runSearch();
      });
      body.querySelector("#weights-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const w = {};
        Object.keys(WEIGHT_LABELS).forEach((k) => {
          w[k] = Number(body.querySelector(`#w-${k}`).value);
        });
        state.customWeights = w;
        if (state.user) {
          try {
            await api.updateProfile({ preferences: { custom_weights: w } });
          } catch {}
        }
        closeModal();
        showToast("Pesos aplicados com sucesso.", "success");
        if (state.view === "results") runSearch();
      });
    }
  );
}

// --- Modal: Login / Cadastro / Recuperação ---
function openAuthModal(initialTab = "login") {
  const renderTab = (tab) => {
    if (tab === "register") {
      return `
        <form id="auth-form" class="form-stack">
          <div class="field"><label for="a-name">Nome completo</label><input id="a-name" required minlength="2" /></div>
          <div class="field"><label for="a-email">E-mail</label><input id="a-email" type="email" required /></div>
          <div class="field"><label for="a-pass">Senha (mín. 8 caracteres, letras e números)</label><input id="a-pass" type="password" required minlength="8" /></div>
          <div class="field"><label for="a-cep">CEP preferencial (opcional)</label><input id="a-cep" maxlength="9" placeholder="01310-100" value="${escapeHtml(maskCep(state.cep))}" /></div>
          <div class="alert alert-error hidden" id="auth-err"></div>
          <button type="submit" class="btn btn-accent">Criar conta</button>
          <div style="text-align:center"><button type="button" class="link-btn" data-switch="login">Já tenho conta — Entrar</button></div>
        </form>
      `;
    }
    if (tab === "forgot") {
      return `
        <form id="forgot-form" class="form-stack">
          <p class="muted" style="margin:0;font-size:0.86rem">Informe seu e-mail para gerar um código temporário de redefinição de senha.</p>
          <div class="field"><label for="f-email">E-mail cadastrado</label><input id="f-email" type="email" required /></div>
          <div class="alert alert-error hidden" id="auth-err"></div>
          <div class="alert alert-info hidden" id="forgot-info"></div>
          <button type="submit" class="btn btn-primary">Solicitar recuperação</button>
        </form>
        <hr style="border:none;border-top:1px solid var(--border);margin:16px 0" />
        <form id="reset-form" class="form-stack">
          <div class="field"><label for="r-token">Código de recuperação</label><input id="r-token" required /></div>
          <div class="field"><label for="r-pass">Nova senha (mín. 8 caracteres, letras e números)</label><input id="r-pass" type="password" required minlength="8" /></div>
          <button type="submit" class="btn btn-accent">Redefinir senha</button>
          <div style="text-align:center"><button type="button" class="link-btn" data-switch="login">Voltar ao login</button></div>
        </form>
      `;
    }
    return `
      <form id="auth-form" class="form-stack">
        <div class="field"><label for="a-email">E-mail</label><input id="a-email" type="email" required /></div>
        <div class="field"><label for="a-pass">Senha</label><input id="a-pass" type="password" required /></div>
        <div class="alert alert-error hidden" id="auth-err"></div>
        <button type="submit" class="btn btn-accent">Entrar</button>
        <div style="display:flex;justify-content:space-between;font-size:0.85rem">
          <button type="button" class="link-btn" data-switch="register">Criar conta</button>
          <button type="button" class="link-btn" data-switch="forgot">Esqueci minha senha</button>
        </div>
      </form>
    `;
  };

  const titles = { login: "Entrar no Price Hunter", register: "Criar conta gratuita", forgot: "Recuperar senha" };
  openModal(titles[initialTab], renderTab(initialTab), (body) => {
    body.querySelectorAll("[data-switch]").forEach((btn) => {
      btn.addEventListener("click", () => openAuthModal(btn.dataset.switch));
    });
    const cepInput = body.querySelector("#a-cep");
    if (cepInput) {
      cepInput.addEventListener("input", () => {
        cepInput.value = maskCep(cepInput.value);
      });
    }
    const authForm = body.querySelector("#auth-form");
    if (authForm) {
      authForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const errBox = body.querySelector("#auth-err");
        errBox.classList.add("hidden");
        try {
          if (initialTab === "register") {
            await api.register({
              name: body.querySelector("#a-name").value,
              email: body.querySelector("#a-email").value,
              password: body.querySelector("#a-pass").value,
              preferred_cep: body.querySelector("#a-cep").value,
            });
            showToast("Conta criada! Bem-vindo ao Price Hunter.", "success");
          } else {
            await api.login({
              email: body.querySelector("#a-email").value,
              password: body.querySelector("#a-pass").value,
            });
            showToast("Login realizado com sucesso.", "success");
          }
          await refreshSession();
          closeModal();
          if (state.view === "results") renderFilteredOffers();
        } catch (err) {
          errBox.textContent = err.message;
          errBox.classList.remove("hidden");
        }
      });
    }
    const forgotForm = body.querySelector("#forgot-form");
    if (forgotForm) {
      forgotForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const errBox = body.querySelector("#auth-err");
        const infoBox = body.querySelector("#forgot-info");
        errBox.classList.add("hidden");
        try {
          const res = await api.forgotPassword(body.querySelector("#f-email").value);
          infoBox.innerHTML = escapeHtml(res.message) +
            (res.dev_reset_token ? `<br/><strong>Código local:</strong> <span class="code-chip">${escapeHtml(res.dev_reset_token)}</span>` : "");
          infoBox.classList.remove("hidden");
          if (res.dev_reset_token) {
            body.querySelector("#r-token").value = res.dev_reset_token;
          }
        } catch (err) {
          errBox.textContent = err.message;
          errBox.classList.remove("hidden");
        }
      });
    }
    const resetForm = body.querySelector("#reset-form");
    if (resetForm) {
      resetForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const errBox = body.querySelector("#auth-err");
        errBox.classList.add("hidden");
        try {
          const res = await api.resetPassword({
            token: body.querySelector("#r-token").value,
            new_password: body.querySelector("#r-pass").value,
          });
          showToast(res.message, "success");
          openAuthModal("login");
        } catch (err) {
          errBox.textContent = err.message;
          errBox.classList.remove("hidden");
        }
      });
    }
  });
}

// --- Modal: Perfil ---
function openProfileModal() {
  if (!state.user) return;
  const u = state.user;
  openModal(
    "Meu Perfil e Preferências",
    `
      <form id="profile-form" class="form-stack">
        <div class="field"><label for="p-name">Nome</label><input id="p-name" value="${escapeHtml(u.name)}" required /></div>
        <div class="field"><label>E-mail</label><input value="${escapeHtml(u.email)}" disabled /></div>
        <div class="field"><label for="p-cep">CEP preferencial</label><input id="p-cep" maxlength="9" value="${escapeHtml(u.preferred_cep_formatted || "")}" placeholder="01310-100" /></div>
        <div class="field">
          <label for="p-mode">Modo padrão de pesquisa</label>
          <select id="p-mode">
            <option value="all" ${u.preferences.default_mode === "all" ? "selected" : ""}>Todos</option>
            <option value="new" ${u.preferences.default_mode === "new" ? "selected" : ""}>Produtos Novos</option>
            <option value="used" ${u.preferences.default_mode === "used" ? "selected" : ""}>Usados &amp; Seminovos</option>
          </select>
        </div>
        <div class="field">
          <label for="p-budget">Orçamento de referência (R$, opcional)</label>
          <input id="p-budget" type="number" min="0" step="50" value="${u.preferences.max_budget ?? ""}" placeholder="Ex.: 2500" />
        </div>
        <div class="alert alert-error hidden" id="prof-err"></div>
        <button type="submit" class="btn btn-accent">Salvar perfil</button>
      </form>
    `,
    (body) => {
      const cepEl = body.querySelector("#p-cep");
      cepEl.addEventListener("input", () => {
        cepEl.value = maskCep(cepEl.value);
      });
      body.querySelector("#profile-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const errBox = body.querySelector("#prof-err");
        errBox.classList.add("hidden");
        try {
          const { user } = await api.updateProfile({
            name: body.querySelector("#p-name").value,
            preferred_cep: cepEl.value,
            preferences: {
              default_mode: body.querySelector("#p-mode").value,
              max_budget: body.querySelector("#p-budget").value || null,
            },
          });
          state.user = user;
          updateAuthUI();
          if (user.preferred_cep) {
            els.inputCep.value = maskCep(user.preferred_cep);
            resolveCep(user.preferred_cep, true);
          }
          closeModal();
          showToast("Perfil salvo com sucesso.", "success");
        } catch (err) {
          errBox.textContent = err.message;
          errBox.classList.remove("hidden");
        }
      });
    }
  );
}

// --- Favoritos ---
async function toggleFavorite(offer) {
  if (!state.user) {
    showToast("Faça login para salvar favoritos.", "error");
    openAuthModal("login");
    return;
  }
  const existing = state.favorites.find((f) => f.original_url === offer.url);
  try {
    if (existing) {
      await api.deleteFavorite(existing.id);
      showToast("Oferta removida dos favoritos.");
    } else {
      await api.addFavorite({
        offer_id: offer.id,
        product_key: offer.product_key,
        product_title: offer.title,
        brand: offer.brand,
        model: offer.model,
        store: offer.store,
        observed_price: offer.price,
        shipping_price: offer.shipping_price,
        final_price: offer.final_price,
        original_url: offer.url,
        image_url: offer.image_url,
        condition: offer.condition,
        cep_used: state.location?.cep || state.cep,
        opportunity_score: offer.opportunity_score,
      });
      showToast("Oferta salva nos seus favoritos!", "success");
    }
    await refreshFavorites();
    if (state.view === "results") renderFilteredOffers();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function renderFavorites() {
  if (!state.user) {
    els.favoritesList.innerHTML = `<div class="card">Faça login para visualizar seus favoritos.</div>`;
    return;
  }
  if (!state.favorites.length) {
    els.favoritesList.innerHTML = `
      <div class="card">
        <strong>Você ainda não salvou nenhum favorito.</strong>
        <p class="muted" style="margin:6px 0 0">Pesquise produtos e clique em "☆ Favoritar" para acompanhar ofertas reais.</p>
      </div>
    `;
    return;
  }
  els.favoritesList.innerHTML = state.favorites
    .map((f) => {
      const delta = Number(f.price_delta || 0);
      const deltaHtml =
        delta < 0
          ? `<span class="badge badge-best_overall">Baixou ${formatBRL(Math.abs(delta))}</span>`
          : delta > 0
          ? `<span class="badge badge-cond-used">Subiu ${formatBRL(delta)}</span>`
          : `<span class="badge badge-cond-new">Sem variação observada</span>`;
      return `
        <article class="offer-card">
          <div class="offer-img-box">
            ${
              f.image_url
                ? `<img src="${escapeHtml(f.image_url)}" alt="${escapeHtml(f.product_title)}" loading="lazy" />`
                : `<span class="img-placeholder">Sem imagem</span>`
            }
          </div>
          <div class="offer-main">
            <div class="badge-row">
              <span class="badge badge-cond-${escapeHtml(f.condition)}">${escapeHtml(conditionLabel(f.condition))}</span>
              ${deltaHtml}
            </div>
            <h3 class="offer-title">${escapeHtml(f.product_title)}</h3>
            <div class="offer-meta-line">
              <span><strong>${escapeHtml(f.store)}</strong></span>
              ${f.cep_used ? `<span>CEP consultado: ${escapeHtml(maskCep(f.cep_used))}</span>` : ""}
              <span>Salvo em ${formatDateTime(f.created_at)}</span>
            </div>
          </div>
          <div class="offer-aside">
            <div class="price-block">
              <div class="muted" style="font-size:0.76rem">Preço observado ao salvar</div>
              <div class="price-main">${formatBRL(f.observed_price)}</div>
              ${f.final_price != null ? `<div class="price-final">Final: ${formatBRL(f.final_price)}</div>` : ""}
            </div>
            <div class="offer-actions">
              <a class="btn btn-accent" href="${escapeHtml(f.original_url)}" target="_blank" rel="noopener noreferrer">
                Abrir oferta original ↗
              </a>
              <button type="button" class="btn btn-danger btn-sm" data-del-fav="${f.id}">
                Remover favorito
              </button>
            </div>
          </div>
        </article>
      `;
    })
    .join("");

  els.favoritesList.querySelectorAll("[data-del-fav]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api.deleteFavorite(btn.dataset.delFav);
        showToast("Favorito removido.");
        await refreshFavorites();
      } catch (err) {
        showToast(err.message, "error");
      }
    });
  });
}

// --- Inicialização & Eventos ---
function bindEvents() {
  document.getElementById("btn-home").addEventListener("click", () => setView("home"));
  document.getElementById("btn-providers").addEventListener("click", openProvidersModal);
  document.getElementById("btn-open-providers-home").addEventListener("click", openProvidersModal);
  document.getElementById("btn-weights").addEventListener("click", openWeightsModal);
  els.btnAuth.addEventListener("click", () => openAuthModal("login"));
  els.btnProfile.addEventListener("click", openProfileModal);
  els.btnLogout.addEventListener("click", async () => {
    await api.logout();
    await refreshSession();
    if (state.view === "favorites") setView("home");
    showToast("Você saiu da sua conta.");
  });

  document.getElementById("btn-favorites-nav").addEventListener("click", async () => {
    if (!state.user) {
      openAuthModal("login");
      return;
    }
    await refreshFavorites();
    setView("favorites");
    renderFavorites();
  });

  document.getElementById("btn-back-from-favs").addEventListener("click", () => {
    setView(state.lastSearch ? "results" : "home");
  });

  // Modal close
  els.modalClose.addEventListener("click", closeModal);
  els.modalBackdrop.addEventListener("click", (e) => {
    if (e.target === els.modalBackdrop) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !els.modalBackdrop.classList.contains("hidden")) closeModal();
  });

  // Mode tabs
  document.querySelectorAll(".mode-tab").forEach((tab) => {
    tab.addEventListener("click", () => setMode(tab.dataset.mode || "all"));
  });

  // CEP mask & auto-resolve
  els.inputCep.addEventListener("input", () => {
    els.inputCep.value = maskCep(els.inputCep.value);
    clearTimeout(cepLookupTimer);
    if (normalizeCepDigits(els.inputCep.value).length === 8) {
      cepLookupTimer = setTimeout(() => resolveCep(els.inputCep.value, true), 250);
    }
  });

  // Search form submit
  els.searchForm.addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch();
  });

  // Sort & Filters (instantâneo sem recarregar)
  els.selectSort.addEventListener("change", () => {
    state.sortBy = els.selectSort.value;
    renderFilteredOffers();
  });

  const bindFilter = (id, key, isCheckbox = false) => {
    const el = document.getElementById(id);
    if (!el) return;
    const ev = isCheckbox || el.tagName === "SELECT" ? "change" : "input";
    el.addEventListener(ev, () => {
      state.filters[key] = isCheckbox ? el.checked : el.value;
      renderFilteredOffers();
    });
  };
  bindFilter("f-price-min", "priceMin");
  bindFilter("f-price-max", "priceMax");
  bindFilter("f-store", "store");
  bindFilter("f-condition", "condition");
  bindFilter("f-brand", "brand");
  bindFilter("f-category", "category");
  bindFilter("f-max-delivery", "maxDelivery");
  bindFilter("f-max-distance", "maxDistance");
  bindFilter("f-min-rating", "minRating");
  bindFilter("f-free-shipping", "freeShipping", true);
  bindFilter("f-pickup", "pickup", true);
  bindFilter("f-warranty", "warranty", true);
  bindFilter("f-exact-only", "exactOnly", true);

  document.getElementById("btn-clear-filters").addEventListener("click", () => resetFilters(true));
  document.getElementById("btn-toggle-filters").addEventListener("click", () => {
    els.filtersPanel.classList.toggle("open");
  });
}

async function init() {
  bindEvents();
  if (state.cep) {
    els.inputCep.value = maskCep(state.cep);
    resolveCep(state.cep, true);
  }
  await Promise.all([refreshSession(), loadHome()]);
}

init();
