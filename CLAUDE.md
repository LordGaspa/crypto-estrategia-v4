# Projeto: Backtesting/Otimização de Estratégias de Trading Cripto

## Regra crítica (sempre válida, em qualquer sessão/cliente)

**NUNCA altere nada relacionado ao "Código Ômega" original (backend/frontend) sem perguntar antes.**
Esse é o produto/dashboard original do usuário. Todo o trabalho de pesquisa (v2, v3, v4) vive em
arquivos paralelos, novos, que não sobrescrevem nem tocam no código original.

Cada nova versão (v2 → v3 → v4 → v5) foi pedida como **arquivos novos**, preservando as versões
anteriores intactas para comparação lado a lado. Ao evoluir a estratégia, siga esse padrão:
crie `_v6` etc. em vez de editar uma versão anterior "no lugar", a menos que seja pedido
explicitamente. Exceção já aceita: ajustes de UI/exibição dentro do `app_v4.py` (ex.: calendário
editável, seletor de modo) podem ser editados in-place, já que o nome do arquivo não carrega mais
o significado de "versão da estratégia" — o que não pode mudar in-place é a lógica de
sinal/entrada/saída em `estrategia_core.py` sem uma trilha de comparação.

## Estrutura do projeto

### v2 — baseline
- `otimizador_v2_robustez.py`: grid search, rankeado por Lucro (%), com Score de Robustez por
  vizinhança (raio 3, pesos decrescentes) para evitar overfitting a um único ponto do grid.
- `walkforward_validacao.py`: validação walk-forward multi-janela sobre 7 ativos
  (FET, BTC, ETH, SOL, XRP, BNB, TRX).

### v3 — saída em trailing stop
- `otimizador_v3_trailing.py`: evolução do v2. Entrada e stop inicial (ATR) iguais; a partir de
  1x o risco inicial de lucro flutuante, o stop vira trailing (máxima desde a entrada − ATR ×
  multiplicador_trailing, só sobe). O cruzamento de médias contrário deixou de fechar posição —
  agora só filtra novas entradas contra a tendência. `multiplicador_trailing` testado em
  {1.0, 1.5, 2.0, 3.0, 4.0}.
- Comparado ao v2 via `comparativo_v2_vs_v3_walkforward.csv`. **Resultado: v3 foi descartado** —
  o app final (v4) usa a lógica de saída original (cruzamento + stop ATR fixo), não a trailing.

### v4 — versão base, com holdout lacrado e portfólio completo
- `estrategia_core.py`: módulo compartilhado com a lógica de sinais/posição — `calcular_sinais()`
  (cruzamento de médias + filtro de tendência), `simular_posicao()` (stop ATR fixo),
  `simular_posicao_trailing()` (a lógica v3, mantida só pra quem quiser reexplorar) e
  `estado_posicao_atual()` (usada pelo radar ao vivo do `app_v4.py`). É a **fonte única da
  verdade**: `otimizador_v4.py`, `app_v4.py` e os testes em `tests/` chamam as mesmas funções, pra
  garantir que o radar nunca diverge do que o backtest testou.
- `config_v4.py`: define, por ativo, período de desenvolvimento (início do histórico até 12
  meses atrás) e período **lacrado/holdout** (últimos 12 meses) via `separar_periodos()`.
  `classificar_liquidez()` decide o grupo de custo (ver abaixo). `ATIVOS_PORTFOLIO_V4` é o dict
  `{symbol: interval}` das 22 criptos (fonte: `STRATEGY_PORTFOLIO` do frontend original, código
  Ômega, só leitura). `carregar_dados()` baixa da Binance e cacheia em
  `cache_dados/{symbol}_{interval}.parquet`; se a Binance bloquear por geolocalização (ex.: deploy
  no Streamlit Cloud), cai pro cache mesmo desatualizado — não deixa o app quebrar. Compartilhado
  por todos os outros scripts v4/v5.
- `otimizador_v4.py`: rankeia por **Calmar Ratio** (retorno anualizado / drawdown máximo), com
  o mesmo Score de Robustez do v2 aplicado sobre o Calmar (vizinhança raio 3, pesos
  decrescentes `{1: 1.0, 2: 0.5, 3: 0.25}` em cada uma das 5 dimensões do grid). Calcula também o
  **Deflated Sharpe Ratio** (Bailey & López de Prado) considerando o nº de combinações testadas
  por ativo. Custos diferenciados (definidos em `config_v4.py`): ativos líquidos (as 8 bluechips
  em `ATIVOS_LIQUIDOS`: BTC, ETH, BNB, SOL, XRP, DOGE, TRX, LINK) 0,1% taxa + 0,05% slippage;
  os 14 restantes (`ATIVOS_MENOS_LIQUIDOS`, complemento automático — memecoins/tokens menores)
  0,1% taxa + 0,175% slippage. Roda sobre as 22 criptos do `ATIVOS_PORTFOLIO_V4`, sem cortes.
  Gera `otimizador_v4_{ativo}.csv` por ativo + `otimizador_v4_RESUMO_ATIVOS.csv` agregado — **é
  esse resumo que alimenta o radar do `app_v4.py` com a lista de ativos/DSR**, mesmo que os
  parâmetros otimizados por ativo não sejam mais o que decide os sinais (ver v5 abaixo).
- `portfolio_v4.py`: combina os 22 ativos com pesos por volatilidade inversa
  (`peso_i = (1/vol_i) / Σ(1/vol_j)`, sobre retornos diários no período de desenvolvimento) —
  gera `portfolio_v4_pesos.csv` e `portfolio_v4_resultado.csv`. Baseado nos parâmetros otimizados
  por ativo (legado — o `app_v4.py` hoje calcula seu próprio portfólio "robusto" ao vivo, ver v5).
- `portfolio_v4_diagnostico_longo.py`: diagnóstico complementar — mesma combinação, mas só com
  ativos de 6+ anos de histórico (exclui temporariamente PENGU, BONK, TAO, SUI, 1MBABYDOGE **só
  nesse teste**, não do portfólio real), pra ter uma janela comum mais longa e estatisticamente
  mais robusta.
- `holdout_v4.py`: contém `validar_holdout_final()`, a **única** função que toca o período
  lacrado (não é chamada automaticamente por nenhum outro script v4/v5). Só deve ser executada
  quando o usuário pedir explicitamente, com a flag `--eu-confirmo-holdout-final`. **Já foi
  executada** — resultado final em `holdout_v4_resultado.csv` / `holdout_v4_portfolio_resultado.csv`
  (portfólio completo, 22 ativos, últimos 12 meses). Esse resultado é a validação final — não
  deve ser usado para mais uma rodada de otimização/ajuste de parâmetros.
  ⚠️ **Exceção conhecida**: `estrategia_ouro_v5.py` (abaixo) também lê a fatia de holdout
  diretamente, pra comparar receita-ouro vs otimizada nos dois períodos — foi uma decisão
  consciente de gastar essa única olhada extra ao destilar a `RECEITA_ROBUSTA`. Depois disso, o
  holdout volta a ficar intocado: não reotimizar contra ele de novo.

### v5 — receita robusta por grupo (o que o app usa em produção hoje)
Motivação: o `otimizador_v4.py` escolhe 1 combinação vencedora entre ~28 mil por ativo — e boa
parte desse resultado é curve-fitting, não edge real. A v5 audita isso e substitui os parâmetros
otimizados por ativo por **2 receitas fixas, uma por grupo de liquidez**, muito mais honestas.

- `estrategia_ouro_v5.py`: pega o **top-100 por Calmar** de cada ativo (nos CSVs do
  `otimizador_v4.py`), agrupa por liquidez (`ATIVOS_LIQUIDOS` = "veterana", resto = "nova") e tira
  a **moda** de cada parâmetro do grid — uma "receita-ouro" por grupo, aplicada igual a todo
  ativo do grupo (não mais 1 otimização por ativo). Gera `estrategia_ouro_v5_por_ativo.csv`.
  Achado central (ver `RELATORIO_ESTRATEGIA_OURO.md`): a receita-ouro retém só ~22%
  (veteranas) / ~4% (novas) do retorno "otimizado" — o resto era overfitting, sobretudo nas
  moedas novas. Refinamento posterior (corte de 5% por Score de Robustez em vez do top-100 puro)
  produziu a **`RECEITA_ROBUSTA`**, hoje congelada em `config_v4.py`:
  - veterana (8 líquidas): rápida=5, lenta=100, filtro=50, atr_período=7, atr_mult=6.0
  - nova (14 restantes): rápida=12, lenta=30, filtro=100, atr_período=20, atr_mult=5.0
  `config_v4.py::grupo_ouro(symbol)` decide qual receita usar por ativo.
- **`app_v4.py` usa `RECEITA_ROBUSTA` para TUDO em produção** — radar de sinais, gráficos e
  portfólio ao vivo (`computar_portfolio_robusto()`) — e **não** os parâmetros otimizados por
  ativo do `otimizador_v4_RESUMO_ATIVOS.csv` (esse CSV ainda é lido, mas só como manifesto:
  lista de ativos, intervalo, DSR pro aviso visual). Guardrail explícito: não reotimizar a
  `RECEITA_ROBUSTA` contra o holdout — ela foi derivada 100% do período de desenvolvimento.
- Fases 1–3 do `PLANO_EVOLUCAO.md` (concluídas) adicionaram, em cima da `RECEITA_ROBUSTA`:
  `walkforward_robusta_v4.py` (validação multi-regime bull/lateral/bear, 115 janelas × 22
  ativos), `trailing_teste_v4.py` (trailing stop reconfirmado como pior, descartado de novo),
  `regime_btc_v4.py` + `backtest_btc_filter_v4.py` (sizing dinâmico por regime do BTC — modos
  Defensivo/Filtrado/Agressivo), `vol_targeting_v4.py` (sizing por fração de risco fixa) e
  `slippage_realista_v4.py` (stress test de slippage nas saídas por stop). Resultados
  consolidados em `fronteira_modos_v4.csv` e expostos no `app_v4.py` nas seções "Modo de
  Alocação" e "Validação Multi-Regime".
- `app_v4.py`: dashboard Streamlit (tema escuro, aproximando o visual do dashboard React
  original). Contém:
  - Radar de sinais dos 22 ativos (ALTA/BAIXA), calculado em tempo real com a `RECEITA_ROBUSTA`
    do grupo do ativo (lógica de cruzamento + filtro + stop ATR — **não** a versão trailing do
    v3) via `estrategia_core.estado_posicao_atual()`.
  - Badge por ativo: chip Veterana/Nova, Deflated Sharpe Ratio, aviso "menos confiável" pra
    ativos do grupo novo (DSR < 5% = aviso extra).
  - Peso de cada ativo no portfólio ao vivo (inverse-vol sobre a `RECEITA_ROBUSTA`,
    `computar_portfolio_robusto()`; `portfolio_v4_pesos.csv` ainda existe mas é legado).
  - Resumo do portfólio no topo: retorno anualizado, drawdown máximo e Calmar — separando
    claramente período de desenvolvimento (veteranas, ~5 anos) vs holdout (22 ativos, últimos 12
    meses, rotulado "validação real, mercado de baixa").
  - Seção "Modo de Alocação" (regime BTC ao vivo) e expander "Validação Multi-Regime"
    (walk-forward por regime bull/lateral/bear + fronteira de modos).
  - Análise detalhada por ativo: gráfico de capital estratégia vs buy&hold com calendário
    editável (`curva_capital_intervalo()`), separando visualmente desenvolvimento e holdout.
  - Cards do radar ordenados pela data do último cruzamento (sinal mais recente primeiro).
  - Para rodar: `.venv\Scripts\streamlit run app_v4.py` (a partir da raiz do projeto).

### Linha descartada — day-trade curto (Fase 1)
`daytrade_*.py` e os CSVs correspondentes são de uma pesquisa separada (Binance Spot,
minutos-a-horas) que **não deu certo**: custo domina o edge em 12/12 combinações testadas, 0/8
ativos com Sharpe significativo em qualquer regime. Ver `RELATORIO_DAYTRADE_FASE1.md` antes de
retomar essa linha — não é bug de implementação, é o edge bruto sendo menor que o piso de custo
da Binance Spot. O holdout desse experimento (`daytrade_holdout.py`) **não foi executado** e deve
seguir intocado a menos que uma nova candidata mostre algo promissor no período de
desenvolvimento primeiro.

### v6 — tentativas de capturar mais bull (nenhuma promovida)
Sete candidatas testadas para atacar a fraqueza conhecida (bate B&H em só 19% das janelas de
bull), todas mantendo o sinal de ENTRADA idêntico e mexendo só na saída/sizing:
`walkforward_v6_adx.py` (filtro ADX — descartado, e a 1ª versão tinha bug: ADX mede força sem
direção, prendia posição em queda forte; corrigido com +DI>-DI e ainda assim neutro-a-pior),
`walkforward_v6c_scaleout.py` (saída parcial), `walkforward_v6d_scaleout_btc.py` (scale-out só em
BULL do BTC), `walkforward_v6e_reentry.py` (reentrada rápida), `walkforward_v6g_scaleout_agressivo.py`
(scale-out + sizing), `walkforward_v6h_scaleout_trailing.py` (trailing na metade remanescente),
`walkforward_v6i_volume_entrada.py` (confirmação de volume na entrada). Funções novas e ADITIVAS
em `estrategia_core.py`: `simular_posicao_filtro_adx`, `simular_posicao_scale_out`,
`simular_posicao_scale_out_trailing` (as originais não foram tocadas). Ver
`RELATORIO_V6_CAPTURA_BULL.md`. **Padrão recorrente: o que ajuda bull custa lateral/bear.**

### Riqueza terminal — correção metodológica importante (LER ANTES DE COMPARAR VARIANTES)
`riqueza_terminal_v6.py`, `riqueza_terminal_v6_exposicao.py`, `riqueza_terminal_v6_spotcap.py`,
`riqueza_terminal_v6_projecao.py` + `RELATORIO_RIQUEZA_TERMINAL.md`. Dois achados que mudam como
avaliar qualquer variante daqui pra frente:
1. **Mediana por regime NÃO é a métrica certa** — medianas não compõem. O scale-out ganhava em
   mediana de bull e é o 2º pior em dinheiro acumulado. Sempre calcular riqueza terminal
   compondo CRONOLOGICAMENTE a curva do portfólio.
2. **Sempre checar exposição agregada antes de creditar ganho a qualquer sizing > 1×.** O modo
   AGRESSIVO (1.5× em BULL) passa de 100% do capital em 28,4% do tempo → exige margem, NÃO é
   Spot. Com teto de 100% ele rende +89,5%/ano em vez de +128,9% — quase empatado com o
   Defensivo (+87,4%). O app agora exibe esse aviso (`modos_margem_v6.csv` +
   `carregar_modos_margem()` em `app_v4.py`). Custo de juros e risco de liquidação **não estão
   modelados em lugar nenhum do projeto**.

## Testes
`tests/test_estrategia_core.py` (a estratégia v4/v5 viva) e `tests/test_daytrade_core.py` (linha
descartada) seguem a mesma disciplina: sinal de cruzamento+filtro, **sem look-ahead** (sinal
passado não muda quando chegam candles futuros), reimplementação independente de
`simular_posicao`, **golden-master** (números congelados num dataset sintético — pega refactors
que mudam resultado em silêncio) e **concordância radar-vs-backtest** (`estado_posicao_atual()`
tem que bater com o que o backtest implica no último candle). Depois de mexer em
`estrategia_core.py` ou `otimizador_v4.executar_backtest_v4`, rodar
`.venv\Scripts\python.exe -m pytest tests/ -q` e conferir que essas categorias continuam passando
antes de confiar em números novos de backtest.

## Ambiente
- Windows, venv em `.venv/`.
- `otimizador_v4_*.csv` (um por ativo) e `otimizador_v4_RESUMO_ATIVOS.csv` guardam os
  resultados da otimização por ativo — arquivos grandes, tratar como saída gerada, não editar
  manualmente.
