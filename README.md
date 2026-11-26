# Price Hunter — Inteligência de Preços, Oportunidades e Compras por CEP

O **Price Hunter** é uma aplicação web full-stack para comparação inteligente de ofertas de produtos novos, seminovos/OpenBox e usados considerando **preço final (produto + frete), prazo de entrega, proximidade geográfica por CEP, confiabilidade da loja/vendedor e custo-benefício**.

## Funcionalidades Implementadas

- **Pesquisa inteligente multi-fonte em tempo real** com arquitetura modular de adaptadores e tolerância a falhas (se uma fonte falhar, as demais continuam respondendo e o usuário é avisado).
- **CEP brasileiro e localização aproximada**: validação de CEP, consulta via **ViaCEP** / **BrasilAPI v2**, coordenadas aproximadas via **AwesomeAPI CEP** / **OpenStreetMap Nominatim**, e cálculo de distância aproximada (nunca expondo endereço completo de vendedores particulares).
- **Normalização e equivalência rigorosa de produtos**: filtra acessórios irrelevantes (capas, películas, cabos) e sinaliza diferenças de modelo/variante (ex.: `Ryzen 5 5600G` vs `Ryzen 5 3600`, `iPhone 15` vs `iPhone 15 Pro`) sem agrupá-los automaticamente de forma enganosa.
- **Price Hunter Opportunity Score (0–100)**:
  - Pesos adaptativos para **produtos novos** vs. **usados/seminovos** e personalização de pesos pelo usuário;
  - Transparência total dos 6 fatores contribuintes;
  - Proteção contra viés (nunca classifica como *Excelente oportunidade* apenas pelo menor preço ou com dados insuficientes);
  - Comparativo automático **Menor preço final vs. Entrega mais rápida**.
- **Filtros e ordenação instantâneos** sem recarregar a página (preço, loja, marca, categoria, condição, prazo, distância, avaliação, frete grátis, retirada presencial, garantia).
- **Contas de usuário, perfil e favoritos**: cadastro, login, logout, recuperação de senha, CEP preferencial, preferências de pesquisa, favoritos persistidos em SQLite com hash `scrypt` e cookies `HttpOnly`. Inclui schema PostgreSQL/RLS pronto para evolução para **Supabase** (`supabase/schema.sql`).
- **Histórico real de preços**: registra observações reais de preço a cada pesquisa e exibe estatísticas apenas quando há dados suficientes (caso contrário, informa que o histórico está em formação).

## Fontes de Dados

Veja [docs/PROVIDERS.md](docs/PROVIDERS.md) para a investigação completa de cada fonte:

- **Ativas em tempo real (sem credencial):** KaBuM! (Catalog v2), Carrefour Brasil (VTEX Catalog + Simulação real de Frete e Retirada por CEP), Bemol (VTEX + Frete/Retirada por CEP), Loja Electrolux (VTEX + Frete por CEP).
- **Prontas para ativação via `.env`:** Mercado Livre (OAuth 2.0 — novos e usados), Google Shopping BR (SerpAPI).
- **Adaptadores preparados para credenciais comerciais:** Amazon Brasil (PA-API 5.0), Shopee Brasil (Affiliate Open API), Magazine Luiza (Partners), Casas Bahia (Awin).
- **Documentada sem API pública:** OLX Brasil (sem scraping irregular).

## Como Executar

> **Requisitos:** Python 3.12+ (nenhuma dependência externa obrigatória; usa apenas biblioteca padrão do Python + SQLite).

```bash
# 1. (Opcional) Copie o exemplo de variáveis de ambiente
cp .env.example .env

# 2. Inicie a aplicação
python3 run.py
```

Acesse no navegador: **http://127.0.0.1:8000**

## Como Executar os Testes

```bash
python3 -m unittest discover -s tests -v
```

## Estrutura do Projeto

```text
├── run.py                       # Ponto de entrada do servidor
├── .env.example                 # Template de variáveis de ambiente
├── docs/PROVIDERS.md            # Documentação das integrações e limites
├── supabase/schema.sql          # Schema SQL + RLS pronto para Supabase
├── price_hunter/
│   ├── config.py                # Configurações e pesos padrão
│   ├── security.py              # Validação, scrypt, tokens e rate limiting
│   ├── db.py                    # Persistência SQLite (usuários, favoritos, histórico)
│   ├── location.py              # ViaCEP, BrasilAPI, AwesomeAPI CEP e Haversine
│   ├── normalizer.py            # Interpretação de busca, equivalência e deduplicação
│   ├── scoring.py               # Price Hunter Opportunity Score e trade-offs
│   ├── auth_service.py          # Cadastro, login, recuperação e perfil
│   ├── search_service.py        # Pipeline completo de busca
│   ├── server.py                # API REST + servidor de arquivos estáticos
│   └── providers/               # Adaptadores modulares (base, kabum, vtex, ml, stubs, registry)
├── static/
│   ├── index.html               # SPA responsiva
│   ├── styles.css               # Design system desktop + mobile
│   └── js/                      # Módulos ES (api.js, utils.js, app.js)
└── tests/
    └── test_price_hunter.py     # Testes unitários e de integração
```
