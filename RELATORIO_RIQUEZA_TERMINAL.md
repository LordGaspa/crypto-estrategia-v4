# Riqueza terminal — quanto vale a pena perder em bear para ganhar em bull?

**Data:** 2026-08-01 · **Scripts:** `riqueza_terminal_v6.py`, `riqueza_terminal_v6_exposicao.py`,
`riqueza_terminal_v6_spotcap.py`, `riqueza_terminal_v6_projecao.py` ·
**Pergunta:** todas as análises anteriores compararam variantes por *mediana por regime*. Isso não
é como o dinheiro se acumula. Este relatório refaz a comparação por **montante final composto** —
e responde qual combinação de perda em bear e ganho em bull vale mais ao longo de 10 anos.

---

## Resumo executivo

**A crítica metodológica que motivou este relatório estava certa, e muda conclusões anteriores.**
O scale-out, que parecia a melhor candidata do v6 pelas medianas por regime, é a **segunda pior em
dinheiro acumulado** ($157.906 vs $226.837 da Base, em 5 anos reais). Medianas por regime não
compõem — riqueza sim.

**Correção importante sobre o modo Agressivo:** os $597 mil que ele produz **exigem margem** — a
exposição agregada do portfólio passa de 100% do capital em 28,4% do tempo. Isso não é Binance
Spot, tem custo de juros e risco de liquidação que nenhum backtest deste projeto modela. Limitado
a Spot puro (teto de 100%), o Agressivo entrega **$239.381 contra $226.837 da Base — só 5,5% a
mais**, não 2,6×. A vantagem quase toda vinha de alavancagem.

**Resposta direta à sua pergunta:** o câmbio entre perda em bear e ganho em bull **nunca é 1:1** —
é sempre desfavorável, e piora quanto mais raro for o bull. Com bull em 50% dos anos, aceitar 10pp
a mais de perda em bear exige **+16,8pp** em bull só pra empatar. Com bull em 30% dos anos, exige
**+47,3pp**. A assimetria do juro composto cobra caro por perdas.

**E a resposta prática é desconfortável:** nenhuma das variantes testadas bate o Buy&Hold em
riqueza terminal nos cenários de retorno alto. A estratégia só vence quando o futuro é ruim — e aí
vence por não perder. É exatamente o mesmo diagnóstico de "proteção de capital, não captura de
alta", agora confirmado em dinheiro, não em medianas.

---

## 1. O que foi medido

| | |
|---|---|
| **Janela principal** | 8 veteranas, 2020-08-12 a 2025-08-01 (4,97 anos), composição cronológica real |
| **Janela secundária** | 22 ativos, 2024-12-18 a 2025-08-01 (0,62 ano — curta demais, só referência) |
| **Portfólio** | pesos inverse-vol, mesma metodologia de `portfolio_v4.py` |
| **Capital inicial** | $10.000 |
| **Custos** | taxa + slippage diferenciados por liquidez (idênticos ao motor de produção) |
| **Holdout** | **LACRADO** — nada aqui tocou os últimos 12 meses |

---

## 2. Parte A — Riqueza terminal medida (5 anos reais)

| Variante | Capital final | Múltiplo | CAGR | DD máx | Precisa margem? |
|---|--:|--:|--:|--:|:--|
| BTC_Agressivo | $597.220 | 59,7× | 127,7% | 45,6% | **SIM (28,4% do tempo)** |
| ScaleOut+Agressivo | $348.216 | 34,8× | 104,3% | 40,7% | **SIM** |
| **Buy&Hold** | **$322.045** | 32,2× | 101,1% | **75,1%** | não |
| BTC_Filtrado | $227.478 | 22,7× | 87,5% | 39,8% | não |
| **Base (produção hoje)** | **$226.837** | 22,7× | 87,4% | 45,7% | não |
| VolTarget15 | $223.719 | 22,4× | 86,9% | 40,5% | sim (alavancagem implícita) |
| ScaleOut | $157.906 | 15,8× | 74,2% | 41,5% | não |
| FiltroVolume | $145.935 | 14,6× | 71,5% | **24,4%** | não |

**Leitura:** a Base perde para o Buy&Hold em dinheiro ($227k vs $322k) mas com drawdown muito
menor (45,7% vs 75,1%) — o preço da proteção é real e mensurável. O ScaleOut, campeão das medianas
por regime, fica em penúltimo. O FiltroVolume acumula menos que todos, mas com drawdown de 24,4%,
quase um terço do Buy&Hold.

### 2b. Checagem de margem (a correção mais importante deste relatório)

| Sizing | Exposição mediana | Passa de 100%? | Com margem | **Spot puro (teto 100%)** |
|---|--:|--:|--:|--:|
| Base | 0,49 | nunca | $226.837 | $226.837 |
| Filtrado | 0,39 | nunca | $225.662 | $225.662 (DD 39,9%) |
| **Agressivo 1,5×** | 0,47 | **28,4% do tempo** | $612.566 | **$239.381** |
| Escalado 2,0× | 0,54 | 36,9% | $1.265.805 | $158.585 |
| Escalado 2,5× | 0,66 | 42,6% | $2.253.858 | $105.939 |

**Leitura:** escalar posição só funciona se você puder tomar emprestado. Em Spot puro o teto de
100% corta a exposição justamente quando muitos ativos estão posicionados ao mesmo tempo — que é
exatamente o bull forte que você queria capturar. Por isso 2,0× e 2,5× **pioram** o resultado com
teto. O ganho honesto do Agressivo em Spot é ~5%, não 160%.

---

## 3. Parte B — A fórmula do câmbio

Riqueza compõe geometricamente:

```
ln(1 + g_anual) = p_bull·ln(1+r_bull) + p_lat·ln(1+r_lat) + p_bear·ln(1+r_bear)
Capital_final   = Capital_inicial · (1 + g_anual)^anos
```

Retorno anual mediano medido, por regime (portfólio):

| Variante | BULL | LATERAL | BEAR |
|---|--:|--:|--:|
| Base | +85,4% | +8,8% | −14,2% |
| BTC_Filtrado | +82,1% | +7,8% | −12,0% |
| ScaleOut | +61,7% | +6,1% | −10,5% |
| FiltroVolume | +55,7% | +8,3% | **−4,1%** |
| Buy&Hold | **+141,7%** | +15,0% | **−45,5%** |

**Tabela de indiferença** — quantos pontos percentuais EXTRAS em bull são necessários para
compensar exatamente uma perda ADICIONAL em bear (mesma riqueza final):

| Freq. de bull | bear −5pp | bear −10pp | bear −15pp | bear −20pp |
|---|--:|--:|--:|--:|
| 30% dos anos | +21,6pp | +47,3pp | +78,4pp | +116,3pp |
| 40% dos anos | +13,0pp | +27,8pp | +44,8pp | +64,6pp |
| **50% dos anos** | **+8,0pp** | **+16,8pp** | +26,7pp | +37,9pp |
| 60% dos anos | +4,7pp | +9,8pp | +15,5pp | +21,7pp |

**Leitura — esta é a resposta formal à sua pergunta:** o câmbio nunca é 1:1. Aceitar 10pp a mais de
perda em bear exige +16,8pp em bull se o bull for metade dos anos; +47,3pp se o bull for só 30%.
Quanto mais raro o bull, mais caro fica cada ponto perdido no bear — porque há menos ocasiões de
recuperar. É por isso que perder menos costuma valer mais do que ganhar mais.

---

## 4. Parte C e D — Monte Carlo de 10 anos (block bootstrap)

Método: reamostragem de blocos contíguos de 120 dias dos retornos diários reais, preservando
clusters de regime. Todas as variantes usam os **mesmos blocos** (comparação pareada — mesmo
"futuro" sorteado para todas). 5.000 simulações.

Resultados em **múltiplo do capital**, não em dólares — os retornos de 2020-2025 em cripto são
extraordinários e não devem ser lidos como previsão. O que é robusto é a **ordem**, não o número.

### Cenário A — retornos históricos integrais (otimista irreal)

| Variante | P5 | P25 | Mediana | P95 | P(perder) |
|---|--:|--:|--:|--:|--:|
| Buy&Hold | 7,7× | 106,5× | **840×** | 177.981× | 0,8% |
| Base | 16,0× | 97,9× | 457× | 25.321× | 0,0% |
| BTC_Filtrado | 16,6× | 101,9× | 452× | 25.159× | 0,0% |
| FiltroVolume | 14,5× | 67,4× | 251× | 9.801× | 0,0% |
| ScaleOut | 9,6× | 54,1× | 234× | 10.866× | 0,1% |

### Cenários com "haircut" — e se cripto parar de dar 87%/ano?

| Haircut | Melhor mediana | Melhor P25 (cenário ruim) | Menor P(perda) |
|---|---|---|---|
| 50% (metade do retorno) | Buy&Hold (25,6×) | **FiltroVolume** | **FiltroVolume** (5,1%) |
| 75% (um quarto) | Buy&Hold (4,5×) | **FiltroVolume** | **FiltroVolume** (21,6%) |
| 90% (quase nada) | **FiltroVolume** (2,0×) | **FiltroVolume** | **FiltroVolume** (36,5%) |

**Leitura:** o Buy&Hold vence na mediana enquanto o retorno bruto de cripto for alto — porque
estar sempre comprado captura mais da cauda direita. Mas ele é o pior em P5 e P25 (cenários
ruins) e tem a maior probabilidade de perder dinheiro. Quanto mais o futuro decepciona, mais as
variantes defensivas sobem no ranking — e no cenário mais pessimista, o FiltroVolume vence em
**todas** as métricas ao mesmo tempo.

Comparação pareada (Cenário A, mesmo futuro): Base > ScaleOut em **99%** das simulações; Base >
FiltroVolume em 86%; Buy&Hold > Base em 70%.

---

## 5. Conclusões

1. **Medianas por regime enganam — riqueza terminal é a métrica certa.** O ScaleOut ganhava em
   mediana de bull e perde em dinheiro. Sua crítica metodológica estava correta e reverteu a
   conclusão do relatório anterior.

2. **O ganho do modo Agressivo era majoritariamente alavancagem.** Em Spot puro (sem margem), a
   vantagem cai de +163% para +5,5%. Qualquer decisão baseada naqueles $597k estaria comprando
   risco de liquidação e custo de juros não modelados.

3. **O câmbio bear/bull nunca é 1:1 e depende da frequência de bull.** Com bull em metade dos
   anos, cada 10pp a mais de perda em bear exige +16,8pp em bull só para empatar. A assimetria do
   juro composto favorece estruturalmente perder menos.

4. **Nenhuma variante bate o Buy&Hold em riqueza mediana enquanto o retorno de cripto for alto.**
   A estratégia troca retorno por drawdown muito menor (45,7% vs 75,1%) e por P5/P25 muito
   melhores. Isso é uma escolha legítima — mas é uma escolha, não uma superioridade.

5. **A vantagem da estratégia aparece exatamente quando o futuro decepciona.** Quanto maior o
   haircut, melhor o ranking das variantes defensivas; no cenário mais pessimista o FiltroVolume
   vence em mediana, P25 e probabilidade de perda simultaneamente.

6. **Se o critério for "dormir tranquilo", o FiltroVolume é o mais forte candidato ainda não
   promovido** — drawdown de 24,4% (vs 45,7% da Base) e a menor probabilidade de perder dinheiro
   em todos os cenários testados, ao custo de ~35% menos capital acumulado no cenário histórico.

### Regra de decisão condicional

- **Se você acredita que cripto continuará entregando retornos próximos aos de 2020-2025** →
  Buy&Hold acumula mais na mediana; a estratégia só se justifica pela redução de drawdown.
- **Se você acredita em retornos futuros na metade do histórico ou menos** → as variantes
  defensivas dominam em P25 e probabilidade de perda; o FiltroVolume é o melhor perfil.
- **Entre os dois extremos** → Base e BTC_Filtrado são praticamente empatados (diferença dentro do
  ruído, P(A>B) ≈ 50%); o BTC_Filtrado entrega o mesmo dinheiro com ~6pp menos de drawdown, o que
  o torna a escolha marginalmente melhor entre as duas.

---

## 6. Ressalvas obrigatórias

- **5 anos de histórico não predizem 10 anos.** A janela 2020-2025 contém o bull excepcional de
  2020-2021. Qualquer bootstrap herda isso. Os múltiplos do Cenário A são irreais como previsão —
  por isso os cenários com haircut existem.
- **A frequência futura de regimes é a maior incerteza e domina a resposta.** Ela não é estimável
  a partir de 5 anos de dados.
- **N_efetivo ≈ 2 de 18 ativos** (documentado na Fase 1): o portfólio é muito menos diversificado
  do que 8 ou 22 ativos sugerem. A dispersão real pode ser maior que a simulada.
- **Viés de sobrevivência**: os 22 ativos são os que existem hoje na Binance. Ativos que morreram
  não estão na amostra.
- **As janelas anuais rolantes se sobrepõem** — não são observações independentes. O bootstrap usa
  blocos contíguos para mitigar, mas a amostra efetiva é bem menor que o número de janelas.
- **Drawdown é restrição comportamental, não só número.** A variante Agressivo (com margem) exige
  aguentar 45,6% de queda **com dívida** — e o Buy&Hold exige aguentar 75,1%. Se você vender no
  fundo, o backtest não se realiza.
- **Custo de juros e risco de liquidação de margem não estão modelados** em nenhum número deste
  relatório.
- **Valores em USD** (pares USDT). Converter para BRL adiciona exposição cambial, que é uma
  variável separada e não analisada aqui.
- **Nada aqui foi validado no holdout** — permanece lacrado. Estes são números do período de
  desenvolvimento e projeções sob premissas explícitas, não validação fora da amostra.

---

**Arquivos:** `riqueza_terminal_v6_parteA.csv`, `riqueza_terminal_v6_spotcap.csv`,
`riqueza_terminal_v6_parteB_indiferenca.csv`, `riqueza_terminal_v6_parteC_montecarlo.csv`,
`riqueza_terminal_v6_parteC_pareado.csv`, `riqueza_terminal_v6_parteD_cenarios.csv`.
