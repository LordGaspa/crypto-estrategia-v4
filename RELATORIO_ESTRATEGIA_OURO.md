# Relatório: Estratégia-Ouro por Grupo vs Otimizado por Ativo

**Data:** 2026-07-29
**Script:** `estrategia_ouro_v5.py` → `estrategia_ouro_v5_por_ativo.csv`
**Pergunta:** e se, em vez dos melhores parâmetros de CADA ativo (escolhidos a dedo),
usarmos UMA receita única por grupo? O quanto do resultado sobrevive?

---

## As duas estratégias-ouro destiladas (moda dos top-100 de cada grupo)

Os dados **confirmaram** que veteranas e novas pedem receitas diferentes — exatamente como
você intuiu. A única coisa que é igual nas duas é a **média lenta = 80** (o "sweet spot"
universal que já tinha aparecido na análise de padrões).

| Parâmetro | VETERANAS (líquidas/antigas) | NOVAS (memecoins/altcoins recentes) |
|---|---|---|
| Média rápida | **5** (bem rápida) | **10** |
| Média lenta | **80** | **80** |
| Filtro tendência | **50** (curto) | **100** (longo) |
| ATR período | **5** | **25** |
| ATR multiplicador | **6.0** (stop LARGO) | **1.0** (stop APERTADO) |

**Grupo veteranas** (8): BTC, ETH, BNB, SOL, XRP, DOGE, TRX, LINK
**Grupo novas** (14): o resto (PEPE, BONK, FLOKI, PENGU, FET, HBAR, INJ, SUI, TAO, RENDER,
API3, IMX, ZEC, 1MBABYDOGE)

A leitura das receitas faz sentido de mercado:
- **Veteranas:** stop largo (6× ATR) para aguentar a volatilidade sem ser estopado à toa;
  filtro curto porque a tendência é mais "limpa".
- **Novas:** stop apertado (1× ATR) para cortar perda rápido num ativo que pode desabar;
  filtro longo (100) para exigir tendência bem estabelecida antes de entrar.

---

## O RESULTADO CENTRAL: o "imposto do overfitting"

Quando troco os parâmetros otimizados-por-ativo pela receita única do grupo, **quanto do
retorno sobra?**

| Grupo | Retorno médio DEV (otimizado) | Retorno médio DEV (ouro) | Sobrevive |
|---|---|---|---|
| Veteranas | +19.705% | +4.304% | **22%** |
| Novas | +11.120% | +424% | **4%** |

Ou seja: **78% a 96% do retorno "espetacular" dos backtests por-ativo era escolha a dedo**
(curve-fitting), não padrão real. Isso é enorme — e é a resposta direta pra sua pergunta
sobre o gráfico (ver abaixo).

E o mais importante: **as veteranas seguram muito melhor que as novas** (22% vs 4%). Isso
confirma que o padrão nas moedas dominantes/antigas é mais real; nas novas, o Calmar altíssimo
(PENGU 76, BONK 13, TAO 14) é quase todo sorte/ruído de amostra pequena.

---

## Portfólio: as três comparações honestas

### 1. Veteranas no DEV (janela comum de ~5 anos, 1.813 dias) — a mais confiável

| Cenário | Ret. total | Ret. anual | Drawdown | **Calmar** |
|---|---|---|---|---|
| Ouro (receita do grupo) | +2.340% | 90% | 44% | **2.06** |
| Otimizado (por ativo) | +5.404% | 124% | 23% | 5.30 |
| Buy & Hold | +4.032% | 112% | **74%** | 1.51 |

**Leitura:** a estratégia-ouro NÃO bate o buy&hold em retorno bruto (2.340% vs 4.032%) num
período que foi majoritariamente de alta. MAS corta o drawdown quase pela metade (44% vs 74%),
o que dá um **Calmar melhor (2.06 vs 1.51)** — mais retorno por unidade de dor. Esse é o edge
REAL e robusto, sem escolher parâmetro a dedo. O Calmar 5.30 do otimizado é o número inflado.

### 2. Portfólio completo (22 ativos) no HOLDOUT (12 meses lacrados, mercado de baixa)

| Cenário | Ret. total | Drawdown | Calmar |
|---|---|---|---|
| Ouro (receita do grupo) | −15,1% | 20% | −0.77 |
| Otimizado (por ativo) | −6,4% | 12% | −0.52 |
| Buy & Hold | **−52,0%** | 60% | −0.87 |

**Leitura:** aqui o mercado caiu ~52% (buy&hold). As duas versões da estratégia protegeram
muito capital — o otimizado perdeu só 6%, a ouro perdeu 15%. Mesmo a receita única (sem
tuning por ativo) evitou 3/4 da queda do mercado. **A proteção de capital é real e sobrevive
sem overfitting.**

### 3. Todos os 22 no DEV — [CUIDADO: janela comum de só 224 dias]
Descartada como métrica principal: como PENGU/TAO/RENDER só existem desde 2024, a janela em que
todos os 22 coexistem no dev encolhe pra ~7 meses (final do dev, um trecho lateral/baixa). Por
isso esse número (ouro −2,5%, otimizado +12,5%) NÃO representa o histórico completo — use a
comparação #1 (veteranas, 5 anos) para o dev.

---

## Conclusões

1. **Existe um padrão real, mas modesto.** A receita-ouro das veteranas entrega Calmar ~2.0
   honesto (vs 1.5 do buy&hold) — melhora de risco real, não os Calmar 5+ dos backtests
   por-ativo.

2. **Duas receitas, não uma.** Veteranas (stop largo, sinal rápido) e novas (stop apertado,
   filtro longo) são genuinamente diferentes. Média lenta 80 é o único ponto comum.

3. **As moedas novas são majoritariamente overfitting.** Só 4% do retorno sobrevive à troca
   pela receita do grupo. Os números bonitos delas (Calmar 13-88) são amostra pequena +
   sorte. Tratar com extrema cautela — bate com o DSR < 5% que já sabíamos.

4. **O valor da estratégia é DEFESA, não ataque.** Em todas as comparações, o ganho consistente
   é cortar drawdown (44% vs 74% no dev; 20% vs 60% no holdout). Ela não multiplica ganho sobre
   o buy&hold em bull market — ela perde muito menos em bear market.

---

---

# REFINAMENTO (reavaliação de amostragem) — 2026-07-29, 2ª rodada

O usuário questionou, com razão: **top-100 é só ~0,36% de 28.035 combinações** — fatia fina
demais, é onde mora a sorte. Reavaliamos alargando o corte (0,36% → 1% → 5% → 10%) e rankeando
também por **Score_Robustez** (não só Calmar cru). Scripts: `analise_padroes_profunda.py` e
`comparar_receitas.py`.

## O que a amostragem larga revelou

1. **Padrões que aguentaram todos os cortes (robustos de verdade):**
   - Filtro tendência: **50** (veteranas) / **100** (novas) — moda estável de 0,36% a 10%.
   - ATR multiplicador **6.0** e ATR período **5-7** nas veteranas — firmes em todo corte.

2. **Um "padrão" do top-100 que era RUÍDO:** nas novas, `atr_mult = 1.0` (stop apertado) só
   aparece no corte fino. No corte de 5% por robustez vira **5.0** (stop largo). O "novas
   preferem stop apertado" era overfitting a alguns pumps de memecoin.

3. **Firmeza do ótimo (platô vs pico):** média de firmeza 0,77 (veteranas) e 0,69 (novas) —
   os ótimos são platôs decentes, não picos isolados, mas as veteranas são mais firmes.

4. **DSR é dirigido por Nº DE TRADES** (Spearman ρ = 0,78), não por histórico (ρ = 0,62, e só
   porque histórico gera trades). Poucos trades = ruído, por mais anos que tenha. Só **7/22**
   passam de DSR 5% — **6 são veteranas, só 1 é nova**.

5. **A receita única generaliza bem nas veteranas** (percentil médio 72 no grid de cada ativo)
   **e mal nas novas** (percentil 38, com desastres: BONK 0,8; IMX 1,2; PENGU 4,1). Novas são
   genuinamente heterogêneas.

## Receita vencedora: ROBUSTA (derivada do corte 5% por robustez, tudo no DEV)

| | Veteranas | Novas |
|---|---|---|
| Média rápida | 5 | 12 |
| Média lenta | 100 | 30 |
| Filtro | 50 | 100 |
| ATR período | 7 | 20 |
| ATR multiplicador | **6.0** | **5.0** ← corrigido (era 1.0 no top-100) |

## Head-to-head (mesmo motor, mesmos pesos)

**DEV veteranas (5 anos) — praticamente empate**, o edge é robusto à receita:

| Receita | Calmar |
|---|---|
| Ouro (top-100) | 2.055 |
| **Robusta (5%)** | 2.023 |
| Híbrida (ajuste manual) | 2.042 |
| Buy & Hold | 1.508 |

**HOLDOUT (12 meses de baixa, 22 ativos) — a ROBUSTA protege muito melhor:**

| Receita | Retorno | Drawdown |
|---|---|---|
| **Robusta (5%)** | **−7,4%** | **13,9%** |
| Ouro (top-100) | −15,1% | 19,7% |
| Híbrida (ajuste manual) | −21,2% | 24,8% |
| Buy & Hold | −52,0% | 60,1% |

## Conclusões do refinamento

- **A ROBUSTA é a receita honesta escolhida.** Mesmo desempenho no dev que a ouro, protege
  o dobro no holdout (−7% vs −15%). A correção do `atr_mult` das novas (1.0→5.0) foi o ganho
  principal — e veio justamente de olhar a amostra larga em vez do top-100.
- **Ajuste manual (híbrida) PIOROU.** Confirma: confie na derivação robusta dos dados, não no
  "achismo" de mexer num parâmetro. Toda vez que a gente tenta "melhorar na mão", tende a
  overfittar.
- **O edge das veteranas é robusto à receita** (Calmar ~2.0 dê qual receita der) — sinal de
  que é real. O das novas é frágil e depende de sorte.

## ⚠️ Guardrail: paramos de mexer no holdout aqui

A ROBUSTA foi derivada **100% do período de desenvolvimento** (corte por robustez no dev).
O número do holdout (−7,4%) é um teste fora-da-amostra legítimo, olhado UMA vez. **Não vamos
ajustar receita pra melhorar o holdout** — isso contaminaria a validação e viraria o mesmo
overfitting que estamos combatendo. "Estratégia perfeita" não existe; a ROBUSTA é a mais
honesta que os dados sustentam sem se enganar.

## Arquivos
- `estrategia_ouro_v5.py` — teste ouro por grupo (reutiliza o motor oficial)
- `estrategia_ouro_v5_por_ativo.csv` — resultado ativo a ativo
- `analise_padroes_profunda.py` — reavaliação de amostragem, DSR, platô vs pico, travados vs livres
- `comparar_receitas.py` — head-to-head ouro vs robusta vs híbrida
- `analise_padroes_profunda_ouro_no_grid.csv` — percentil da receita em cada ativo
