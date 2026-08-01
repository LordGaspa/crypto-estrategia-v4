# -*- coding: utf-8 -*-
# DAYTRADE - FASE 1.0: SPIKE DE REALIDADE ECONOMICA
# ----------------------------------------------------------------------------
# Pergunta mais barata e mais importante antes de construir qualquer coisa:
# dado o horizonte de minutos-a-horas, os movimentos tipicos de BTC/ETH sequer
# chegam perto de cobrir o custo de ida-e-volta da Binance Spot com frequencia
# suficiente? Se a resposta for "estruturalmente nao", e uma parada honesta e
# barata antes de construir walk-forward, holdout, etc.
#
# Nao toca em nada do v2/v3/v4/Codigo Omega. Script descartavel, standalone.
#
# Como rodar:
#   .venv\Scripts\python.exe daytrade_reality_check.py

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from binance.client import Client

ATIVOS = ["BTCUSDT", "ETHUSDT"]
TIMEFRAMES = {"5m": Client.KLINE_INTERVAL_5MINUTE, "15m": Client.KLINE_INTERVAL_15MINUTE}
MESES_HISTORICO = 3

# horizonte de posicao em candles (bate com "minutos a poucas horas")
# 5m: 12 candles = 1h, 36 candles = 3h | 15m: 4 candles = 1h, 12 candles = 3h
HORIZONTES_CANDLES = {"5m": [12, 36], "15m": [4, 12]}

# cenarios de custo: taxa por lado x slippage por lado
CENARIOS_TAXA = {"sem_BNB": 0.001, "com_BNB": 0.00075}
CENARIOS_SLIPPAGE = {"otimista": 0.0003, "conservador": 0.0010}


def carregar_klines(symbol: str, interval_val: str, meses: int) -> pd.DataFrame:
    client = Client(ping=False)
    start_date = (datetime.now() - timedelta(days=meses * 30)).strftime("%d %b, %Y")
    klines = client.get_historical_klines(symbol, interval_val, start_date)
    if not klines:
        return pd.DataFrame()
    df = pd.DataFrame(
        klines,
        columns=["t_abert", "abertura", "maxima", "minima", "fechamento", "volume",
                 "t_fech", "q_vol", "n_trades", "tk_b", "tk_q", "ignore"],
    )
    for col in ["abertura", "maxima", "minima", "fechamento", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df["t_abert"] = pd.to_datetime(df["t_abert"], unit="ms")
    return df[["t_abert", "abertura", "maxima", "minima", "fechamento", "volume"]]


def breakeven_round_trip(taxa_lado: float, slippage_lado: float) -> float:
    """Movimento % minimo pra empatar depois de taxa+slippage nos dois lados
    (aproximacao de primeira ordem, suficiente pro numero-manchete)."""
    return 2 * taxa_lado + 2 * slippage_lado


def fracao_movimentos_supera_breakeven(fechamento: np.ndarray, n_candles: int, breakeven_pct: float) -> float:
    """Fraz do total de pontos de partida em que o movimento (em qualquer
    direcao) nos proximos n_candles supera o breakeven. Sem look-ahead no
    sentido de "usar pra operar" -- aqui e so uma medida descritiva da
    volatilidade tipica do ativo, nao um sinal de entrada."""
    n = len(fechamento)
    if n <= n_candles:
        return float("nan")
    preco_ini = fechamento[: n - n_candles]
    preco_fim = fechamento[n_candles:]
    mov_abs_pct = np.abs(preco_fim / preco_ini - 1.0)
    return float(np.mean(mov_abs_pct > breakeven_pct))


def main():
    print("=" * 78)
    print("FASE 1.0 - SPIKE DE REALIDADE ECONOMICA (day-trade curto, Binance Spot)")
    print("=" * 78)

    linhas = []
    for ativo in ATIVOS:
        for tf_str, tf_val in TIMEFRAMES.items():
            print(f"\n>>> Baixando {ativo} {tf_str} ({MESES_HISTORICO} meses)...")
            df = carregar_klines(ativo, tf_val, MESES_HISTORICO)
            if df.empty:
                print(f"[AVISO] sem dados para {ativo} {tf_str}, pulando.")
                continue
            fechamento = df["fechamento"].values
            print(f"    {len(df)} candles carregados ({df['t_abert'].iloc[0]} a {df['t_abert'].iloc[-1]})")

            for horiz in HORIZONTES_CANDLES[tf_str]:
                horas = horiz * int(tf_str.rstrip("m")) / 60.0
                for nome_taxa, taxa in CENARIOS_TAXA.items():
                    for nome_slip, slip in CENARIOS_SLIPPAGE.items():
                        breakeven = breakeven_round_trip(taxa, slip)
                        frac = fracao_movimentos_supera_breakeven(fechamento, horiz, breakeven)
                        linhas.append({
                            "ativo": ativo,
                            "timeframe": tf_str,
                            "horizonte_candles": horiz,
                            "horizonte_horas": round(horas, 2),
                            "cenario_taxa": nome_taxa,
                            "cenario_slippage": nome_slip,
                            "breakeven_%": round(breakeven * 100, 3),
                            "frac_movimentos_supera_breakeven_%": round(frac * 100, 1),
                        })

    df_res = pd.DataFrame(linhas)
    if df_res.empty:
        print("\nNenhum resultado -- confira conexao com a Binance.")
        return

    out_path = "daytrade_reality_check_resultado.csv"
    df_res.to_csv(out_path, index=False)

    print("\n" + "=" * 78)
    print("RESUMO -- fracao de vezes que o movimento em N candles supera o breakeven")
    print("(quanto MAIOR essa fracao, mais frequentemente o mercado da uma chance")
    print(" de lucro que cobre o custo -- nao significa que a estrategia acerta a")
    print(" direcao, so mede se o piso economico e alcancavel com alguma frequencia)")
    print("=" * 78)
    piv = df_res.pivot_table(
        index=["ativo", "timeframe", "horizonte_horas"],
        columns=["cenario_taxa", "cenario_slippage"],
        values="frac_movimentos_supera_breakeven_%",
    )
    print(piv.to_string())

    print("\nBreakeven %% por cenario (fixo, independe do ativo/horizonte):")
    be = df_res.drop_duplicates(["cenario_taxa", "cenario_slippage"])[
        ["cenario_taxa", "cenario_slippage", "breakeven_%"]
    ]
    print(be.to_string(index=False))

    print(f"\nSalvo em {out_path}")
    print("\nLEITURA: se a fracao no cenario 'sem_BNB'+'conservador' (o mais honesto,")
    print("pessimista) ficar baixa (ex. <30-40%%), o piso de custo raramente e")
    print("alcancado -- e um sinal de alerta estrutural antes de construir qualquer")
    print("estrategia de sinal em cima disso. Isso NAO prova nem descarta edge (nao")
    print("olhamos direcao nem timing) -- so mede se o premio minimo de lucro esta")
    print("la, fisicamente, com que frequencia.")


if __name__ == "__main__":
    main()
