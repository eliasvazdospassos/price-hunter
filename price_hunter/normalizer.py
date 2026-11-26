"""Normalização de consultas, detecção de produtos equivalentes e deduplicação de ofertas."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

KNOWN_BRANDS = {
    "amd", "intel", "nvidia", "asus", "gigabyte", "msi", "asrock", "corsair", "kingston",
    "crucial", "samsung", "apple", "motorola", "xiaomi", "redmi", "poco", "realme", "lg",
    "sony", "philips", "tcl", "aoc", "acer", "dell", "lenovo", "hp", "positivo", "vaio",
    "electrolux", "brastemp", "consul", "midea", "philco", "mondial", "arno", "oster",
    "bosch", "makita", "dewalt", "black+decker", "stanley", "vonder", "tramontina",
    "logitech", "razer", "hyperx", "redragon", "jbl", "bose", "anker", "tp-link",
    "wd", "seagate", "sandisk", "galax", "zotac", "pny", "sapphire", "xfx", "evga",
    "deepcool", "cooler master", "nzxt", "lian li", "superframe", "pichau", "rise mode",
}

ACCESSORY_KEYWORDS = {
    "pelicula", "capa", "capinha", "case", "maleta", "bolsa", "mochila", "suporte",
    "cabo", "adaptador", "carregador", "fonte", "bateria", "conector", "adesivo",
    "controle remoto", "peca", "reposicao", "borracha", "filtro", "mangueira",
    "escova", "kit limpeza", "pasta termica", "protetor", "chaveiro", "pulseira",
    "tripé", "tripe", "braço", "braco", "base cooler", "cooler fan",
}

USED_KEYWORDS = ("usado", "usada", "segunda mao", "vitrine", "outlet")
SEMINOVO_KEYWORDS = ("seminovo", "seminova", "openbox", "open box", "como novo")
REFURBISHED_KEYWORDS = ("recondicionado", "recondicionada", "refurbished", "reembalado", "recertificado")

CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "Smartphones": ("iphone", "galaxy", "smartphone", "celular", "redmi", "motorola", "moto g", "poco", "xiaomi"),
    "Computadores e Notebooks": ("notebook", "laptop", "macbook", "chromebook", "pc gamer", "desktop", "all in one", "ultrabook"),
    "Componentes de Informática": (
        "ryzen", "core i3", "core i5", "core i7", "core i9", "processador", "placa de video",
        "rtx", "gtx", "radeon", "placa mae", "memoria ram", "ddr4", "ddr5", "ssd", "nvme", "hd ", "fonte ", "gabinete", "water cooler",
    ),
    "Televisores": ("smart tv", "tv ", "qled", "oled", "uhd", "4k tv", "televisor"),
    "Eletrodomésticos": ("geladeira", "refrigerador", "fogao", "micro-ondas", "microondas", "lavadora", "lava e seca", "ar condicionado", "air fryer", "fritadeira", "aspirador", "cafeteira"),
    "Móveis": ("cadeira", "mesa", "escrivaninha", "sofa", "armario", "guarda-roupa", "estante", "cama", "colchao"),
    "Ferramentas": ("furadeira", "parafusadeira", "serra", "lixadeira", "esmerilhadeira", "chaves", "martelo", "martelete", "multimetro"),
    "Periféricos e Áudio": ("teclado", "mouse", "headset", "fone", "monitor", "webcam", "microfone", "caixa de som"),
}

# Padrões de especificações técnicas importantes para evitar agrupar modelos distintos
SPEC_PATTERNS = [
    re.compile(r"\b(\d{2,4}\s?(?:gb|tb))\b"),  # armazenamento/RAM: 128gb, 1tb
    re.compile(r"\b(\d{2}\s?(?:pol|polegadas|\"|''))\b"),  # tela: 55 pol, 15.6"
    re.compile(r"\b(rtx\s?\d{4}\s?(?:ti|super)?|rx\s?\d{4}\s?(?:xt|xtx)?|gtx\s?\d{4}\s?(?:ti)?)\b"),
    re.compile(r"\b(ryzen\s?[3579]\s?\d{4}[a-z0-9]*)\b"),
    re.compile(r"\b(i[3579][\-\s]?\d{4,5}[a-z]*)\b"),
    re.compile(r"\b(iphone\s?\d{2}\s?(?:pro\s?max|pro|plus|mini|e)?)\b"),
    re.compile(r"\b(galaxy\s?[asmz]\d{2}\s?(?:ultra|plus|\+|fe)?)\b"),
    re.compile(r"\b(ddr[45])\b"),
    re.compile(r"\b(\d{3,4}w)\b"),
    re.compile(r"\b(110v|127v|220v|bivolt)\b"),
]

STOPWORDS = {
    "de", "da", "do", "das", "dos", "para", "com", "sem", "em", "na", "no", "por",
    "e", "a", "o", "os", "as", "um", "uma", "novo", "nova", "usado", "usada",
    "seminovo", "seminova", "original", "oficial", "cor", "preto", "preta",
    "branco", "branca", "prata", "cinza", "azul", "verde", "vermelho", "rosa",
    "dourado", "titânio", "titanio", "grafite",
}


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_text(text: str) -> str:
    cleaned = strip_accents(text).lower()
    cleaned = re.sub(r"[^a-z0-9\+\-\.\" ]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def tokenize(text: str) -> list[str]:
    norm = normalize_text(text)
    tokens = re.findall(r"[a-z0-9]+", norm)
    return [t for t in tokens if len(t) >= 2 and t not in STOPWORDS]


def detect_condition(title: str, explicit_condition: str | None = None, is_openbox: bool = False) -> str:
    """Classifica condição em: new, used, seminovo, refurbished."""
    norm = normalize_text(title)
    if explicit_condition in ("used", "seminovo", "refurbished"):
        return explicit_condition
    if any(k in norm for k in REFURBISHED_KEYWORDS):
        return "refurbished"
    if is_openbox or any(k in norm for k in SEMINOVO_KEYWORDS):
        return "seminovo"
    if any(k in norm for k in USED_KEYWORDS):
        return "used"
    if explicit_condition == "new":
        return "new"
    return "new"


def detect_brand(title: str, explicit_brand: str = "") -> str:
    if explicit_brand and normalize_text(explicit_brand) not in ("", "nao informado", "generico", "outros", "marca"):
        return explicit_brand.strip()
    norm = normalize_text(title)
    for brand in sorted(KNOWN_BRANDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(brand)}\b", norm):
            return brand.upper() if len(brand) <= 3 else brand.title()
    return ""


def detect_category(text: str, explicit_category: str = "") -> str:
    norm = normalize_text(text)
    for cat, hints in CATEGORY_HINTS.items():
        if any(h in norm for h in hints):
            return cat
    if explicit_category:
        # Simplifica caminho hierárquico (ex.: "Hardware/Processadores/AMD" -> "Hardware / Processadores")
        parts = [p.strip() for p in explicit_category.strip("/").split("/") if p.strip()]
        if parts:
            return " / ".join(parts[:2])
    return "Geral"


def extract_specs(text: str) -> list[str]:
    norm = normalize_text(text)
    found: list[str] = []
    for pat in SPEC_PATTERNS:
        for m in pat.finditer(norm):
            val = re.sub(r"\s+", " ", m.group(1)).strip()
            if val and val not in found:
                found.append(val)
    return found


def extract_model_codes(text: str) -> list[str]:
    """Extrai códigos alfanuméricos de modelo (ex.: 5600g, rtx4060, s24, bro88ae)."""
    norm = normalize_text(text)
    codes: list[str] = []
    for tok in re.findall(r"\b[a-z0-9\-]{3,16}\b", norm):
        has_digit = any(c.isdigit() for c in tok)
        if not has_digit:
            continue
        # Ignora unidades genéricas já cobertas por specs
        if re.match(r"^\d+(gb|tb|mb|hz|khz|mhz|ghz|w|v|mm|cm|kg|ml|l|pol)$", tok):
            continue
        if tok not in codes:
            codes.append(tok)
    return codes[:6]


def is_accessory_title(title: str) -> bool:
    norm = normalize_text(title)
    return any(re.search(rf"\b{re.escape(k)}\b", norm) for k in ACCESSORY_KEYWORDS)


@dataclass
class QueryAnalysis:
    raw_query: str
    normalized_query: str
    tokens: list[str]
    brand: str
    category: str
    specs: list[str]
    model_codes: list[str]
    condition_intent: str  # "all" | "new" | "used"
    wants_accessory: bool
    product_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_query": self.raw_query,
            "normalized_query": self.normalized_query,
            "tokens": self.tokens,
            "brand": self.brand,
            "category": self.category,
            "specs": self.specs,
            "model_codes": self.model_codes,
            "condition_intent": self.condition_intent,
            "wants_accessory": self.wants_accessory,
            "product_key": self.product_key,
        }


def analyze_query(query: str, mode: str = "all") -> QueryAnalysis:
    norm = normalize_text(query)
    tokens = tokenize(query)
    brand = detect_brand(query)
    category = detect_category(query)
    specs = extract_specs(query)
    model_codes = extract_model_codes(query)
    wants_accessory = is_accessory_title(query)

    cond_intent = mode if mode in ("all", "new", "used") else "all"
    if cond_intent == "all":
        if any(k in norm for k in USED_KEYWORDS + SEMINOVO_KEYWORDS + REFURBISHED_KEYWORDS):
            cond_intent = "used"

    # Chave determinística estável para histórico de preços
    key_tokens = sorted(set(tokens))
    product_key = "-".join(key_tokens[:8]) or hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]

    return QueryAnalysis(
        raw_query=query,
        normalized_query=norm,
        tokens=tokens,
        brand=brand,
        category=category,
        specs=specs,
        model_codes=model_codes,
        condition_intent=cond_intent,
        wants_accessory=wants_accessory,
        product_key=product_key,
    )


def enrich_and_deduplicate_offers(
    offers: list[dict[str, Any]],
    analysis: QueryAnalysis,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Normaliza, filtra acessórios irrelevantes, deduplica URLs/itens idênticos
    e classifica equivalência de modelo para evitar comparar modelos diferentes como iguais.
    Retorna (ofertas_filtradas, grupos_equivalentes).
    """
    seen_urls: set[str] = set()
    seen_store_title_price: set[tuple[str, str, float]] = set()
    processed: list[dict[str, Any]] = []

    q_tokens = set(analysis.tokens)
    q_specs = set(analysis.specs)
    q_codes = set(analysis.model_codes)

    for raw in offers:
        url = (raw.get("url") or "").strip()
        title = (raw.get("title") or "").strip()
        price = raw.get("price")
        if not url or not title or price is None or float(price) <= 0:
            continue

        # Deduplicação exata por URL ou (loja, título normalizado, preço)
        url_key = url.split("?")[0].rstrip("/").lower()
        norm_title = normalize_text(title)
        dedup_sig = (raw.get("store", "").lower(), norm_title, round(float(price), 2))
        if url_key in seen_urls or dedup_sig in seen_store_title_price:
            continue
        seen_urls.add(url_key)
        seen_store_title_price.add(dedup_sig)

        offer = dict(raw)
        offer["brand"] = detect_brand(title, offer.get("brand", ""))
        offer["category"] = detect_category(title, offer.get("category", ""))
        offer["condition"] = detect_condition(
            title,
            offer.get("condition"),
            bool(offer.get("is_openbox", False)),
        )
        o_specs = extract_specs(title)
        o_codes = extract_model_codes(title)
        o_tokens = set(tokenize(title))

        offer["specs"] = o_specs
        offer["model"] = " ".join(o_specs[:2] or o_codes[:2]).upper()

        # Filtra acessórios quando o usuário pesquisou o produto principal
        title_is_accessory = is_accessory_title(title)
        if title_is_accessory and not analysis.wants_accessory:
            continue

        # Relevância lexical em relação à busca
        if q_tokens:
            overlap = len(q_tokens & o_tokens) / len(q_tokens)
        else:
            overlap = 0.5

        # Análise rigorosa de equivalência (não agrupa modelos diferentes!)
        differences: list[str] = []
        exact_match = True

        # Verifica códigos de modelo (ex.: pesquisou 5600g mas oferta é 3600 ou 5600x)
        if q_codes:
            missing_codes = [c for c in q_codes if c not in norm_title]
            if missing_codes:
                exact_match = False
                extra_codes = [c for c in o_codes if c not in q_codes][:2]
                if extra_codes:
                    differences.append(
                        f"Modelo diferente do pesquisado (encontrado: {', '.join(extra_codes).upper()}; buscado: {', '.join(missing_codes).upper()})"
                    )
                else:
                    differences.append(f"Código/modelo não confirmado: {', '.join(missing_codes).upper()}")

        # Verifica especificações (ex.: 128gb vs 256gb, Pro vs base)
        if q_specs:
            for sp in q_specs:
                if sp not in norm_title:
                    exact_match = False
                    differences.append(f"Especificação diferente ou não informada: {sp.upper()}")

        # Verifica divergência de tamanho de tela (ex.: buscou "TV 55" mas oferta é 58" ou 50")
        if analysis.category in ("Televisores", "Periféricos e Áudio"):
            q_sizes = re.findall(r"\b(24|27|32|40|42|43|50|55|58|60|65|70|75|85)\b", analysis.normalized_query)
            o_sizes = re.findall(r"\b(24|27|32|40|42|43|50|55|58|60|65|70|75|85)(?:\s?(?:pol|polegadas|\"|''|\b))", norm_title)
            if q_sizes and o_sizes and not any(sz in o_sizes for sz in q_sizes):
                exact_match = False
                differences.append(f"Tamanho de tela diferente (encontrado: {o_sizes[0]}\"; buscado: {q_sizes[0]}\")")

        # Detecta variantes importantes (Pro, Max, Plus, Ultra, Ti, XT, Mini) não pedidas na busca
        variant_words = ("pro", "max", "ultra", "plus", "mini", "lite", "fe", "ti", "super", "xt", "xtx")
        for vw in variant_words:
            in_query = bool(re.search(rf"\b{vw}\b", analysis.normalized_query))
            in_offer = bool(re.search(rf"\b{vw}\b", norm_title))
            if in_offer and not in_query:
                exact_match = False
                differences.append(f"Variante '{vw.upper()}' não especificada na busca")

        if exact_match and overlap >= 0.6:
            eq_status = "exact"
            eq_label = "Modelo equivalente confirmado"
        elif overlap >= 0.45 and not differences:
            eq_status = "probable"
            eq_label = "Provável equivalente"
        else:
            eq_status = "variant"
            eq_label = "Modelo/variante diferente — verifique antes de comparar"

        # Grupo de equivalência inclui marca + specs + códigos para NÃO misturar modelos distintos
        sig_parts = [
            normalize_text(offer["brand"]) or "sem-marca",
            *(sorted(o_specs[:3]) if o_specs else sorted(o_codes[:2])),
        ]
        eq_group = " | ".join(p for p in sig_parts if p) or norm_title[:40]
        product_key_parts = [p for p in [normalize_text(offer["brand"]), *o_specs[:2], *o_codes[:2]] if p]
        offer["product_key"] = (
            "-".join(re.findall(r"[a-z0-9]+", " ".join(product_key_parts))[:8])
            or analysis.product_key
        )
        offer["relevance_score"] = round(overlap * 100)
        offer["equivalence_status"] = eq_status
        offer["equivalence_label"] = eq_label
        offer["equivalence_group"] = eq_group
        offer["model_differences"] = differences

        # Descarta resultados claramente não relacionados
        if q_tokens and overlap < 0.25:
            continue

        processed.append(offer)

    # Agrupamento transparente apenas para produtos com equivalência confirmada
    group_map: dict[str, list[dict[str, Any]]] = {}
    for o in processed:
        if o["equivalence_status"] == "exact":
            group_map.setdefault(o["equivalence_group"], []).append(o)

    groups = [
        {
            "group_id": gid,
            "offer_count": len(items),
            "min_price": min(i["price"] for i in items),
            "stores": sorted({i["store"] for i in items}),
        }
        for gid, items in group_map.items()
        if len(items) >= 2
    ]

    return processed, groups
