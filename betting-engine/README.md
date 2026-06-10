# ChapaFut ⚡ — Calculadora Transparente de Apostas (futebol)

> **O que isto é:** uma calculadora que estima probabilidades com um modelo
> auditável (Poisson), cruza com as odds da sua casa de apostas e **mostra
> todos os números** de todos os mercados pedidos.
>
> **O que isto NÃO é:** não é casa de apostas, não recomenda estratégia,
> não decide nada. **Quem decide o risco e o que apostar é sempre você**,
> na sua própria casa. Defaults são sugestões — nunca travas — e **nada é
> filtrado silenciosamente**.

## Instalação rápida (VPS Ubuntu/Debian — um comando)

Cole no terminal do VPS; o instalador baixa tudo, instala, pede sua chave
da API-Football e roda o teste de conexão:

```bash
curl -fsSL https://raw.githubusercontent.com/Jullio2025/bitcoin-puzzle-hunter/claude/sports-betting-recommender-q9cveu/betting-engine/setup.sh -o setup.sh && bash setup.sh
```

Depois disso, para abrir o motor a qualquer momento:

```bash
~/bitcoin-puzzle-hunter/betting-engine/run.sh
```

## Instalação manual (qualquer máquina, Python 3.11+)

```bash
cd betting-engine
pip install -r requirements.txt
cp .env.example .env       # preencha APISPORTS_KEY (plano Pro da API-Sports)
```

## Uso

**Painel web (navegador, PC ou celular):**

```bash
./run-web.sh               # sobe em http://IP_DO_VPS:8000
```

Acesso protegido por senha (`PANEL_USER`/`PANEL_PASSWORD` no `.env`; o
instalador pergunta e grava). Fluxo: escolher data/liga → marcar jogos →
preencher filtros → tabela completa por mercado, com lambdas e regras
explicados, montagem de múltipla (sempre com odd combinada + probabilidade
combinada + chance de perder, juntos) e tracker integrado.

**Deixar online 24h (serviço do sistema):**

```bash
bash install-service.sh    # liga no boot e reinicia sozinho se cair
journalctl -u chapafut -f  # logs ao vivo
```

**Terminal:**

```bash
python main.py             # menu interativo amigável
python main.py ligas       # teste de conexão + ligas com cobertura
python main.py relatorio   # relatório do tracker
```

Fluxo típico: opção **2** do menu → escolha a data e os jogos → informe seus
filtros (faixa de odd, probabilidade mínima, EV mínimo, simples/múltipla —
qualquer valor é aceito) → o motor imprime, por jogo, os lambdas com a
explicação de origem, o ajuste de cada regra e a tabela completa de mercados.

## O que é exibido por mercado (o coração do produto)

| Coluna | Fórmula | Significado |
|---|---|---|
| `p_model` | Poisson | probabilidade estimada pelo modelo |
| `1/odd` | 1/odd | prob. implícita na odd (e taxa de acerto p/ empatar) |
| `edge` | p_model − 1/odd | positivo = o modelo vê valor |
| `EV` | p·(odd−1) − (1−p) | valor esperado por unidade apostada |
| `rec.` | 1/(odd−1) | vitórias p/ recuperar 1 derrota (a 1.20, são **5**) |

Mercados fora dos seus filtros **aparecem mesmo assim**, marcados com o
motivo (ex.: `odd 1.30 < mín 1.50`). A escolha é sua.

### Múltiplas
A odd combinada **nunca aparece sozinha**. O bilhete sempre mostra juntos:
odd combinada (produto das odds), probabilidade combinada (produto dos
p_model — **aproximação que assume independência entre as pernas**) e a
**chance de perder o bilhete** (1 − prob. combinada).

## O modelo (sem caixa-preta)

Mercados de contagem (gols, cartões, escanteios) usam **Poisson**:

- `λ gols casa` = média(gols que o mandante marca **em casa**, gols que o
  visitante sofre **fora**) — últimos N jogos, **busca separada por time e
  por mando** (N padrão: 5).
- `λ cartões` = cartões/jogo do mandante em casa + do visitante fora,
  ajustado pela média do árbitro (regra `arbitro_cartoes`).
- `λ escanteios` = média cruzada (ataque de um × defesa do outro), casa + fora.
- **1X2**: dois Poissons independentes (casa × fora) somados na grade de
  placares.

Cada λ é impresso com a conta de origem, e cada regra imprime seu
multiplicador e justificativa. Você consegue refazer qualquer número na mão.

## Regras plugáveis (`rules/`)

Cada parâmetro de análise é um módulo independente registrado com
`@register`. As inclusas: `mando_de_campo`, `forma`, `arbitro_cartoes`,
`cartoes_time`, `escanteios`, `estilo_de_jogo` (proxy: chutes a gol),
`escalacao`, `clima` (aguardando fonte externa), `importancia`.

Para criar uma nova: copie um arquivo de `rules/`, mude `name` e implemente
`apply(ctx) -> RuleResult`. Nada no núcleo precisa mudar. Regras podem ser
ativadas/desativadas na interface.

## Dados

- **Fonte atual:** API-Football (api-sports.io), endpoint direto
  `v3.football.api-sports.io`, header `x-apisports-key`.
- **Cobertura:** o motor usa apenas ligas com
  `coverage.fixtures.statistics_fixtures = true` (senão cartões/escanteios
  vêm vazios). Teste com `python main.py ligas`.
- **Árbitro:** a API não tem agregado por árbitro; o motor varre os jogos
  da liga/temporada, filtra pelo nome e calcula a média — tudo cacheado.
- **Cache em disco** (`cache/`) com TTL por tipo de dado + pausa entre
  chamadas (`REQUEST_PAUSE`) para respeitar o rate limit.
- **Outras fontes (ex.: SofaScore):** todo consumo de dados passa pela
  interface de `ApiFootballClient`; para plugar outra fonte, implemente
  outra classe com os mesmos métodos públicos e injete no lugar.

## Tracker

Registre cada aposta que **você decidiu** fazer (jogo, mercado, odd, stake,
p_model) e liquide depois. O relatório mostra hit rate real, ROI real e o
p_model médio (para checar calibração do modelo). Com menos de **100**
apostas liquidadas, o relatório avisa: a variância domina e **não dá para
concluir** se existe edge.

## Testes

```bash
python -m unittest discover tests
```

Cobrem Poisson, edge, EV, `wins_to_recover`, 1X2, múltiplas, parser de odds,
registro de regras e o pipeline completo com dados falsos (sem rede).

## Estrutura

```
config.py        # defaults (sugestões), chaves, TTLs, mapeamento de mercados
api_client.py    # API-Football + cache em disco + rate limit
features.py      # últimas N por mando, cartões, escanteios, gols, árbitro
scoring.py       # Poisson, edge, EV, wins_to_recover, 1X2, múltiplas
odds_parser.py   # /odds -> {(mercado, lado, linha): odd}
rules/           # um módulo por parâmetro + registrador central
recommender.py   # lambdas + regras + métricas de transparência
tracker.py       # hit rate e ROI reais
main.py          # interface interativa
tests/           # testes unitários
```

---

**Aviso:** aposta esportiva envolve risco real de perda. Este software é uma
ferramenta de cálculo e estudo; resultados passados e probabilidades
estimadas não garantem nada. Jogue com responsabilidade (18+).
