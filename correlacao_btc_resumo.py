# -*- coding: utf-8 -*-
# CORRELAÇÃO COM BTC + RESUMO DO WALK-FORWARD MULTI-ATIVOS
# ----------------------------------------------------------------------------
# Script novo e independente. Não altera nada do Código Ômega original.
#
# Para cada altcoin (ETH, SOL, XRP, BNB, TRX) e também FET (extra, pra
# referência, já que não fazia parte do pedido original de correlação), calcula
# a correlação de Pearson entre os retornos % candle-a-candle (4h) e os
# retornos do BTC, no período em que os dois ativos têm dado em comum.
#
# Depois junta com a média do retorno de TESTE (fora da amostra) de cada
# ativo, tirada das janelas geradas pelo walkforward_validacao.py.
#
# Pré-requisito: já ter rodado walkforward_validacao.py (ele baixa e faz cache
# dos preços em cache_dados/, e gera os CSVs walkforward_janelas_<ATIVO>_4h.csv).
#
# Como rodar:
#   python correlacao_btc_resumo.py

import os
import sys
import glob
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

CACHE_DIR = "cache_dados"
TIMEFRAME = "4h"
ATIVO_BASE = "BTCUSDT"
ALTCOINS = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "TRXUSDT", "FETUSDT"]


def carregar_precos(symbol: str) -> pd.DataFrame:
    path = os.path.join(CACHE_DIR, f"{symbol}_{TIMEFRAME}.parquet")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Cache não encontrado para {symbol}: {path}. "
            "Rode walkforward_validacao.py primeiro (ele baixa e faz cache dos preços)."
        )
    df = pd.read_parquet(path)
    if "t_abert" not in df.columns:
        raise ValueError(
            f"Cache de {symbol} está no formato antigo (sem timestamp). Apague "
            f"{path} e rode walkforward_validacao.py de novo."
        )
    return df[["t_abert", "fechamento"]].copy()


def correlacao_com_btc(df_btc: pd.DataFrame, df_alt: pd.DataFrame):
    """Retorna (correlacao, n_candles_usados) entre os retornos % candle-a-candle
    de df_alt e df_btc, alinhados por timestamp (interseção dos dois períodos)."""
    btc = df_btc.rename(columns={"fechamento": "fechamento_btc"})
    alt = df_alt.rename(columns={"fechamento": "fechamento_alt"})
    merged = pd.merge(btc, alt, on="t_abert", how="inner").sort_values("t_abert")
    merged["ret_btc"] = merged["fechamento_btc"].pct_change()
    merged["ret_alt"] = merged["fechamento_alt"].pct_change()
    merged = merged.dropna(subset=["ret_btc", "ret_alt"])
    if len(merged) < 30:
        return float("nan"), len(merged)
    return merged["ret_btc"].corr(merged["ret_alt"]), len(merged)


def media_retorno_teste(ativo: str):
    """Lê o CSV de janelas do walkforward para o ativo e retorna
    (media_retorno_teste_%, n_janelas). (None, 0) se o CSV não existir."""
    candidatos = glob.glob(f"walkforward_janelas_{ativo}_*.csv")
    if not candidatos:
        return None, 0
    df = pd.read_csv(candidatos[0])
    if "Lucro_Estrategia_TESTE_%" not in df.columns or df.empty:
        return None, 0
    return round(df["Lucro_Estrategia_TESTE_%"].mean(), 2), len(df)


def main():
    df_btc = carregar_precos(ATIVO_BASE)

    linhas = []

    # BTC entra como referência (correlação = 1.0 consigo mesmo)
    media_btc, n_janelas_btc = media_retorno_teste(ATIVO_BASE)
    linhas.append(
        {
            "Ativo": ATIVO_BASE,
            "Correlacao_com_BTC": 1.0,
            "Candles_Correlacao": len(df_btc),
            "Media_Retorno_TESTE_WalkForward_%": media_btc,
            "N_Janelas_WalkForward": n_janelas_btc,
        }
    )

    for alt in ALTCOINS:
        print(f">>> Calculando correlação {alt} x {ATIVO_BASE}...")
        df_alt = carregar_precos(alt)
        corr, n = correlacao_com_btc(df_btc, df_alt)
        media_teste, n_janelas = media_retorno_teste(alt)
        linhas.append(
            {
                "Ativo": alt,
                "Correlacao_com_BTC": round(corr, 4) if pd.notna(corr) else None,
                "Candles_Correlacao": n,
                "Media_Retorno_TESTE_WalkForward_%": media_teste,
                "N_Janelas_WalkForward": n_janelas,
            }
        )

    df_resumo = pd.DataFrame(linhas)
    df_resumo.to_csv("resumo_correlacao_btc_walkforward.csv", index=False)
    print("\n✅ Salvo: resumo_correlacao_btc_walkforward.csv\n")
    print(df_resumo.to_string(index=False))


if __name__ == "__main__":
    main()
