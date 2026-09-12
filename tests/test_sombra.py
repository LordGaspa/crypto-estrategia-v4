# -*- coding: utf-8 -*-
"""Testes do modo sombra (sombra.py).

A função `decidir` é pura de propósito — sem rede, sem disco — então tudo aqui
roda offline. O que ela precisa acertar:

  - começar ZERADA: ativo visto pela primeira vez não vira ordem (regra 3.5 do
    plano; comprar tudo que já estava em ALTA seria entrar atrasado em tudo ao
    mesmo tempo e sujaria a medição no primeiro dia);
  - registrar o desvio entre o preço que o backtest assume e o preço real do
    momento da decisão — é a medição que justifica a fase inteira;
  - respeitar filtros da Binance e guardrails, marcando o motivo da recusa;
  - nunca vender o que não abriu.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from executor_ordens import COMPRA, VENDA, extrair_filtros
from sombra import Leitura, decidir

AGORA = datetime(2026, 9, 12, 12, 0, 0)
SEM_PARAR = "/caminho/que/nao/existe/PARAR"


def filtros(min_notional="5"):
    return extrair_filtros({"symbol": "X", "baseAsset": "X", "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.00001",
         "minQty": "0.00001", "maxQty": "9000"},
        {"filterType": "NOTIONAL", "minNotional": min_notional},
    ]})


def leitura(ativo="BTCUSDT", posicionado=True, preco="100", preco_sinal="98",
            peso="0.09", min_notional="5", candle="2026-09-12 08:00:00"):
    return Leitura(ativo=ativo, interval="6h", posicionado=posicionado,
                   preco_agora=Decimal(preco),
                   preco_sinal=Decimal(preco_sinal) if preco_sinal else None,
                   candle_sinal=candle, stop=90.0,
                   filtros=filtros(min_notional), peso=Decimal(peso))


def rodar(estado, leituras, capital="500"):
    return decidir(estado, leituras, capital=Decimal(capital),
                   agora=AGORA, caminho_parar=SEM_PARAR)


VAZIO = {"posicoes": {}, "vistos": {}}


# ---------------------------------------------------------------------------
# começa zerada
# ---------------------------------------------------------------------------
def test_primeira_vez_nao_gera_ordem():
    """Ativo já em ALTA quando o bot liga não vira compra."""
    linhas, estado = rodar(VAZIO, [leitura(posicionado=True)])
    assert linhas == []
    assert estado["vistos"]["BTCUSDT"] is True
    assert estado["posicoes"] == {}


def test_sinal_novo_depois_de_ligar_gera_compra():
    _, e1 = rodar(VAZIO, [leitura(posicionado=False)])       # conhece em BAIXA
    linhas, e2 = rodar(e1, [leitura(posicionado=True)])      # virou ALTA
    assert len(linhas) == 1
    assert linhas[0]["acao"] == COMPRA and linhas[0]["status"] == "registrada"
    assert "BTCUSDT" in e2["posicoes"]


def test_sem_mudanca_nao_registra_nada():
    _, e1 = rodar(VAZIO, [leitura(posicionado=True)])
    linhas, _ = rodar(e1, [leitura(posicionado=True)])
    assert linhas == []


# ---------------------------------------------------------------------------
# a medição que justifica a fase
# ---------------------------------------------------------------------------
def test_registra_desvio_entre_preco_do_backtest_e_preco_real():
    """O backtest assume entrada a 98; na hora da decisão o preço é 100.
    Esses +2,04% são exatamente a discrepância 3.2 que a Fase 4 mede."""
    _, e1 = rodar(VAZIO, [leitura(posicionado=False)])
    linhas, _ = rodar(e1, [leitura(posicionado=True, preco="100", preco_sinal="98")])
    l = linhas[0]
    assert l["preco_backtest"] == Decimal("98")
    assert l["preco_agora"] == Decimal("100")
    assert l["desvio_pct"] == pytest.approx(2.0408, abs=1e-3)


def test_registra_atraso_desde_o_candle_do_sinal():
    _, e1 = rodar(VAZIO, [leitura(posicionado=False)])
    linhas, _ = rodar(e1, [leitura(posicionado=True, candle="2026-09-12 08:00:00")])
    assert linhas[0]["atraso_h"] == 4.0        # candle 08:00, decisao 12:00


def test_sem_preco_de_sinal_nao_inventa_desvio():
    _, e1 = rodar(VAZIO, [leitura(posicionado=False)])
    linhas, _ = rodar(e1, [leitura(posicionado=True, preco_sinal=None)])
    assert linhas[0]["desvio_pct"] is None


# ---------------------------------------------------------------------------
# ciclo completo
# ---------------------------------------------------------------------------
def test_venda_fecha_a_posicao():
    _, e1 = rodar(VAZIO, [leitura(posicionado=False)])
    _, e2 = rodar(e1, [leitura(posicionado=True)])
    linhas, e3 = rodar(e2, [leitura(posicionado=False)])
    assert linhas[0]["acao"] == VENDA and linhas[0]["status"] == "registrada"
    assert e3["posicoes"] == {}


def test_nao_vende_o_que_nao_abriu():
    """Sinal vira para BAIXA sem que a sombra tivesse comprado (entrou em ALTA
    já ligada). Registrar como venda executada inventaria um lucro."""
    _, e1 = rodar(VAZIO, [leitura(posicionado=True)])       # primeira vez: sem compra
    linhas, _ = rodar(e1, [leitura(posicionado=False)])
    assert linhas[0]["status"] == "ignorada"
    assert "sem posicao" in linhas[0]["motivo"]


def test_valor_da_venda_usa_o_preco_do_momento():
    _, e1 = rodar(VAZIO, [leitura(posicionado=False, preco="100")])
    _, e2 = rodar(e1, [leitura(posicionado=True, preco="100")])
    qty = e2["posicoes"]["BTCUSDT"]["qty"]
    linhas, _ = rodar(e2, [leitura(posicionado=False, preco="150")])
    assert linhas[0]["valor_usd"] == pytest.approx(qty * 150, abs=0.01)


# ---------------------------------------------------------------------------
# filtros e guardrails
# ---------------------------------------------------------------------------
def test_ordem_abaixo_do_minimo_e_recusada_com_motivo():
    """Capital pequeno + peso pequeno => abaixo do mínimo da Binance. Tem que
    aparecer no registro, não sumir."""
    _, e1 = rodar(VAZIO, [leitura(posicionado=False)])
    linhas, e2 = rodar(e1, [leitura(posicionado=True, peso="0.01", min_notional="10")],
                       capital="100")
    assert linhas[0]["status"] == "recusada"
    assert "abaixo do mínimo" in linhas[0]["motivo"]
    assert e2["posicoes"] == {}          # nao abriu posicao


def test_exposicao_acima_de_100_e_bloqueada():
    """Garante que a sombra continua medindo uma operação SPOT: passar de 100%
    do capital exigiria margem."""
    estado = {"posicoes": {"OUTRO": {"qty": 1, "preco": 495, "valor_usd": 495}},
              "vistos": {"BTCUSDT": False}}
    linhas, e = rodar(estado, [leitura(posicionado=True, peso="0.5")])
    assert linhas[0]["status"] == "bloqueada"
    assert "exposicao" in linhas[0]["motivo"]
    assert "BTCUSDT" not in e["posicoes"]


def test_kill_switch_bloqueia(tmp_path):
    parar = tmp_path / "PARAR"
    parar.write_text("")
    _, e1 = decidir(VAZIO, [leitura(posicionado=False)], agora=AGORA,
                    caminho_parar=str(parar))
    linhas, _ = decidir(e1, [leitura(posicionado=True)], agora=AGORA,
                        caminho_parar=str(parar))
    assert linhas[0]["status"] == "bloqueada"
    assert "kill_switch" in linhas[0]["motivo"]


# ---------------------------------------------------------------------------
# vários ativos
# ---------------------------------------------------------------------------
def test_varios_ativos_na_mesma_rodada():
    base = [leitura("BTCUSDT", False), leitura("ETHUSDT", False), leitura("XRPUSDT", False)]
    _, e1 = rodar(VAZIO, base)
    linhas, e2 = rodar(e1, [leitura("BTCUSDT", True), leitura("ETHUSDT", False),
                            leitura("XRPUSDT", True)])
    assert {l["ativo"] for l in linhas} == {"BTCUSDT", "XRPUSDT"}
    assert set(e2["posicoes"]) == {"BTCUSDT", "XRPUSDT"}


def test_estado_nao_e_mutado_no_lugar():
    """`decidir` devolve estado novo; mutar o recebido esconderia bugs de ordem
    de chamada e quebraria qualquer retry."""
    _, e1 = rodar(VAZIO, [leitura(posicionado=False)])
    copia = {"posicoes": dict(e1["posicoes"]), "vistos": dict(e1["vistos"])}
    rodar(e1, [leitura(posicionado=True)])
    assert e1 == copia
