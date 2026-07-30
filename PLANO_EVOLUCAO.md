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

## Fase 1 — Validação honesta multi-regime (nota C → A; rigor A− → A+)

*Antes de tentar ganhar mais, provar que o edge atual é real além de um único mercado de baixa.*

- [ ] **Walk-forward multi-janela para a receita robusta:** reotimizar/reavaliar em várias
  janelas rolantes cobrindo **altas E baixas** (reusar a estrutura do walk-forward do v2). Saída =
  **distribuição** de resultados (mediana, pior caso, % de janelas positivas), não um número só.
- [ ] **Significância da robusta:** recomputar DSR **para a receita robusta** (não a otimizada) e
  adicionar um teste de reality-check/bootstrap (a robusta bate um trend-following aleatório?).
- [ ] **Novo holdout lacrado** para a robusta — e **não olhar** até o fim (o antigo já foi
  espiado demais).
- [ ] **Correlação e "apostas efetivas":** medir a correlação real da cesta e o número efetivo de
  apostas independentes; documentar explicitamente o viés de sobrevivência.

**Entrega:** um "sim/não/quanto" honesto sobre o edge — a base para caçar retorno com segurança.

---

## Fase 2 — Busca honesta por MAIS RETORNO (só depois da Fase 1)

*Três alavancas legítimas de retorno, cada uma testada na bancada da Fase 1 + holdout novo, UMA vez.*

- [ ] **Alavanca A — segurar vencedores (a maior):** revisitar a saída. Testar **trailing stop**
  (a ideia do v3, mas agora sobre a receita robusta e com objetivo de retorno) e/ou trocar o
  "cruzamento contrário" por uma saída mais lenta. Meta: capturar mais da cauda direita.
- [ ] **Alavanca B — dimensionamento por volatilidade-alvo:** em vez de só vol-inversa (que
  minimiza risco e limita retorno), mirar uma volatilidade-alvo do portfólio — usar mais capital
  quando o mercado está calmo em tendência. Fonte clássica e defensável de mais retorno.
- [ ] **Alavanca C — filtro de regime do BTC:** quando o BTC está em alta confirmada (acima da
  média longa), **pressionar** (sizes maiores / cesta mais agressiva); quando abaixo, defensivo.
  Pode aumentar retorno na alta E melhorar proteção na baixa.
- [ ] **Fronteira de escolha:** entregar 3 receitas — **Defensivo** (atual), **Equilibrado**,
  **Agressivo** — com o número honesto (retorno/DD/Calmar OOS) de cada uma. Você escolhe o ponto;
  o app mostra o trade-off sem maquiar.

**Entrega:** um caminho real para mais lucro, com o custo (drawdown) explícito e validado OOS.

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
