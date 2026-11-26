/**
 * Cliente HTTP da API REST do Price Hunter.
 */

async function request(url, options = {}) {
  const opts = {
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  };
  if (opts.body && typeof opts.body === "object") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(url, opts);
  let data = {};
  try {
    data = await resp.json();
  } catch {
    data = {};
  }
  if (!resp.ok) {
    const err = new Error(data.error || `Erro HTTP ${resp.status}`);
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  getHome: () => request("/api/home"),
  getProviders: () => request("/api/providers"),
  lookupCep: (cep) => request(`/api/cep/${encodeURIComponent(cep)}`),
  search: ({ q, cep, mode, weights }) => {
    const params = new URLSearchParams({ q, cep, mode: mode || "all" });
    if (weights) {
      params.set("weights", JSON.stringify(weights));
    }
    return request(`/api/search?${params.toString()}`);
  },
  getMe: () => request("/api/auth/me"),
  register: (payload) => request("/api/auth/register", { method: "POST", body: payload }),
  login: (payload) => request("/api/auth/login", { method: "POST", body: payload }),
  logout: () => request("/api/auth/logout", { method: "POST", body: {} }),
  updateProfile: (payload) => request("/api/auth/profile", { method: "PATCH", body: payload }),
  forgotPassword: (email) => request("/api/auth/forgot-password", { method: "POST", body: { email } }),
  resetPassword: (payload) => request("/api/auth/reset-password", { method: "POST", body: payload }),
  listFavorites: () => request("/api/favorites"),
  addFavorite: (payload) => request("/api/favorites", { method: "POST", body: payload }),
  deleteFavorite: (id) => request(`/api/favorites/${id}`, { method: "DELETE" }),
};
