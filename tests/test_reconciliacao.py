# -*- coding: utf-8 -*-
"""Testes da reconciliação de posição (reconciliacao.py).

Nenhum toca a rede. A corretora falsa reproduz dois comportamentos REAIS da
Binance que foram verificados na testnet e que definem o módulo inteiro:

  1. o saldo da conta mistura as moedas do bot com as suas;
  2. a comissão da COMPRA é cobrada no ativo comprado, então entra na carteira
     menos do que o `executedQty` da ordem diz.

Cobrem: reconstrução da posição só a partir das ordens do bot, desconto de
comissão, detecção de divergência (venda manual), preservação das moedas do
usuário e o fechamento de posição que fecha SÓ o que é do bot.
"""
from decimal import Decimal

import pytest

from executor_ordens import PREFIXO_ORDEM, id_ordem, COMPRA, VENDA
from reconciliacao import (
    OK, SEM_POSICAO, DIVERGENTE, ERRO,
    posicao_do_bot, reconciliar, vender_posicao_do_bot,
)

TAXA = Decimal("0.001")   # 0,1%, a taxa padrão da Binance


def info_par(symbol="BTCUSDT", base="BTC"):
    return {"symbol": symbol, "baseAsset": base, "quoteAsset": "USDT", "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001", "maxQty": "9000"},
        {"filterType": "NOTIONAL", "minNotional": "5"},
    ]}


class CorretoraFake:
    """Corretora com histórico de ordens e trades, imitando a Binance real."""

    def __init__(self, saldo="0", preco="50000"):
        self._saldo = Decimal(saldo)
        self._preco = Decimal(preco)
        self.ordens = []
        self.trades = []
        self.enviadas = []
        self._proximo_id = 1
        self.falha = None

    # --- helpers de montagem do cenário -----------------------------------
    def registrar(self, lado, qty, client_order_id, cobra_taxa_em_base=None):
        """Registra uma ordem + o trade correspondente, e move o saldo.

        `cobra_taxa_em_base` default: True para compra (a Binance cobra a taxa
        no ativo comprado), False para venda (cobra em USDT)."""
        qty = Decimal(str(qty))
        oid = self._proximo_id
        self._proximo_id += 1
        self.ordens.append({"orderId": oid, "clientOrderId": client_order_id,
                            "symbol": "BTCUSDT", "side": lado, "status": "FILLED",
                            "executedQty": str(qty)})
        comprou = lado == COMPRA
        em_base = comprou if cobra_taxa_em_base is None else cobra_taxa_em_base
        comissao = qty * TAXA if em_base else (qty * self._preco * TAXA)
        self.trades.append({"orderId": oid, "qty": str(qty), "isBuyer": comprou,
                            "commission": str(comissao),
                            "commissionAsset": "BTC" if em_base else "USDT"})
        self._saldo += (qty - (comissao if em_base else 0)) if comprou else -qty
        return oid

    def creditar(self, qty):
        """Moedas que o usuário trouxe por conta própria."""
        self._saldo += Decimal(str(qty))

    def debitar(self, qty):
        """Venda manual / transferencia / saque pelas costas do bot."""
        self._saldo -= Decimal(str(qty))

    # --- interface Corretora ----------------------------------------------
    def info_symbol(self, symbol):
        if self.falha:
            raise self.falha
        return info_par()

    def preco(self, symbol):
        return self._preco

    def saldo(self, ativo):
        return self._saldo if ativo == "BTC" else Decimal("100000")

    def listar_ordens(self, symbol, limite=1000):
        return list(self.ordens)

    def listar_trades(self, symbol, limite=1000):
        return list(self.trades)

    def buscar_ordem(self, symbol, client_order_id):
        return next((o for o in self.ordens if o["clientOrderId"] == client_order_id), None)

    def ordem_mercado(self, symbol, lado, qty, client_order_id):
        self.enviadas.append((symbol, lado, qty, client_order_id))
        self.registrar(lado, qty, client_order_id)
        return {"symbol": symbol, "status": "FILLED", "clientOrderId": client_order_id,
                "executedQty": str(qty), "cummulativeQuoteQty": str(qty * self._preco),
                "fills": [{"qty": str(qty), "price": str(self._preco)}]}


def coid(n=1, lado=COMPRA):
    return id_ordem("BTCUSDT", f"2026-09-08T{n:02d}:00", lado)


# ---------------------------------------------------------------------------
# reconstrução da posição
# ---------------------------------------------------------------------------
def test_sem_historico_nao_ha_posicao():
    assert posicao_do_bot(CorretoraFake(), "BTCUSDT").qty == 0


def test_posicao_desconta_a_comissao_da_compra():
    """O ponto central: a taxa sai em BTC, então entra menos do que a ordem diz.
    Contar pelo executedQty faria o bot tentar vender o que não tem."""
    c = CorretoraFake()
    c.registrar(COMPRA, "0.00255", coid(1))

    pos = posicao_do_bot(c, "BTCUSDT")
    esperado = Decimal("0.00255") * (1 - TAXA)      # 0.00254745
    assert pos.qty == esperado
    assert pos.qty < Decimal("0.00255")             # menor que o executedQty
    assert pos.qty == c.saldo("BTC")                # e bate com a carteira


def test_compra_e_venda_zeram_a_posicao():
    c = CorretoraFake()
    c.registrar(COMPRA, "0.002", coid(1))
    liquido = posicao_do_bot(c, "BTCUSDT").qty
    c.registrar(VENDA, liquido, coid(2, VENDA))
    assert posicao_do_bot(c, "BTCUSDT").qty == 0


def test_compras_somam():
    c = CorretoraFake()
    c.registrar(COMPRA, "0.001", coid(1))
    c.registrar(COMPRA, "0.002", coid(2))
    pos = posicao_do_bot(c, "BTCUSDT")
    assert pos.compras == 2
    assert pos.qty == Decimal("0.003") * (1 - TAXA)


def test_ordens_que_nao_sao_do_bot_sao_ignoradas():
    """Compras suas, feitas na mão, não entram na posição do bot."""
    c = CorretoraFake()
    c.registrar(COMPRA, "0.5", "compra-manual-do-usuario")
    assert posicao_do_bot(c, "BTCUSDT").qty == 0


def test_so_o_prefixo_omega_conta():
    c = CorretoraFake()
    c.registrar(COMPRA, "0.001", coid(1))                 # do bot
    c.registrar(COMPRA, "0.5", "web_abc123")              # da interface da Binance
    pos = posicao_do_bot(c, "BTCUSDT")
    assert pos.compras == 1
    assert pos.qty == Decimal("0.001") * (1 - TAXA)
    assert all(str(i) for i in pos.ids_ordens)
    assert PREFIXO_ORDEM in coid(1)


# ---------------------------------------------------------------------------
# reconciliação
# ---------------------------------------------------------------------------
def test_sem_posicao_pode_operar():
    r = reconciliar(CorretoraFake(), "BTCUSDT")
    assert r.status == SEM_POSICAO and r.pode_operar


def test_posicao_intacta_e_ok():
    c = CorretoraFake()
    c.registrar(COMPRA, "0.002", coid(1))
    r = reconciliar(c, "BTCUSDT")
    assert r.status == OK and r.pode_operar
    assert r.qty_vendavel == r.qty_bot
    assert r.saldo_alheio == 0


def test_moedas_do_usuario_nao_entram_na_posicao_do_bot():
    """Você tem 1 BTC seu; o bot comprou 0.002. Ele enxerga só os 0.002 — e
    reporta o resto como seu."""
    c = CorretoraFake()
    c.creditar("1.0")
    c.registrar(COMPRA, "0.002", coid(1))

    r = reconciliar(c, "BTCUSDT")
    assert r.status == OK
    assert r.qty_bot == Decimal("0.002") * (1 - TAXA)
    assert r.saldo_alheio == Decimal("1.0")
    assert r.qty_vendavel < r.saldo_conta       # nunca vende o saldo inteiro


def test_venda_manual_gera_divergencia():
    """Você vendeu pelo celular o que o bot achava que era dele."""
    c = CorretoraFake()
    c.registrar(COMPRA, "0.002", coid(1))
    c.debitar("0.001")

    r = reconciliar(c, "BTCUSDT")
    assert r.status == DIVERGENTE
    assert not r.pode_operar
    assert "venda manual" in r.motivo


def test_saque_total_gera_divergencia():
    c = CorretoraFake()
    c.registrar(COMPRA, "0.002", coid(1))
    c.debitar(c.saldo("BTC"))
    assert reconciliar(c, "BTCUSDT").status == DIVERGENTE


def test_poeira_de_arredondamento_nao_e_divergencia():
    """Resíduo microscópico não pode travar o bot — seria um alarme falso a
    cada rodada."""
    c = CorretoraFake()
    c.registrar(COMPRA, "0.002", coid(1))
    c.debitar("0.00000001")
    assert reconciliar(c, "BTCUSDT").status == OK


def test_falha_de_api_nao_libera_operacao():
    c = CorretoraFake()
    c.falha = ConnectionError("API fora do ar")
    r = reconciliar(c, "BTCUSDT")
    assert r.status == ERRO and not r.pode_operar


# ---------------------------------------------------------------------------
# fechamento de posição
# ---------------------------------------------------------------------------
def test_fecha_apenas_a_posicao_do_bot():
    """O teste que justifica o módulo: com 1 BTC seu na conta, fechar a posição
    do bot NÃO pode vender o seu BTC."""
    c = CorretoraFake()
    c.creditar("1.0")
    c.registrar(COMPRA, "0.002", coid(1))

    r = vender_posicao_do_bot(c, "BTCUSDT", "2026-09-08T12:00")
    assert r.ok
    assert r.qty <= Decimal("0.002")
    assert c.saldo("BTC") >= Decimal("1.0")      # o seu BTC continua lá


def test_nao_fecha_posicao_se_houver_divergencia():
    c = CorretoraFake()
    c.registrar(COMPRA, "0.002", coid(1))
    c.debitar("0.001")

    r = vender_posicao_do_bot(c, "BTCUSDT", "2026-09-08T12:00")
    assert r.status == "recusada"
    assert not c.enviadas


def test_nao_fecha_o_que_nao_existe():
    r = vender_posicao_do_bot(CorretoraFake(), "BTCUSDT", "2026-09-08T12:00")
    assert r.status == "recusada" and "nao tem posicao" in r.motivo


def test_posicao_residual_pequena_demais_nao_e_vendavel():
    """Sobra abaixo do lote mínimo: reportar, não fingir que fechou."""
    c = CorretoraFake()
    c.registrar(COMPRA, "0.000005", coid(1))     # abaixo do minQty de 0.00001
    r = vender_posicao_do_bot(c, "BTCUSDT", "2026-09-08T12:00")
    assert r.status == "recusada" and not c.enviadas


def test_ciclo_completo_comprar_e_fechar():
    c = CorretoraFake()
    c.registrar(COMPRA, "0.002", coid(1))
    assert reconciliar(c, "BTCUSDT").status == OK

    r = vender_posicao_do_bot(c, "BTCUSDT", "2026-09-08T12:00")
    assert r.ok
    assert reconciliar(c, "BTCUSDT").status == SEM_POSICAO


def test_poeira_de_ciclo_anterior_nao_bloqueia_nova_compra():
    """Depois de um ciclo completo sobra um resto invendável (a compra entrega
    menos que o pedido por causa da taxa, e a venda arredonda pra baixo no passo
    do lote). Se esse resto contasse como posição aberta, o bot travaria naquele
    ativo e nunca voltaria a comprar."""
    c = CorretoraFake()
    c.registrar(COMPRA, "0.002", coid(1))
    vender_posicao_do_bot(c, "BTCUSDT", "2026-09-08T12:00")

    r = reconciliar(c, "BTCUSDT")
    assert r.status == SEM_POSICAO and r.pode_operar
    assert r.poeira > 0                      # o resto existe e é reportado
    assert r.poeira < Decimal("0.00001")     # e é menor que o lote mínimo
