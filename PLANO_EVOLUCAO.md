# Plano de Evolução — derivado da Revisão #1 (2026-07-29)

Objetivo: subir as duas dimensões nota **C** (robustez do resultado e engenharia de software)
e puxar as demais para **A+**, **buscando honestamente mais retorno** — sem voltar ao
overfitting que acabamos de escapar.

Princípio-guia: **primeiro tornar a bancada confiável, depois caçar retorno nela.** Caçar
retorno numa bancada que pode mentir (lógica duplicada, validação n=1) é como acelerar sem saber
se o velocímetro funciona.

---

## A verdade sobre "mais lucro" (ler antes de tudo)

Mais retorno é **possível e legítimo**, mas não é de graça. Existe um motivo concreto para
acreditar que dá pra ganhar mais: **cripto é fat-tailed** — poucos rallys explosivos fazem quase
todo o retorno. E a saída atual (stop ATR fixo **OU cruzamento contrário**) provavelmente **corta
os vencedores cedo**: numa alta parabólica, o primeiro repique de −20% dispara o cruzamento
contrário e você sai — perdendo os +300% que vinham depois. Ou seja: **a maior alavanca honesta
de retorno é capturar melhor a cauda direita**, segurando os vencedores por mais tempo.

O que **não** dá pra fazer: mais retorno **e** menos risco **e** mais certeza ao mesmo tempo —
essa é a armadilha do overfitting. O caminho real é escolher **deliberadamente** um ponto na
fronteira risco/retorno e **validá-lo fora da amostra**. Vamos te dar essa escolha (defensivo →
equilibrado → agressivo), com o número honesto de cada um.

---

## Fase 0 — Fundação de software (destrava tudo; nota C+ → A/A+) ✅ CONCLUÍDA (2026-07-29)

*Não muda nada da estratégia. É o conserto de maior retorno sobre esforço e o pré-requisito de
segurança para todo o resto.*

- [x] **Fonte única da verdade da estratégia:** criado `estrategia_core.py` (funções puras
  `calcular_sinais`, `simular_posicao`, `estado_posicao_atual`). `executar_backtest_v4` (P&L) e
  o radar (`estado_atual_posicao`, agora wrapper fino) chamam o MESMO core. Fim da duplicação.
- [x] **Config como fonte única de parâmetros:** `RECEITA_ROBUSTA` e `grupo_ouro` movidos para
  `config_v4.py`; app importa de lá. (Scripts de pesquisa comparar_receitas/analise_profunda
  mantêm variantes locais de propósito — são artefatos de comparação já rodados.)
- [x] **Testes (`pytest`, 7 passando):** golden-master do motor (valores congelados, trava
  regressão — confirmou que o refactor não mudou nada); concordância radar vs referência
  independente; ausência de look-ahead; sinais corretos; simular_posicao vs reimplementação.
- [x] **Limpeza:** removidas constantes/CSVs mortos do app (PESOS_CSV, PORTFOLIO_*). `requirements-dev.txt` criado.
- [ ] *(nice-to-have adiado)* centralizar `montar_df_fast` (indicadores) no core — hoje o app já
  é DRY internamente; só os scripts de pesquisa (holdout/portfolio/ouro) têm cópias próprias.

**Entregue:** radar e backtest agora compartilham o core — o radar não pode mais divergir do
que a estratégia testou. Verificado no browser: números idênticos aos de antes do refactor.

---

## Fase 1 — Validação honesta multi-regime (nota C → A; rigor A− → A+) ✅ CONCLUÍDA (2026-07-29)

*Antes de tentar ganhar mais, provar que o edge atual é real além de um único mercado de baixa.*

- [x] **Walk-forward multi-janela (fixo, sem re-otimização):** 115 janelas anuais × 22 ativos
  no período de desenvolvimento. Script: `walkforward_robusta_v4.py`.
  - Resultado global: 59% janelas positivas, mediana +15%/janela.
  - **BEAR (40 janelas):** estratégia −14% vs B&H −54% → **bate B&H em 93%** das janelas. Ponto forte.
  - **LATERAL (16 janelas):** estratégia +28% vs B&H −3% → **bate B&H em 81%** das janelas.
  - **BULL (59 janelas):** estratégia +55% vs B&H +156% → **bate B&H em apenas 19%**. Fraqueza principal.
- [x] **Bootstrap Sharpe + wilcoxon por ativo:** BTC, ETH, DOGE, LINK, SOL, FET, PEPE,
  FLOKI, BONK, TRX, XRP têm p-value < 0.05 (Sharpe bootstrap significativo). IMX, API3,
  PENGU, ZEC, HBAR sem edge significativo.
- [x] **Correlação e apostas efetivas:** N_efetivo = 2 de 18 ativos (janela 3 anos em comum —
  dado limitado, mas confirma alta correlação entre ativos cripto). Diversificação efetiva ~11%.
- [x] **App atualizado** com seção "Validação Multi-Regime" (expander) mostrando distribuição,
  tabela de regimes e leitura honesta.

**Diagnóstico:** é uma **estratégia de proteção de capital**, não de captura de alta. O edge é real
em BEAR e LATERAL. Em BULL, a saída por cruzamento corta cedo os vencedores — essa é a
fraqueza a atacar na Fase 2.

---

## Fase 2 — Busca honesta por MAIS RETORNO ✅ CONCLUÍDA (2026-07-29)

*Três alavancas testadas — resultados honestos abaixo.*

- [x] **Alavanca A — Trailing Stop DESCARTADO:** testado nas 115 janelas (script `trailing_teste_v4.py`).
  EQUILIBRADO (ativa 1.5× risco, trail ATR×3.0) piorou em TODOS os regimes:
  BULL: mediana +5% vs defensivo +55%; BEAR: +42% DD médio vs +38%. Causa: com parâmetros
  lentos (lenta=100, ATR×6.0 de stop inicial), o cruzamento contrário é saída mais eficiente
  que qualquer trailing — ele aguarda a reversão real em vez de sair em qualquer pullback.
  **Conclusão: o cruzamento + stop fixo já é o melhor mecanismo de saída para esses params.**

- [x] **Alavanca C — Filtro de regime BTC (simulado):** script `regime_btc_v4.py`.
  Usar regime BTC como sinal de tamanho de posição:
  - **FILTRO_BTC** (0.5× em anos BEAR do BTC): capital dev $81K → $109K (+35%), mesma mediana.
  - **AGRESSIVO_BTC** (1.5× em BULL, 0.5× em BEAR): capital $81K → $380K (+370%!);
    BULL mediana +82% vs +55% defensivo; BEAR mediana −14% (inalterado — por B&H BTC BEAR
    coincidir com B&H ativos BEAR, o fator 0.5× se aplica mas o ganho em proteção é modesto).
  - **Caveat:** escalonamento SIMULADO sobre os retornos do walk-forward, não re-backtest real.
    Para validar: re-implementar com sizes dinâmicos no motor de backtest (Fase 3).

- [x] **Alavanca B — Vol-targeting:** `vol_targeting_v4.py`. Dimensiona cada posição de forma que
  o capital em risco por trade seja uma fração fixa R (em vez de sempre 100% do capital).
  Resultados (médias ponderadas pelos pesos do portfólio, período de desenvolvimento):

  | R (risco/trade) | Ret/ano | Drawdown | Calmar | Sharpe |
  |:----------------|--------:|---------:|-------:|-------:|
  | Baseline (100%) |  +60.7% |    62.3% |  1.158 |  0.886 |
  | R=10%           |  +44.5% |    44.0% |  1.201 |  0.941 |
  | R=15%           |  +61.4% |    55.5% |  1.314 |  0.963 |
  | R=20%           |  +75.1% |    64.2% |  1.379 |  0.973 |
  | R=30%           |  +92.3% |    76.7% |  1.388 |  0.973 |

  **Descoberta:** vol-targeting a R=15-20% melhora Calmar em +14-19% e Sharpe em +9-10% vs
  baseline. Mecanismo: em mercados calmos (ATR baixo), implicitamente usa alavancagem; em
  voláteis, reduz exposição. R=15% é o ponto de melhor Calmar per-ativo (mediana 1.230 vs 0.881).
  R=20%+ melhora mais o portfólio ponderado mas aumenta drawdown acima do baseline.
  **Conclusão: R=15% é o ponto ótimo para quem quer melhorar risco-retorno sem mais drawdown.**

- [ ] **Fronteira completa no app:** expor Baseline / Vol-target R=15% / BTC-Filtrado /
  BTC-Agressivo com métricas honestas de cada combinação.

**Entrega completa Fase 2:** trailing DESCARTADO (Alavanca A), vol-targeting CONCLUÍDO com
R_ótimo=15% (Alavanca B), filtro BTC CONCLUÍDO com re-backtest real (Alavanca C).

---

## Fase 3 — Produto e realismo (mecânica A− → A+; deploy robusto) ✅ PARCIAL (2026-07-29)

- [x] **Re-backtest real com sizes dinâmicos:** `backtest_btc_filter_v4.py` implementa o filtro
  de regime BTC como sizing dinâmico real por trade (não escalamento de retornos). Cada trade
  é dimensionado pelo regime BTC trailing 12m no momento da abertura — sem look-ahead.
  Resultados (médias ponderadas pelos pesos do portfólio, período de desenvolvimento):

  | Modo       | Ret/ano | Drawdown | Calmar | Sharpe |
  |:-----------|--------:|---------:|-------:|-------:|
  | DEFENSIVO  |  +60.7% |    62.3% |  1.158 |  0.886 |
  | FILTRADO   |  +62.7% |    58.5% |  1.219 |  0.909 |
  | AGRESSIVO  |  +79.7% |    68.7% |  1.316 |  0.938 |

  **Descobertas chave:**
  - O antigo $81K→$380K (+370%) da Fase 2 era artefato de escalar retornos anuais compostos.
    O re-backtest real mostra +31% de retorno anualizado (60.7%→79.7%) — expressivo, mas honesto.
  - AGRESSIVO melhora o Calmar em +14% (1.316 vs 1.158) e o retorno anual em +19pp.
  - Trade-off: +6.4pp de drawdown adicional.
  - Efeito concentrado nas **veteranas**: SOL (+37.9k%), DOGE (+30.8k%), BNB (+17.5k%) ganham
    dramaticamente. Novas ficam estáveis ou pioram ligeiramente (sem correlação confiável com BTC).
  - FILTRADO melhora Calmar (+5%) e REDUZ drawdown (-3.8pp) — proteção sem leverage.

- [x] **App atualizado:** modo de alocação agora exibe métricas do re-backtest real em cada card
  (ret/ano, DD, Calmar ponderados pelo portfólio). Caption atualizado de "simulação" para
  "re-backtest real". Função `carregar_resumo_filter()` carrega `backtest_btc_filter_v4_resumo_ponderado.csv`.

- [x] **Slippage realista:** `slippage_realista_v4.py`. Modelo: saídas por stop recebem slippage
  extra = `gap_frac × ATR/preço` (gap_frac = 5% para líquidas, 25% para ilíquidas). Resultados:

  | Modelo    | Ret/ano | Drawdown | Calmar | Sharpe |
  |:----------|--------:|---------:|-------:|-------:|
  | Baseline  |  +60.7% |    62.3% |  1.158 |  0.886 |
  | Realista  |  +57.6% |    63.5% |  1.091 |  0.858 |
  | Delta     |   −3.1pp |   +1.2pp | −0.067 | −0.028 |

  **Diagnóstico:** impacto modesto no portfólio ponderado (−3.1pp/ano, −6% no Calmar).
  Causa: maioria das saídas é por cruzamento (mercado ordeiro), não por stop. Ativos mais
  afetados: PEPE (−347pp total, 4 stops × ATR alto × ilíquido) e FET (−312pp, 6 stops).
  A proteção em BEAR ainda se sustenta sob slippage realista — a estratégia sai antes do crash
  (cruzamento), não quando está em queda livre (stop gap). Portfólio continua Calmar > 1.

- [ ] **Deploy** com os guardrails no lugar (Fase 0) — aí você confia no que está no celular.

---

## Ordem sugerida

**Fase 0 → 1 → 2 → 3.** A tentação é pular pra Fase 2 (o lucro). Mas sem a Fase 0 você pode estar
otimizando um radar que mente, e sem a Fase 1 você não sabe se o que vai amplificar é real. Fazer
na ordem é o que diferencia "mais lucro de verdade" de "mais um backtest bonito".

## Como medir progresso
Ao final de cada fase, atualizar as notas em `REVISAO_PROJETO.md` (nova revisão datada) e
comparar com a #1.
