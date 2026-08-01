# -*- coding: utf-8 -*-
# DAYTRADE - MODELO DE CUSTO (taxa + slippage + breakeven)
# ----------------------------------------------------------------------------
# Modulo novo, independente do v4 (nao importa slippage_realista_v4.py nem
# otimizador_v4.py -- so reaproveita o ESPIRITO: cenario base + cenario de
# estresse, sempre lado a lado, nunca um numero unico).
#
# Limitacao documentada (repetir em todo output que usar este modulo): candles
# OHLC nao tem bid/ask real. O que segue estima risco de impacto/timing, nao o
# spread verdadeiro. Por isso sempre reportar base E estresse juntos.

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# TAXA (Binance Spot, taker -- as candidatas deste projeto usam ordem a
# mercado pra garantir execucao dentro do horizonte de minutos-a-horas)
# ----------------------------------------------------------------------------
TAXA_TAKER_BASE = 0.001    # 0,1%/lado -- padrao honesto, nao assume BNB
TAXA_TAKER_BNB = 0.00075   # 0,075%/lado -- cenario alternativo explicito

# ----------------------------------------------------------------------------
# SLIPPAGE
# ----------------------------------------------------------------------------
SLIPPAGE_BASE = 0.0005     # 0,05%/lado -- mesma ordem do tier liquido do v4
GAP_FRAC_ESTRESSE = 0.15   # fracao do range do candle de entrada (cenario estresse)


def custo_round_trip_pct(taxa_lado: float, slippage_lado_entrada: float,
                          slippage_lado_saida: float) -> float:
    """Aproximacao de primeira ordem (soma linear) do custo de ida-e-volta.
    Suficiente pro numero-manchete de breakeven; o motor de backtest
    (daytrade_backtest.py) usa o calculo multiplicativo exato nos precos,
    igual executar_backtest_v4 faz no v4."""
    return 2 * taxa_lado + slippage_lado_entrada + slippage_lado_saida


def breakeven_move_pct(taxa_lado: float, slippage_lado: float) -> float:
    """Movimento % minimo necessario pra empatar depois do round-trip,
    assumindo o mesmo slippage nos dois lados."""
    return custo_round_trip_pct(taxa_lado, slippage_lado, slippage_lado)


def slippage_por_range(maxima: np.ndarray, minima: np.ndarray, abertura: np.ndarray,
                        gap_frac: float = GAP_FRAC_ESTRESSE) -> np.ndarray:
    """Variante de estresse: slippage estimado como fracao do range do candle
    de entrada -- candle largo (volatil) tende a ter mais slippage real que
    candle estreito. Mesmo espirito de slippage_realista_v4.py, reimplementado
    aqui (nao importado) pra manter o desacoplamento entre as duas linhagens."""
    maxima = np.asarray(maxima, dtype=float)
    minima = np.asarray(minima, dtype=float)
    abertura = np.asarray(abertura, dtype=float)
    return gap_frac * (maxima - minima) / abertura


def tabela_sensibilidade(slippage_estresse_medio: float = None) -> pd.DataFrame:
    """Grade 2x2 (taxa BNB/sem-BNB x slippage base/estresse) de breakeven,
    pro relatorio de sensibilidade (item E7 do plano). Se slippage_estresse_medio
    nao for passado, usa GAP_FRAC_ESTRESSE * um range tipico de 0,3% como proxy
    (so pra grade estatica -- o backtest de verdade usa slippage_por_range por
    candle, nao esse proxy)."""
    if slippage_estresse_medio is None:
        slippage_estresse_medio = GAP_FRAC_ESTRESSE * 0.003

    linhas = []
    for nome_taxa, taxa in (("sem_BNB", TAXA_TAKER_BASE), ("com_BNB", TAXA_TAKER_BNB)):
        for nome_slip, slip in (("base", SLIPPAGE_BASE), ("estresse", slippage_estresse_medio)):
            linhas.append({
                "cenario_taxa": nome_taxa,
                "cenario_slippage": nome_slip,
                "breakeven_%": round(breakeven_move_pct(taxa, slip) * 100, 4),
            })
    return pd.DataFrame(linhas)


if __name__ == "__main__":
    print("Tabela de sensibilidade (breakeven %% por cenario):")
    print(tabela_sensibilidade().to_string(index=False))
