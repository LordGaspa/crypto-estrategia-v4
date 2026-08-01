# -*- coding: utf-8 -*-
# WALK-FORWARD V6i -- CONFIRMACAO DE VOLUME NA ENTRADA DO CRUZAMENTO
# ----------------------------------------------------------------------------
# Nao e uma estrategia nova -- e um FILTRO DE QUALIDADE sobre a entrada ja
# validada (RECEITA_ROBUSTA): so aceita o sinal de cruzamento se o volume da
# vela do cruzamento estiver ACIMA da sua propria media movel de 20 candles
# (volume "confirma" o movimento, nao e so ruido de baixa participacao).
# Saida continua IDENTICA (simular_posicao original, sem mudanca nenhuma).
#
# config_v4.carregar_dados() NAO guarda volume (cache do v4 descarta essa
# coluna) -- este script busca OHLCV com volume separadamente, cache proprio
# em cache_dados_v6i/ (nao mistura com cache_dados/ do v4).
#
# So periodo de DESENVOLVIMENTO -- holdout continua travado.
#
# Como rodar:
#   .venv\Scripts\python.exe walkforward_v6i_volume_entrada.py

import os
import sys
import warnings
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from binance.client import Client

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, CAPITAL_INICIAL, CANDLES_POR_DIA, TIMEFRAME_MAP,
    ANOS_DE_DADOS_BACKTEST, separar_periodos, classificar_liquidez, RECEITA_ROBUSTA, grupo_ouro,
)
from estrategia_core import calcular_sinais, simular_posicao
import walkforward_v6c_scaleout as wf

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

CACHE_DIR_V6I = "cache_dados_v6i"
VOL_PERIODO = 20
VOL_MULTIPLICADOR = 1.0  # volume so precisa estar ACIMA da propria media -- confirmacao simples


def carregar_dados_com_volume(symbol: str, interval_str: str) -> pd.DataFrame:
    """Como config_v4.carregar_dados, mas mantem a coluna volume (o cache do
    v4 descarta). Cache proprio, mesmo padrao de fallback em falha de API."""
    os.makedirs(CACHE_DIR_V6I, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR_V6I, f"{symbol}_{interval_str}.parquet")

    def _ler_cache():
        try:
            df_cache = pd.read_parquet(cache_path)
            if "t_abert" in df_cache.columns and "volume" in df_cache.columns:
                return df_cache
        except Exception:
            pass
        return None

    if os.path.exists(cache_path):
        idade_horas = (datetime.now().timestamp() - os.path.getmtime(cache_path)) / 3600
        if idade_horas < 24:
            df_cache = _ler_cache()
            if df_cache is not None:
                return df_cache

    try:
        client = Client(ping=False)
        interval_val = TIMEFRAME_MAP[interval_str]
        start_date = (datetime.now() - timedelta(days=ANOS_DE_DADOS_BACKTEST * 365)).strftime("%d %b, %Y")
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


def montar_df_fast_com_volume(df: pd.DataFrame, params: dict) -> dict:
    fast = wf.montar_df_fast(df, params)
    volume = df["volume"]
    media_vol = volume.rolling(VOL_PERIODO).mean()
    fast["volume_confirma"] = (volume > VOL_MULTIPLICADOR * media_vol).fillna(False).values
    return fast


def main():
    linhas = []
    print("=" * 100)
    print(f"WALK-FORWARD V6i -- CONFIRMACAO DE VOLUME NA ENTRADA (volume > {VOL_MULTIPLICADOR}x media {VOL_PERIODO})")
    print("=" * 100)

    for ativo, interval_str in ATIVOS_PORTFOLIO_V4.items():
        grupo = grupo_ouro(ativo)
        params = RECEITA_ROBUSTA[grupo]
        info_liq = classificar_liquidez(ativo)
        candles_dia = CANDLES_POR_DIA[interval_str]

        df = carregar_dados_com_volume(ativo, interval_str)
        if df.empty:
            print(f"  [SEM DADOS] {ativo}")
            continue
        periodos = separar_periodos(df["t_abert"])
        idx_dev_fim = periodos["idx_dev_fim"]
        if idx_dev_fim < wf.MIN_CANDLES_JANELA:
            continue

        df_dev = df.iloc[:idx_dev_fim].reset_index(drop=True)
        df_fast = montar_df_fast_com_volume(df_dev, params)
        janelas = wf.gerar_janelas_anuais(df, idx_dev_fim)
        if not janelas:
            continue

        for jan in janelas:
            mr, ml, mf, ap = params["media_rapida"], params["media_lenta"], params["media_filtro"], params["atr_periodo"]
            ini, fim = jan["idx_ini"], jan["idx_fim"]
            m_rapida = df_fast[f"ma_{mr}"][ini:fim]
            m_lenta = df_fast[f"ma_{ml}"][ini:fim]
            m_filtro = df_fast[f"ma_f_{mf}"][ini:fim]
            abertura = df_fast["abertura"][ini:fim]
            minima = df_fast["minima"][ini:fim]
            fechamento = df_fast["fechamento"][ini:fim]
            atr = df_fast[f"atr_{ap}"][ini:fim]
            t_abert = df_fast["t_abert"][ini:fim]
            volume_confirma = df_fast["volume_confirma"][ini:fim]
            multi = params["atr_multiplicador"]
            n = len(fechamento)
            if n < wf.MIN_CANDLES_JANELA:
                continue

            sinais_compra, sinais_venda = calcular_sinais(m_rapida, m_lenta, m_filtro, fechamento)
            sinais_compra_filtrado = sinais_compra & volume_confirma

            bh = float((fechamento[-1] - abertura[0]) / abertura[0]) if abertura[0] > 0 else 0.0
            regime_janela = "BULL" if bh > 0.25 else ("BEAR" if bh < -0.25 else "LATERAL")

            eventos_base, _ = simular_posicao(abertura, minima, atr, sinais_compra, sinais_venda, multi, info_liq["slippage"])
            eventos_vol, _ = simular_posicao(abertura, minima, atr, sinais_compra_filtrado, sinais_venda, multi, info_liq["slippage"])

            eq_base, tr_base = wf._equity_de_eventos_base(eventos_base, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)
            eq_vol, tr_vol = wf._equity_de_eventos_base(eventos_vol, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)

            met_base = wf._metricas_de_equity(eq_base, abertura, fechamento, t_abert, candles_dia)
            met_vol = wf._metricas_de_equity(eq_vol, abertura, fechamento, t_abert, candles_dia)

            linhas.append({
                "Ativo": ativo, "Grupo": grupo, "Ano": jan["ano"], "Regime": regime_janela,
                "BuyHold_%": met_base["bh_pct"],
                "Retorno_Base_%": met_base["retorno_pct"], "DD_Base_%": met_base["dd_pct"], "Trades_Base": tr_base,
                "Retorno_VolFiltro_%": met_vol["retorno_pct"], "DD_VolFiltro_%": met_vol["dd_pct"], "Trades_VolFiltro": tr_vol,
            })

    df_out = pd.DataFrame(linhas)
    df_out.to_csv("comparativo_v4_vs_v6i_volume_entrada_walkforward.csv", index=False)

    print(f"\nTotal de janelas-ativo: {len(df_out)}")
    print("\nPor regime:")
    for regime in ["BULL", "LATERAL", "BEAR"]:
        sub = df_out[df_out["Regime"] == regime]
        if sub.empty:
            continue
        n = len(sub)
        med_base = sub["Retorno_Base_%"].median()
        med_vol = sub["Retorno_VolFiltro_%"].median()
        med_bh = sub["BuyHold_%"].median()
        pct_base_bate_bh = (sub["Retorno_Base_%"] > sub["BuyHold_%"]).mean() * 100
        pct_vol_bate_bh = (sub["Retorno_VolFiltro_%"] > sub["BuyHold_%"]).mean() * 100
        pct_vol_bate_base = (sub["Retorno_VolFiltro_%"] > sub["Retorno_Base_%"]).mean() * 100
        dd_base = sub["DD_Base_%"].median()
        dd_vol = sub["DD_VolFiltro_%"].median()
        tr_base_tot = sub["Trades_Base"].sum()
        tr_vol_tot = sub["Trades_VolFiltro"].sum()
        print(f"  {regime:<8} ({n:3d} janelas): B&H {med_bh:+7.1f}% | "
              f"Base {med_base:+7.1f}% (bate B&H {pct_base_bate_bh:5.1f}%, DD {dd_base:5.1f}%) | "
              f"VolFiltro {med_vol:+7.1f}% (bate B&H {pct_vol_bate_bh:5.1f}%, DD {dd_vol:5.1f}%) | "
              f"Vol>Base em {pct_vol_bate_base:5.1f}%")
        print(f"    trades totais -- Base: {tr_base_tot}, VolFiltro: {tr_vol_tot}")

    print(f"\nSalvo: comparativo_v4_vs_v6i_volume_entrada_walkforward.csv ({len(df_out)} linhas)")


if __name__ == "__main__":
    main()
