/**
 * Utilitários de formatação, máscara de CEP e escape seguro contra XSS.
 */

export function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function formatBRL(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }
  return Number(value).toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
}

export function normalizeCepDigits(raw) {
  return String(raw || "").replace(/\D/g, "").slice(0, 8);
}

export function maskCep(raw) {
  const digits = normalizeCepDigits(raw);
  if (digits.length > 5) {
    return `${digits.slice(0, 5)}-${digits.slice(5)}`;
  }
  return digits;
}

export function conditionLabel(cond) {
  return (
    {
      new: "Novo",
      used: "Usado",
      seminovo: "Seminovo / OpenBox",
      refurbished: "Recondicionado",
    }[cond] || cond
  );
}

export function formatDateTime(unixTs) {
  if (!unixTs) return "";
  return new Date(unixTs * 1000).toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function showToast(message, kind = "") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.remove();
  }, 3600);
}
