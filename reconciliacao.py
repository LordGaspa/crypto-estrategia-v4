# -*- coding: utf-8 -*-
"""Reconciliação de posição (Fase 2 do PLANO_EXECUCAO_AUTOMATICA.md).

A regra que evita a categoria mais perigosa de bug: **o bot nunca confia na
própria memória.** Antes de agir ele reconstrói, a partir do histórico da
própria corretora, quanto ele comprou e vendeu — e compara com o saldo real.

Duas descobertas moldaram este módulo:

1. **O saldo da conta NÃO é a posição do bot.** Se você tiver BTC comprado por
   conta própria, ele aparece no mesmo saldo. Um `vender_tudo` ingênuo venderia
   suas moedas junto. A posição do bot é reconstruída a partir das ordens que
   carregam o prefixo `omega-` no `clientOrderId` — é ele que diz "esta ordem foi
   minha". O que exceder isso é seu, e o bot não encosta.

2. **A comissão sai do ativo comprado** (verificado na testnet em 2026-09-08):
   uma compra de `executedQty = 0.00255000` BTC cobra `0.00000255` BTC de taxa,
   então entram na carteira **0.00254745**. Contar pela ordem superestima a
   posição — o bot tentaria vender o que não existe, e toda reconciliação
   acusaria uma divergência falsa. Por isso a contagem é feita sobre os
   **trades** (que trazem `commission` e `commissionAsset`), não sobre as ordens.

Limite conhecido: o histórico da Binance é paginado e finito. Para um bot
rodando por muitos meses, `LIMITE_HISTORICO` precisa virar paginação por
`fromId`. Enquanto a operação for de poucos ativos e poucos trades por semana,
o limite atual cobre com folga — mas isto está aqui para não ser esquecido.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from executor_ordens import (
    PREFIXO_ORDEM, VENDA, Corretora, ResultadoOrdem,
    extrair_filtros, quantidade_para_vender, enviar_ordem,
)

# Quantos registros de histórico buscar por consulta (máximo da API: 1000).
LIMITE_HISTORICO = 1000

# Tolerância para "poeira" de arredondamento ao comparar posição x saldo.
# Um resíduo microscópico não é divergência real; some da corretora por
# arredondamento no último dígito. Fica MUITO abaixo de qualquer ordem viável.
TOLERANCIA_POEIRA = Decimal("0.00000002")

OK = "ok"
SEM_POSICAO = "sem_posicao"
DIVERGENTE = "divergente"
ERRO = "erro"


@dataclass(frozen=True)
class PosicaoBot:
    """Quanto do ativo o bot comprou e ainda não vendeu, líquido de comissão."""
    symbol: str
    qty: Decimal
    compras: int = 0
    vendas: int = 0
    ids_ordens: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class Reconciliacao:
    status: str
    symbol: str
    qty_bot: Decimal = Decimal(0)       # o que o bot acha que é dele
    saldo_conta: Decimal = Decimal(0)   # o que existe de fato na carteira
    qty_vendavel: Decimal = Decimal(0)  # o que ele pode vender com segurança
    saldo_alheio: Decimal = Decimal(0)  # o excedente — é seu, o bot não toca
    poeira: Decimal = Decimal(0)        # resto invendável de ciclos anteriores
    motivo: str = ""

    @property
    def pode_operar(self) -> bool:
        return self.status in (OK, SEM_POSICAO)


def posicao_do_bot(corretora: Corretora, symbol: str) -> PosicaoBot:
    """Reconstrói a posição do bot a partir do histórico da corretora.

    Só conta ordens cujo `clientOrderId` começa com `omega-` — as nossas. E soma
    pelos trades, descontando a comissão quando ela é cobrada no próprio ativo
    (ver cabeçalho do módulo)."""
    ordens = corretora.listar_ordens(symbol, LIMITE_HISTORICO)

    ids_do_bot = {
        o["orderId"] for o in ordens
        if str(o.get("clientOrderId", "")).startswith(PREFIXO_ORDEM)
    }
    if not ids_do_bot:
        return PosicaoBot(symbol, Decimal(0))

    info = corretora.info_symbol(symbol)
    base = info["baseAsset"]

    qty = Decimal(0)
    compras = vendas = 0
    for t in corretora.listar_trades(symbol, LIMITE_HISTORICO):
        if t["orderId"] not in ids_do_bot:
            continue                      # trade seu, feito na mão — ignorar
        quantidade = Decimal(str(t["qty"]))
        if t["isBuyer"]:
            qty += quantidade
            # a taxa da COMPRA sai em BTC (o ativo comprado): desconta.
            if t.get("commissionAsset") == base:
                qty -= Decimal(str(t.get("commission", "0")))
            compras += 1
        else:
            qty -= quantidade
            # a taxa da VENDA sai em USDT, nao mexe na quantidade do ativo.
            vendas += 1

    return PosicaoBot(symbol, max(qty, Decimal(0)), compras, vendas, tuple(sorted(ids_do_bot)))


def reconciliar(corretora: Corretora, symbol: str) -> Reconciliacao:
    """Confere a posição do bot contra o saldo real. Falha => não opera.

    `divergente` significa que sumiram moedas que o bot achava ter — uma venda
    manual, uma transferência, um saque. Nesse caso ele **não** decide nada
    sozinho: para naquele ativo e avisa."""
    try:
        info = corretora.info_symbol(symbol)
        base = info["baseAsset"]
        filtros = extrair_filtros(info)
        pos = posicao_do_bot(corretora, symbol)
        saldo = corretora.saldo(base)
        preco = corretora.preco(symbol)
    except Exception as e:
        return Reconciliacao(ERRO, symbol, motivo=f"{type(e).__name__}: {e}")

    if pos.qty <= 0:
        return Reconciliacao(SEM_POSICAO, symbol, Decimal(0), saldo,
                             saldo_alheio=saldo,
                             motivo="bot nao tem posicao aberta neste ativo")

    # Posição pequena demais para ser vendida = POEIRA, não posição aberta.
    #
    # Ela aparece sozinha em todo ciclo: a compra entrega menos que o pedido
    # (taxa em BTC) e a venda arredonda pra baixo no passo do lote, então sempre
    # sobra um resto. Tratar esse resto como "posicionado" travaria o bot para
    # sempre naquele ativo — ele nunca voltaria a comprar. Descoberto por
    # test_ciclo_completo_comprar_e_fechar.
    q = quantidade_para_vender(pos.qty, filtros)
    if not q.aceita or q.qty * preco < filtros.valor_minimo:
        return Reconciliacao(SEM_POSICAO, symbol, Decimal(0), saldo,
                             saldo_alheio=max(saldo - pos.qty, Decimal(0)),
                             poeira=pos.qty,
                             motivo=(f"resto de {pos.qty} {base} pequeno demais para vender "
                                     f"(poeira de ciclos anteriores) — nao conta como posicao"))

    if saldo + TOLERANCIA_POEIRA < pos.qty:
        return Reconciliacao(
            DIVERGENTE, symbol, pos.qty, saldo, qty_vendavel=saldo,
            motivo=(f"o bot registrou {pos.qty} {base} mas a carteira tem "
                    f"{saldo}. Faltam {pos.qty - saldo} {base} — houve venda "
                    f"manual, transferencia ou saque. Nao vou operar {symbol} "
                    f"ate isso ser resolvido."))

    return Reconciliacao(OK, symbol, pos.qty, saldo,
                         qty_vendavel=pos.qty,
                         saldo_alheio=max(saldo - pos.qty, Decimal(0)))


def vender_posicao_do_bot(corretora: Corretora, symbol: str, candle) -> ResultadoOrdem:
    """Fecha a posição do bot — e SOMENTE ela.

    Diferente de `executor_ordens.vender_tudo`, que liquida o saldo inteiro do
    ativo. Se você tiver moedas próprias na mesma conta, elas não são tocadas."""
    rec = reconciliar(corretora, symbol)

    if rec.status == ERRO:
        return ResultadoOrdem("erro", symbol, VENDA, "", motivo=rec.motivo)
    if rec.status == DIVERGENTE:
        return ResultadoOrdem("recusada", symbol, VENDA, "", motivo=rec.motivo)
    if rec.status == SEM_POSICAO:
        return ResultadoOrdem("recusada", symbol, VENDA, "",
                              motivo="bot nao tem posicao para fechar")

    try:
        filtros = extrair_filtros(corretora.info_symbol(symbol))
    except Exception as e:
        return ResultadoOrdem("erro", symbol, VENDA, "", motivo=f"{type(e).__name__}: {e}")

    q = quantidade_para_vender(rec.qty_vendavel, filtros)
    if not q.aceita:
        return ResultadoOrdem("recusada", symbol, VENDA, "", motivo=q.motivo)

    return enviar_ordem(corretora, symbol, VENDA, q.qty, candle)
