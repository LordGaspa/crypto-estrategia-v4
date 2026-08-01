# Relatório — Sistema de Day-Trade Curto, Fase 1 (Pesquisa e Backtest Honesto)

**Data:** 2026-07-31 · **Escopo:** só pesquisa/backtest, sem execução real (conforme combinado)
**Veredito:** ❌ **NÃO PROSSEGUIR** para uma fase de automação/execução real com as candidatas
testadas. Achado estrutural, não um bug de implementação — ver diagnóstico abaixo.

---

## Resumo executivo

Testamos 3 estratégias de day-trade curto (minutos a poucas horas) em Binance Spot, nos 8 ativos
mais líquidos do portfólio, em 2 timeframes (15m e 5m), com e sem filtro de confirmação por
volume — 12 combinações no total, cada uma validada por walk-forward em ~13-18 meses de histórico
(216 janelas de ~4 semanas) e cruzada por regime de volatilidade e de tendência (bull/lateral/bear).

**Nenhuma das 12 combinações teve expectativa líquida positiva.** Pior: **zero de 8 ativos**
mostrou Sharpe estatisticamente significativo (bootstrap p<0,05) em **qualquer** combinação. O
corte por regime de tendência — pedido explicitamente para não validar só no momento atual —
confirma que o resultado é negativo em bear, bull **e** lateral, sem exceção.

Isso não é overfitting nem parâmetro mal escolhido: é o mesmo fenômeno em três desenhos de
estratégia independentes, dois timeframes, oito ativos e três regimes de mercado. O diagnóstico
(seção 3) mostra a causa raiz: o edge bruto do sinal de entrada, quando existe, é da ordem de
poucos pontos-base — muito menor que o piso de custo de ida-e-volta da Binance Spot (~30-40
pontos-base). Nenhum desenho de saída (stop/alvo/tempo) consegue transformar um edge bruto tão
pequeno em lucro líquido depois de taxa+slippage.

---

## 1. O que foi testado

| | |
|---|---|
| **Universo** | 8 ativos líquidos: BTC, ETH, BNB, SOL, XRP, DOGE, TRX, LINK |
| **Timeframes** | 15m e 5m |
| **Candidatas** | Reversão à média (RSI/Bollinger), Momentum (cruzamento EMA + filtro), Rompimento (canal Donchian) |
| **Variantes** | Com e sem filtro de confirmação por volume |
| **Histórico** | 15-18 meses de desenvolvimento (holdout de 6 semanas travado, NÃO tocado) |
| **Custo base** | 0,1%/lado (sem BNB) + 0,05%/lado slippage = ~0,30% ida-e-volta |
| **Custo estresse** | slippage escalado pelo range do candle de entrada (mercado mais agitado) |
| **Capital de referência** | $500 |

Receita fixa por candidata (não foi feito grid search — parâmetros escolhidos por raciocínio de
domínio e pela Fase 1.0, não ajustados contra os dados antes de travar):

| Candidata | Parâmetros-chave |
|---|---|
| Reversão à média | RSI(14)<20 ou toque na banda de Bollinger (2,5σ), stop 0,6%, alvo 1,0%, time-stop 3h |
| Momentum | EMA 9/21 + filtro EMA 50, stop 0,8%, alvo 1,5%, time-stop 4h |
| Rompimento | Canal Donchian 20 candles, stop 0,8%, alvo 2,0%, time-stop 4h |

## 2. Fase 1.0 — o piso econômico (feito antes de qualquer estratégia)

Breakeven de ida-e-volta no cenário mais honesto (sem desconto BNB, slippage conservador):
**0,40%**. Fração de vezes que o movimento de preço supera esse piso:

| Horizonte | BTC | ETH |
|---|---|---|
| ~1 hora | 22% | 30% |
| ~3 horas | 43% | 51% |

Conclusão da época: viável em horizontes de ~3h+, apertado em ~1h. As candidatas foram desenhadas
com isso em mente (time-stops de 3-4h). Mesmo assim, o resultado do walk-forward foi negativo —
o problema não é só o horizonte, é o edge direcional em si (seção 3).

## 3. Diagnóstico — por que todas as candidatas falharam (BTC, reversão à média, 15m)

Antes de rodar a varredura completa, isolamos a causa: o **sinal de entrada em si** (RSI<20, sem
qualquer stop/alvo) prevê o movimento futuro?

| Horizonte à frente | Retorno médio nos sinais | % positivos nos sinais | % positivos baseline (qualquer ponto) |
|---|---|---|---|
| 1h (4 candles) | +0,012% | 53,0% | 50,3% |
| 3h (12 candles) | -0,020% | 52,6% | 50,4% |
| 6h (24 candles) | -0,007% | 52,0% | 50,3% |
| 12h (48 candles) | -0,070% | 52,1% | 50,2% |

O sinal tem uma vantagem estatística real, mas **minúscula**: 2-3 pontos percentuais de taxa de
acerto acima do acaso, e retorno médio de poucos centésimos de %. Isso é ordens de grandeza menor
que o breakeven de 0,30-0,40%. Nenhuma engenharia de stop/alvo/tempo consegue transformar um edge
bruto desse tamanho em lucro líquido — o resultado do backtest completo (abaixo) confirma
exatamente isso.

## 4. Resultado do walk-forward — todas as 12 combinações (período de desenvolvimento)

| Candidata | Volume | Timeframe | Trades | Trades/mês | Net%/trade (base) | Win% bruto | Win% líquido | "Imposto" custo/mês | Calmar médio | Ativos c/ Sharpe significativo |
|---|:-:|:-:|--:|--:|--:|--:|--:|--:|--:|:-:|
| Rompimento | ✓ | 15m | 6461 | 48,6 | **-0,294%** | 40,2% | 30,5% | 14,6%/mês | -0,91 | 0/8 |
| Momentum | ✓ | 15m | 2068 | 15,6 | **-0,296%** | 40,5% | 31,8% | 4,7%/mês | -0,78 | 0/8 |
| Reversão | — | 15m | 8416 | 63,3 | -0,298% | 45,0% | 37,5% | 19,0%/mês | -0,94 | 0/8 |
| Momentum | — | 15m | 6917 | 52,0 | -0,298% | 38,6% | 29,7% | 15,6%/mês | -0,92 | 0/8 |
| Reversão | ✓ | 15m | 6226 | 46,8 | -0,298% | 44,7% | 37,5% | 14,1%/mês | -0,91 | 0/8 |
| Rompimento | — | 15m | 8790 | 66,1 | -0,303% | 40,4% | 30,2% | 19,8%/mês | -0,95 | 0/8 |
| *(todas as combinações 5m: net%/trade entre -0,306% e -0,317%, Calmar entre -0,90 e -1,00 — piores que 15m em todos os casos)* | | | | | | | | | | |

**Todas as 12 linhas são negativas.** A melhor delas (Momentum + filtro de volume, 15m) ainda
perde -0,296%/trade líquido e projeta **-$16,16/mês** num capital de $500 — e mesmo essa "menos
ruim" não tem nenhum ativo com significância estatística.

O filtro de volume **ajuda, mas não resolve**: reduz a frequência de trades (menos "imposto de
custo") e melhora o resultado absoluto em quase todos os casos, mas nunca vira o sinal pra
positivo. Isso confirma a ideia original do usuário (força no volume das velas) tem mérito como
filtro de qualidade — só não é suficiente sozinha para superar o piso de custo aqui.

**5m é sistematicamente pior que 15m** (mais trades, mais imposto de custo) — confirma o achado
da Fase 1.0 de que horizontes mais curtos têm menos margem.

## 5. Corte por regime de tendência (bull / lateral / bear)

Pedido explícito do usuário: não validar só no momento de mercado atual. O período de
desenvolvimento (18 meses) foi majoritariamente **lateral**, com alguns meses de **bear** (fev e
jun/2026) e pouca cobertura de **bull** forte — reflexo de o mercado ter passado a maior parte do
tempo sem um movimento direcional de 30 dias acima de ±20%.

Resultado: **negativo em bull, lateral e bear, em todas as 12 combinações, sem exceção.** Faixa de
net%/trade por regime:

| Regime | Faixa de net%/trade (todas as candidatas/variantes) |
|---|---|
| Bear | -0,245% a -0,380% |
| Bull | -0,249% a -0,309% |
| Lateral | -0,281% a -0,323% |

Não existe um regime "escondido" onde a estratégia funciona. O problema é estrutural (custo >
edge), não dependente do momento de mercado — é por isso que aparece igual nos três regimes.

## 6. Por que isso é diferente do v4 (swing)

| | v4 (swing, 4h-1d) | Day-trade (Fase 1, minutos-horas) |
|---|---|---|
| Trades/ano | ~15-90 por ativo | ~190-2000 por ativo/ano |
| Movimento médio por trade | Dezenas a centenas de % | Frações de 1% |
| Custo de ida-e-volta | ~0,15-0,3% | ~0,30-0,40% (igual ou pior) |
| Custo como fração do movimento | Desprezível | **Domina o resultado** |

O mesmo custo de transação que é irrelevante no swing (porque o movimento capturado é gigante
comparado a ele) se torna o fator decisivo no day-trade (porque o movimento por trade é do mesmo
tamanho ou menor que o custo). Não é uma limitação da implementação — é matemática de custo de
transação vs. tamanho do movimento, o mesmo motivo pelo qual scalping profissional depende de
maker rebates, VIP fee tiers e infraestrutura de baixa latência que uma conta Spot de varejo não
tem.

## 7. Sobre o holdout travado

**Decisão: NÃO executar `daytrade_holdout.py --eu-confirmo-holdout-final`.** O script existe e
está pronto, mas rodá-lo agora violaria o propósito do holdout — ele só deve ser gasto (é uma
única olhada, não recuperável) quando o período de desenvolvimento mostrar algo honestamente
promissor pra validar. Aqui, 12 de 12 combinações falharam no dev período com 0/8 ativos
significativos em cada uma — não há candidata "escolhida" pra validar. Gastar o holdout agora não
adicionaria informação, só queimaria a única validação final que temos.

## 8. Veredito e critério de parada (conforme definido no plano aprovado)

Marcando explicitamente os critérios de "parar honestamente" do plano original:

- ✅ **Expectativa líquida ≤ 0 no cenário base, mesmo com edge bruto aparente** — confirmado em
  todas as 12 combinações.
- ✅ **Bootstrap indica que o "edge" é ruído** — confirmado: 0/8 ativos significativos, em toda
  combinação.
- ✅ **Breakeven perto ou acima do movimento típico alcançável, estruturalmente** — confirmado já
  na Fase 1.0 e reconfirmado no diagnóstico da seção 3 (edge bruto de poucos pontos-base vs.
  piso de custo de 30-40 pontos-base).

Os três critérios de parada do plano bateram. **Nenhum** critério de "seguir" bateu.

## 9. Recomendação

**Não prosseguir** para uma fase de automação/execução real com as estratégias testadas aqui.
Isso não significa que "day-trade nunca funciona" — significa que reversão à média, momentum e
rompimento simples, do jeito que foram desenhados e nos custos reais da Binance Spot de varejo,
não têm edge suficiente. Caminhos honestos para o futuro, se quiser continuar essa linha (nenhum
com garantia de sucesso, todos exigiriam uma nova Fase 1 própria):

1. **Reduzir custo, não aumentar sinal**: taxas VIP (alto volume) ou ordens limit/maker em vez de
   market/taker cortariam o piso de custo pela metade ou mais — mas ordens limit mudam a mecânica
   de execução (nem sempre preenchem) e exigiriam um modelo de fill mais sofisticado.
2. **Menos trades, mais seletivos**: o filtro de volume já mostrou essa direção (menos trades,
   resultado menos ruim). Filtros ainda mais restritivos poderiam, em tese, isolar os poucos
   sinais com edge real — mas o risco de overfitting sobe rápido quando se afina demais um filtro
   contra o mesmo histórico.
3. **Voltar pro horizonte que já funciona**: a estratégia v4 (swing) já é validada, honesta e no
   ar. A "conta de padeiro" de lucro pequeno e frequente é atraente psicologicamente, mas os
   números aqui mostram que ela não é gratuita — o edge tem que ser proporcionalmente maior que o
   custo, e isso é mais fácil de achar em movimentos grandes (swing) do que pequenos (day-trade).

---

---

## Addendum — sub-5 minutos e "volume puro" horas/dias (2026-07-31, mesma sessão)

### A) Sub-5 minutos: rejeitado no teste barato, nem chegou a virar candidata

Antes de investir em construir a infraestrutura completa pra um horizonte mais curto, rodamos só
o teste de breakeven (Fase 1.0) em 1 minuto:

| Horizonte | 1min | 5min | 15min | 30min | 60min |
|---|--:|--:|--:|--:|--:|
| % dos movimentos que cobrem o custo | 0,1% | 1,8% | 7,3% | 14,5% | 25,4% |

Em 1 minuto, 999 em cada 1000 vezes o mercado nem chega perto de cobrir o custo — inviável
fisicamente numa conta Spot de varejo, independente de qualquer sinal. **Não foi construída
nenhuma candidata pra esse horizonte.**

Nota sobre a curva: o crescimento desacelera a cada horizonte (fator 15x → 4x → 2x → 1,75x →
1,5x → ...), consistente com escala de raiz do tempo de um random walk — nunca "dobra" de forma
sustentada, e mesmo em 1 dia fica em ~83%, não 100%. Importante: essa métrica mede só se o
movimento **existe**, não se dá pra **prever a direção** — não é proxy de lucratividade.

### B) "Volume puro" (spike de volume + vela de alta, SEM cruzamento de médias), horas-a-dias

Pedido explícito do usuário: testar uma candidata baseada só na força do volume da vela (objetivo:
identificar movimentação de "big players"), em timeframes de 4h e 1d, sem qualquer lógica de
médias móveis. Implementada em `calcular_sinais_volume_puro` (`daytrade_core.py`): compra quando
o volume supera 2,5× a média dos 20 candles anteriores **e** o candle fecha em alta.

| Timeframe | Trades totais (8 ativos) | Net%/trade médio | Calmar médio | Ativos significativos |
|---|--:|--:|--:|:-:|
| 4h | 372 | -0,240% | -0,272 | 0/8 |
| 1d | 47 | -1,793% | -0,420 | 0/8 |

Resultado agregado ainda negativo nos dois timeframes — **1d tem amostra pequena demais pra
confiar** (3-9 trades por ativo em 18 meses) e não deve ser interpretado como conclusivo.

**Achado mais interessante veio do corte por regime de tendência no 4h**:

| Regime (4h) | Trades | Net%/trade médio | Win% líquido |
|---|--:|--:|--:|
| Bear | 48 | **+1,344%** | 50,0% |
| Bull | 44 | **+0,616%** | 43,2% |
| Lateral | 280 | -0,646% | 34,6% |

Em 4h, "volume puro" é **positivo** em regimes de tendência (bull e bear) e só perde no lateral —
que domina o período (280 de 372 trades), puxando o agregado pra negativo. Faz sentido
intuitivamente: spike de volume + vela de alta é mais informativo quando o mercado já tem uma
direção (confirma o movimento) do que num mercado sem direção (mais chance de ser ruído/whipsaw).

**Por que isso NÃO é "achamos uma estratégia lucrativa" ainda**, apesar de parecer promissor:
1. **0/8 ativos com significância estatística** mesmo no 4h agregado — o mesmo critério que
   reprovou tudo na Fase 1 continua reprovando aqui.
2. O ativo isolado com melhor resultado (DOGE 4h, +68,9%, Calmar 1,28) é exatamente o tipo de
   "vencedor isolado entre 8 tentativas" que pode ser sorte, não edge — mais um motivo pra não
   comemorar ainda.
3. O corte "só funciona em bull/bear" foi **observado depois de olhar os dados** — usar esse
   filtro pra construir uma "candidata 2.0" e testar de novo nos MESMOS dados seria exatamente o
   tipo de re-otimização que este projeto inteiro existe pra evitar (o mesmo raciocínio do
   `GUARDRAIL` documentado na estratégia-ouro v5). Se quiser perseguir essa pista, o caminho
   honesto é tratá-la como uma HIPÓTESE NOVA e validá-la com o mesmo rigor (walk-forward completo,
   holdout travado) — não aproveitar o olhar que já demos nesses dados.

**Veredito do addendum**: não é um "sim" nem um "não" limpo como o resto da Fase 1 — é um "talvez,
mas não provado" que aponta uma direção plausível (volume como confirmação de tendência, não como
sinal isolado) pra uma eventual Fase 1-B dedicada, se o usuário quiser investir mais tempo nisso.
Holdout continua travado e intocado.

**Arquivos deste addendum**: `daytrade_volume_puro_janelas.csv`, `daytrade_volume_puro_resumo.csv`,
`daytrade_volume_puro_regime_tendencia.csv`. `daytrade_core.py::calcular_sinais_volume_puro` +
3 testes novos em `tests/test_daytrade_core.py` (20/20 passando). `daytrade_walkforward.py`
ganhou `main_volume_puro()` (rodar com `python daytrade_walkforward.py --volume-puro`) e a tag de
regime de tendência (`tag_regime_tendencia`) foi adicionada ao pipeline principal também.

---

**Arquivos desta fase:** `daytrade_reality_check.py`, `daytrade_config.py`, `daytrade_custos.py`,
`daytrade_core.py` (+ `tests/test_daytrade_core.py`, 16/16 passando), `daytrade_backtest.py`,
`daytrade_walkforward.py`, `daytrade_holdout.py` (não executado). CSVs:
`daytrade_reality_check_resultado.csv`, `daytrade_walkforward_janelas.csv` (216 linhas),
`daytrade_walkforward_resumo.csv` (12 linhas), `daytrade_walkforward_regime_tendencia.csv`
(36 linhas). Cache intraday em `cache_dados_daytrade/` (git-ignorado).
