# v7 — Os parâmetros ideais dependem do setor de mercado?

**Data:** 2026-08-02 · **Scripts:** `setores_v7.py`, `otimizador_v7_setores.py`,
`setores_v7_consistencia.py`, `setores_v7_padrao_universal.py` ·
**Hipótese testada:** a estratégia hoje divide os ativos por **liquidez** (veterana/nova). O
mercado cripto, porém, se organiza por **setor** (MEME, Layer 1, IA, Infra, Pagamentos, Gaming).
Se ativos do mesmo setor se movem de forma parecida, os parâmetros ideais deveriam ser mais
parecidos **dentro** do setor do que entre setores — e valeria uma receita por setor.

---

## Resumo executivo

**A hipótese foi refutada, e o motivo da refutação é o achado mais interessante deste relatório.**

Agrupar por setor **não** organiza os parâmetros melhor do que agrupar aleatoriamente. O teste de
permutação (5.000 agrupamentos aleatórios do mesmo tamanho) deu não-significativo nos quatro
timeframes: p = 0,30 (4h), 0,78 (6h), 0,54 (8h), 0,48 (12h). A consistência setorial observada
(41-47%) é essencialmente idêntica à consistência de grupos formados por sorteio (42-47%).

**Por que:** existe consistência real e forte nos parâmetros — mas ela é **universal**, não
setorial. O multiplicador de ATR = 6,0 é o preferido em 53-65% de **todos** os 43 ativos,
independente de setor. O período de ATR = 5 aparece em 40-51%. Como todo mundo quer os mesmos
valores, qualquer agrupamento (setorial ou aleatório) exibe a mesma concordância — e por isso o
setor não acrescenta informação.

**O que isso valida:** a `RECEITA_ROBUSTA` em produção usa exatamente `atr_multiplicador = 6.0`
no grupo veterana. O padrão universal encontrado em 43 ativos, 4 timeframes e um universo 2×
maior confirma independentemente essa escolha — que foi derivada de outros dados, por outro
método. Isso é uma confirmação genuína, não circular.

---

## 1. O que foi testado

| | |
|---|---|
| **Universo** | 43 ativos (21 novos, escolhidos por maior capitalização dentro de cada setor) |
| **Filtro de entrada** | mínimo 3 anos de histórico (menos que isso não sobra dev após o holdout) |
| **Setores** | MEME (7), LAYER1 (14), IA (6), GAMING (6), INFRA (5), PAGAMENTOS (5) |
| **Timeframes** | 4h, 6h, 8h, 12h — todos os ativos em todos (172 grades) |
| **Grade** | 28.035 combinações por grade — mesma do `otimizador_v4.py`, mesmo motor e custos |
| **Critério de perfil** | top 5% por **Score de Robustez** (não por Calmar puro — lição do v5) |
| **Holdout** | **LACRADO** |

Decisão metodológica: buscamos o parâmetro mais **constante**, não o mais lucrativo. Buscar lucro
máximo foi o que produziu o "imposto do overfitting" documentado em `RELATORIO_ESTRATEGIA_OURO.md`.

---

## 2. Consistência dentro do setor vs. acaso (o teste que decide)

| Timeframe | Consistência setorial | Consistência de grupos aleatórios | p-value | Veredito |
|---|--:|--:|--:|---|
| 4h | 47,4% | 46,6% | 0,2992 | não significativo |
| 6h | 41,1% | 42,4% | 0,7808 | não significativo |
| 8h | 42,5% | 42,7% | 0,5366 | não significativo |
| 12h | 47,0% | 46,9% | 0,4822 | não significativo |

**Leitura:** em 6h a divisão setorial foi até *pior* que o acaso. Nenhum timeframe chega perto de
significância. Se derivássemos uma "receita por setor" a partir daqui, estaríamos ajustando ruído
— exatamente o erro que a `RECEITA_ROBUSTA` foi criada para evitar.

---

## 3. O padrão que existe é universal, não setorial

Valor preferido por parâmetro, no universo inteiro (43 ativos):

| Parâmetro | 4h | 6h | 8h | 12h |
|---|--:|--:|--:|--:|
| **atr_multiplicador** | **6,0 (60%)** | 6,0 (53%) | 6,0 (53%) | **6,0 (65%)** |
| **atr_periodo** | 5 (51%) | 5 (42%) | 5 (40%) | 5 (51%) |
| media_filtro | 100 (33%) | 150 (28%) | 50 (33%) | 100 (33%) |
| media_rapida | 21 (33%) | 21 (16%) | 5 (23%) | 21 (21%) |
| media_lenta | 200 (26%) | 80 (23%) | 50 (23%) | 40 (21%) |

**Leitura:** os dois parâmetros do **stop** (multiplicador e período do ATR) são estáveis e
concordantes em todo o universo; os três parâmetros de **médias móveis** são dispersos e sem
consenso. Ou seja: o mercado cripto "concorda" sobre como dimensionar o stop, mas não sobre qual
velocidade de média usar. Isso é coerente com o que já sabíamos — a estratégia é defensiva, e o
que a faz funcionar é o stop largo (ATR×6), não a escolha fina das médias.

---

## 4. O timeframe ideal depende do setor?

Contagem de qual timeframe venceu (maior Calmar do topo da grade) por ativo:

| Setor | 4h | 6h | 8h | 12h |
|---|--:|--:|--:|--:|
| GAMING | 0 | 1 | 3 | 2 |
| IA | 2 | 1 | 1 | 2 |
| INFRA | 1 | 0 | 1 | 3 |
| LAYER1 | 1 | 5 | 4 | 4 |
| MEME | 2 | 1 | 2 | 1 |
| PAGAMENTOS | 2 | 0 | 2 | 1 |

Qui-quadrado: **p = 0,6725 — não significativo.** (Ressalva: 24 células com frequência esperada
< 5, o teste é pouco confiável nesse tamanho de amostra; mas não há sinal aparente nem
visualmente.)

**Leitura:** não há evidência de que o timeframe ideal dependa do setor. A hipótese de que
"memecoin quer timeframe rápido e Layer 1 quer lento" não aparece nos dados.

O que **é** significativo (p < 0,004 nos 4 timeframes) é que o **Calmar do topo da grade** difere
entre setores — MEME atinge Calmar mediano de 13,3 em 4h contra 1,4 de PAGAMENTOS. Mas isso é
majoritariamente artefato: MEME tem histórico curto e volatilidade extrema, o que faz o topo de
uma grade de 28 mil combinações atingir valores altíssimos por sorte. É a estatística mais
contaminada por overfitting do projeto inteiro — não deve ser lida como "a estratégia funciona
melhor em memecoin".

---

## 5. Conclusões

1. **A hipótese setorial foi refutada em todos os 4 timeframes.** Setor não organiza os
   parâmetros melhor que sorteio aleatório. Não há base para criar receitas por setor.

2. **A consistência real é universal:** ATR multiplicador = 6,0 (53-65% dos ativos) e ATR período
   = 5 (40-51%). O stop é a parte estável da estratégia; as médias móveis são a parte instável.

3. **Isso é uma validação independente da `RECEITA_ROBUSTA`.** O `atr_multiplicador = 6.0` que ela
   usa no grupo veterana emergiu de novo, num universo com quase o dobro de ativos, incluindo 21
   que nunca participaram da derivação original, em 4 timeframes. Confirmação por caminho
   independente é raro neste projeto — vale registrar.

4. **Não há evidência de que o timeframe ideal dependa do setor** (p = 0,67).

5. **Nada foi promovido a produção.** A `RECEITA_ROBUSTA` continua intacta. O resultado deste
   relatório é uma hipótese descartada com rigor — que tem valor: evita que a gente construa uma
   camada de complexidade (6 receitas em vez de 2) sobre um padrão que não existe.

---

## 6. Ressalvas

- **A classificação setorial tem ambiguidade real** (documentada em `setores_v7.py::AMBIGUOS`):
  BNB é L1 mas também token de exchange; ZEC é privacidade agrupada em pagamentos; IMX é L2 e
  gaming. Uma classificação diferente poderia mudar marginalmente os números — mas dificilmente
  reverteria um p-value de 0,30-0,78.
- **21 dos 43 ativos são novos** e nunca passaram pelo pipeline de validação do v4. Entraram aqui
  só como amostra para o teste de consistência, não como candidatos ao portfólio.
- **Viés de sobrevivência**: os ativos foram escolhidos por capitalização *hoje*. Setores inteiros
  que morreram (ex.: várias narrativas de 2021) não estão representados.
- **`Melhor_Calmar` é topo de grade** — usar apenas como sinal exploratório, nunca como estimativa
  de desempenho.
- **Holdout permanece lacrado.** Nada aqui o tocou.

---

**Arquivos:** `setores_v7_consistencia.csv`, `setores_v7_padrao_universal.csv`,
`otimizador_v7_RESUMO.csv`, e 172 grades `otimizador_v7_{ATIVO}_{TF}.csv` (git-ignoradas por
tamanho).
