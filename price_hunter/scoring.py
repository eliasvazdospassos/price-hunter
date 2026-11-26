"""Price Hunter Opportunity Score (0–100) com pesos configuráveis e explicabilidade total."""

from __future__ import annotations

import statistics
from typing import Any

from .config import DEFAULT_NEW_WEIGHTS, DEFAULT_USED_WEIGHTS

FACTOR_LABELS = {
    "competitive_price": "Preço competitivo",
    "total_cost": "Custo total (produto + frete)",
    "delivery_speed": "Velocidade de entrega / disponibilidade",
    "seller_reputation": "Confiabilidade da loja e vendedor",
    "condition_warranty": "Condição e garantia",
    "proximity": "Proximidade e retirada presencial",
}

CONDITION_BASE_SCORES = {
    "new": 92.0,
    "seminovo": 76.0,
    "refurbished": 68.0,
    "used": 54.0,
}

# Reputação base para lojas oficiais verificadas quando avaliação individual do anúncio não está disponível
STORE_BASELINE_TRUST = {
    "KaBuM!": 88.0,
    "Carrefour Brasil": 84.0,
    "Bemol": 85.0,
    "Loja Electrolux": 90.0,
    "Mercado Livre": 82.0,
    "Amazon Brasil": 90.0,
    "Magazine Luiza": 86.0,
    "Casas Bahia": 80.0,
    "Shopee Brasil": 72.0,
}


def normalize_weights(custom: dict[str, Any] | None, is_used_profile: bool) -> dict[str, float]:
    """Normaliza pesos customizados ou aplica o perfil adaptativo (novo vs. usado) somando 100%."""
    base = dict(DEFAULT_USED_WEIGHTS if is_used_profile else DEFAULT_NEW_WEIGHTS)
    if custom and isinstance(custom, dict):
        for k in base:
            if k in custom:
                try:
                    val = float(custom[k])
                    if 0 <= val <= 100:
                        base[k] = val
                except (TypeError, ValueError):
                    pass
    total = sum(base.values())
    if total <= 0:
        base = dict(DEFAULT_USED_WEIGHTS if is_used_profile else DEFAULT_NEW_WEIGHTS)
        total = sum(base.values())
    return {k: round((v / total) * 100.0, 1) for k, v in base.items()}


def _score_relative_price(value: float, reference_median: float, reference_min: float) -> tuple[float, str]:
    if reference_median <= 0:
        return 50.0, "Sem referência suficiente de mercado para comparar preço."
    ratio_vs_median = value / reference_median
    # Quanto menor em relação à mediana, maior a nota (curva suave com teto 98)
    if value <= reference_min * 1.005:
        score = 96.0
        desc = "Entre os menores valores encontrados nesta pesquisa."
    elif ratio_vs_median <= 0.85:
        score = 92.0
        desc = f"{round((1 - ratio_vs_median) * 100)}% abaixo da mediana das ofertas."
    elif ratio_vs_median <= 0.98:
        score = 78.0 + (0.98 - ratio_vs_median) * 100
        desc = f"{round((1 - ratio_vs_median) * 100)}% abaixo da mediana observada."
    elif ratio_vs_median <= 1.05:
        score = 72.0 - (ratio_vs_median - 0.98) * 120
        desc = "Na média de preço do mercado atual."
    elif ratio_vs_median <= 1.25:
        score = 62.0 - (ratio_vs_median - 1.05) * 160
        desc = f"{round((ratio_vs_median - 1) * 100)}% acima da mediana observada."
    else:
        score = max(15.0, 30.0 - (ratio_vs_median - 1.25) * 35)
        desc = f"{round((ratio_vs_median - 1) * 100)}% acima da mediana observada."
    return round(min(98.0, max(12.0, score)), 1), desc


def _score_delivery(offer: dict[str, Any]) -> tuple[float, str, bool]:
    """Retorna (nota, explicação, dado_incompleto)."""
    days = offer.get("delivery_days")
    pickup = bool(offer.get("pickup_available"))
    pickup_only = bool(offer.get("pickup_only"))

    if pickup_only:
        dist = offer.get("distance_km")
        if dist is not None and dist <= 15:
            return 84.0, f"Apenas retirada presencial próxima (~{dist} km).", False
        if dist is not None and dist <= 40:
            return 62.0, f"Apenas retirada presencial (~{dist} km).", False
        return 42.0, "Apenas retirada presencial (sem entrega informada).", True

    if days is not None:
        d = int(days)
        if d <= 1:
            return 98.0, "Entrega ultrarrápida (até 1 dia útil).", False
        if d <= 3:
            return 88.0, f"Entrega rápida ({d} dias úteis).", False
        if d <= 5:
            return 76.0, f"Prazo padrão ({d} dias úteis).", False
        if d <= 9:
            return 60.0, f"Prazo moderado ({d} dias úteis).", False
        return 40.0, f"Prazo longo ({d} dias úteis).", False

    if pickup:
        return 75.0, "Retirada disponível; prazo de entrega não informado.", True

    return 52.0, "Prazo de entrega não informado pela fonte (consulte na loja).", True


def _score_reputation(offer: dict[str, Any]) -> tuple[float, str, bool]:
    rating = offer.get("seller_rating")
    rating_count = int(offer.get("rating_count") or 0)
    store = offer.get("store", "")
    is_mp = bool(offer.get("is_marketplace"))
    baseline = STORE_BASELINE_TRUST.get(store, 65.0)
    if is_mp:
        baseline = max(55.0, baseline - 10.0)

    if rating is not None and float(rating) > 0:
        r = float(rating)
        # Escala 0–5 normalizada para 0–100, ponderada com baseline da loja quando poucas avaliações
        rating_score = min(100.0, max(10.0, (r / 5.0) * 100.0))
        if rating_count >= 10:
            final = rating_score * 0.75 + baseline * 0.25
            desc = f"Nota {r:.1f}/5 ({rating_count} avaliações) em {store}."
        else:
            final = rating_score * 0.45 + baseline * 0.55
            desc = f"Nota {r:.1f}/5 ({rating_count} avaliações); reputação complementar da loja {store}."
        return round(final, 1), desc, False

    if is_mp:
        seller = offer.get("seller_name") or "vendedor parceiro"
        return (
            round(baseline, 1),
            f"Vendido por {seller} via marketplace {store}; avaliação individual não informada.",
            True,
        )

    return (
        round(baseline, 1),
        f"Venda direta por {store}; avaliação individual do item indisponível.",
        False,
    )


def _score_condition_warranty(offer: dict[str, Any]) -> tuple[float, str, bool]:
    condition = offer.get("condition", "new")
    warranty = (offer.get("warranty") or "").strip()
    base = CONDITION_BASE_SCORES.get(condition, 60.0)

    w_lower = warranty.lower()
    if warranty and "sem garantia" not in w_lower:
        if any(k in w_lower for k in ("12", "1 ano", "24", "2 anos", "36", "3 anos")):
            base = min(98.0, base + 8.0)
            desc = f"Condição '{_condition_pt(condition)}' com garantia informada ({warranty})."
        else:
            base = min(95.0, base + 4.0)
            desc = f"Condição '{_condition_pt(condition)}' ({warranty})."
        return round(base, 1), desc, False

    if condition == "new":
        if "sem garantia" in w_lower:
            return 68.0, "Produto novo, porém anúncio informa 'Sem garantia' — verifique antes de comprar.", False
        return 82.0, "Produto novo (garantia legal padrão esperada; não detalhada pela API).", True

    return (
        round(base, 1),
        f"Produto {_condition_pt(condition)} sem garantia adicional declarada.",
        True,
    )


def _score_proximity(offer: dict[str, Any]) -> tuple[float, str, bool]:
    dist = offer.get("distance_km")
    pickup = bool(offer.get("pickup_available"))
    same_state = bool(offer.get("same_state"))

    if dist is not None:
        approx_txt = " (aprox.)" if offer.get("distance_is_approximate", True) else ""
        if dist <= 5:
            return 98.0, f"Muito próximo ao seu CEP: {dist} km{approx_txt}.", False
        if dist <= 15:
            return 88.0, f"Próximo ao seu CEP: {dist} km{approx_txt}.", False
        if dist <= 35:
            return 74.0, f"Mesma região: {dist} km{approx_txt}.", False
        if dist <= 100:
            return 56.0, f"Distância intermediária: {dist} km{approx_txt}.", False
        return 35.0, f"Distante do seu CEP: {dist} km{approx_txt}.", False

    if pickup:
        return 82.0, "Retirada presencial disponível para o seu CEP.", False

    if same_state:
        return 68.0, "Mesma UF do CEP informado (distância exata indisponível).", True

    return 50.0, "Localização do estoque/vendedor não informada pela fonte.", True


def _fmt_brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _condition_pt(condition: str) -> str:
    return {
        "new": "Novo",
        "used": "Usado",
        "seminovo": "Seminovo / OpenBox",
        "refurbished": "Recondicionado",
    }.get(condition, condition)


def classify_score(score: int, evaluation_limited: bool, missing_count: int) -> tuple[str, str]:
    """
    Classifica a oportunidade. Nunca classifica como 'Excelente oportunidade' apenas por preço baixo
    ou quando faltam dados críticos demais.
    """
    if score >= 80 and not (evaluation_limited and missing_count >= 3):
        return "Excelente oportunidade", "excellent"
    if score >= 65:
        return "Boa oportunidade", "good"
    if score >= 45:
        return "Oportunidade razoável", "fair"
    return "Pouco vantajosa", "poor"


def score_and_rank_offers(
    offers: list[dict[str, Any]],
    mode: str = "all",
    custom_weights: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Calcula Price Hunter Opportunity Score, badges e comparativos de custo vs. velocidade.
    """
    if not offers:
        return [], {
            "weights_applied": normalize_weights(custom_weights, mode == "used"),
            "highlights": {},
            "tradeoff_summary": None,
            "market_stats": None,
        }

    # Calcula referências de mercado usando ofertas com equivalência 'exact' ou 'probable' primeiro
    comparable = [o for o in offers if o.get("equivalence_status") in ("exact", "probable")]
    if len(comparable) < 2:
        comparable = offers

    prices = [float(o["price"]) for o in comparable if o.get("price") and float(o["price"]) > 0]
    median_price = statistics.median(prices) if prices else 0.0
    min_price = min(prices) if prices else 0.0

    final_prices = [
        float(o["final_price"])
        for o in comparable
        if o.get("final_price") is not None and float(o["final_price"]) > 0
    ]
    median_final = statistics.median(final_prices) if final_prices else median_price
    min_final = min(final_prices) if final_prices else min_price

    small_sample = len(comparable) < 2
    profile_weights_sample = normalize_weights(custom_weights, mode == "used")

    for offer in offers:
        is_used = offer.get("condition") in ("used", "seminovo", "refurbished") or mode == "used"
        weights = normalize_weights(custom_weights, is_used)

        price = float(offer["price"])
        shipping = offer.get("shipping_price")
        if shipping is not None:
            final_price = round(price + float(shipping), 2)
            offer["final_price"] = final_price
            shipping_known = True
        elif offer.get("free_shipping"):
            offer["shipping_price"] = 0.0
            final_price = round(price, 2)
            offer["final_price"] = final_price
            shipping_known = True
        else:
            final_price = round(price, 2)
            offer["final_price"] = None  # Transparência: não finge que frete é zero!
            shipping_known = False

        missing_factors = 0
        if small_sample:
            missing_factors += 1

        # 1. Preço competitivo
        s_price, d_price = _score_relative_price(price, median_price, min_price)
        if small_sample:
            d_price = "Amostra pequena nesta consulta — avaliação comparativa limitada."

        # 2. Custo total (produto + frete)
        if shipping_known:
            s_total, d_total = _score_relative_price(final_price, median_final, min_final)
            if offer.get("shipping_price") == 0:
                s_total = min(98.0, s_total + 4.0)
                d_total = f"Frete grátis confirmado. {d_total}"
            else:
                d_total = f"Total {_fmt_brl(final_price)} (inclui frete de {_fmt_brl(float(offer['shipping_price']))})."
        else:
            missing_factors += 1
            s_total = max(35.0, min(68.0, s_price - 10.0))
            d_total = "Frete não retornado pela API para este CEP — preço final pode variar."

        # 3. Velocidade de entrega
        s_deliv, d_deliv, m_deliv = _score_delivery(offer)
        if m_deliv:
            missing_factors += 1

        # 4. Confiabilidade
        s_rep, d_rep, m_rep = _score_reputation(offer)
        if m_rep:
            missing_factors += 1

        # 5. Condição e garantia
        s_cond, d_cond, m_cond = _score_condition_warranty(offer)
        if m_cond:
            missing_factors += 1

        # 6. Proximidade
        s_prox, d_prox, m_prox = _score_proximity(offer)
        if m_prox and is_used:
            missing_factors += 1

        # Penalização moderada se o modelo divergir do pesquisado
        eq_penalty = 0.0
        if offer.get("equivalence_status") == "variant":
            eq_penalty = 8.0

        raw_factors = [
            ("competitive_price", s_price, d_price),
            ("total_cost", s_total, d_total),
            ("delivery_speed", s_deliv, d_deliv),
            ("seller_reputation", s_rep, d_rep),
            ("condition_warranty", s_cond, d_cond),
            ("proximity", s_prox, d_prox),
        ]

        weighted_sum = 0.0
        factor_details = []
        for key, f_score, explanation in raw_factors:
            w = weights[key]
            contrib = round((f_score * w) / 100.0, 1)
            weighted_sum += contrib
            factor_details.append(
                {
                    "key": key,
                    "label": FACTOR_LABELS[key],
                    "score": round(f_score, 1),
                    "weight": w,
                    "weighted_points": contrib,
                    "explanation": explanation,
                }
            )

        # Regra anti-viés: nunca classificar como excelente só pelo menor preço se reputação/condição forem ruins
        final_score = int(round(max(5.0, min(99.0, weighted_sum - eq_penalty))))
        if s_rep < 55 or s_cond < 55:
            final_score = min(final_score, 74)

        eval_limited = missing_factors >= 2 or not shipping_known
        if eval_limited and missing_factors >= 3:
            final_score = min(final_score, 78)

        classification, tier = classify_score(final_score, eval_limited, missing_factors)

        offer["opportunity_score"] = final_score
        offer["opportunity_label"] = classification
        offer["opportunity_tier"] = tier
        offer["evaluation_limited"] = eval_limited
        offer["evaluation_note"] = (
            "Avaliação parcialmente limitada: alguns dados (frete, prazo ou reputação) não foram fornecidos pela fonte."
            if eval_limited
            else "Avaliação completa com preço, frete/disponibilidade e confiabilidade."
        )
        offer["score_profile"] = "used" if is_used else "new"
        offer["score_breakdown"] = factor_details
        offer["badges"] = []

    # --- Cálculo de selos/classificações comparativas ---
    badge_pool = [o for o in offers if o.get("equivalence_status") != "variant"] or offers
    highlights: dict[str, str] = {}

    # 1. Menor preço anunciado
    cheapest = min(badge_pool, key=lambda o: float(o["price"]))
    cheapest["badges"].append({"id": "lowest_price", "label": "Menor preço"})
    highlights["lowest_price"] = cheapest["id"]

    # 2. Menor preço final (quando há frete calculado)
    with_final = [o for o in badge_pool if o.get("final_price") is not None]
    if with_final:
        lowest_final = min(with_final, key=lambda o: float(o["final_price"]))
        if lowest_final["id"] != cheapest["id"] or len(with_final) >= 2:
            lowest_final["badges"].append({"id": "lowest_final", "label": "Menor preço final"})
        highlights["lowest_final"] = lowest_final["id"]

    # 3. Entrega mais rápida
    with_delivery = [o for o in badge_pool if o.get("delivery_days") is not None]
    fastest = None
    if with_delivery:
        fastest = min(with_delivery, key=lambda o: (int(o["delivery_days"]), float(o.get("final_price") or o["price"])))
        fastest["badges"].append({"id": "fastest_delivery", "label": "Entrega mais rápida"})
        highlights["fastest_delivery"] = fastest["id"]

    # 4. Melhor oportunidade geral
    best_overall = max(
        badge_pool,
        key=lambda o: (int(o["opportunity_score"]), -float(o.get("final_price") or o["price"])),
    )
    best_overall["badges"].append({"id": "best_overall", "label": "Melhor oportunidade geral"})
    highlights["best_overall"] = best_overall["id"]

    # 5. Melhor custo-benefício (equilíbrio entre preço relativo, entrega e garantia)
    def value_metric(o: dict[str, Any]) -> float:
        by_key = {f["key"]: f["score"] for f in o["score_breakdown"]}
        return (
            by_key["competitive_price"] * 0.40
            + by_key["total_cost"] * 0.25
            + by_key["seller_reputation"] * 0.20
            + by_key["condition_warranty"] * 0.15
        )

    best_value = max(badge_pool, key=value_metric)
    if all(b["id"] != "best_value" for b in best_value["badges"]):
        best_value["badges"].append({"id": "best_value", "label": "Melhor custo-benefício"})
    highlights["best_value"] = best_value["id"]

    # 6. Oferta próxima / Retirada
    for o in offers:
        dist = o.get("distance_km")
        if (dist is not None and dist <= 25) or o.get("pickup_available"):
            o["badges"].append({"id": "nearby", "label": "Próxima ao CEP / Retirada"})

    # --- Insight de trade-off: Menor preço vs. Entrega mais rápida ---
    tradeoff = None
    if fastest and with_final:
        cheapest_final = min(with_final, key=lambda o: float(o["final_price"]))
        if fastest["id"] == cheapest_final["id"]:
            tradeoff = {
                "type": "win_win",
                "title": "Melhor cenário: menor preço final e entrega mais rápida coincidem",
                "detail": (
                    f"{fastest['store']} oferece o menor total ({_fmt_brl(fastest['final_price'])}) "
                    f"com o menor prazo ({fastest['delivery_days']} dias úteis)."
                ),
            }
        elif cheapest_final.get("delivery_days") is not None:
            price_diff = round(float(fastest.get("final_price") or fastest["price"]) - float(cheapest_final["final_price"]), 2)
            days_saved = int(cheapest_final["delivery_days"]) - int(fastest["delivery_days"])
            if days_saved > 0 and price_diff >= 0:
                tradeoff = {
                    "type": "speed_vs_price",
                    "title": f"Economize {days_saved} dia(s) útil(eis) por {_fmt_brl(price_diff)} a mais",
                    "detail": (
                        f"Entrega mais rápida em {fastest['store']} ({fastest['delivery_days']} dias úteis) "
                        f"vs. menor preço final em {cheapest_final['store']} "
                        f"({_fmt_brl(cheapest_final['final_price'])}, {cheapest_final['delivery_days']} dias úteis)."
                    ),
                }

    # Ordenação padrão: Maior Opportunity Score
    offers.sort(key=lambda o: (-int(o["opportunity_score"]), float(o.get("final_price") or o["price"])))

    market_stats = {
        "min_price": round(min_price, 2),
        "median_price": round(median_price, 2),
        "max_price": round(max(prices), 2) if prices else 0.0,
        "comparable_count": len(comparable),
        "total_count": len(offers),
    }

    return offers, {
        "weights_applied": profile_weights_sample,
        "highlights": highlights,
        "tradeoff_summary": tradeoff,
        "market_stats": market_stats,
    }
