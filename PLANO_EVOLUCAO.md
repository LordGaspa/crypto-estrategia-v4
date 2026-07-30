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

## Fase 2 — Busca honesta por MAIS RETORNO ✅ PARCIAL (2026-07-29)

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

- [ ] **Alavanca B — Vol-targeting:** ainda não testada. Prioridade secundária.

- [ ] **Fronteira de 3 receitas para o app:** mostrar Defensivo / Filtrado / Agressivo com
  sliders de trade-off. Depende de re-backtest real com sizes dinâmicos (não mera escala).

**Entrega parcial:** identificamos que a Alavanca C (filtro BTC) é o caminho mais promissor para
mais retorno. O passo concreto é implementar sizes dinâmicos no motor de backtest.

---

## Fase 3 — Produto e realismo (mecânica A− → A+; deploy robusto)

- [ ] **Slippage realista:** modelar slippage que piora com a volatilidade/queda (especialmente
  ilíquidas no momento do stop) — para a proteção não ficar superestimada.
- [ ] **App:** expor a fronteira (Defensivo/Equilibrado/Agressivo), a distribuição OOS e as
  limitações (viés de sobrevivência, correlação) de forma visível e honesta.
- [ ] **Deploy** com os guardrails no lugar (Fase 0) — aí você confia no que está no celular.

---

## Ordem sugerida

**Fase 0 → 1 → 2 → 3.** A tentação é pular pra Fase 2 (o lucro). Mas sem a Fase 0 você pode estar
otimizando um radar que mente, e sem a Fase 1 você não sabe se o que vai amplificar é real. Fazer
na ordem é o que diferencia "mais lucro de verdade" de "mais um backtest bonito".

## Como medir progresso
Ao final de cada fase, atualizar as notas em `REVISAO_PROJETO.md` (nova revisão datada) e
comparar com a #1.
