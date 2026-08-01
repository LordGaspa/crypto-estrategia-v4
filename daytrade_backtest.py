# -*- coding: utf-8 -*-
# DAYTRADE BACKTEST - motor de P&L, indicadores, metricas e relatorio de
# economia por trade (Fase 1)
# ----------------------------------------------------------------------------
# Modulo novo, paralelo a otimizador_v4.py -- nao importa nem edita nada do
# v4. Reimplementa (nao importa) a formula do Deflated Sharpe Ratio (Bailey &
# Lopez de Prado) e a logica de equity/Calmar/Sharpe de executar_backtest_v4,
# no mesmo espirito, mas desacoplado (ver nota de desacoplamento em
# daytrade_config.py).
#
# Divisao de responsabilidade (mesmo padrao do v4):
#   - daytrade_core.py     -> sinais + timeline de posicao (puro, sem custo)
#   - daytrade_custos.py   -> modelo de taxa/slippage/breakeven
#   - este arquivo          -> indicadores (equiv. a montar_df_fast) + camada
#                              de P&L (custo aplicado aos eventos) + metricas
#                              + relatorio de economia por trade
#
# IMPORTANTE sobre slippage: gerar_eventos_daytrade() sempre roda com
# slippage=0.0 (precos "limpos" nos eventos) -- o custo (taxa + slippage,
# cenario base OU estresse) e aplicado DEPOIS, na camada de P&L
# (calcular_pl_trades), usando arrays de slippage por candle. Isso permite
# comparar cenarios de custo (base vs. estresse) no MESMO conjunto de trades
# (mesmo timing de entrada/saida), em vez de re-simular a posicao pra cada
# cenario -- o que contaminaria a comparacao (stops diferentes disparariam em
# momentos diferentes). E uma decisao deliberada de design desta fase.

import numpy as np
import pandas as pd
from scipy.stats import norm

from daytrade_core import (
    calcular_sinais_mean_reversion,
    calcular_sinais_momentum,
    calcular_sinais_rompimento,
    calcular_sinais_volume_puro,
    calcular_sinais_volume_spike_anterior,
    filtro_volume_forte,
    simular_posicao_daytrade,
)
from daytrade_custos import TAXA_TAKER_BASE, SLIPPAGE_BASE, slippage_por_range

CAPITAL_INICIAL_DAYTRADE = 1000.0
MIN_DIAS_SHARPE_DAYTRADE = 20  # dias corridos minimos pra confiar no Sharpe (janelas curtas)

# Params-padrao por candidata (usados por scripts que nao especificam os
# proprios; walk-forward testa uma grade em cima desses, nao so o default)
PARAMS_PADRAO = {
    "mean_reversion": {
        "rsi_periodo": 14, "rsi_entrada": 30.0, "banda_periodo": 20, "banda_mult": 2.0,
        "stop_pct": 0.010, "alvo_pct": 0.015, "max_barras_posicao": 36,
    },
    "momentum": {
        "m_rapida": 9, "m_lenta": 21, "m_filtro": 50,
        "stop_pct": 0.010, "alvo_pct": 0.015, "max_barras_posicao": 36,
    },
    "rompimento": {
        "rompimento_periodo": 20,
        "stop_pct": 0.010, "alvo_pct": 0.020, "max_barras_posicao": 36,
    },
    "volume_puro": {
        "vol_periodo": 20, "vol_multiplicador": 2.5,
        "stop_pct": 0.04, "alvo_pct": 0.08, "max_barras_posicao": 20,
    },
    "volume_spike_anterior": {
        "multiplicador_compra": 10.0, "multiplicador_venda": 10.0,
        "stop_pct": 0.02, "alvo_pct": 0.04, "max_barras_posicao": 24,
    },
}


def montar_indicadores_daytrade(df: pd.DataFrame, params: dict) -> dict:
    """Equivalente a montar_df_fast do v4: calcula RSI, Bollinger, EMAs e canal
    de Donchian a partir do DataFrame OHLCV bruto. Devolve dict de arrays numpy
    -- entrada das funcoes puras de daytrade_core.py."""
    d = df.copy()
    rsi_periodo = params.get("rsi_periodo", 14)
    banda_periodo = params.get("banda_periodo", 20)
    banda_mult = params.get("banda_mult", 2.0)
    m_rapida = params.get("m_rapida", 9)
    m_lenta = params.get("m_lenta", 21)
    m_filtro = params.get("m_filtro", 50)
    rompimento_periodo = params.get("rompimento_periodo", 20)
    vol_periodo = params.get("vol_periodo", 20)

    delta = d["fechamento"].diff()
    ganho = delta.clip(lower=0).rolling(rsi_periodo).mean()
    perda = (-delta.clip(upper=0)).rolling(rsi_periodo).mean()
    rs = ganho / perda.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).fillna(50.0)

    sma = d["fechamento"].rolling(banda_periodo).mean()
    std = d["fechamento"].rolling(banda_periodo).std()
    banda_inferior = sma - banda_mult * std
    media_reversao = sma

    ema_rapida = d["fechamento"].ewm(span=m_rapida, adjust=False).mean()
    ema_lenta = d["fechamento"].ewm(span=m_lenta, adjust=False).mean()
    ema_filtro = d["fechamento"].ewm(span=m_filtro, adjust=False).mean()

    maxima_recente = d["maxima"].rolling(rompimento_periodo).max().shift(1)

    volume_media_prev = d["volume"].shift(1).rolling(vol_periodo).mean()

    out = {
        "t_abert": d["t_abert"].values if "t_abert" in d.columns else None,
        "abertura": d["abertura"].values,
        "minima": d["minima"].values,
        "maxima": d["maxima"].values,
        "fechamento": d["fechamento"].values,
        "volume": d["volume"].values,
        "rsi": rsi.bfill().values,
        "banda_inferior": banda_inferior.bfill().values,
        "media_reversao": media_reversao.bfill().values,
        "ema_rapida": ema_rapida.values,
        "ema_lenta": ema_lenta.values,
        "ema_filtro": ema_filtro.values,
        "maxima_recente": maxima_recente.bfill().values,
        "volume_media_prev": volume_media_prev.values,
    }
    return out


def gerar_eventos_daytrade(df_arrays: dict, candidata: str, params: dict,
                            usar_filtro_volume: bool = False):
    """Sinais (dispatch por `candidata`) + timeline de posicao, SEMPRE com
    slippage=0.0 -- ver nota de topo do arquivo sobre por que o custo fica
    fora daqui. `candidata` em {"mean_reversion","momentum","rompimento",
    "volume_puro"}."""
    if candidata == "mean_reversion":
        compra, venda = calcular_sinais_mean_reversion(
            df_arrays["fechamento"], df_arrays["rsi"], df_arrays["banda_inferior"],
            df_arrays["media_reversao"], rsi_entrada=params.get("rsi_entrada", 30.0),
        )
    elif candidata == "momentum":
        compra, venda = calcular_sinais_momentum(
            df_arrays["ema_rapida"], df_arrays["ema_lenta"], df_arrays["ema_filtro"],
            df_arrays["fechamento"],
        )
    elif candidata == "rompimento":
        compra, venda = calcular_sinais_rompimento(
            df_arrays["fechamento"], df_arrays["maxima_recente"],
        )
    elif candidata == "volume_puro":
        # o filtro de volume JA esta embutido no sinal -- nao combinar com
        # usar_filtro_volume=True (seria um segundo filtro redundante)
        compra, venda = calcular_sinais_volume_puro(
            df_arrays["volume"], df_arrays["abertura"], df_arrays["fechamento"],
            vol_periodo=params.get("vol_periodo", 20),
            vol_multiplicador=params.get("vol_multiplicador", 2.5),
        )
    elif candidata == "volume_spike_anterior":
        compra, venda = calcular_sinais_volume_spike_anterior(
            df_arrays["volume"], df_arrays["abertura"], df_arrays["fechamento"],
            multiplicador_compra=params.get("multiplicador_compra", 10.0),
            multiplicador_venda=params.get("multiplicador_venda", 10.0),
        )
    else:
        raise ValueError(f"candidata desconhecida: {candidata}")

    if usar_filtro_volume:
        filtro = filtro_volume_forte(
            df_arrays["volume"],
            vol_periodo=params.get("vol_periodo", 20),
            vol_multiplicador=params.get("vol_multiplicador", 1.5),
        )
        compra = compra & filtro

    eventos, estado = simular_posicao_daytrade(
        df_arrays["abertura"], df_arrays["minima"], df_arrays["maxima"],
        compra, venda,
        stop_pct=params["stop_pct"], alvo_pct=params["alvo_pct"],
        max_barras_posicao=params["max_barras_posicao"], slippage=0.0,
    )
    return eventos, estado


def calcular_pl_trades(eventos, fechamento, taxa, slippage_entrada_arr, slippage_saida_arr,
                        capital_inicial=CAPITAL_INICIAL_DAYTRADE):
    """Aplica taxa + slippage (arrays por candle) aos eventos 'limpos' de
    gerar_eventos_daytrade, caminha o capital e devolve (equity_curve, trades).
    Cada trade tem P&L bruto E liquido, motivo de saida e duracao -- a base do
    relatorio de economia por trade (item E do plano)."""
    n = len(fechamento)
    capital = capital_inicial
    posicionado = False
    quantidade = 0.0
    equity_curve = np.empty(n)
    equity_curve[0] = capital
    trades = []
    trade_atual = None

    ev_idx = 0
    n_ev = len(eventos)
    for i in range(1, n):
        if ev_idx < n_ev and eventos[ev_idx][1] == i:
            ev = eventos[ev_idx]
            if ev[0] == "entrada":
                _, idx_ent, preco_bruto, _stop = ev
                preco_liq = preco_bruto * (1 + slippage_entrada_arr[i])
                quantidade = (capital / preco_liq) * (1 - taxa)
                trade_atual = {
                    "entrada_idx": idx_ent, "entrada_preco_bruto": preco_bruto,
                    "capital_antes": capital,
                }
                posicionado = True
            else:  # saida
                _, idx_sai, preco_bruto, _stop, motivo = ev
                preco_liq = preco_bruto * (1 - slippage_saida_arr[i])
                capital_novo = (quantidade * preco_liq) * (1 - taxa)
                gross_pct = (preco_bruto / trade_atual["entrada_preco_bruto"] - 1.0) * 100.0
                net_pct = (capital_novo / trade_atual["capital_antes"] - 1.0) * 100.0
                trades.append({
                    "entrada_idx": trade_atual["entrada_idx"],
                    "saida_idx": idx_sai,
                    "duracao_candles": idx_sai - trade_atual["entrada_idx"],
                    "motivo_saida": motivo,
                    "gross_pct": round(gross_pct, 4),
                    "net_pct": round(net_pct, 4),
                    "net_usd": round(capital_novo - trade_atual["capital_antes"], 4),
                })
                capital = capital_novo
                posicionado = False
                trade_atual = None
            ev_idx += 1
        equity_curve[i] = capital if not posicionado else (quantidade * fechamento[i])

    return equity_curve, trades


def metricas_de_equity(equity_curve: np.ndarray, t_abert: np.ndarray, candles_por_dia: int) -> dict:
    """Retorno total/anualizado, drawdown, Calmar, Sharpe -- mesmas formulas de
    executar_backtest_v4 (reimplementadas, nao importadas)."""
    n = len(equity_curve)
    capital_inicial = equity_curve[0]
    capital_final = equity_curve[-1]
    retorno_total = (capital_final - capital_inicial) / capital_inicial

    running_max = np.maximum.accumulate(equity_curve)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd_series = (running_max - equity_curve) / running_max
    max_dd = float(np.nanmax(dd_series)) if n > 0 else 0.0

    dias = (t_abert[-1] - t_abert[0]) / np.timedelta64(1, "D") if t_abert is not None else None
    anos = dias / 365.25 if dias and dias > 0 else None
    retorno_anualizado = (1 + retorno_total) ** (1 / anos) - 1 if anos and anos > 0 else None

    calmar = None
    if max_dd > 0 and retorno_anualizado is not None:
        calmar = retorno_anualizado / max_dd

    sharpe = None
    ret_diario = None
    if candles_por_dia and n >= candles_por_dia * MIN_DIAS_SHARPE_DAYTRADE:
        eq_diario = equity_curve[::candles_por_dia]
        ret_diario = np.diff(eq_diario) / eq_diario[:-1]
        ret_diario = ret_diario[np.isfinite(ret_diario)]
        if len(ret_diario) >= MIN_DIAS_SHARPE_DAYTRADE and np.std(ret_diario, ddof=1) > 0:
            sharpe = float((np.mean(ret_diario) / np.std(ret_diario, ddof=1)) * np.sqrt(365.0))

    return {
        "retorno_total_pct": round(retorno_total * 100.0, 2),
        "retorno_anualizado_pct": round(retorno_anualizado * 100.0, 2) if retorno_anualizado is not None else None,
        "drawdown_pct": round(max_dd * 100.0, 2),
        "calmar": calmar,
        "sharpe": sharpe,
        "ret_diario": ret_diario,
    }


def executar_backtest_daytrade(df_arrays: dict, candidata: str, params: dict,
                                taxa: float, candles_por_dia: int,
                                usar_filtro_volume: bool = False,
                                gap_frac_estresse: float = None,
                                incluir_equity: bool = False) -> dict:
    """Roda uma candidata (mean_reversion/momentum/rompimento, com ou sem
    filtro de volume) e devolve metricas + trades pro cenario BASE de custo
    (taxa passada + SLIPPAGE_BASE) e, se gap_frac_estresse for informado,
    tambem pro cenario de ESTRESSE (slippage por range do candle de entrada)
    -- MESMO conjunto de trades nos dois cenarios (ver nota de topo do
    arquivo)."""
    eventos, _ = gerar_eventos_daytrade(df_arrays, candidata, params, usar_filtro_volume)
    n = len(df_arrays["fechamento"])
    fechamento = df_arrays["fechamento"]
    t_abert = df_arrays.get("t_abert")

    slip_base_arr = np.full(n, SLIPPAGE_BASE)
    eq_base, trades_base = calcular_pl_trades(eventos, fechamento, taxa, slip_base_arr, slip_base_arr)
    met_base = metricas_de_equity(eq_base, t_abert, candles_por_dia)
    met_base["num_trades"] = len(trades_base)

    resultado = {
        "base": {**met_base, "trades": trades_base, "equity": eq_base if incluir_equity else None},
        "estresse": None,
    }

    if gap_frac_estresse is not None:
        slip_estresse_arr = slippage_por_range(
            df_arrays["maxima"], df_arrays["minima"], df_arrays["abertura"], gap_frac_estresse
        )
        eq_est, trades_est = calcular_pl_trades(eventos, fechamento, taxa, slip_estresse_arr, slip_estresse_arr)
        met_est = metricas_de_equity(eq_est, t_abert, candles_por_dia)
        met_est["num_trades"] = len(trades_est)
        resultado["estresse"] = {**met_est, "trades": trades_est, "equity": eq_est if incluir_equity else None}

    return resultado


def deflated_sharpe_ratio_daytrade(sr_hat, sr_all, T, skewness, kurt_pearson):
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014) -- mesma formula
    de otimizador_v4.deflated_sharpe_ratio, reimplementada aqui (nao
    importada, ver nota de desacoplamento em daytrade_config.py)."""
    N = len(sr_all)
    if N < 2 or T < 10:
        return None
    var_sr = float(np.var(sr_all, ddof=1))
    if var_sr <= 0 or not np.isfinite(var_sr):
        return None
    euler_gamma = 0.5772156649015329
    z1 = norm.ppf(1 - 1.0 / N)
    z2 = norm.ppf(1 - 1.0 / (N * np.e))
    sr0 = np.sqrt(var_sr) * ((1 - euler_gamma) * z1 + euler_gamma * z2)
    denom_sq = 1 - skewness * sr_hat + ((kurt_pearson - 1) / 4.0) * sr_hat ** 2
    if denom_sq <= 0 or not np.isfinite(denom_sq):
        return None
    psr = norm.cdf(((sr_hat - sr0) * np.sqrt(T - 1)) / np.sqrt(denom_sq))
    return float(psr)


def relatorio_economia_trades(trades: list, taxa: float, slippage_lado: float,
                                capital_referencia: float, n_dias_periodo: float) -> dict:
    """Relatorio de economia por trade (item E do plano) -- o que decide se
    'lucro pequeno e frequente' e realista, ANTES de olhar Calmar/Sharpe."""
    from daytrade_custos import breakeven_move_pct

    n_trades = len(trades)
    if n_trades == 0 or n_dias_periodo <= 0:
        return {
            "n_trades": 0, "breakeven_pct": round(breakeven_move_pct(taxa, slippage_lado) * 100, 4),
            "net_pct_medio": None, "net_pct_mediano": None,
            "win_rate_bruto_pct": None, "win_rate_liquido_pct": None,
            "trades_por_dia": 0.0, "trades_por_mes": 0.0,
            "imposto_custo_pct_mes": None, "projecao_usd_mes": None,
        }

    net_pcts = np.array([t["net_pct"] for t in trades])
    gross_pcts = np.array([t["gross_pct"] for t in trades])
    net_usds = np.array([t["net_usd"] for t in trades])

    trades_por_dia = n_trades / n_dias_periodo
    trades_por_mes = trades_por_dia * 30.0
    custo_round_trip_pct = 2 * taxa + 2 * slippage_lado
    imposto_custo_pct_mes = trades_por_mes * custo_round_trip_pct * 100.0

    projecao_usd_mes = float(np.sum(net_usds)) / n_dias_periodo * 30.0

    return {
        "n_trades": n_trades,
        "breakeven_pct": round(breakeven_move_pct(taxa, slippage_lado) * 100, 4),
        "net_pct_medio": round(float(np.mean(net_pcts)), 4),
        "net_pct_mediano": round(float(np.median(net_pcts)), 4),
        "win_rate_bruto_pct": round(float(np.mean(gross_pcts > 0)) * 100, 1),
        "win_rate_liquido_pct": round(float(np.mean(net_pcts > 0)) * 100, 1),
        "trades_por_dia": round(trades_por_dia, 2),
        "trades_por_mes": round(trades_por_mes, 1),
        "imposto_custo_pct_mes": round(imposto_custo_pct_mes, 2),
        "projecao_usd_mes_no_periodo": round(projecao_usd_mes * capital_referencia / CAPITAL_INICIAL_DAYTRADE, 2),
    }
