# Plano — execução automática de ordens na Binance

**Status:** planejamento. Nenhuma linha de código de execução foi escrita ainda.
**Objetivo final:** o sistema abre e fecha posições sozinho, seguindo os mesmos sinais que hoje
chegam por e-mail, sem intervenção manual.

---

## 1. A decisão que muda tudo: Spot antes de margem

O pedido original foi margem. Este plano começa em **Spot** por um motivo que vem dos nossos
próprios números, não de cautela genérica:

| Fato já medido no projeto | Onde está |
|---|---|
| A `RECEITA_ROBUSTA` foi derivada e validada **comprada, sem alavancagem** | `estrategia_ouro_v5.py`, `holdout_v4.py` |
| O ganho aparente do modo Agressivo **era alavancagem**: passava de 100% do capital em 28,4% do tempo | `riqueza_terminal_v6_exposicao.py` |
| Com teto Spot, o Agressivo cai de +128,9%/ano para **+89,5%** — quase empatado com o Defensivo (+87,4%) | `riqueza_terminal_v6_spotcap.py` |
| Custo de juros de margem e risco de liquidação: **não modelados em lugar nenhum** | — |

Traduzindo: neste sistema específico, a alavancagem já foi medida e entrega **pouco**, enquanto
adiciona um risco que nunca quantificamos. Margem não sai do plano — vai para a Fase 7, depois que
existir um backtest que modele juros e liquidação. Se o resultado lá for fraco (o que os dados
acima sugerem), teremos economizado o risco de descobrir isso com dinheiro real.

**Além disso, uma diferença estrutural que a margem introduz:** em Spot, o pior caso é o ativo ir a
zero — você perde o que colocou. Em margem, o pior caso é liquidação, que pode acontecer numa mecha
de 15 minutos que o backtest (que só olha fechamento de candle de 4–12h) **nunca teria visto**. Não
é o mesmo jogo com números maiores; é outro jogo.

---

## 2. O que já está pronto e vai ser reaproveitado

Não começamos do zero. O que existe hoje:

- `estrategia_core.estado_posicao_atual()` — decide ALTA/BAIXA. **Fonte única da verdade.**
- `monitor_sinais.py` — já roda 24/7 no servidor, já detecta mudança de sinal, já tem:
  - checagem de dado velho (`MAX_ATRASO_HORAS = 30`) que **exclui** o ativo da comparação;
  - escrita atômica de estado (`os.replace`) que não corrompe se o processo morrer no meio;
  - política de "se o aviso falhar, não salva o estado" — na próxima rodada tenta de novo.
- Servidor fora dos EUA (contorna o bloqueio geográfico da Binance).

O executor entra **como uma camada ao lado** disso, não por cima. O `monitor_sinais.py` continua
avisando; o executor age. Se o executor quebrar, o e-mail continua chegando e você opera na mão.

**Regra de arquitetura inegociável:** o executor **não recalcula** a estratégia com lógica própria.
Ele chama `estado_posicao_atual()`, exatamente como o monitor. Qualquer reimplementação paralela
viraria, com o tempo, uma estratégia diferente da que validamos — e ninguém perceberia.

---

## 3. As cinco discrepâncias entre o backtest e o mundo real

Isto é o coração técnico do projeto. Cada uma precisa de uma decisão explícita e, depois, de
medição. São elas que fazem um bot "que funcionou no backtest" perder dinheiro na vida real.

### 3.1 O stop-loss não existe do mesmo jeito

O backtest checa `mínima <= stop` **no fechamento do candle**. Duas traduções possíveis:

- **Stop de verdade na corretora** (ordem OCO): protege contra crash de madrugada, mas dispara
  *intrabar* — vende em pontos que o backtest nunca vendeu. Muda os resultados.
- **Checagem no fechamento**, replicando o backtest: fiel ao que validamos, mas fica exposto entre
  candles (até 12h descoberto).

**Decisão proposta:** replicar o backtest (checagem no fechamento) na Fase 1, porque é o único jeito
de comparar maçã com maçã. Medir a diferença na Fase 4. Só considerar OCO depois, com número na mão.

### 3.2 Preço de entrada

Backtest entra na **abertura do candle seguinte**. O cron roda a cada 30 min, então na prática
entramos alguns minutos depois. Diferença pequena, mas precisa ser medida — não presumida.

### 3.3 Custo real vs. custo assumido

Assumimos 0,1% de taxa + 0,05% (líquidas) / 0,175% (menos líquidas) de slippage. Na Fase 4 vamos
comparar contra os **fills reais**. Se o slippage real for maior, o `slippage_realista_v4.py` já
existe para reavaliar a estratégia inteira com o número correto.

O `testnet_smoke.py` já mede e imprime esse desvio (deu −0,018% em US$ 200 de BTC). **Esse número
não vale como evidência**: o livro de ofertas da testnet é sintético e raso. Serve só para provar
que a medição funciona; o dado que conta vem da Fase 4, com o mercado real.

### 3.4 Mínimos da Binance — medido, e menos grave do que eu previa

A Binance tem filtros por par: `NOTIONAL` (valor mínimo por ordem) e `LOT_SIZE` (a quantidade
precisa ser múltipla de um passo).

> **Correção.** A primeira versão deste plano afirmava que US$ 500 em 22 ativos geraria ordens
> abaixo do mínimo, e recomendava cortar para 8 ativos. **Isso estava errado.** Consultei os
> filtros reais da Binance (2026-09-08) e o mínimo é de **US$ 1 a US$ 5** por par, não os US$ 5–10
> que eu supus. A recomendação de cortar ativos fica **retirada**.

Números medidos, aplicando os pesos por volatilidade inversa reais (`portfolio_v4_pesos.csv`):

| Capital | Ativos recusados (de 22) |
|---|---|
| US$ 50 | 17 |
| US$ 100 | 8 |
| US$ 150 | 3 |
| US$ 175 | 1 (HBAR) |
| **US$ 200** | **0 — todos passam** |
| US$ 500 | 0 |

O ativo mais exigente é o **HBAR**: com peso de 2,60%, ele sozinho exige um capital total de
**US$ 192** para que sua fatia alcance o mínimo de US$ 5 da Binance.

**Conclusão:** o piso do portfólio completo é de **~US$ 200**. Com os US$ 500 planejados há folga
de 2,5×, e os 22 ativos ficam mantidos. Nenhuma mudança de escopo é necessária.

O que **continua** valendo desta seção: a recusa nunca pode ser silenciosa. Se um dia o capital cair
(por prejuízo) abaixo do piso de algum ativo, o bot tem que **avisar**, não simplesmente deixar de
comprar. Isso está implementado e testado em `calcular_quantidade()`, que devolve motivo legível em
vez de uma quantidade zero muda.

### 3.5 O primeiro dia

No dia em que o bot ligar, vários ativos já estarão em ALTA há semanas — sinal antigo, preço já
subiu. Comprar todos de uma vez é entrar tarde em tudo simultaneamente.

**Decisão proposta:** o bot começa **zerado** e só entra em sinais **novos**, que nascerem depois de
ele ligar. Mesma filosofia do "primeira execução nunca alerta" que já está no monitor.

---

## 4. Fases

Cada fase tem um critério de aprovação. Nenhuma fase começa sem a anterior aprovada.

### Fase 0 — Decisões e chaves (você, ~30 min)

- [ ] Confirmar capital inicial — **mínimo medido: US$ 200** para os 22 ativos (ver 3.4);
      com US$ 500 há folga de 2,5×
- [ ] Criar chave de API na Binance com **permissão de trade LIGADA e permissão de saque DESLIGADA**
- [ ] Restringir a chave ao **IP do servidor Oracle** (a Binance permite allowlist de IP)
- [ ] Definir teto de perda diária que aciona parada automática

> A permissão de saque desligada é a proteção mais importante do projeto inteiro: mesmo que o
> servidor seja invadido e a chave vazada, ninguém consegue **tirar** dinheiro da conta.
>
> **Eu nunca vou pedir, ver ou digitar suas chaves.** Elas vão para o `monitor.env` no servidor
> (permissão 600, já bloqueado no `.gitignore`), do mesmo jeito que a senha do e-mail.

### Fase 1 — Camada de ordens, contra a *testnet* (eu, ~1 dia)

**Status: APROVADA na testnet em 2026-09-08.** Compra enviada, ordem repetida bloqueada, posição
desfeita. Arquivos: `executor_ordens.py`, `tests/test_executor_ordens.py` (37 testes, sem rede) e
`testnet_smoke.py` (o teste contra a testnet real).

> **O que a testnet pegou, e que os testes não pegariam.** A primeira versão confiava na Binance
> recusar um `clientOrderId` repetido. **Ela não recusa.** A unicidade do id só vale enquanto a
> ordem está ABERTA — e ordem a mercado preenche instantaneamente, liberando o id. Na primeira
> execução o teste **comprou duas vezes**. Não havia proteção nenhuma contra o cron rodando em
> duplicidade.
>
> Correção: antes de enviar, o executor **consulta a corretora** ("essa ordem já existe?"). Se a
> consulta falhar, a ordem **não é enviada** (*fail closed*) — perder uma entrada é melhor que
> comprar em dobro. O teste `test_a_corretora_sozinha_NAO_protege_contra_id_repetido` trava essa
> regressão.
>
> É a justificativa inteira desta fase: nenhum teste com corretora falsa acharia isso, porque a
> falsa fazia o que eu *achava* que a Binance fazia. Só o ambiente real desmente a premissa.

- Envolve a API da Binance: consulta saldo, lê os filtros do par (`exchangeInfo`), arredonda a
  quantidade para o `LOT_SIZE`, envia ordem a mercado, confirma o preenchimento
- **Idempotência**: cada ordem carrega um `clientOrderId` derivado de `(ativo, candle, direção)`.
  Se o cron rodar duas vezes por qualquer motivo, a segunda tentativa é **rejeitada pela própria
  Binance** como duplicada, em vez de comprar duas vezes
- Roda inteiro contra a **Binance Spot Testnet** — dinheiro de brinquedo, API idêntica

**Aprovação:** abre e fecha posição na testnet, e ordem duplicada é corretamente recusada.

### Fase 2 — Reconciliação (eu, ~1 dia)

**Status: APROVADA na testnet em 2026-09-08.** Arquivos: `reconciliacao.py`,
`tests/test_reconciliacao.py` (19 testes) e `testnet_reconciliacao.py`.

A regra que evita a categoria mais perigosa de bug: **o bot nunca confia na própria memória.** Ele
reconstrói a posição a partir do histórico da própria corretora — contando só as ordens cujo
`clientOrderId` começa com `omega-` — e compara com o saldo real.

Três coisas que a implementação obrigou a acertar:

**1. O saldo da conta NÃO é a posição do bot.** Se você tiver BTC comprado por conta própria, ele
aparece no mesmo saldo, e um `vender_tudo` ingênuo venderia as suas moedas junto. Prova na testnet:
a conta tinha 1 BTC pré-existente, o bot operou por cima e fechou a posição — o 1 BTC saiu
**1.00000000, intacto**.

**2. A comissão sai do ativo comprado.** Uma compra de `executedQty = 0.00255000` BTC cobra
`0.00000255` BTC de taxa: entram **0.00254745**. Contar pela ordem superestimaria a posição — o bot
tentaria vender o que não tem, e toda reconciliação acusaria divergência falsa. Por isso a contagem
sai dos **trades**, que trazem `commission`, não das ordens.

**3. Sobra poeira em todo ciclo** — descoberto por um teste que falhou. A compra entrega menos que o
pedido (taxa) e a venda arredonda para baixo no passo do lote, então sempre resta um pouco
(0.00000470 BTC na última rodada). Se esse resto contasse como posição aberta, **o bot travaria
naquele ativo para sempre** e nunca voltaria a comprar. Agora um resto invendável é classificado
como poeira, reportado, e não bloqueia nada.

Divergência real (venda manual, transferência, saque) → o bot **para naquele ativo e avisa**; não
decide nada sozinho.

### Fase 3 — Guardrails (eu, ~1 dia)

**Status: CONCLUÍDA em 2026-09-08.** `guardrails.py` + `tests/test_guardrails.py` (44 testes).

O módulo **não faz chamada de rede**: recebe números prontos e devolve um veredito. É deliberado —
guardrail que depende de I/O falha junto com aquilo que deveria proteger, e do jeito que está dá
para testar todos os caminhos sem tocar a corretora. Regra geral: **na dúvida, não opera.**

Cada um responde a uma falha real e conhecida:

| Guardrail | Contra o quê |
|---|---|
| Reusar o `MAX_ATRASO_HORAS = 30` do monitor | operar com dado velho de cache |
| Sanidade de preço (fora de ±X% do último candle → recusa) | dado corrompido / erro de API |
| Teto de perda diária → para tudo e avisa | espiral de perdas |
| Teto de valor por ordem | bug de cálculo mandar 100× o pretendido |
| Disjuntor: N erros seguidos → para e avisa | falha em loop consumindo capital |
| **Kill switch**: arquivo `PARAR` na pasta → não opera | você desligar tudo em 5 segundos, do celular |
| Teto de exposição agregada em 100% | garantir que continua Spot, sem margem acidental |

**Como usar o kill switch** (o único que é operado por você):

```bash
touch PARAR    # o bot para de operar na próxima rodada
rm PARAR       # volta a operar
```

Um arquivo, e não uma variável de ambiente ou uma flag no código, justamente porque precisa
funcionar no susto: por SSH, do celular, sem reiniciar nada e sem esperar deploy.

**Aprovação:** teste automatizado para cada trava, nos dois sentidos — o caso que ela libera e o
caso que ela bloqueia. Trava que só bloqueia deixaria o bot parado para sempre; trava que só libera
é decoração.

### Fase 4 — Sombra: roda em paralelo, sem dinheiro (nós, 4 a 8 semanas)

**Status: NO AR desde 2026-09-12.** `sombra.py` + `tests/test_sombra.py` (14 testes), acoplado à
rodada horária do monitor. Coleta em andamento.

O bot roda de verdade, recebe sinais de verdade, **registra a ordem que teria enviado — e não
envia**. Todo dia compara com o que o backtest diria.

Não usa chave de API: só endpoints públicos (preço e filtros dos pares). É impossível esse arquivo
mover dinheiro.

Cada decisão grava lado a lado o preço que o **backtest assume** (abertura do candle do sinal), o
preço **real no instante da decisão** e o atraso entre os dois. Essa coluna de desvio, acumulada por
semanas, é a resposta para a discrepância 3.2. Junto vão o stop vigente (3.1), o tamanho que caberia
nos filtros reais da Binance (3.4) e o veredito dos guardrails.

Uma trava foi deliberadamente desligada aqui: a de "preço absurdo". Ela existe para cruzar duas
fontes independentes (ticker × candle) e pegar dado corrompido; na sombra só há uma fonte, e usar o
preço do candle do sinal como referência bloquearia, como se fosse erro de API, todo sinal de um
ativo que andou mais de 10% desde o cruzamento — rotina em memecoin. Ela volta no executor real.

É aqui que as cinco discrepâncias da Seção 3 deixam de ser teoria e viram número.

**Aprovação:** as decisões do bot batem com o backtest, e o desvio acumulado é pequeno e explicável.
Não há atalho nesta fase — é ela que separa "funciona" de "achamos que funciona".

### Fase 5 — Dinheiro real, mínimo (você + eu, 4 semanas)

Um ativo. O menor valor que passe do `MIN_NOTIONAL`. E-mail a cada ordem executada.

O objetivo **não é lucro** — é provar que a execução real bate com a Fase 4. Lucro ou prejuízo neste
valor é ruído estatístico; o que importa é a fidelidade.

**Aprovação:** 4 semanas sem intervenção manual, fills dentro do slippage assumido.

### Fase 6 — Escala gradual (nós, 2 a 3 meses)

Aumentar em degraus (25% → 50% → 100% do capital planejado), um degrau por mês, e só se o anterior
correu limpo. Entram aqui os pesos por volatilidade inversa e o rebalanceamento.

### Fase 7 — Avaliar margem (a decidir)

**Só depois de tudo acima.** E começa com backtest, não com código:

1. Refazer a riqueza terminal modelando **juros de margem** (a Binance cobra por hora) e
   **liquidação** (com dados intrabar de 1h, não 4–12h — o backtest atual não enxerga a mecha que
   liquida)
2. Se o resultado líquido não superar o Spot com folga clara, **encerramos aqui** — e teremos a
   resposta de graça
3. Só se superar: alavancagem baixa (1,5×–2× no máximo), um ativo, repetindo as Fases 4→6

---

## 5. Cronograma realista

| | Otimista | Realista |
|---|---|---|
| Fases 0–3 (código + testes) | 3 dias | 1 semana |
| Fase 4 (sombra) | 4 semanas | 8 semanas |
| Fase 5 (real mínimo) | 4 semanas | 4 semanas |
| Fase 6 (escala) | 2 meses | 3 meses |
| **Até capital cheio** | **~4 meses** | **~6 meses** |
| Fase 7 (margem) | — | +1 mês de estudo |

O código é a parte rápida — cerca de uma semana. **O tempo é quase todo de observação**, e é
justamente ele que não dá pra comprimir sem transformar o projeto numa aposta.

---

## 6. O que continua sendo seu

Automatizar a execução **não** automatiza a decisão de continuar. Você segue responsável por:

- **Ligar e desligar.** O kill switch existe pra ser usado.
- **Vigiar o regime.** A estratégia é reconhecidamente fraca em bull (bate o buy & hold em só 19%
  das janelas). Se vier um bull forte, o bot vai ganhar menos que comprar e segurar — e isso é
  esperado, não é defeito.
- **Aceitar o drawdown.** O sistema já teve quedas de dois dígitos no histórico. Desligar no fundo
  transforma uma perda temporária em perda permanente — é o erro mais caro e mais comum.
- **Revisar de tempos em tempos.** Nenhuma estratégia funciona pra sempre. Se o comportamento
  descolar do backtest de forma sustentada, é sinal de reavaliar, não de aumentar a aposta.

---

## 7. Regras que este plano herda do projeto

- Não tocar no **Código Ômega** original (regra do `CLAUDE.md`)
- Não mexer nas funções existentes de `estrategia_core.py` — só adição, como foi feito na v6
- **Holdout permanece lacrado.** Nada aqui reotimiza contra ele
- Arquivos novos, versões antigas preservadas
- Testes seguindo a disciplina de `tests/`: golden-master, sem look-ahead, corretora falsa

---

**Aviso:** este é um sistema pessoal, baseado na `RECEITA_ROBUSTA` derivada do período de
desenvolvimento. Não é recomendação de investimento. Retorno passado em backtest não garante
retorno futuro, e execução automática amplifica tanto acertos quanto erros de implementação.
