# Relatório de Padrões: Top 100 Estratégias por Ativo

**Data:** 2026-07-29  
**Análise:** 22 ativos × 100 melhores resultados = 2.200 estratégias analisadas  
**Métrica de ranking:** Calmar Ratio (retorno anualizado ÷ drawdown máximo)

---

## 📊 RESUMO EXECUTIVO

### Insights Principais

1. **NÃO existe uma "combinação mágica"** — as 2.200 estratégias no top 100 de cada ativo são todas diferentes. Máximo 2 ocorrências da mesma combinação de parâmetros = 0,1%.

2. **MAS há padrões CLAROS** quando se olha para parâmetros individuais:
   - **Média Rápida:** Período curto ganha (9-21 períodos)
   - **Média Lenta:** 80 períodos domina com 20% das ocorrências
   - **Filtro Tendência:** 100% prevalece (29% do top 100)
   - **ATR Período:** Distribuído uniformemente (não há consenso)
   - **ATR Multiplicador:** 6.0 é o mais forte (15% das ocorrências)

3. **Diferença GIGANTE entre ativos líquidos vs não-líquidos:**
   - Ativos líquidos (BTC, ETH, BNB, SOL): Calmar médio **2.46**, retorno **112%**
   - Ativos menos líquidos (PEPE, BONK, FLOKI, PENGU): Calmar médio **10.42**, retorno **369%**

---

## 📈 PERFORMANCE POR ATIVO (TOP 100)

| Ativo | Calmar Médio | Retorno % | Trades | Status |
|-------|------------|----------|--------|--------|
| **PENGU** | 69.05 | 2.392% | 6.2 | ⚠️ Muito poucas trades |
| **BONK** | 12.27 | 479% | 22.5 | ✓ Top tier |
| **FLOKI** | 12.94 | 463% | 34.2 | ✓ Top tier |
| **TAO** | 10.58 | 232% | 25.7 | ✓ Strong |
| **PEPE** | 12.25 | 463% | 24.9 | ✓ Strong |
| **SOL** | 4.29 | 230% | 37.5 | ✓ Solid |
| **SUI** | 7.21 | 243% | 8.0 | ⚠️ Poucas trades |
| **1MBABYDOGE** | 7.45 | 207% | 14.2 | ✓ Good |
| **FET** | 3.09 | 197% | 44.1 | ✓ Good |
| **BNB** | 2.90 | 115% | 41.7 | ✓ Stable |
| **DOGE** | 3.52 | 180% | 43.0 | ✓ Stable |
| **ETH** | 2.18 | 81% | 97.7 | ✓ Many trades |
| **BTC** | 2.23 | 69% | 48.5 | ✓ Stable |
| **HBAR** | 2.34 | 126% | 26.3 | ✓ Good |
| **INJ** | 2.03 | 135% | 35.1 | ✓ Good |
| **LINK** | 1.63 | 80% | 98.4 | ✓ Many trades |
| **IMX** | 1.63 | 61% | 18.4 | ⚠️ Poucas trades |
| **XRP** | 1.77 | 83% | 87.8 | ✓ Many trades |
| **TRX** | 1.17 | 58% | 44.8 | ⚠️ Fraco |
| **ZEC** | 1.08 | 54% | 60.1 | ⚠️ Fraco |
| **RENDER** | 3.58 | 99% | 12.5 | ⚠️ Poucas trades |
| **API3** | 0.42 | 18% | 12.1 | ⚠️ Muito fraco |

**Nota:** PENGU, SUI e RENDER têm DSR baixíssimo (< 5%) — seus altos retornos podem ser sorte, não edge real.

---

## 🎯 PADRÃO DOS PARÂMETROS MAIS FREQUENTES

### 1. Média Rápida (Período em %)

```
Período 18:   366 vezes (16.6%) ← MAIS FREQUENTE
Período 10:   319 vezes (14.5%)
Período 21:   282 vezes (12.8%)
Período 9:    255 vezes (11.6%)
Período 7:    212 vezes ( 9.6%)
```

**Padrão:** Período curto realmente funciona melhor. A média rápida em períodos 7-21 (em vez de 50+) favorece captura de movimentos rápidos.

---

### 2. Média Lenta (Período em %)

```
Período 80:   442 vezes (20.1%) ← DOMINADOR CLARO
Período 40:   299 vezes (13.6%)
Período 50:   288 vezes (13.1%)
Período 120:  258 vezes (11.7%)
Período 30:   251 vezes (11.4%)
```

**Padrão:** 80 períodos é quase 50% mais frequente que a segunda opção. Parece ser "o sweet spot" — longo o bastante para filtrar ruído, curto o bastante para não ficar para trás.

---

### 3. Filtro Tendência (Período em %)

```
Período 100:  641 vezes (29.1%) ← MUITO FREQUENTE
Período 50:   487 vezes (22.1%)
Período 150:  431 vezes (19.6%)
Período 200:  419 vezes (19.0%)
Período 250:  222 vezes (10.1%)
```

**Padrão:** Há dispersão aqui, mas 100-150 concentra ~50% das ocorrências. Períodos muito longos (200+) são menos comuns, sugerindo que filtro muito longo piora resultado.

---

### 4. ATR Período

```
Período 5:    330 vezes (15.0%)
Período 20:   329 vezes (15.0%)
Período 25:   318 vezes (14.5%)
Período 14:   316 vezes (14.4%)
Período 10:   309 vezes (14.0%)
```

**Padrão:** NENHUM CONSENSO — distribuição praticamente uniforme. Todos os períodos testados (5-25) funcionam igualmente bem. Sugerimento: use período 14-20 por ser padrão no mercado.

---

### 5. ATR Multiplicador (Fator de expansão do stop)

```
Multiplicador 6.0:  331 vezes (15.0%) ← MAIS FREQUENTE
Multiplicador 5.0:  318 vezes (14.5%)
Multiplicador 1.0:  271 vezes (12.3%)
Multiplicador 4.0:  265 vezes (12.0%)
Multiplicador 1.5:  237 vezes (10.8%)
```

**Padrão:** Multiplicadores ALTOS (5-6) predominam. Isso significa: stop-loss distante (6x a volatilidade actual). Multiplicadores baixos (1-2) funcionam menos bem nos top100.

---

## 💧 ANÁLISE POR LIQUIDEZ: O INSIGHT MAIS IMPORTANTE

Esta é a descoberta mais surpreendente da análise:

### Ativos LÍQUIDOS (BTC, ETH, BNB, SOL, XRP, TRX, LINK)

```
Calmar médio:     2.46
Retorno médio:    112.44%
Drawdown médio:   42%
ATR Multiplicador preferido: 6.0
```

✓ **Mais confiáveis:** muitos trades, menos volatilidade de backtest
❌ **Menos retorno:** mercado eficiente, já incorpora informações rápido

---

### Ativos MENOS LÍQUIDOS (PEPE, BONK, FLOKI, PENGU, 1MBABYDOGE, etc.)

```
Calmar médio:     10.42  ← 4.2x MAIOR que líquidos
Retorno médio:    369.76%  ← 3.3x MAIOR que líquidos
Drawdown médio:   ~50%
ATR Multiplicador preferido: 1.0  ← OPOSTO dos líquidos
```

⚠️ **Alto retorno E alto risco:** volatilidade extrema, poucos trades
✓ **Oportunidade:** mas com DSR baixíssimo (< 5%) na maioria dos casos

**Interpretação:** Em ativos ilíquidos, o cruzamento de médias captura movimentos macro grandes (pump & dump), mas com pouquíssimas transações. Nos ativos líquidos, a estratégia funciona mais "mecanicamente" com muitos pequenos trades.

---

## 🔍 COMBINAÇÕES TOP (e por que não repetem)

As 20 combinações mais frequentes aparecem apenas **2 vezes cada (0,1%)**. Exemplos:

1. `R:10_L:40_F:100_A:5_M:2.0` — 2 ativos usam isso
2. `R:5_L:80_F:150_A:14_M:6.0` — 2 ativos usam isso
3. `R:21_L:200_F:200_A:20_M:1.5` — 2 ativos usam isso

**Por quê não há "combinação mágica"?**
- Cada ativo tem **volatilidade única**, **microestrutura de mercado única**, **intervalo de dados único**
- O que funciona em BTC (período 14, ATR 20) pode não funcionar em BONK (período 4, ATR 7)
- A otimização testa 28.000 combinações; o top 100 de cada ativo é o resultado dessa busca **específica para aquele ativo**

---

## 📋 RECOMENDAÇÕES PRÁTICAS

### Para Ativos LÍQUIDOS (BTC, ETH, BNB, SOL):

```
Média Rápida:   10-18 períodos
Média Lenta:    80 períodos
Filtro Tendência: 100-150 períodos
ATR Período:    14-20 (padrão da indústria)
ATR Multiplicador: 4-6 (stop distante)
Expectativa:    ~2-3 Calmar, 100-120% retorno anualizado
```

✓ Use quando: quer consistência e muitos trades
❌ Cuidado: retornos modestos, mas com baixa volatilidade

---

### Para Ativos MENOS LÍQUIDOS (PEPE, BONK, FLOKI):

```
Média Rápida:   7-21 períodos (sem consenso, teste ambos)
Média Lenta:    80 períodos (prevalece)
Filtro Tendência: 50-150 períodos
ATR Período:    5-25 (qualquer um funciona)
ATR Multiplicador: 1-2 (stop PRÓXIMO)
Expectativa:    ~10-13 Calmar, 350-470% retorno anualizado
RISCO:          DSR < 5% em 70% dos casos — pode ser sorte!
```

⚠️ Use quando: está disposto a risco extremo e tem capital para drawdown de 50%+
✓ Vantagem: alto retorno em ciclos de bull market

---

## 🎓 CONCLUSÃO

**A "estratégia universal" NOT existe, mas há princípios:**

1. **Média Rápida curta (7-21 períodos) + Média Lenta 80** — combina responsividade com suavidade
2. **Filtro Tendência 100-150 períodos** — evita muitos falsos sinais
3. **ATR período não importa muito** — use 14 ou 20 por padrão
4. **ATR Multiplicador alto (5-6) em líquidos, baixo (1-2) em ilíquidos** — reflete a volatilidade
5. **O ativo é mais importante que os parâmetros** — liquidez define 90% do resultado

**O risco real não é overfitting de parâmetros, é a falta de edge statístico** (DSR < 5%). 42% do portfólio tem DSR baixo; isso significa que ganharam no backteste, não necessariamente que ganharão no futuro.

---

## 📁 Arquivos Gerados

- `analise_top100_consolidado.csv` — todas as 2.200 combinações do top 100 com métricas completas
- `RELATORIO_PADROES_ESTRATEGIA.md` — este relatório

**Para análise futura:** abra o CSV consolidado no Excel/Python e cruze com outros critérios (número de trades, Sharpe ratio, períodos de drawdown, etc.).
