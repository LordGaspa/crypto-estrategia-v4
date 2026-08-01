# -*- coding: utf-8 -*-
"""Testes de daytrade_core.py (Fase 1 do sistema de day-trade curto).

Mesma disciplina de tests/test_estrategia_core.py:
  - calcular_sinais_*: condições corretas em arrays pequenos e controlados;
  - ausência de look-ahead: sinal passado não muda quando chegam candles futuros;
  - simular_posicao_daytrade: bate com uma reimplementação independente;
  - golden-master: valores congelados num dataset sintético fixo.
"""
import numpy as np
import pandas as pd

from daytrade_core import (
    calcular_sinais_mean_reversion,
    calcular_sinais_momentum,
    calcular_sinais_rompimento,
    calcular_sinais_volume_puro,
    calcular_sinais_volume_spike_anterior,
    filtro_volume_forte,
    simular_posicao_daytrade,
)


# ---------------------------------------------------------------------------
# dados sintéticos determinísticos (para look-ahead e golden-master)
# ---------------------------------------------------------------------------
def build_synthetic(n=800, seed=42):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0, 0.004, n)
    ret[100:140] -= 0.006   # queda -> zona de sobrevenda
    ret[300:330] += 0.006   # alta -> rompimento
    close = 100 * np.cumprod(1 + ret)
    abertura = np.empty(n)
    abertura[0] = 100
    abertura[1:] = close[:-1]
    maxima = np.maximum(abertura, close) * (1 + np.abs(rng.normal(0, 0.002, n)))
    minima = np.minimum(abertura, close) * (1 - np.abs(rng.normal(0, 0.002, n)))
    volume = np.abs(rng.normal(1000, 200, n))
    volume[150:155] *= 4  # spikes de volume artificiais
    return pd.DataFrame({
        "abertura": abertura, "maxima": maxima, "minima": minima,
        "fechamento": close, "volume": volume,
    })


def montar_indicadores_teste(df, rsi_periodo=14, banda_periodo=20, banda_mult=2.0,
                              m_rapida=9, m_lenta=21, m_filtro=50,
                              rompimento_periodo=20):
    """Helper SÓ do teste (não importa daytrade_backtest.py, que ainda não
    existe nesta etapa) -- cálculo simples e independente de RSI/Bollinger/EMA,
    suficiente pra alimentar as funções puras de daytrade_core.py."""
    d = df.copy()
    delta = d["fechamento"].diff()
    ganho = delta.clip(lower=0).rolling(rsi_periodo).mean()
    perda = (-delta.clip(upper=0)).rolling(rsi_periodo).mean()
    rs = ganho / perda.replace(0, np.nan)
    d["rsi"] = 100 - (100 / (1 + rs))
    d["rsi"] = d["rsi"].fillna(50.0)

    sma = d["fechamento"].rolling(banda_periodo).mean()
    std = d["fechamento"].rolling(banda_periodo).std()
    d["banda_inferior"] = sma - banda_mult * std
    d["media_reversao"] = sma

    d["ema_rapida"] = d["fechamento"].ewm(span=m_rapida, adjust=False).mean()
    d["ema_lenta"] = d["fechamento"].ewm(span=m_lenta, adjust=False).mean()
    d["ema_filtro"] = d["fechamento"].ewm(span=m_filtro, adjust=False).mean()

    d["maxima_recente"] = d["maxima"].rolling(rompimento_periodo).max().shift(1)
    return d.bfill()


# ---------------------------------------------------------------------------
# 1) calcular_sinais_mean_reversion
# ---------------------------------------------------------------------------
def test_mean_reversion_compra_por_rsi_baixo():
    fechamento = np.array([100.0, 99.0, 98.0, 97.0])
    rsi = np.array([50.0, 40.0, 25.0, 60.0])  # índice 2: RSI < 30
    banda_inf = np.full(4, 50.0)  # nunca fura (fora do alcance)
    media_rev = np.full(4, 200.0)  # nunca alcança (venda não dispara aqui)
    compra, venda = calcular_sinais_mean_reversion(fechamento, rsi, banda_inf, media_rev)
    assert list(compra) == [False, False, True, False]
    assert not venda.any()


def test_mean_reversion_compra_por_banda_inferior():
    fechamento = np.array([100.0, 99.0, 93.0, 97.0])
    rsi = np.full(4, 50.0)  # nunca dispara por RSI
    banda_inf = np.array([90.0, 90.0, 94.0, 90.0])  # índice 2: fechamento(93) < banda(94)
    media_rev = np.full(4, 200.0)
    compra, _ = calcular_sinais_mean_reversion(fechamento, rsi, banda_inf, media_rev)
    assert list(compra) == [False, False, True, False]


def test_mean_reversion_venda_ao_alcancar_media():
    fechamento = np.array([90.0, 95.0, 101.0, 105.0])
    rsi = np.full(4, 50.0)
    banda_inf = np.full(4, -1.0)
    media_rev = np.full(4, 100.0)  # índice 2: fechamento(101) > média(100)
    _, venda = calcular_sinais_mean_reversion(fechamento, rsi, banda_inf, media_rev)
    assert list(venda) == [False, False, True, True]


# ---------------------------------------------------------------------------
# 2) calcular_sinais_momentum (mesmo formato de calcular_sinais do v4)
# ---------------------------------------------------------------------------
def test_momentum_compra_no_cruzamento_com_filtro():
    rapida = np.array([1.0, 1.0, 3.0, 3.0])
    lenta = np.array([2.0, 2.0, 2.0, 2.0])
    filtro = np.array([0.0, 0.0, 0.0, 0.0])
    fech = np.array([5.0, 5.0, 5.0, 5.0])
    compra, venda = calcular_sinais_momentum(rapida, lenta, filtro, fech)
    assert list(compra) == [False, False, True, False]
    assert not venda.any()


def test_momentum_filtro_bloqueia_compra():
    rapida = np.array([1.0, 1.0, 3.0, 3.0])
    lenta = np.array([2.0, 2.0, 2.0, 2.0])
    filtro = np.array([10.0, 10.0, 10.0, 10.0])
    fech = np.array([5.0, 5.0, 5.0, 5.0])
    compra, _ = calcular_sinais_momentum(rapida, lenta, filtro, fech)
    assert not compra.any()


def test_momentum_venda_no_cruzamento_para_baixo():
    rapida = np.array([3.0, 3.0, 1.0, 1.0])
    lenta = np.array([2.0, 2.0, 2.0, 2.0])
    filtro = np.array([0.0, 0.0, 0.0, 0.0])
    fech = np.array([5.0, 5.0, 5.0, 5.0])
    _, venda = calcular_sinais_momentum(rapida, lenta, filtro, fech)
    assert list(venda) == [False, False, True, False]


# ---------------------------------------------------------------------------
# 3) calcular_sinais_rompimento
# ---------------------------------------------------------------------------
def test_rompimento_compra_quando_supera_maxima_recente():
    fechamento = np.array([100.0, 101.0, 105.0, 102.0])
    maxima_recente = np.array([102.0, 102.0, 102.0, 102.0])
    compra, venda = calcular_sinais_rompimento(fechamento, maxima_recente)
    assert list(compra) == [False, False, True, False]
    assert not venda.any()


# ---------------------------------------------------------------------------
# 3b) calcular_sinais_volume_puro (sem cruzamento -- pedido do usuario)
# ---------------------------------------------------------------------------
def test_volume_puro_compra_com_spike_e_vela_de_alta():
    volume = np.array([100.0] * 25 + [500.0])
    abertura = np.array([10.0] * 26)
    fechamento = np.array([10.0] * 25 + [10.5])  # vela de alta
    compra, venda = calcular_sinais_volume_puro(volume, abertura, fechamento, vol_periodo=20, vol_multiplicador=2.0)
    assert compra[-1] == True
    assert not venda.any()


def test_volume_puro_nao_compra_com_spike_e_vela_de_baixa():
    volume = np.array([100.0] * 25 + [500.0])
    abertura = np.array([10.0] * 26)
    fechamento = np.array([10.0] * 25 + [9.5])  # vela de BAIXA -- nao deve comprar mesmo com spike
    compra, _ = calcular_sinais_volume_puro(volume, abertura, fechamento, vol_periodo=20, vol_multiplicador=2.0)
    assert compra[-1] == False


def test_volume_puro_nao_compra_sem_spike():
    volume = np.full(26, 100.0)  # sem spike nenhum
    abertura = np.array([10.0] * 26)
    fechamento = np.array([10.0] * 25 + [10.5])
    compra, _ = calcular_sinais_volume_puro(volume, abertura, fechamento, vol_periodo=20, vol_multiplicador=2.0)
    assert not compra.any()


# ---------------------------------------------------------------------------
# 3c) calcular_sinais_volume_spike_anterior (vs candle anterior, nao media)
# ---------------------------------------------------------------------------
def test_volume_spike_anterior_compra_com_vela_de_alta():
    volume = np.array([10.0, 10.0, 150.0])  # candle 2: 150 > 10x volume[1]=10
    abertura = np.array([100.0, 100.0, 100.0])
    fechamento = np.array([100.0, 100.0, 105.0])
    compra, venda = calcular_sinais_volume_spike_anterior(volume, abertura, fechamento, multiplicador_compra=10.0, multiplicador_venda=10.0)
    assert list(compra) == [False, False, True]
    assert not venda.any()


def test_volume_spike_anterior_venda_com_vela_de_baixa():
    volume = np.array([10.0, 10.0, 150.0])
    abertura = np.array([100.0, 100.0, 100.0])
    fechamento = np.array([100.0, 100.0, 95.0])  # vela de baixa
    compra, venda = calcular_sinais_volume_spike_anterior(volume, abertura, fechamento, multiplicador_compra=10.0, multiplicador_venda=10.0)
    assert not compra.any()
    assert list(venda) == [False, False, True]


def test_volume_spike_anterior_nao_dispara_abaixo_do_multiplicador():
    volume = np.array([10.0, 10.0, 50.0])  # 50 < 10x10=100 -- nao e spike
    abertura = np.array([100.0, 100.0, 100.0])
    fechamento = np.array([100.0, 100.0, 105.0])
    compra, venda = calcular_sinais_volume_spike_anterior(volume, abertura, fechamento, multiplicador_compra=10.0, multiplicador_venda=10.0)
    assert not compra.any()
    assert not venda.any()


# ---------------------------------------------------------------------------
# 4) filtro_volume_forte
# ---------------------------------------------------------------------------
def test_filtro_volume_forte_detecta_spike():
    volume = np.array([100.0] * 25 + [500.0])  # spike no último candle
    filtro = filtro_volume_forte(volume, vol_periodo=20, vol_multiplicador=1.5)
    assert filtro[-1] == True
    assert not filtro[:20].any()  # sem histórico suficiente no início


def test_filtro_volume_forte_nao_inclui_candle_atual_na_media():
    # volume crescente de forma constante -- o candle atual não deve contar
    # na própria média (senão nunca dispararia o filtro corretamente)
    volume = np.concatenate([np.full(20, 100.0), [1000.0]])
    filtro = filtro_volume_forte(volume, vol_periodo=20, vol_multiplicador=2.0)
    assert filtro[-1] == True  # média das 20 anteriores = 100, 1000 > 2*100


# ---------------------------------------------------------------------------
# 5) ausência de look-ahead
# ---------------------------------------------------------------------------
def test_sem_look_ahead_mean_reversion():
    df = build_synthetic()
    d = montar_indicadores_teste(df)
    compra_full, venda_full = calcular_sinais_mean_reversion(
        d["fechamento"].values, d["rsi"].values, d["banda_inferior"].values, d["media_reversao"].values
    )
    corte = 500
    compra_parcial, venda_parcial = calcular_sinais_mean_reversion(
        d["fechamento"].values[:corte], d["rsi"].values[:corte],
        d["banda_inferior"].values[:corte], d["media_reversao"].values[:corte]
    )
    assert np.array_equal(compra_full[:corte], compra_parcial)
    assert np.array_equal(venda_full[:corte], venda_parcial)


def test_sem_look_ahead_momentum():
    df = build_synthetic()
    d = montar_indicadores_teste(df)
    compra_full, venda_full = calcular_sinais_momentum(
        d["ema_rapida"].values, d["ema_lenta"].values, d["ema_filtro"].values, d["fechamento"].values
    )
    corte = 500
    compra_parcial, venda_parcial = calcular_sinais_momentum(
        d["ema_rapida"].values[:corte], d["ema_lenta"].values[:corte],
        d["ema_filtro"].values[:corte], d["fechamento"].values[:corte]
    )
    assert np.array_equal(compra_full[:corte], compra_parcial)
    assert np.array_equal(venda_full[:corte], venda_parcial)


def test_sem_look_ahead_volume_puro():
    df = build_synthetic()
    compra_full, _ = calcular_sinais_volume_puro(df["volume"].values, df["abertura"].values, df["fechamento"].values)
    corte = 500
    compra_parcial, _ = calcular_sinais_volume_puro(
        df["volume"].values[:corte], df["abertura"].values[:corte], df["fechamento"].values[:corte]
    )
    assert np.array_equal(compra_full[:corte], compra_parcial)


def test_sem_look_ahead_filtro_volume():
    df = build_synthetic()
    filtro_full = filtro_volume_forte(df["volume"].values)
    corte = 500
    filtro_parcial = filtro_volume_forte(df["volume"].values[:corte])
    assert np.array_equal(filtro_full[:corte], filtro_parcial)


# ---------------------------------------------------------------------------
# 6) simular_posicao_daytrade vs reimplementação independente
# ---------------------------------------------------------------------------
def _referencia_walk_daytrade(abertura, minima, maxima, compra, venda,
                               stop_pct, alvo_pct, max_barras, slippage):
    n = len(abertura)
    pos = False
    stop = alvo = 0.0
    entrada_idx = None
    saldo_eventos = []
    for i in range(1, n):
        if not pos and compra[i - 1] and abertura[i] > 0:
            ent = abertura[i] * (1 + slippage)
            stop = ent * (1 - stop_pct)
            alvo = ent * (1 + alvo_pct)
            entrada_idx = i
            pos = True
            saldo_eventos.append(("entrada", i))
        elif pos:
            if minima[i] < stop:
                pos = False
                saldo_eventos.append(("saida", i, "stop"))
            elif maxima[i] > alvo:
                pos = False
                saldo_eventos.append(("saida", i, "alvo"))
            elif venda[i - 1]:
                pos = False
                saldo_eventos.append(("saida", i, "sinal"))
            elif (i - entrada_idx) >= max_barras:
                pos = False
                saldo_eventos.append(("saida", i, "tempo"))
    return saldo_eventos, pos


def test_simular_posicao_daytrade_bate_com_referencia():
    df = build_synthetic(seed=7)
    d = montar_indicadores_teste(df)
    compra, venda = calcular_sinais_mean_reversion(
        d["fechamento"].values, d["rsi"].values, d["banda_inferior"].values, d["media_reversao"].values
    )
    for slip in (0.0, 0.001):
        eventos, estado = simular_posicao_daytrade(
            d["abertura"].values, d["minima"].values, d["maxima"].values,
            compra, venda, stop_pct=0.01, alvo_pct=0.015, max_barras_posicao=12, slippage=slip
        )
        ref_ev, ref_pos = _referencia_walk_daytrade(
            d["abertura"].values, d["minima"].values, d["maxima"].values,
            compra, venda, 0.01, 0.015, 12, slip
        )
        eventos_norm = []
        for e in eventos:
            if e[0] == "entrada":
                eventos_norm.append(("entrada", e[1]))
            else:
                eventos_norm.append(("saida", e[1], e[4]))
        assert eventos_norm == ref_ev
        assert estado["posicionado"] == ref_pos


def test_simular_posicao_daytrade_motivo_stop():
    abertura = np.array([100.0, 100.0, 95.0, 95.0, 95.0])
    minima = np.array([100.0, 100.0, 93.0, 95.0, 95.0])  # índice 2 fura o stop
    maxima = np.array([100.0, 100.0, 96.0, 95.0, 95.0])
    compra = np.array([True, False, False, False, False])
    venda = np.zeros(5, dtype=bool)
    eventos, estado = simular_posicao_daytrade(
        abertura, minima, maxima, compra, venda, stop_pct=0.05, alvo_pct=0.20, max_barras_posicao=50
    )
    assert eventos[0][0] == "entrada"
    assert eventos[1][0] == "saida"
    assert eventos[1][4] == "stop"
    assert not estado["posicionado"]


def test_simular_posicao_daytrade_motivo_tempo():
    n = 10
    abertura = np.full(n, 100.0)
    minima = np.full(n, 99.5)
    maxima = np.full(n, 100.5)
    compra = np.array([True] + [False] * (n - 1))
    venda = np.zeros(n, dtype=bool)
    eventos, estado = simular_posicao_daytrade(
        abertura, minima, maxima, compra, venda,
        stop_pct=0.5, alvo_pct=0.5, max_barras_posicao=3
    )
    assert eventos[-1][0] == "saida"
    assert eventos[-1][4] == "tempo"
    # entrou no índice 1, saiu por tempo em 1+3=4
    assert eventos[-1][1] == 4


# ---------------------------------------------------------------------------
# 7) golden-master: valores congelados
# ---------------------------------------------------------------------------
def test_golden_master_mean_reversion():
    df = build_synthetic()
    d = montar_indicadores_teste(df)
    compra, venda = calcular_sinais_mean_reversion(
        d["fechamento"].values, d["rsi"].values, d["banda_inferior"].values, d["media_reversao"].values
    )
    eventos, estado = simular_posicao_daytrade(
        d["abertura"].values, d["minima"].values, d["maxima"].values,
        compra, venda, stop_pct=0.01, alvo_pct=0.015, max_barras_posicao=12, slippage=0.0005
    )
    num_trades = sum(1 for e in eventos if e[0] == "saida")
    assert num_trades == 54
    assert eventos[0][0] == "entrada"
    assert round(float(eventos[0][2]), 4) == round(float(d["abertura"].values[eventos[0][1]]) * 1.0005, 4)
    motivos = [e[4] for e in eventos if e[0] == "saida"]
    assert motivos.count("stop") == 33
    assert motivos.count("alvo") == 6
    assert motivos.count("tempo") == 6
    assert motivos.count("sinal") == 9
