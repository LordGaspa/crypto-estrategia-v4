# -*- coding: utf-8 -*-
# DAYTRADE - CONFIG (Fase 1: pesquisa e backtest, sem execucao real)
# ----------------------------------------------------------------------------
# Modulo NOVO e PARALELO ao config_v4.py -- nao importa nem edita nada do
# v2/v3/v4/Codigo Omega. E uma estrategia diferente (day-trade curto, minutos
# a poucas horas, Binance Spot) para um horizonte diferente da estrategia de
# swing (4h-1d) do v4, entao tem seu proprio universo/cache/split dev-holdout
# em vez de reaproveitar RECEITA_ROBUSTA/ATIVOS_LIQUIDOS por import (decisao
# deliberada: evita acoplamento silencioso entre as duas linhagens).
#
# Achado da Fase 1.0 (daytrade_reality_check.py): o breakeven de ida-e-volta
# so e superado com frequencia razoavel (>=~40-50%) em horizontes de ~3h;
# em ~1h fica em 22-30%. Por isso o time-stop das candidatas em daytrade_core.py
# favorece o lado de "poucas horas", nao "minutos".

import os
from datetime import datetime, timedelta
import pandas as pd
from binance.client import Client

# ----------------------------------------------------------------------------
# UNIVERSO -- so os 8 ativos liquidos (mesma lista conceitual do
# config_v4.ATIVOS_LIQUIDOS, mas REDECLARADA aqui, nao importada -- ver nota
# acima sobre desacoplamento). Memecoins/altcoins ficam de fora: no v4 esse
# grupo ja leva 3,5x mais slippage: no horizonte de swing isso e
# arredondamento, no horizonte de minutos-a-horas pode ser o edge inteiro.
# ----------------------------------------------------------------------------
UNIVERSO_DAYTRADE = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "TRXUSDT", "LINKUSDT",
]

TIMEFRAME_MAP_DAYTRADE = {
    "5m": Client.KLINE_INTERVAL_5MINUTE,
    "15m": Client.KLINE_INTERVAL_15MINUTE,
    "1h": Client.KLINE_INTERVAL_1HOUR,
    "4h": Client.KLINE_INTERVAL_4HOUR,
    "1d": Client.KLINE_INTERVAL_1DAY,
}

# candles por dia de calendario (pra resample/anualizacao de metricas)
CANDLES_POR_DIA_DAYTRADE = {"5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}

CACHE_DIR_DAYTRADE = "cache_dados_daytrade"
CACHE_VALIDADE_HORAS_DAYTRADE = 6  # dados intraday envelhecem mais rapido que os do v4

MESES_HISTORICO_DAYTRADE = 18
HOLDOUT_SEMANAS_DAYTRADE = 6  # ~10% de 18 meses, proporcional ao 12/96 do v4


def carregar_dados_intraday(symbol: str, interval_str: str,
                             meses_historico: int = MESES_HISTORICO_DAYTRADE) -> pd.DataFrame:
    """Mesmo contrato/comportamento de config_v4.carregar_dados: cache parquet,
    fallback pro cache (mesmo que velho) se a API falhar. Diferenca chave:
    mantem a coluna 'volume' (o cache do v4 descarta), necessaria pra
    filtro_volume_forte() em daytrade_core.py."""
    os.makedirs(CACHE_DIR_DAYTRADE, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR_DAYTRADE, f"{symbol}_{interval_str}.parquet")

    def _ler_cache():
        try:
            df_cache = pd.read_parquet(cache_path)
            if "t_abert" in df_cache.columns:
                return df_cache
        except Exception:
            pass
        return None

    if os.path.exists(cache_path):
        idade_horas = (datetime.now().timestamp() - os.path.getmtime(cache_path)) / 3600
        if idade_horas < CACHE_VALIDADE_HORAS_DAYTRADE:
            df_cache = _ler_cache()
            if df_cache is not None:
                return df_cache

    try:
        client = Client(ping=False)
        interval_val = TIMEFRAME_MAP_DAYTRADE[interval_str]
        start_date = (datetime.now() - timedelta(days=meses_historico * 30)).strftime("%d %b, %Y")
        klines = client.get_historical_klines(symbol, interval_val, start_date)
        if not klines:
            raise ValueError("Binance devolveu 0 candles")
        df = pd.DataFrame(
            klines,
            columns=["t_abert", "abertura", "maxima", "minima", "fechamento", "volume",
                     "t_fech", "q_vol", "n_trades", "tk_b", "tk_q", "ignore"],
        )
        for col in ["abertura", "maxima", "minima", "fechamento", "volume"]:
            df[col] = pd.to_numeric(df[col])
        df["t_abert"] = pd.to_datetime(df["t_abert"], unit="ms")
        df = df[["t_abert", "abertura", "maxima", "minima", "fechamento", "volume"]]
        try:
            df.to_parquet(cache_path)
        except Exception as e:
            print(f"[AVISO] cache nao salvo: {e}")
        return df
    except Exception as e:
        print(f"[ERRO] {symbol} {interval_str}: {e} -- tentando cache (mesmo velho)")
        df_cache = _ler_cache()
        if df_cache is not None:
            return df_cache
        return pd.DataFrame()


def separar_periodos_daytrade(datas: pd.Series, hoje: datetime = None) -> dict:
    """Mesmo formato de retorno de config_v4.separar_periodos, mas o corte e
    por SEMANAS (HOLDOUT_SEMANAS_DAYTRADE), nao meses -- faz mais sentido no
    horizonte intraday. `hoje` injetavel so pra testes."""
    datas = datas.reset_index(drop=True).astype("datetime64[ns]")
    n_total = len(datas)
    if hoje is None:
        hoje = datetime.now()
    corte_data = pd.Timestamp(hoje).as_unit("ns") - timedelta(weeks=HOLDOUT_SEMANAS_DAYTRADE)
    idx_corte = int(datas.searchsorted(corte_data))
    idx_corte = max(0, min(idx_corte, n_total))
    return {
        "idx_dev_fim": idx_corte,
        "idx_holdout_fim": n_total,
        "data_corte": corte_data,
        "dev_inicio": datas.iloc[0] if n_total > 0 else None,
        "dev_fim": datas.iloc[idx_corte - 1] if idx_corte > 0 else None,
        "holdout_inicio": datas.iloc[idx_corte] if idx_corte < n_total else None,
        "holdout_fim": datas.iloc[-1] if n_total > 0 else None,
    }
