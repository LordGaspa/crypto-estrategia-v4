# v6 — Busca por mais captura de alta (bull), preservando o lado defensivo

**Data:** 2026-08-01 · **Script(s):** `walkforward_v6c_scaleout.py`,
`walkforward_v6d_scaleout_btc.py`, `walkforward_v6e_reentry.py`, funções novas em
`estrategia_core.py` (`simular_posicao_filtro_adx`, `simular_posicao_scale_out`) ·
**Escopo:** a `RECEITA_ROBUSTA` (v4/v5, em produção) bate Buy&Hold em só 19% das janelas de bull
(sai cedo demais no cruzamento contrário) — este relatório testa 4 variações de saída/entrada
que tentam capturar mais alta sem sacrificar a proteção já validada em bear/lateral.

---

## Resumo executivo

De 4 candidatas testadas, **nenhuma é um "sim" limpo**, e nenhuma foi promovida a produção. A
melhor delas — **saída parcial (scale-out 50%)** — melhora o retorno mediano em bull (+59,0%→
+68,0%) e até reduz o drawdown mediano lá (39,9%→37,3%), mas piora o lateral visivelmente
(+26,9%→+17,4%, bate B&H 76,5%→64,7%) e aprofunda um pouco a perda mediana em bear, embora sem
piorar a taxa de proteção (bate B&H no bear ficou igual: 92,5%). As outras três candidatas —
filtro de força de tendência (ADX), a combinação de scale-out com o regime do BTC, e reentrada
rápida — não trouxeram ganho líquido em bull e ainda pioraram lateral/bear. Nenhum desses números
foi testado por significância estatística (bootstrap) nem tocou o holdout — são só walk-forward
de desenvolvimento, mesma disciplina do `walkforward_robusta_v4.py`.

## 1. O que foi testado

| | |
|---|---|
| **Universo** | 22 ativos, `ATIVOS_PORTFOLIO_V4` |
| **Período** | Desenvolvimento (walk-forward anual, 115 janelas-ativo) — holdout intocado |
| **Base de comparação** | `RECEITA_ROBUSTA` atual (produção), sinal de entrada IDÊNTICO em todas as candidatas |
| **Custos** | Diferenciados por liquidez, mesmos de `otimizador_v4.py` |
| **O que muda por candidata** | Só a lógica de SAÍDA (ou reentrada) — entrada nunca mudou |

## 2. Candidata A — Filtro de força de tendência (ADX)

Ignora o cruzamento contrário se a tendência ainda estiver forte (ADX alto). Primeira versão
(só ADX, sem direção) tinha um bug: ADX mede força, não direção — ficava preso em posições
perdedoras durante quedas fortes. Corrigido exigindo também `+DI > -DI` (tendência forte tem que
ser de ALTA).

| Regime | Base | ADX (corrigido) | ADX bate Base |
|---|--:|--:|--:|
| BULL | +59,0% | +58,2% | 43,1% |
| LATERAL | +26,9% | +14,9% | 29,4% |
| BEAR | -14,7% | -23,6% | 32,5% |

**Leitura:** mesmo corrigido, o filtro é neutro-a-pior em todo regime. A `RECEITA_ROBUSTA` já usa
médias muito lentas (100 períodos nas veteranas) — quando o cruzamento dispara, o ADX geralmente
já caiu junto. Filtrar um sinal atrasado com outro filtro atrasado não ajuda: eles tendem a
concordar. **Descartada.**

## 3. Candidata B — Saída parcial (scale-out)

Primeiro cruzamento contrário fecha só uma fração da posição; o resto continua com o mesmo stop
ATR, sem tentar prever se foi pullback ou reversão real.

| Regime | Base | Scale-out 50% | DD Base | DD Scale-out | SO bate Base |
|---|--:|--:|--:|--:|--:|
| BULL | +59,0% | **+68,0%** | 39,9% | **37,3%** | 37,9% |
| LATERAL | +26,9% | +17,4% | 31,1% | 35,8% | 35,3% |
| BEAR | -14,7% | -17,8% | 38,4% | 36,2% | 47,5% |

**Leitura:** é a única candidata com sinal genuíno de melhora em bull — retorno mediano sobe e
drawdown mediano cai. O mecanismo: perde um pouco na maioria das janelas de bull (SO<Base em 62%
delas) mas ganha muito mais numa minoria onde a tendência continuou forte — é a "captura de cauda
direita" que o `PLANO_EVOLUCAO.md` já tinha identificado como a alavanca honesta de mais retorno.
O custo é real em lateral (pior em toda métrica) e o bear aprofunda um pouco a perda mediana,
embora a taxa de proteção (bate B&H) não mude.

Testada sensibilidade da fração (25%/50%/75%): 50% já é um ponto de equilíbrio razoável — 25% e
50% empatam em bull (melhor caso), mas 50% tem o melhor resultado em lateral dos três, e 75%
protege um pouco mais o bear à custa de bull. Nenhuma fração testada domina as outras em todos os
regimes.

## 4. Candidata B+regime — Scale-out só em anos BULL do BTC

Tentativa de isolar o ganho de bull do custo em lateral: só ativa o scale-out quando o regime do
BTC (retorno trailing 12m) está em BULL no momento da entrada do trade.

| Regime | Base | Scale-out condicional | Scale-out simples (B) |
|---|--:|--:|--:|
| BULL | +59,0% | +65,7% | +68,0% |
| LATERAL | +26,9% | **+7,3%** | +17,4% |
| BEAR | -14,7% | **-19,9%** | -17,8% |

**Leitura:** piorou em vez de ajudar — ficou pior que o scale-out SIMPLES em lateral e bear, com
ganho em bull ligeiramente menor. Causa: o regime do BTC (retorno de 12 meses) é um indicador
atrasado e global — continua marcado como BULL bem depois de um ativo individual já ter virado.
Vários trades dentro de janelas ruins (pelo desempenho do próprio ativo) ainda usavam a regra de
scale-out porque o BTC isoladamente não tinha virado ainda. **Descartada — a versão simples (B)
é estritamente melhor que esta combinação.**

## 5. Candidata C — Reentrada rápida após cruzamento, em BULL do BTC

Depois de uma saída por cruzamento (não stop) em regime BULL do BTC, observa até 10 candles: se o
preço fizer nova máxima acima do pico já alcançado na posição anterior, reentra imediatamente sem
esperar um novo cruzamento de médias completo.

| Regime | Base | Reentry | Reentry bate Base |
|---|--:|--:|--:|
| BULL | +59,0% | +59,0% | 31,0% |
| LATERAL | +26,9% | +15,9% | 29,4% |
| BEAR | -14,7% | -17,6% | 7,5% |

**Leitura:** nenhum ganho em bull (mediana idêntica) e piora em lateral/bear — em 92,5% das
janelas de bear, a reentrada rápida deixou o resultado pior que a base. Gatilho (nova máxima em
10 candles pós-cruzamento, em BULL do BTC) é raro e, quando dispara, entra com um stop novo mais
apertado (baseado no preço/ATR atual) que não compensa. **Descartada.**

## 6. Bootstrap Sharpe do scale-out — período de dev completo, por ativo

O passo que faltava (seção 3 dizia "ainda não feito"): testar se o ganho de bull do scale-out
sobrevive quando se olha o histórico inteiro do ativo (bull+lateral+bear misturados, do jeito que
realmente aconteceu), não só as janelas rotuladas como bull.

| | Base | Scale-out 50% |
|---|--:|--:|
| Ativos com Sharpe significativo (p<0,05) | 12/22 | **12/22** (idêntico) |
| Ativos onde Scale-out melhora o Sharpe vs Base | — | **7/22** (32%) |

**Leitura:** o resultado NÃO se confirma no período completo. A contagem de ativos significativos
é idêntica, e na MAIORIA dos ativos (15/22, incluindo BTC, BNB e SOL) o Sharpe do período inteiro
piora com scale-out, não melhora. Isso concilia com a seção 3: o ganho existe DENTRO das janelas
rotuladas como bull, mas é diluído pelo custo em lateral/bear quando se olha o histórico completo
de cada ativo. **Conclusão revisada: scale-out não é uma melhoria estatisticamente validada — é
um empate no período completo, apesar do padrão favorável por regime.** Não deveria ser promovido
a produção com base no que foi testado até aqui.

## 7. Rodada 2 — bootstrap + 3 novas variações (pedidas pelo usuário)

Depois da qualificação da seção 6, o usuário pediu mais 3 ideias, todas testadas:

### 7a. Scale-out combinado com sizing (vol-targeting / BTC-Agressivo)

Testada a combinação com BTC-Agressivo (sizing por regime, 1,5x bull / 0,5x bear — já validado
isoladamente na Fase 3):

| Regime | Base | Scale-out | Scale-out + BTC-Agressivo |
|---|--:|--:|--:|
| BULL | +59,0% | +68,0% | +74,9% (DD salta 37,3%→**46,6%**) |
| LATERAL | +26,9% | +17,4% | **+3,6%** |
| BEAR | -14,7% | -17,8% | **-25,5%** |

**Leitura:** a alavancagem amplifica os DOIS lados — um pouco mais de retorno em bull, mas com
drawdown bem mais alto, e lateral/bear pioram mais ainda (mesmo problema de descompasso de regime
da seção 4). Empilhar dois mecanismos que sozinhos já eram no máximo neutros compõe a variância,
não a expectativa. **Descartada.** (vol-targeting não testado separadamente — o padrão de
amplificação já ficou claro o suficiente pra não justificar o teste adicional.)

### 7b. Trailing stop só na metade remanescente pós-scale-out

| Regime | Scale-out puro | Scale-out + trailing na metade |
|---|--:|--:|
| BULL | +68,0% | **+51,5%** (pior, -16,5pp) |
| LATERAL | +17,4% | +22,8% (melhora) |
| BEAR | -17,8% | -22,3% (pior) |

**Leitura:** o scale-out ganha em bull justamente por deixar a metade remanescente correr SEM
stop apertado, capturando as raras tendências fortes que continuam. Colocar trailing nessa metade
corta exatamente essa cauda longa — anula o próprio mecanismo que fazia o scale-out funcionar em
bull. **Descartada.**

### 7c. Confirmação de volume na entrada do cruzamento (filtro, não estratégia nova)

Só aceita o cruzamento de entrada se o volume da vela estiver acima da própria média de 20
candles — reduz ~40% dos trades em todo regime.

| Regime | Base | Com filtro de volume | DD Base→Filtro |
|---|--:|--:|--:|
| BULL | +59,0% | **+46,3%** (pior) | 39,9%→32,9% |
| LATERAL | +26,9% | **-1,1%** (muito pior) | 31,1%→26,5% |
| BEAR | -14,7% | **-9,3%** (melhor) | 38,4%→**28,9%** |

**Leitura:** o filtro torna a estratégia mais conservadora em TODO regime (menos trades, menos
drawdown), o que ajuda bear (bate B&H em 95% das janelas, melhor de tudo testado hoje) mas custa
retorno justamente em bull e lateral — o oposto do objetivo original deste relatório. Pode ser
interessante como uma variante deliberadamente mais defensiva (não testada aqui), mas não resolve
"mais bull sem perder o resto".

## Conclusões

1. **Só a Candidata B (scale-out) mostrou sinal real de melhora em bull** — e mesmo essa não é de
   graça: troca parte da consistência em lateral por mais upside em bull, com o bear
   aproximadamente neutro na métrica que mais importa (taxa de proteção vs Buy&Hold).
2. **Combinar candidatas nem sempre ajuda** — a versão B+regime do BTC piorou em vez de isolar o
   melhor dos dois mundos, porque o regime do BTC é um proxy atrasado para a saúde do ativo
   individual, não um filtro limpo.
3. **Filtros que tentam "prever" se um sinal de saída é falso (ADX, reentrada por breakout) não
   funcionaram** aqui — a `RECEITA_ROBUSTA` já usa parâmetros lentos o bastante pra que esses
   filtros extras cheguem atrasados ou raros demais pra fazer diferença.
4. **Nada disso foi testado por significância estatística** (bootstrap Sharpe) nem no holdout —
   são resultados de walk-forward de desenvolvimento, achados observados depois de rodar, não
   hipóteses validadas antes. Se a Candidata B (scale-out) parecer atraente o suficiente pra
   seguir adiante, o próximo passo honesto seria bootstrap por ativo antes de qualquer decisão de
   promover a produção — isso ainda não foi feito.
5. **Nenhuma candidata foi promovida a produção.** `app_v4.py` continua usando
   `RECEITA_ROBUSTA` sem nenhuma dessas mudanças — essa promoção é uma decisão separada, do
   usuário, depois de ver os resultados.
6. **Atualização (bootstrap, seção 6): mesmo o scale-out não se sustenta no período completo.**
   O bootstrap por ativo mostra contagem de significância idêntica (12/22 nos dois) e Sharpe PIOR
   em 15 de 22 ativos com scale-out — o ganho de bull visto no walk-forward por regime é real
   dentro das janelas de bull, mas se dilui no histórico completo de cada ativo. Nenhuma das 4
   candidatas testadas neste relatório está pronta pra promoção.
7. **Rodada 2 (seção 7): nenhuma das 3 variações adicionais resolveu o problema.** Combinar
   scale-out com sizing amplifica os dois lados (mais bull, mas com muito mais drawdown, e
   lateral/bear pioram). Trailing na metade remanescente mata o próprio mecanismo que fazia o
   scale-out funcionar em bull. Filtro de volume na entrada é o achado mais consistente e
   genuinamente diferente — melhora bear de forma clara (65% das janelas, DD 38%→29%, bate B&H em
   95%) mas **piora** bull e lateral, o oposto do que este relatório buscava.
8. **Depois de 7 candidatas testadas (4 da rodada 1 + 3 da rodada 2), nenhuma entrega "mais bull
   sem perder o resto" de forma validada.** O padrão que mais se repete: mecanismos que ajudam
   bull tendem a custar lateral/bear, e vice-versa — não há almoço grátis visível nas variações
   testadas até aqui. Isso não significa que não exista uma solução — significa que as
   modificações testadas nesta sessão não a encontraram.

## Arquivos

Scripts: `walkforward_v6c_scaleout.py`, `walkforward_v6d_scaleout_btc.py`,
`walkforward_v6e_reentry.py`. Funções novas (aditivas, `estrategia_core.py`):
`simular_posicao_filtro_adx`, `simular_posicao_scale_out` — 23 testes novos em
`tests/test_estrategia_core.py` (46/46 no total, passando). CSVs: `comparativo_v4_vs_v6adx_walkforward.csv`,
`comparativo_v4_vs_v6c_scaleout_walkforward.csv`, `comparativo_v4_vs_v6d_scaleout_btc_walkforward.csv`,
`comparativo_v4_vs_v6e_reentry_walkforward.csv`.
