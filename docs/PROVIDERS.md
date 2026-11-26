# Fontes de Dados e Integrações — Price Hunter

Este documento registra a investigação técnica realizada antes da implementação de cada provedor, seguindo estritamente critérios de legitimidade, respeito aos Termos de Uso e transparência com o usuário.

## Resumo de Status

| Fonte | Status | API Oficial / Pública | Autenticação | Frete por CEP | Novos / Usados |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **KaBuM!** | **Ativa (Real)** | Storefront Catalog v2 (`servicespub.prod.api.aws.grupokabum.com.br`) | Nenhuma | Frete grátis indicado quando disponível | Novos, OpenBox e Usados (Marketplace) |
| **Carrefour Brasil** | **Ativa (Real)** | VTEX Public Storefront Catalog + Checkout Shipping Simulation | Nenhuma | **Sim (real + prazo + retirada)** | Novos |
| **Bemol** | **Ativa (Real)** | VTEX Public Storefront Catalog + Checkout Shipping Simulation | Nenhuma | **Sim (real + prazo + retirada)** | Novos |
| **Loja Electrolux** | **Ativa (Real)** | VTEX Public Storefront Catalog + Checkout Shipping Simulation | Nenhuma | **Sim (real + prazo)** | Novos |
| **Mercado Livre** | **Pronta (OAuth 2.0)** | `https://api.mercadolibre.com/sites/MLB/search` | OAuth 2.0 (`MERCADOLIVRE_ACCESS_TOKEN`) | Frete grátis + Full | Novos e Usados |
| **Google Shopping BR** | **Pronta (SerpAPI)** | SerpAPI Google Shopping Engine (`gl=br`) | `SERPAPI_API_KEY` | Parcial (texto) | Novos e Usados |
| **Amazon Brasil** | **Preparada (Stub)** | PA-API 5.0 (AWS SigV4) | `AMAZON_ACCESS_KEY`, `AMAZON_SECRET_KEY`, `AMAZON_PARTNER_TAG` | Não nativo | Novos e Usados |
| **Shopee Brasil** | **Preparada (Stub)** | Shopee Affiliate Open Platform | `SHOPEE_APP_ID`, `SHOPEE_APP_SECRET` | Não nativo | Novos |
| **Magazine Luiza** | **Preparada (Stub)** | Magalu Partners / Open API | `MAGALU_API_KEY` (homologação comercial) | Com contrato | Novos |
| **Casas Bahia** | **Preparada (Stub)** | Rede de Afiliados Awin Product Feed | `AWIN_API_TOKEN`, `AWIN_PUBLISHER_ID` | Não nativo | Novos |
| **OLX Brasil** | **Sem API Pública** | Apenas OLX Pro (gestão de estoque próprio) | Indisponível para busca de terceiros | N/A | Usados / Retirada |

## Localização e CEP

| Serviço | Papel | Autenticação | Observações |
| :--- | :--- | :--- | :--- |
| **ViaCEP** | Consulta primária de endereço brasileiro por CEP | Nenhuma | Gratuito; retorna logradouro, bairro, cidade, UF e IBGE. Não fornece coordenadas. |
| **BrasilAPI v2** | Fallback de endereço por CEP | Nenhuma | Gratuito e open source. |
| **AwesomeAPI CEP** | Coordenadas aproximadas (lat/lng) por CEP | Nenhuma | Usado exclusivamente para cálculo de distância aproximada (sempre sinalizada ao usuário). |
| **OpenStreetMap Nominatim** | Fallback de geocodificação aproximada por CEP | Nenhuma | Respeita User-Agent identificável e cache local de 24h. |

## Política de Integridade

1. O Price Hunter **não realiza scraping indiscriminado** nem tenta contornar proteções anti-bot (Cloudflare, PolicyAgent, CAPTCHAs).
2. Todos os links de compra direcionam exclusivamente para a URL oficial retornada pela API da fonte consultada.
3. Nenhuma oferta fictícia é misturada aos resultados reais.
4. O histórico de preços é construído exclusivamente a partir de observações reais registradas durante as pesquisas; enquanto não houver observações suficientes, o sistema informa explicitamente que o histórico está em formação.
