# Revisão do Projeto — auditoria externa

> **Como usar este documento:** é um retrato do projeto numa data. Refaça a revisão no
> futuro (mesma estrutura, mesmas dimensões/notas) e compare lado a lado para medir evolução.

---

## Snapshot — Revisão #1

- **Data:** 2026-07-29
- **Revisor:** análise externa "1x" (perspectiva de eng. de software + finanças + cripto)
- **Escopo:** projeto v4 completo (config/otimizador/portfolio/holdout/app) + trilha de pesquisa
  (padrões top-100, estratégia ouro, receita robusta).
- **Natureza:** análise técnica e metodológica. **Não é recomendação de investimento.**

### Notas por dimensão

| Dimensão | Nota #1 | Comentário curto |
|---|:---:|---|
| Rigor estatístico | **A−** | DSR, robustez por vizinhança, holdout lacrado, custos por liquidez |
| Mecânica do backtest | **A−** | Sinal no fechamento, execução na abertura seguinte — sem look-ahead óbvio |
| Honestidade intelectual | **A** | O próprio time diagnosticou o overfitting e assumiu "edge é defesa, não alpha" |
| Engenharia de software | **C+** | Lógica duplicada, zero testes, acoplamento pesquisa↔produção |
| Robustez do resultado | **C** | Edge fino, n=1 de regime OOS, viés de sobrevivência |

**Veredito em uma linha:** como artefato de pesquisa/engenharia, muito acima da média retail —
a disciplina anti-overfitting é séria e rara. Como sistema que ganha dinheiro, **ainda não
comprovado**: overlay de trend-following defensivo, edge fino, dependente de regime, validado em
**um único** regime out-of-sample e num universo só de sobreviventes.

---

## Pontos genuinamente fortes

1. **Maquinaria anti-overfitting de nível profissional:** Deflated Sharpe (Bailey & López de
   Prado), score de robustez por vizinhança, separação dev/holdout com trava por CLI, custos
   diferenciados por liquidez. A maioria dos projetos retail não tem nada disso.
2. **Mecânica correta:** entrada no candle seguinte ao sinal (`abertura[i]` após
   `sinais_compra[i-1]`), stop intrabar (`minima[i] < stop`). Sem look-ahead nem repainting.
3. **Virada intelectual certa:** reconhecer o curve-fitting dos parâmetros por-ativo e migrar
   para receita robusta única — o que separa pesquisa séria de auto-engano.

---

## Riscos de metodologia / finanças (dimensão nota C)

1. **Edge fino e dependente de regime.** Nas veteranas (dev, ~5 anos de alta), a robusta perde
   do buy&hold em retorno bruto (+2.340% vs +4.032%) e só ganha no ajustado a risco (Calmar 2,0
   vs 1,5). Único teste OOS foi UM mercado de baixa. Regimes OOS ≈ 1.
2. **Overfitting de meta-nível.** DSR corrige a grade de 28k combinações, mas não a busca por
   cima dela (v2→v3→v4→ouro→robusta, timeframes, métrica, seleção de ativos). Custo real de
   múltiplos testes > o que o DSR mostra.
3. **Viés de sobrevivência.** Os 22 ativos são os que existem e têm liquidez HOJE na Binance;
   moedas mortas não entram. Testar só sobreviventes infla resultado.
4. **Holdout sendo reutilizado.** Foi lacrado para a versão otimizada; o número da robusta no
   holdout já foi olhado várias vezes. O status de "nunca visto" está corroendo.
5. **"Diversificação" parcialmente ilusória.** Cripto ~0,7–0,9 correlacionada ao BTC; num crash
   tudo desaba junto (holdout: buy&hold da cesta −52%). 22 moedas ≈ ~2 apostas independentes.
   Pesos por vol inversa não são correlation-aware.
6. **Pesos sobre janela curta** (~223 dias comuns, limitada pelas novas) — vol ruidosa e
   específica de regime.

---

## Riscos de engenharia de software (dimensão nota C+)

1. **Lógica da estratégia duplicada — risco nº 1.** Entrada/saída/stop existe em `executar_backtest_v4`
   (P&L) e `estado_atual_posicao` (sinal ao vivo). Batem hoje, mas nada garante — mexer numa e
   esquecer a outra faz o radar mostrar sinal diferente do testado, silenciosamente.
2. **Zero testes** em 2.285 linhas. Faltam: golden-master do motor; teste de que as duas
   implementações do sinal concordam; teste de ausência de look-ahead.
3. **`RECEITA_ROBUSTA` copiada em 3 arquivos** (app_v4, estrategia_ouro_v5, comparar_receitas) —
   devia viver uma vez só no config.
4. **Código/dados mortos:** constantes e CSVs que o app não usa mais seguem versionados.

---

## Riscos específicos de cripto

- **Slippage otimista onde mais dói** (0,175% p/ memecoin é irreal no momento do stop numa
  cascata) → proteção das ilíquidas provavelmente superestimada.
- **Dados de uma exchange só** (Binance): preços/wicks/delistings específicos.
- **Risco de exchange/regulatório** não modelado (fora de escopo, mas real).

---

## Deploy / ops

- App calcula tudo ao vivo (22×2 backtests + download de 8 anos). Streamlit Cloud grátis (1 GB
  RAM, CPU fraca) pode ser lento/estourar memória no cold start — testar cedo, plano B =
  pré-computar CSV.

---

## Bottom line da Revisão #1

Uma bancada de pesquisa boa e disciplinada, que mostrou com integridade que a estratégia **perde
menos em queda e fica para trás em alta** — um seguro, não uma máquina de alpha. O maior risco
agora não é financeiro, é de **engenharia**: lógica duplicada sem teste pode fazer o radar mentir
sem ninguém perceber.

Ver `PLANO_EVOLUCAO.md` para o plano de ação derivado desta revisão.
