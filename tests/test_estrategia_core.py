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

from estrategia_core import (
    calcular_sinais, simular_posicao, estado_posicao_atual, simular_posicao_trailing,
    simular_posicao_filtro_adx, simular_posicao_scale_out, simular_posicao_scale_out_trailing,
)
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
         "maxima": df["maxima"].values,
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
# 6) simular_posicao_trailing: comportamento básico do trailing stop
# ---------------------------------------------------------------------------
def test_trailing_nao_ativa_antes_do_limiar():
    """Enquanto o lucro não atinge ativacao_x × risco, o stop deve ser igual ao
    stop fixo inicial — o trailing ainda não está ativo."""
    # Série que sobe devagar (nunca ativa trailing) e depois cai
    n = 20
    abertura   = np.ones(n) * 100.0
    fechamento = np.ones(n) * 100.0
    maxima     = np.ones(n) * 100.0
    minima     = np.ones(n) * 100.0
    atr        = np.ones(n) * 2.0          # ATR = 2 → stop inicial = 100 - 3×2 = 94
    multi_atr  = 3.0
    # sinal de compra no candle 1 (índice 0)
    compra = np.array([True] + [False] * (n - 1))
    venda  = np.zeros(n, dtype=bool)

    eventos, estado = simular_posicao_trailing(
        abertura, minima, maxima, atr, compra, venda, multi_atr,
        ativacao_x=2.0, mult_trailing=2.0,
    )
    # Deve ter entrado
    assert len(eventos) >= 1 and eventos[0][0] == "entrada"
    # Trailing NÃO ativou: lucro máximo = 0 < ativacao_x × risco
    assert not estado["trailing_ativo"]


def test_trailing_ativa_e_stop_sobe():
    """Após o lucro atingir ativacao_x × risco, o stop trailing deve ser maior
    que o stop fixo inicial."""
    n = 30
    atr_val    = 5.0
    multi_atr  = 2.0
    entrada    = 100.0
    stop_ini   = entrada - atr_val * multi_atr   # 90
    risco_ini  = entrada - stop_ini              # 10
    ativacao_x = 1.0                            # ativa com 1× risco = 10 pontos de lucro
    mult_trail = 1.5

    # Sobe de 100 para 115 (acima de ativacao), depois vai para 111
    preco = np.array(
        [100.0, 100.0, 102.0, 105.0, 110.0, 115.0, 115.0, 115.0, 111.0, 111.0]
        + [111.0] * (n - 10)
    )
    abertura   = preco.copy()
    maxima     = preco.copy()
    minima     = preco.copy()
    atr        = np.full(n, atr_val)
    compra     = np.array([True] + [False] * (n - 1))
    venda      = np.zeros(n, dtype=bool)

    eventos, estado = simular_posicao_trailing(
        abertura, minima, maxima, atr, compra, venda, multi_atr,
        ativacao_x=ativacao_x, mult_trailing=mult_trail,
    )
    # Deve ter entrado
    assert eventos[0][0] == "entrada"
    # Ainda posicionado (preço não caiu o suficiente para bater o trailing)
    # Stop trailing = max_preco - atr_entrada * mult_trail = 115 - 5*1.5 = 107.5 > stop_ini=90
    # O preço em 111 > 107.5, logo ainda não saiu
    assert estado["posicionado"]
    assert estado["trailing_ativo"]
    # Stop atual deve ser >= stop_ini (o trailing só sobe)
    assert estado["stop"] >= stop_ini


def test_trailing_stop_sai_quando_cai_alem_do_trail():
    """A posição deve fechar quando a mínima do candle fica abaixo do stop trailing."""
    n = 20
    atr_val    = 5.0
    multi_atr  = 2.0
    mult_trail = 1.0   # trail muito próximo: stop = max - 1×ATR
    ativacao_x = 0.5   # ativa rápido

    # Sobe até 115, então despenca para 108 (< 115 - 5 = 110) → saída
    preco = np.array([100.0, 102.0, 107.0, 115.0, 108.0] + [108.0] * (n - 5))
    abertura  = preco.copy()
    maxima    = preco.copy()
    minima    = preco.copy()
    # Mínima do candle 4 (despenca) deve ser < stop_trail = 115 - 5 = 110
    minima[4] = 106.0
    atr       = np.full(n, atr_val)
    compra    = np.array([True] + [False] * (n - 1))
    venda     = np.zeros(n, dtype=bool)

    eventos, estado = simular_posicao_trailing(
        abertura, minima, maxima, atr, compra, venda, multi_atr,
        ativacao_x=ativacao_x, mult_trailing=mult_trail,
    )
    tipos = [e[0] for e in eventos]
    assert "entrada" in tipos
    assert "saida" in tipos
    assert not estado["posicionado"]


# ---------------------------------------------------------------------------
# 6b) simular_posicao_filtro_adx (v6b, direcional): cruzamento contrario so
#     e ignorado se ADX forte E +DI>-DI (tendencia forte de ALTA)
# ---------------------------------------------------------------------------
def test_filtro_adx_ignora_cruzamento_com_tendencia_forte_de_alta():
    """Cruzamento contrario no candle 3, ADX alto E +DI>-DI (tendencia forte
    de ALTA) -> deve IGNORAR e continuar posicionado."""
    n = 8
    abertura = np.full(n, 100.0)
    minima = np.full(n, 95.0)  # nunca fura o stop
    atr = np.full(n, 5.0)
    compra = np.array([True] + [False] * (n - 1))
    venda = np.array([False, False, False, True, False, False, False, False])
    adx = np.full(n, 30.0)  # tendencia forte (limiar padrao 20)
    plus_di = np.full(n, 25.0)
    minus_di = np.full(n, 10.0)  # +DI > -DI -> tendencia de ALTA

    eventos, estado = simular_posicao_filtro_adx(
        abertura, minima, atr, adx, plus_di, minus_di, compra, venda, multi_atr=3.0, adx_limiar=20.0
    )
    assert len(eventos) == 1 and eventos[0][0] == "entrada"
    assert estado["posicionado"]  # continua posicionado, cruzamento foi ignorado


def test_filtro_adx_honra_cruzamento_com_tendencia_forte_de_baixa():
    """CASO DO BUG CORRIGIDO (v6b): ADX alto (tendencia forte), mas -DI>+DI
    (a tendencia forte e de BAIXA, nao de alta) -> NAO deve ignorar, tem que
    honrar o cruzamento e sair. Era exatamente este caso que a v6 original
    (so ADX, sem checar direcao) tratava errado, prendendo a posicao numa
    queda forte."""
    n = 8
    abertura = np.full(n, 100.0)
    minima = np.full(n, 95.0)
    atr = np.full(n, 5.0)
    compra = np.array([True] + [False] * (n - 1))
    venda = np.array([False, False, False, True, False, False, False, False])
    adx = np.full(n, 30.0)  # tendencia forte
    plus_di = np.full(n, 10.0)
    minus_di = np.full(n, 25.0)  # -DI > +DI -> tendencia de BAIXA

    eventos, estado = simular_posicao_filtro_adx(
        abertura, minima, atr, adx, plus_di, minus_di, compra, venda, multi_atr=3.0, adx_limiar=20.0
    )
    tipos = [e[0] for e in eventos]
    assert tipos == ["entrada", "saida"]
    assert not estado["posicionado"]


def test_filtro_adx_honra_cruzamento_com_tendencia_fraca():
    """ADX baixo (tendencia fraca), independente da direcao -> deve sair."""
    n = 8
    abertura = np.full(n, 100.0)
    minima = np.full(n, 95.0)
    atr = np.full(n, 5.0)
    compra = np.array([True] + [False] * (n - 1))
    venda = np.array([False, False, False, True, False, False, False, False])
    adx = np.full(n, 10.0)  # tendencia fraca (< limiar 20)
    plus_di = np.full(n, 25.0)
    minus_di = np.full(n, 10.0)  # direcao de alta, mas ADX fraco deve honrar mesmo assim

    eventos, estado = simular_posicao_filtro_adx(
        abertura, minima, atr, adx, plus_di, minus_di, compra, venda, multi_atr=3.0, adx_limiar=20.0
    )
    tipos = [e[0] for e in eventos]
    assert tipos == ["entrada", "saida"]
    assert not estado["posicionado"]


def test_filtro_adx_stop_sai_independente_do_adx():
    """Mesmo com ADX altissimo e tendencia de alta, o STOP tem que funcionar
    -- o filtro so se aplica ao cruzamento, nunca ao stop de risco."""
    n = 6
    abertura = np.array([100.0, 100.0, 100.0, 80.0, 80.0, 80.0])
    minima = np.array([100.0, 100.0, 100.0, 80.0, 80.0, 80.0])  # candle 3 fura o stop
    atr = np.full(n, 5.0)  # stop = 100 - 3*5 = 85
    compra = np.array([True] + [False] * (n - 1))
    venda = np.zeros(n, dtype=bool)  # nenhum cruzamento -- so testa o stop
    adx = np.full(n, 50.0)  # tendencia extremamente forte
    plus_di = np.full(n, 40.0)
    minus_di = np.full(n, 5.0)  # forte tendencia de alta -- ainda assim o stop tem que valer

    eventos, estado = simular_posicao_filtro_adx(
        abertura, minima, atr, adx, plus_di, minus_di, compra, venda, multi_atr=3.0, adx_limiar=20.0
    )
    tipos = [e[0] for e in eventos]
    assert tipos == ["entrada", "saida"]
    assert not estado["posicionado"]


# ---------------------------------------------------------------------------
# 6c) sem look-ahead + reimplementacao independente (filtro_adx)
# ---------------------------------------------------------------------------
def _referencia_walk_adx(abertura, minima, atr, adx, plus_di, minus_di, compra, venda, multi, adx_limiar, slippage=0.0):
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
            validos = np.isfinite(adx[i - 1]) and np.isfinite(plus_di[i - 1]) and np.isfinite(minus_di[i - 1])
            tendencia_forte_alta = validos and adx[i - 1] >= adx_limiar and plus_di[i - 1] > minus_di[i - 1]
            if minima[i] < stop or (venda[i - 1] and not tendencia_forte_alta):
                pos = False
                saldo_eventos.append(("saida", i))
    return saldo_eventos, pos


def _adx_sintetico_com_direcao(n, seed):
    """Gera adx/plus_di/minus_di sinteticos coerentes (plus_di+minus_di<=100
    aproximadamente, como um ADX real) pros testes de propriedade."""
    rng = np.random.default_rng(seed)
    adx = rng.uniform(5, 45, size=n)
    plus_di = rng.uniform(5, 40, size=n)
    minus_di = rng.uniform(5, 40, size=n)
    return adx, plus_di, minus_di


def test_filtro_adx_bate_com_referencia():
    df = build_synthetic(seed=21)
    d = montar(df, P)
    compra, venda = calcular_sinais(d[f"ma_{P['media_rapida']}"], d[f"ma_{P['media_lenta']}"],
                                    d[f"ma_f_{P['media_filtro']}"], d["fechamento"])
    adx, plus_di, minus_di = _adx_sintetico_com_direcao(len(df), seed=99)

    eventos, estado = simular_posicao_filtro_adx(
        d["abertura"], d["minima"], d[f"atr_{P['atr_periodo']}"], adx, plus_di, minus_di,
        compra, venda, P["atr_multiplicador"], adx_limiar=20.0,
    )
    ref_ev, ref_pos = _referencia_walk_adx(
        d["abertura"], d["minima"], d[f"atr_{P['atr_periodo']}"], adx, plus_di, minus_di,
        compra, venda, P["atr_multiplicador"], 20.0,
    )
    assert [(t, i) for (t, i, _p, _s) in eventos] == ref_ev
    assert estado["posicionado"] == ref_pos


def test_sem_look_ahead_filtro_adx():
    df = build_synthetic(seed=21)
    d = montar(df, P)
    compra, venda = calcular_sinais(d[f"ma_{P['media_rapida']}"], d[f"ma_{P['media_lenta']}"],
                                    d[f"ma_f_{P['media_filtro']}"], d["fechamento"])
    adx, plus_di, minus_di = _adx_sintetico_com_direcao(len(df), seed=99)

    eventos_full, _ = simular_posicao_filtro_adx(
        d["abertura"], d["minima"], d[f"atr_{P['atr_periodo']}"], adx, plus_di, minus_di,
        compra, venda, P["atr_multiplicador"], adx_limiar=20.0,
    )
    corte = 800
    eventos_parcial, _ = simular_posicao_filtro_adx(
        d["abertura"][:corte], d["minima"][:corte], d[f"atr_{P['atr_periodo']}"][:corte],
        adx[:corte], plus_di[:corte], minus_di[:corte],
        compra[:corte], venda[:corte], P["atr_multiplicador"], adx_limiar=20.0,
    )
    eventos_full_ate_corte = [e for e in eventos_full if e[1] < corte]
    assert [(t, i) for (t, i, _p, _s) in eventos_full_ate_corte] == \
           [(t, i) for (t, i, _p, _s) in eventos_parcial]


# ---------------------------------------------------------------------------
# 6d) golden-master: filtro ADX direcional produz numero de trades <= sem
#     filtro (ele so pode IGNORAR cruzamentos, nunca criar saidas novas)
# ---------------------------------------------------------------------------
def test_golden_master_filtro_adx_reduz_ou_iguala_trades():
    df = build_synthetic()
    d = montar(df, P)
    compra, venda = calcular_sinais(d[f"ma_{P['media_rapida']}"], d[f"ma_{P['media_lenta']}"],
                                    d[f"ma_f_{P['media_filtro']}"], d["fechamento"])
    eventos_base, _ = simular_posicao(d["abertura"], d["minima"], d[f"atr_{P['atr_periodo']}"],
                                      compra, venda, P["atr_multiplicador"], 0.0)

    adx, plus_di, minus_di = _adx_sintetico_com_direcao(len(df), seed=7)
    eventos_adx, _ = simular_posicao_filtro_adx(
        d["abertura"], d["minima"], d[f"atr_{P['atr_periodo']}"], adx, plus_di, minus_di,
        compra, venda, P["atr_multiplicador"], adx_limiar=20.0,
    )
    n_saidas_base = sum(1 for e in eventos_base if e[0] == "saida")
    n_saidas_adx = sum(1 for e in eventos_adx if e[0] == "saida")
    assert n_saidas_adx <= n_saidas_base
    # com este seed e limiar, o filtro de fato ignora pelo menos 1 cruzamento
    assert n_saidas_adx < n_saidas_base


# ---------------------------------------------------------------------------
# 6e) simular_posicao_scale_out (candidata B do v6): 1o cruzamento fecha so
#     uma fracao, 2o cruzamento fecha o resto
# ---------------------------------------------------------------------------
def test_scale_out_primeiro_cruzamento_fecha_so_fracao():
    n = 8
    abertura = np.full(n, 100.0)
    minima = np.full(n, 95.0)  # nunca fura o stop
    atr = np.full(n, 5.0)
    compra = np.array([True] + [False] * (n - 1))
    venda = np.array([False, False, False, True, False, False, False, False])

    eventos, estado = simular_posicao_scale_out(
        abertura, minima, atr, compra, venda, multi_atr=3.0, fracao_saida_parcial=0.5
    )
    tipos = [e[0] for e in eventos]
    assert tipos == ["entrada", "saida_parcial"]
    assert eventos[1][4] == 0.5  # fracao fechada
    assert estado["posicionado"]  # ainda tem metade aberta
    assert estado["fracao_aberta"] == 0.5


def test_scale_out_segundo_cruzamento_fecha_o_resto():
    n = 12
    abertura = np.full(n, 100.0)
    minima = np.full(n, 95.0)
    atr = np.full(n, 5.0)
    compra = np.array([True] + [False] * (n - 1))
    venda = np.array([False, False, False, True, False, False, False, True, False, False, False, False])

    eventos, estado = simular_posicao_scale_out(
        abertura, minima, atr, compra, venda, multi_atr=3.0, fracao_saida_parcial=0.5
    )
    tipos = [e[0] for e in eventos]
    assert tipos == ["entrada", "saida_parcial", "saida"]
    assert not estado["posicionado"]
    assert estado["fracao_aberta"] is None


def test_scale_out_stop_fecha_tudo_de_uma_vez_sem_parcial_previa():
    n = 6
    abertura = np.array([100.0, 100.0, 100.0, 80.0, 80.0, 80.0])
    minima = np.array([100.0, 100.0, 100.0, 80.0, 80.0, 80.0])  # candle 3 fura o stop
    atr = np.full(n, 5.0)  # stop = 100 - 3*5 = 85
    compra = np.array([True] + [False] * (n - 1))
    venda = np.zeros(n, dtype=bool)  # sem cruzamento -- so testa o stop puro

    eventos, estado = simular_posicao_scale_out(
        abertura, minima, atr, compra, venda, multi_atr=3.0, fracao_saida_parcial=0.5
    )
    tipos = [e[0] for e in eventos]
    assert tipos == ["entrada", "saida"]  # sem saida_parcial -- stop fecha tudo direto
    assert not estado["posicionado"]


def test_scale_out_stop_fecha_resto_apos_parcial():
    n = 10
    abertura = np.array([100.0, 100.0, 100.0, 100.0, 90.0, 80.0, 80.0, 80.0, 80.0, 80.0])
    minima = np.array([100.0, 100.0, 100.0, 100.0, 90.0, 80.0, 80.0, 80.0, 80.0, 80.0])
    atr = np.full(n, 5.0)  # stop = 100 - 3*5 = 85 -- candle 5 (idx 5, minima=80) fura
    compra = np.array([True] + [False] * (n - 1))
    venda = np.array([False, False, False, True] + [False] * (n - 4))  # parcial no candle 4

    eventos, estado = simular_posicao_scale_out(
        abertura, minima, atr, compra, venda, multi_atr=3.0, fracao_saida_parcial=0.5
    )
    tipos = [e[0] for e in eventos]
    assert tipos == ["entrada", "saida_parcial", "saida"]  # stop fecha o restante
    assert not estado["posicionado"]


# ---------------------------------------------------------------------------
# 6f) sem look-ahead + reimplementacao independente (scale_out)
# ---------------------------------------------------------------------------
def _referencia_walk_scale_out(abertura, minima, atr, compra, venda, multi, fracao, slippage=0.0):
    n = len(abertura)
    pos = False
    stop = 0.0
    ja_parcial = False
    saldo_eventos = []
    for i in range(1, n):
        if not pos and compra[i - 1] and abertura[i] > 0:
            ent = abertura[i] * (1 + slippage)
            stop = ent - atr[i - 1] * multi
            pos = True
            ja_parcial = False
            saldo_eventos.append(("entrada", i))
        elif pos:
            if minima[i] < stop:
                pos = False
                saldo_eventos.append(("saida", i))
            elif venda[i - 1]:
                if not ja_parcial:
                    ja_parcial = True
                    saldo_eventos.append(("saida_parcial", i))
                else:
                    pos = False
                    saldo_eventos.append(("saida", i))
    return saldo_eventos, pos


def test_scale_out_bate_com_referencia():
    df = build_synthetic(seed=33)
    d = montar(df, P)
    compra, venda = calcular_sinais(d[f"ma_{P['media_rapida']}"], d[f"ma_{P['media_lenta']}"],
                                    d[f"ma_f_{P['media_filtro']}"], d["fechamento"])
    eventos, estado = simular_posicao_scale_out(
        d["abertura"], d["minima"], d[f"atr_{P['atr_periodo']}"], compra, venda,
        P["atr_multiplicador"], fracao_saida_parcial=0.5,
    )
    ref_ev, ref_pos = _referencia_walk_scale_out(
        d["abertura"], d["minima"], d[f"atr_{P['atr_periodo']}"], compra, venda,
        P["atr_multiplicador"], 0.5,
    )
    eventos_norm = [(e[0], e[1]) for e in eventos]
    assert eventos_norm == ref_ev
    assert estado["posicionado"] == ref_pos


def test_sem_look_ahead_scale_out():
    df = build_synthetic(seed=33)
    d = montar(df, P)
    compra, venda = calcular_sinais(d[f"ma_{P['media_rapida']}"], d[f"ma_{P['media_lenta']}"],
                                    d[f"ma_f_{P['media_filtro']}"], d["fechamento"])
    eventos_full, _ = simular_posicao_scale_out(
        d["abertura"], d["minima"], d[f"atr_{P['atr_periodo']}"], compra, venda,
        P["atr_multiplicador"], fracao_saida_parcial=0.5,
    )
    corte = 800
    eventos_parcial, _ = simular_posicao_scale_out(
        d["abertura"][:corte], d["minima"][:corte], d[f"atr_{P['atr_periodo']}"][:corte],
        compra[:corte], venda[:corte], P["atr_multiplicador"], fracao_saida_parcial=0.5,
    )
    eventos_full_norm = [(e[0], e[1]) for e in eventos_full if e[1] < corte]
    eventos_parcial_norm = [(e[0], e[1]) for e in eventos_parcial]
    assert eventos_full_norm == eventos_parcial_norm


# ---------------------------------------------------------------------------
# 6g) simular_posicao_scale_out_trailing: trailing so ativa APOS a parcial
# ---------------------------------------------------------------------------
def test_scale_out_trailing_stop_fixo_antes_da_parcial():
    """Antes da saida parcial, o stop tem que ficar FIXO (igual ao scale_out
    simples) -- o trailing so pode ativar depois da parcial."""
    n = 6
    abertura = np.full(n, 100.0)
    minima = np.full(n, 99.0)
    maxima = np.full(n, 101.0)
    atr = np.full(n, 5.0)  # stop fixo = 100 - 3*5 = 85
    compra = np.array([True] + [False] * (n - 1))
    venda = np.zeros(n, dtype=bool)  # sem cruzamento -- so garante que o stop nao muda

    eventos, estado = simular_posicao_scale_out_trailing(
        abertura, minima, maxima, atr, compra, venda, multi_atr=3.0,
        fracao_saida_parcial=0.5, mult_trailing=2.0,
    )
    assert eventos[0][3] == 85.0  # stop da entrada
    assert estado["stop"] == 85.0  # nunca mudou (sem cruzamento pra ativar parcial/trailing)


def test_scale_out_trailing_ativa_apos_parcial_e_stop_sobe():
    """Depois da saida parcial, o trailing ativa e o stop deve subir conforme
    o preco faz novas maximas."""
    n = 12
    abertura = np.array([100.0, 100.0, 100.0, 100.0, 105.0, 110.0, 115.0, 115.0, 115.0, 115.0, 115.0, 115.0])
    minima = abertura - 1.0
    maxima = abertura + 1.0
    atr = np.full(n, 5.0)  # stop fixo original = 100-3*5=85
    compra = np.array([True] + [False] * (n - 1))
    # cruzamento no candle 3 (parcial) -- ATR congelado ali = 5.0
    venda = np.array([False, False, False, True] + [False] * (n - 4))

    eventos, estado = simular_posicao_scale_out_trailing(
        abertura, minima, maxima, atr, compra, venda, multi_atr=3.0,
        fracao_saida_parcial=0.5, mult_trailing=2.0,
    )
    assert eventos[1][0] == "saida_parcial"
    # apos a parcial, maxima subiu ate 116 (maxima[6]=115+1), trail = 116 - 5*2 = 106 > stop fixo 85
    assert estado["stop"] > 85.0
    assert estado["trailing_ativo"]


def test_scale_out_trailing_sai_quando_cai_alem_do_trail():
    n = 14
    abertura = np.array([100.0, 100.0, 100.0, 100.0, 110.0, 120.0, 120.0, 120.0, 110.0] + [110.0] * 5)
    minima = abertura.copy()
    maxima = abertura.copy()
    minima[9] = 105.0  # despenca abaixo do trail apos a parcial
    atr = np.full(n, 5.0)
    compra = np.array([True] + [False] * (n - 1))
    venda = np.array([False, False, False, True] + [False] * (n - 4))

    eventos, estado = simular_posicao_scale_out_trailing(
        abertura, minima, maxima, atr, compra, venda, multi_atr=3.0,
        fracao_saida_parcial=0.5, mult_trailing=1.0,
    )
    tipos = [e[0] for e in eventos]
    assert "saida_parcial" in tipos
    assert tipos[-1] == "saida"
    assert not estado["posicionado"]


def test_sem_look_ahead_scale_out_trailing():
    df = build_synthetic(seed=33)
    d = montar(df, P)
    compra, venda = calcular_sinais(d[f"ma_{P['media_rapida']}"], d[f"ma_{P['media_lenta']}"],
                                    d[f"ma_f_{P['media_filtro']}"], d["fechamento"])
    eventos_full, _ = simular_posicao_scale_out_trailing(
        d["abertura"], d["minima"], d["maxima"], d[f"atr_{P['atr_periodo']}"], compra, venda,
        P["atr_multiplicador"], fracao_saida_parcial=0.5, mult_trailing=2.0,
    )
    corte = 800
    eventos_parcial, _ = simular_posicao_scale_out_trailing(
        d["abertura"][:corte], d["minima"][:corte], d["maxima"][:corte], d[f"atr_{P['atr_periodo']}"][:corte],
        compra[:corte], venda[:corte], P["atr_multiplicador"], fracao_saida_parcial=0.5, mult_trailing=2.0,
    )
    eventos_full_norm = [(e[0], e[1]) for e in eventos_full if e[1] < corte]
    eventos_parcial_norm = [(e[0], e[1]) for e in eventos_parcial]
    assert eventos_full_norm == eventos_parcial_norm


# ---------------------------------------------------------------------------
# 7 (antiga 5)) concordância: o radar (estado_posicao_atual) tem que concordar com a
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
