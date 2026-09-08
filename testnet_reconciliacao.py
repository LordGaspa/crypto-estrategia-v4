# -*- coding: utf-8 -*-
"""Critério de aprovação da Fase 2 — reconciliação contra a Binance Spot Testnet.

Prova a coisa que mais importa antes de qualquer dinheiro real: **o bot fecha só
a posição dele, e não encosta nas suas moedas.**

A conta da testnet já vem com 1 BTC de brinquedo. Ele faz o papel do BTC que
você teria comprado por conta própria: o bot tem que enxergar esse saldo, saber
que não é dele, e deixá-lo intacto ao fechar posição.

Uso:
    set -a && . ./testnet.env && set +a
    .venv/Scripts/python.exe testnet_reconciliacao.py
"""
import sys
from datetime import datetime, timezone
from decimal import Decimal

from executor_ordens import CorretoraBinance, ErroCorretora, comprar
from reconciliacao import OK, SEM_POSICAO, DIVERGENTE, reconciliar, vender_posicao_do_bot

PAR = "BTCUSDT"
VALOR_TESTE = Decimal("200")


def mostrar(rec, titulo):
    print(f"\n{titulo}")
    print(f"    status ............ {rec.status}")
    print(f"    posicao do bot .... {rec.qty_bot} BTC")
    print(f"    saldo na conta .... {rec.saldo_conta} BTC")
    print(f"    NAO e do bot ...... {rec.saldo_alheio} BTC   <- suas moedas")
    if rec.poeira:
        print(f"    poeira ............ {rec.poeira} BTC (resto invendavel)")
    if rec.motivo:
        print(f"    motivo ............ {rec.motivo}")


def main():
    print("=" * 70)
    print("  FASE 2 — RECONCILIACAO (Binance Spot TESTNET, dinheiro ficticio)")
    print("=" * 70)

    try:
        corretora = CorretoraBinance(testnet=True)
    except ErroCorretora as e:
        print(f"\n[X] {e}")
        return 1

    # 1. estado inicial ----------------------------------------------------
    inicial = reconciliar(corretora, PAR)
    mostrar(inicial, "[1] Antes de qualquer coisa:")
    alheio_antes = inicial.saldo_alheio

    if inicial.status == DIVERGENTE:
        print("\n[!] Ja ha divergencia — o bot se recusaria a operar. Correto,")
        print("    mas impede o resto do teste. Feche a posicao na mao e repita.")
        return 1

    # 2. compra ------------------------------------------------------------
    candle = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    r = comprar(corretora, PAR, VALOR_TESTE, candle)
    print(f"\n[2] COMPRA de US$ {VALOR_TESTE}: {r.status}")
    if not r.ok:
        print(f"    motivo: {r.motivo}")
        return 1
    print(f"    ordem diz executedQty = {r.qty} BTC")

    depois = reconciliar(corretora, PAR)
    mostrar(depois, "[3] Depois da compra:")

    # A prova da contabilidade: o QUANTO A POSICAO CRESCEU e menor que o
    # executedQty da ordem, porque a taxa foi cobrada no proprio BTC.
    # Comparar a posicao TOTAL com uma unica ordem seria errado: ela acumula o
    # que sobrou de rodadas anteriores.
    entrou = depois.qty_bot - inicial.qty_bot
    if entrou >= r.qty:
        print(f"\n    [X] FALHOU — entraram {entrou} BTC, e deveria ser MENOS que")
        print(f"        o executedQty ({r.qty}): a taxa sai em BTC.")
        return 1
    taxa = r.qty - entrou
    print(f"\n    OK — entraram {entrou} BTC, contra executedQty de {r.qty}")
    print(f"    A diferenca ({taxa} BTC) e a comissao, cobrada no proprio ativo.")
    print("    Contar pela ordem faria o bot tentar vender o que nao tem.")

    # 3. fecha SO a posicao do bot ----------------------------------------
    v = vender_posicao_do_bot(corretora, PAR, candle + "-fechar")
    print(f"\n[4] Fechando SO a posicao do bot: {v.status}")
    if v.ok:
        print(f"    vendeu {v.qty} BTC a {v.preco_medio:.2f}")
    else:
        print(f"    motivo: {v.motivo}")

    final = reconciliar(corretora, PAR)
    mostrar(final, "[5] Depois de fechar:")

    # 4. veredito ----------------------------------------------------------
    print("\n" + "=" * 70)
    if final.saldo_alheio + Decimal("0.0001") < alheio_antes:
        print("  [X] FALHOU — o saldo que NAO era do bot encolheu:")
        print(f"      antes {alheio_antes} -> depois {final.saldo_alheio}")
        print("      O bot vendeu moedas que nao eram dele.")
        return 1

    print("  FASE 2 APROVADA")
    print(f"    - suas moedas intactas: {alheio_antes} -> {final.saldo_alheio} BTC")
    print(f"    - posicao do bot fechada (status: {final.status})")
    print("    - comissao contabilizada no ativo, nao no executedQty")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
