# -*- coding: utf-8 -*-
"""Testes da fonte única da verdade da estratégia (estrategia_core) e do motor
de backtest (executar_backtest_v4).

Cobrem:
  - calcular_sinais: condições de cruzamento + filtro corretas;
  - ausência de look-ahead: sinal passado não muda quando chega candle futuro;
  - simular_posicao: bate com uma reimplementação independente (property test);
  - golden-master: executar_backtest_v4 num dataset sintético fixo produz
    números congelados (trava de regressão — o refactor não pode mudá-los);
  - concordância: o estado do radar (estado_posicao_atual) == a posição
    implícita do backtest no último candle (o radar não pode mentir).
"""
import numpy as np
import pandas as pd

from estrategia_core import calcular_sinais, simular_posicao, estado_posicao_atual
from otimizador_v4 import executar_backtest_v4


# ---------------------------------------------------------------------------
# dados sintéticos determinísticos (IDÊNTICOS aos usados pra capturar o golden)
# ---------------------------------------------------------------------------
def build_synthetic(n=1200, seed=42):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0005, 0.03, n)
    ret[100:200] += 0.01
    ret[400:520] -= 0.012
    ret[700:820] += 0.011
    close = 100 * np.cumprod(1 + ret)
    abertura = np.empty(n)
    abertura[0] = 100
    abertura[1:] = close[:-1]
    maxima = np.maximum(abertura, close) * (1 + np.abs(rng.normal(0, 0.01, n)))
    minima = np.minimum(abertura, close) * (1 - np.abs(rng.normal(0, 0.01, n)))
    t = pd.date_range("2020-01-01", periods=n, freq="6h").values
    return pd.DataFrame({"abertura": abertura, "maxima": maxima, "minima": minima,
                         "fechamento": close, "t_abert": t})


def montar(df, p):
    d = {"abertura": df["abertura"].values, "minima": df["minima"].values,
         "fechamento": df["fechamento"].values, "t_abert": df["t_abert"].values}
    d[f"ma_{p['media_rapida']}"] = df["fechamento"].rolling(p["media_rapida"]).mean().values
    d[f"ma_{p['media_lenta']}"] = df["fechamento"].rolling(p["media_lenta"]).mean().values
    d[f"ma_f_{p['media_filtro']}"] = df["fechamento"].rolling(p["media_filtro"]).mean().values
    tr = pd.concat([df["maxima"] - df["minima"],
                    (df["maxima"] - df["fechamento"].shift()).abs(),
                    (df["minima"] - df["fechamento"].shift()).abs()], axis=1).max(axis=1)
    d[f"atr_{p['atr_periodo']}"] = tr.rolling(p["atr_periodo"]).mean().values
    return d


P = dict(media_rapida=5, media_lenta=20, media_filtro=50, atr_periodo=14, atr_multiplicador=3.0)


# ---------------------------------------------------------------------------
# 1) calcular_sinais
# ---------------------------------------------------------------------------
def test_calcular_sinais_compra_no_cruzamento_para_cima():
    # rápida cruza a lenta para cima no índice 2, fechamento acima do filtro
    rapida = np.array([1.0, 1.0, 3.0, 3.0])
    lenta = np.array([2.0, 2.0, 2.0, 2.0])
    filtro = np.array([0.0, 0.0, 0.0, 0.0])
    fech = np.array([5.0, 5.0, 5.0, 5.0])
    compra, venda = calcular_sinais(rapida, lenta, filtro, fech)
    assert list(compra) == [False, False, True, False]
    assert not venda.any()


def test_calcular_sinais_filtro_bloqueia_compra():
    # mesmo cruzamento, mas fechamento ABAIXO do filtro -> sem compra
    rapida = np.array([1.0, 1.0, 3.0, 3.0])
    lenta = np.array([2.0, 2.0, 2.0, 2.0])
    filtro = np.array([10.0, 10.0, 10.0, 10.0])
    fech = np.array([5.0, 5.0, 5.0, 5.0])
    compra, _ = calcular_sinais(rapida, lenta, filtro, fech)
    assert not compra.any()


def test_calcular_sinais_venda_no_cruzamento_para_baixo():
    rapida = np.array([3.0, 3.0, 1.0, 1.0])
    lenta = np.array([2.0, 2.0, 2.0, 2.0])
    filtro = np.array([0.0, 0.0, 0.0, 0.0])
    fech = np.array([5.0, 5.0, 5.0, 5.0])
    _, venda = calcular_sinais(rapida, lenta, filtro, fech)
    assert list(venda) == [False, False, True, False]


# ---------------------------------------------------------------------------
# 2) ausência de look-ahead: os sinais dos candles passados não podem mudar
#    quando novos candles (futuros) são acrescentados.
# ---------------------------------------------------------------------------
def test_sem_look_ahead():
    df = build_synthetic()
    d = montar(df, P)
    m_r, m_l, m_f, fe = (d[f"ma_{P['media_rapida']}"], d[f"ma_{P['media_lenta']}"],
                         d[f"ma_f_{P['media_filtro']}"], d["fechamento"])
    compra_full, venda_full = calcular_sinais(m_r, m_l, m_f, fe)
    corte = 800
    compra_parcial, venda_parcial = calcular_sinais(m_r[:corte], m_l[:corte], m_f[:corte], fe[:corte])
    assert np.array_equal(compra_full[:corte], compra_parcial)
    assert np.array_equal(venda_full[:corte], venda_parcial)


# ---------------------------------------------------------------------------
# 3) simular_posicao vs reimplementação independente (property test)
# ---------------------------------------------------------------------------
def _referencia_walk(abertura, minima, atr, compra, venda, multi, slippage):
    """Reimplementação independente e óbvia das mesmas regras, pra cruzar."""
    n = len(abertura)
    pos = False
    stop = 0.0
    saldo_eventos = []
    for i in range(1, n):
        if not pos and compra[i - 1] and abertura[i] > 0:
            ent = abertura[i] * (1 + slippage)
            stop = ent - atr[i - 1] * multi
            pos = True
            saldo_eventos.append(("entrada", i))
        elif pos:
            if minima[i] < stop or venda[i - 1]:
                pos = False
                saldo_eventos.append(("saida", i))
    return saldo_eventos, pos


def test_simular_posicao_bate_com_referencia():
    df = build_synthetic(seed=7)
    d = montar(df, P)
    compra, venda = calcular_sinais(d[f"ma_{P['media_rapida']}"], d[f"ma_{P['media_lenta']}"],
                                    d[f"ma_f_{P['media_filtro']}"], d["fechamento"])
    for slip in (0.0, 0.0015):
        eventos, estado = simular_posicao(d["abertura"], d["minima"], d[f"atr_{P['atr_periodo']}"],
                                          compra, venda, P["atr_multiplicador"], slip)
        ref_ev, ref_pos = _referencia_walk(d["abertura"], d["minima"], d[f"atr_{P['atr_periodo']}"],
                                           compra, venda, P["atr_multiplicador"], slip)
        assert [(t, i) for (t, i, _p, _s) in eventos] == ref_ev
        assert estado["posicionado"] == ref_pos


# ---------------------------------------------------------------------------
# 4) golden-master: o motor não pode mudar de comportamento no refactor
#    (valores congelados capturados do código ANTES da unificação)
# ---------------------------------------------------------------------------
def test_golden_master_backtest():
    df = build_synthetic()
    d = montar(df, P)
    res = executar_backtest_v4(d, P, 0, len(df), 0.001, 0.0015, 6, incluir_equity=True)
    assert res["retorno_total_pct"] == -25.65
    assert res["drawdown_pct"] == 39.24
    assert res["num_trades"] == 14
    assert round(res["calmar"], 6) == -0.772463
    assert round(res["sharpe"], 6) == -0.567768
    eq = res["equity"]
    assert len(eq) == 1200
    assert round(float(np.nansum(eq)), 4) == 1138072.8838
    assert round(float(eq[-1]), 6) == 743.485311
    assert round(float(eq[50]), 6) == 1000.0
    assert round(float(eq[500]), 6) == 1055.688359


# ---------------------------------------------------------------------------
# 5) concordância: o radar (estado_posicao_atual) tem que concordar com a
#    posição implícita do backtest no último candle — senão o radar mente.
# ---------------------------------------------------------------------------
def test_concordancia_radar_vs_backtest():
    df = build_synthetic(seed=13)
    d = montar(df, P)
    # posição final segundo a reimplementação independente (referência neutra)
    compra, venda = calcular_sinais(d[f"ma_{P['media_rapida']}"], d[f"ma_{P['media_lenta']}"],
                                    d[f"ma_f_{P['media_filtro']}"], d["fechamento"])
    _, ref_pos = _referencia_walk(d["abertura"], d["minima"], d[f"atr_{P['atr_periodo']}"],
                                  compra, venda, P["atr_multiplicador"], 0.0)
    estado_radar = estado_posicao_atual(d, P, slippage=0.0)
    assert estado_radar["posicionado"] == ref_pos
    # e o preço atual exibido é o último fechamento
    assert estado_radar["preco_atual"] == float(d["fechamento"][-1])
