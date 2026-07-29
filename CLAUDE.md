# Projeto: Backtesting/Otimização de Estratégias de Trading Cripto

## Regra crítica (sempre válida, em qualquer sessão/cliente)

**NUNCA altere nada relacionado ao "Código Ômega" original (backend/frontend) sem perguntar antes.**
Esse é o produto/dashboard original do usuário. Todo o trabalho de pesquisa (v2, v3, v4) vive em
arquivos paralelos, novos, que não sobrescrevem nem tocam no código original.

Cada nova versão (v2 → v3 → v4) foi pedida como **arquivos novos**, preservando as versões
anteriores intactas para comparação lado a lado. Ao evoluir a estratégia, siga esse padrão:
crie `_v5` etc. em vez de editar uma versão anterior "no lugar", a menos que seja pedido
explicitamente.

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

### v4 — versão atual, com holdout lacrado e portfólio completo
- `config_v4.py`: define, por ativo, período de desenvolvimento (início do histórico até 12
  meses atrás) e período **lacrado/holdout** (últimos 12 meses). Compartilhado pelos outros
  scripts v4.
- `otimizador_v4.py`: rankeia por **Calmar Ratio** (retorno anualizado / drawdown máximo), com
  o mesmo Score de Robustez do v2 aplicado sobre o Calmar. Calcula também o **Deflated Sharpe
  Ratio** (Bailey & López de Prado) considerando o nº de combinações testadas. Custos
  diferenciados: ativos líquidos (BTC, ETH, BNB, SOL, XRP e outras bluechips) 0,1% taxa + 0,05%
  slippage; menos líquidos (memecoins/tokens menores: PEPE, BONK, FLOKI, 1MBABYDOGE etc.) 0,1%
  taxa + 0,15–0,2% slippage adicional. Roda sobre as 22 criptos do `STRATEGY_PORTFOLIO`
  (frontend original), sem cortes.
- `portfolio_v4.py`: combina os 22 ativos com pesos por volatilidade inversa
  (`portfolio_v4_pesos.csv`), gera `portfolio_v4_resultado.csv`.
- `portfolio_v4_diagnostico_longo.py`: diagnóstico complementar — mesma combinação, mas só com
  ativos de 6+ anos de histórico (exclui temporariamente PENGU, BONK, TAO, SUI, 1MBABYDOGE **só
  nesse teste**, não do portfólio real), pra ter uma janela comum mais longa e estatisticamente
  mais robusta.
- `holdout_v4.py`: contém `validar_holdout_final()`, a **única** função que toca o período
  lacrado. Só deve ser executada quando o usuário pedir explicitamente, com a flag
  `--eu-confirmo-holdout-final`. **Já foi executada** — resultado final em
  `holdout_v4_resultado.csv` / `holdout_v4_portfolio_resultado.csv` (portfólio completo, 22
  ativos, últimos 12 meses). Esse resultado é a validação final — não deve ser usado para mais
  uma rodada de otimização/ajuste de parâmetros.
- `app_v4.py`: dashboard Streamlit (tema escuro, aproximando o visual do dashboard React
  original). Contém:
  - Radar de sinais dos 22 ativos (ALTA/BAIXA), calculado em tempo real com os parâmetros
    escolhidos pela v4 (lógica de cruzamento + filtro + stop ATR — **não** a versão trailing do
    v3).
  - Badge por ativo: Calmar Ratio, Deflated Sharpe Ratio, grupo de liquidez. DSR < 5% recebe
    aviso visual ("resultado histórico curto, tratar com cautela").
  - Peso de cada ativo no portfólio (`portfolio_v4_pesos.csv`).
  - Resumo do portfólio no topo: retorno anualizado, drawdown máximo e Calmar — separando
    claramente período de desenvolvimento vs holdout (holdout rotulado como "validação real,
    mercado de baixa").
  - Análise detalhada por ativo: gráfico de capital estratégia vs buy&hold, separando
    visualmente desenvolvimento e holdout.
  - Cards do radar ordenados pela data do último cruzamento (sinal mais recente primeiro).
  - Para rodar: `.venv\Scripts\streamlit run app_v4.py` (a partir da raiz do projeto).

## Ambiente
- Windows, venv em `.venv/`.
- `otimizador_v4_*.csv` (um por ativo) e `otimizador_v4_RESUMO_ATIVOS.csv` guardam os
  resultados da otimização por ativo — arquivos grandes, tratar como saída gerada, não editar
  manualmente.
