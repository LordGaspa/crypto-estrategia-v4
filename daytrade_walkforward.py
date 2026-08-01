# -*- coding: utf-8 -*-
# DAYTRADE WALK-FORWARD - valida uma receita FIXA (sem re-otimizar por janela)
# nos 8 ativos liquidos, com tag de regime de volatilidade e comparacao das
# 3 candidatas (com e sem filtro de volume).
# ----------------------------------------------------------------------------
# So enxerga o periodo de DESENVOLVIMENTO (separar_periodos_daytrade) -- o
# holdout travado (ultimas HOLDOUT_SEMANAS_DAYTRADE) fica reservado pra
# daytrade_holdout.py, rodado uma unica vez no fim.
#
# RECEITA_DAYTRADE abaixo e UM recipe fixo por candidata (nao um grid search)
# -- mesma disciplina de walkforward_robusta_v4.py, que valida uma receita ja
# derivada, sem re-otimizar por janela. Os parametros foram escolhidos por
# raciocinio de dominio (nao testados contra os dados antes de travar):
#   - max horas em posicao favorece o lado de "poucas horas" (achado da
#     Fase 1.0: breakeven so e superado com frequencia razoavel em ~3h+);
#   - stop/alvo calibrados a partir do breakeven medido (~0,3-0,4%), nao
#     arbitrarios.

import numpy as np
import pandas as pd
from scipy.stats import norm

from daytrade_config import (
    UNIVERSO_DAYTRADE, CANDLES_POR_DIA_DAYTRADE, HOLDOUT_SEMANAS_DAYTRADE,
    carregar_dados_intraday, separar_periodos_daytrade,
)
from daytrade_backtest import (
    montar_indicadores_daytrade, executar_backtest_daytrade, relatorio_economia_trades,
    CAPITAL_INICIAL_DAYTRADE,
)
from daytrade_custos import TAXA_TAKER_BASE, SLIPPAGE_BASE, GAP_FRAC_ESTRESSE

TIMEFRAMES_WALKFORWARD = ["15m", "5m"]
CANDIDATAS = ["mean_reversion", "momentum", "rompimento"]
JANELA_DIAS = 28  # ~4 semanas, janela fixa de reporte (nao re-otimiza nada)
CAPITAL_REFERENCIA = 500.0  # o "~$500" que o usuario definiu como escala de teste

# receita fixa por candidata -- max_horas_posicao convertido em candles por
# timeframe em tempo de execucao (ver horas_para_candles)
RECEITA_DAYTRADE = {
    "mean_reversion": {
        "rsi_periodo": 14, "rsi_entrada": 20.0, "banda_periodo": 20, "banda_mult": 2.5,
        "stop_pct": 0.006, "alvo_pct": 0.010, "max_horas_posicao": 3.0,
        "vol_periodo": 20, "vol_multiplicador": 1.5,
    },
    "momentum": {
        "m_rapida": 9, "m_lenta": 21, "m_filtro": 50,
        "stop_pct": 0.008, "alvo_pct": 0.015, "max_horas_posicao": 4.0,
        "vol_periodo": 20, "vol_multiplicador": 1.5,
    },
    "rompimento": {
        "rompimento_periodo": 20,
        "stop_pct": 0.008, "alvo_pct": 0.020, "max_horas_posicao": 4.0,
        "vol_periodo": 20, "vol_multiplicador": 1.5,
    },
    # candidata pedida explicitamente pelo usuario: SO forca do volume (spike +
    # vela de alta), sem cruzamento de medias, pensada pra horizonte de
    # horas-a-dias (nao minutos) -- ver secao propria em main_volume_puro().
    "volume_puro": {
        "vol_periodo": 20, "vol_multiplicador": 2.5,
        "stop_pct": 0.04, "alvo_pct": 0.08, "max_horas_posicao": 96.0,  # ate 4 dias
    },
    # pedido explicito do usuario: volume 10x MAIOR QUE O CANDLE ANTERIOR (nao
    # media movel), candle de 1h, BTC/ETH/BNB apenas -- teste pontual, curioso.
    "volume_spike_anterior": {
        "multiplicador_compra": 10.0, "multiplicador_venda": 10.0,
        "stop_pct": 0.02, "alvo_pct": 0.04, "max_horas_posicao": 24.0,
    },
}

TIMEFRAMES_VOLUME_PURO = ["4h", "1d"]  # horas-a-dias, nao minutos


def horas_para_candles(horas: float, timeframe: str) -> int:
    minutos_candle = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}[timeframe]
    return max(1, int(round(horas * 60 / minutos_candle)))


def tag_regime_volatilidade(df_arrays: dict, timeframe: str) -> np.ndarray:
    """Regime de volatilidade por candle: ATR%% (range % medio) numa janela de
    ~1 dia, dividido em tercis baixo/medio/alto USANDO SO O PERIODO DE DEV
    (o unico que este script enxerga -- sem contaminacao do holdout)."""
    candles_dia = CANDLES_POR_DIA_DAYTRADE[timeframe]
    maxima = pd.Series(df_arrays["maxima"])
    minima = pd.Series(df_arrays["minima"])
    fechamento = pd.Series(df_arrays["fechamento"])
    range_pct = (maxima - minima) / fechamento
    atr_pct = range_pct.rolling(candles_dia).mean().bfill()
    try:
        tercis = pd.qcut(atr_pct, 3, labels=["baixa", "media", "alta"], duplicates="drop")
    except ValueError:
        tercis = pd.Series(["media"] * len(atr_pct))
    return tercis.astype(str).to_numpy()


def tag_regime_tendencia(df_arrays: dict, timeframe: str,
                          janela_dias: int = 30, limiar_pct: float = 20.0) -> np.ndarray:
    """Regime de TENDENCIA por candle (pedido do usuario: nao validar so no
    momento atual, cruzar com bull/lateral/bear tambem): retorno acumulado do
    PROPRIO ativo numa janela retroativa de `janela_dias` dias, > +limiar_pct
    = bull, < -limiar_pct = bear, senao lateral. Limiar menor que o do v4
    (±25%% em 12 meses) porque a janela aqui e de 30 dias, nao 1 ano -- cripto
    rotineiramente move ±20%% num mes. Sem look-ahead: usa só o retorno ATE o
    candle atual (shift implicito pelo pct_change com a propria janela passada)."""
    candles_dia = CANDLES_POR_DIA_DAYTRADE[timeframe]
    janela_candles = max(2, janela_dias * candles_dia)
    fechamento = pd.Series(df_arrays["fechamento"])
    ret_acumulado = fechamento.pct_change(periods=janela_candles) * 100.0
    regime = pd.Series(np.where(
        ret_acumulado > limiar_pct, "bull",
        np.where(ret_acumulado < -limiar_pct, "bear", "lateral")
    ))
    regime[ret_acumulado.isna()] = "lateral"  # inicio da serie sem janela suficiente
    return regime.to_numpy()


def bootstrap_sharpe_daytrade(ret_diario: np.ndarray, n_boot: int = 2000) -> dict:
    """Mesmo metodo de walkforward_robusta_v4.bootstrap_sharpe (reimplementado,
    nao importado): resample com reposicao dos retornos diarios liquidos,
    p_value = fracao de bootstraps com Sharpe<=0."""
    ret = ret_diario[np.isfinite(ret_diario)]
    T = len(ret)
    if T < 10 or np.std(ret, ddof=1) == 0:
        return {"sharpe_obs": None, "p_value": None, "T": T}
    sharpe_obs = float(np.mean(ret) / np.std(ret, ddof=1) * np.sqrt(365))
    rng = np.random.default_rng(42)
    boot = []
    for _ in range(n_boot):
        idx = rng.integers(0, T, size=T)
        r = ret[idx]
        s = np.std(r, ddof=1)
        if s > 0:
            boot.append(float(np.mean(r) / s * np.sqrt(365)))
    if not boot:
        return {"sharpe_obs": sharpe_obs, "p_value": None, "T": T}
    p_value = float(np.mean(np.array(boot) <= 0))
    return {"sharpe_obs": sharpe_obs, "p_value": p_value, "T": T}


def montar_params_execucao(candidata: str, timeframe: str) -> dict:
    p = dict(RECEITA_DAYTRADE[candidata])
    p["max_barras_posicao"] = horas_para_candles(p.pop("max_horas_posicao"), timeframe)
    return p


def rodar_combo(ativo: str, timeframe: str, candidata: str, usar_volume: bool):
    """Roda UMA combinacao (ativo x timeframe x candidata x com/sem volume) no
    periodo de DEV inteiro, devolve trades com timestamp/regime anexados."""
    df = carregar_dados_intraday(ativo, timeframe)
    # limite minimo de candles pro periodo de dev -- 150 e suficiente pros
    # timeframes de minutos (milhares de candles em 18 meses) e pro 1d, onde
    # 18 meses de historico so da ~500 candles totais (~499 apos reservar o
    # holdout), um numero fixo de 500 cortaria o 1d fora por pouco.
    MIN_CANDLES_DEV = 150
    if df.empty or len(df) < MIN_CANDLES_DEV:
        return None
    periodos = separar_periodos_daytrade(df["t_abert"])
    df_dev = df.iloc[: periodos["idx_dev_fim"]].reset_index(drop=True)
    if len(df_dev) < MIN_CANDLES_DEV:
        return None

    params = montar_params_execucao(candidata, timeframe)
    arrays = montar_indicadores_daytrade(df_dev, params)
    regimes_vol = tag_regime_volatilidade(arrays, timeframe)
    regimes_tend = tag_regime_tendencia(arrays, timeframe)
    candles_dia = CANDLES_POR_DIA_DAYTRADE[timeframe]

    res = executar_backtest_daytrade(
        arrays, candidata, params, taxa=TAXA_TAKER_BASE, candles_por_dia=candles_dia,
        usar_filtro_volume=usar_volume, gap_frac_estresse=GAP_FRAC_ESTRESSE, incluir_equity=True,
    )

    t_abert = pd.to_datetime(arrays["t_abert"])
    trades = res["base"]["trades"]
    for t in trades:
        t["t_entrada"] = t_abert[t["entrada_idx"]]
        t["regime"] = regimes_vol[t["entrada_idx"]]
        t["regime_tendencia"] = regimes_tend[t["entrada_idx"]]
        t["ativo"] = ativo
        t["timeframe"] = timeframe
        t["candidata"] = candidata
        t["usar_volume"] = usar_volume

    n_dias = len(df_dev) / candles_dia
    return {
        "ativo": ativo, "timeframe": timeframe, "candidata": candidata, "usar_volume": usar_volume,
        "trades": trades, "n_dias": n_dias,
        "met_base": res["base"], "met_estresse": res["estresse"],
        "t_abert": t_abert,
    }


def agregar_janelas(resultados: list) -> pd.DataFrame:
    """Bucketiza os trades de TODOS os resultados em janelas fixas de
    JANELA_DIAS, por (candidata, usar_volume, timeframe) -- comparacao honesta
    entre candidatas na MESMA grade de janelas, agregando os 8 ativos."""
    linhas = []
    for grupo_key, grupo_res in _agrupar(resultados).items():
        candidata, usar_volume, timeframe = grupo_key
        todos_trades = [t for r in grupo_res for t in r["trades"]]
        if not todos_trades:
            continue
        df_tr = pd.DataFrame(todos_trades)
        t0 = df_tr["t_entrada"].min()
        df_tr["janela"] = ((df_tr["t_entrada"] - t0).dt.days // JANELA_DIAS).astype(int)
        for janela, sub in df_tr.groupby("janela"):
            linhas.append({
                "candidata": candidata, "usar_volume": usar_volume, "timeframe": timeframe,
                "janela": int(janela),
                "data_ini": sub["t_entrada"].min(), "data_fim": sub["t_entrada"].max(),
                "n_trades": len(sub),
                "net_pct_medio": round(sub["net_pct"].mean(), 4),
                "net_pct_soma": round(sub["net_pct"].sum(), 4),
                "win_rate_liquido_pct": round((sub["net_pct"] > 0).mean() * 100, 1),
                "regime_vol_dominante": sub["regime"].mode().iloc[0] if not sub["regime"].mode().empty else None,
                "regime_tendencia_dominante": sub["regime_tendencia"].mode().iloc[0] if not sub["regime_tendencia"].mode().empty else None,
            })
    return pd.DataFrame(linhas)


def relatorio_por_regime_tendencia(resultados: list) -> pd.DataFrame:
    """Corte pedido explicitamente pelo usuario: nao validar so no momento de
    mercado atual -- separa o net_pct por regime de TENDENCIA (bull/lateral/
    bear) pra cada (candidata, usar_volume, timeframe), pra ver se o resultado
    se sustenta em qualquer regime ou só num deles."""
    linhas = []
    for grupo_key, grupo_res in _agrupar(resultados).items():
        candidata, usar_volume, timeframe = grupo_key
        todos_trades = [t for r in grupo_res for t in r["trades"]]
        if not todos_trades:
            continue
        df_tr = pd.DataFrame(todos_trades)
        for regime, sub in df_tr.groupby("regime_tendencia"):
            linhas.append({
                "candidata": candidata, "com_filtro_volume": usar_volume, "timeframe": timeframe,
                "regime_tendencia": regime,
                "n_trades": len(sub),
                "net_pct_medio": round(sub["net_pct"].mean(), 4),
                "net_pct_soma": round(sub["net_pct"].sum(), 4),
                "win_rate_liquido_pct": round((sub["net_pct"] > 0).mean() * 100, 1),
            })
    return pd.DataFrame(linhas).sort_values(["candidata", "com_filtro_volume", "timeframe", "regime_tendencia"])


def _agrupar(resultados: list) -> dict:
    grupos = {}
    for r in resultados:
        key = (r["candidata"], r["usar_volume"], r["timeframe"])
        grupos.setdefault(key, []).append(r)
    return grupos


def montar_resumo(resultados: list) -> pd.DataFrame:
    """Resumo agregado por (candidata, usar_volume, timeframe): economia por
    trade (item E do plano) + Calmar/Sharpe medios + significancia bootstrap
    (fracao de ativos com p<0.05), tudo cenario BASE de custo."""
    linhas = []
    for grupo_key, grupo_res in _agrupar(resultados).items():
        candidata, usar_volume, timeframe = grupo_key
        todos_trades = [t for r in grupo_res for t in r["trades"]]
        n_dias_total = sum(r["n_dias"] for r in grupo_res)

        rep = relatorio_economia_trades(
            todos_trades, TAXA_TAKER_BASE, SLIPPAGE_BASE, CAPITAL_REFERENCIA, n_dias_total
        )

        calmars = [r["met_base"]["calmar"] for r in grupo_res if r["met_base"]["calmar"] is not None]
        n_significativos = 0
        n_com_dados = 0
        for r in grupo_res:
            rd = r["met_base"].get("ret_diario")
            if rd is None or len(rd) < 10:
                continue
            n_com_dados += 1
            bstrap = bootstrap_sharpe_daytrade(rd)
            if bstrap["p_value"] is not None and bstrap["p_value"] < 0.05 and bstrap["sharpe_obs"] > 0:
                n_significativos += 1

        # cenario de estresse (pra tabela de sensibilidade)
        todos_trades_estresse = []
        for r in grupo_res:
            if r["met_estresse"] is not None:
                todos_trades_estresse.extend(r["met_estresse"]["trades"])
        rep_estresse = relatorio_economia_trades(
            todos_trades_estresse, TAXA_TAKER_BASE, GAP_FRAC_ESTRESSE * 0.003,
            CAPITAL_REFERENCIA, n_dias_total,
        ) if todos_trades_estresse else None

        linhas.append({
            "candidata": candidata, "com_filtro_volume": usar_volume, "timeframe": timeframe,
            "n_ativos": len(grupo_res),
            "n_trades_total": rep["n_trades"],
            "trades_por_mes_medio": rep["trades_por_mes"],
            "breakeven_pct": rep["breakeven_pct"],
            "net_pct_medio": rep["net_pct_medio"],
            "net_pct_mediano": rep["net_pct_mediano"],
            "win_rate_bruto_pct": rep["win_rate_bruto_pct"],
            "win_rate_liquido_pct": rep["win_rate_liquido_pct"],
            "imposto_custo_pct_mes": rep["imposto_custo_pct_mes"],
            "projecao_usd_mes_capital500": rep["projecao_usd_mes_no_periodo"],
            "calmar_medio_base": round(float(np.mean(calmars)), 3) if calmars else None,
            "net_pct_medio_estresse": rep_estresse["net_pct_medio"] if rep_estresse else None,
            "projecao_usd_mes_estresse": rep_estresse["projecao_usd_mes_no_periodo"] if rep_estresse else None,
            "n_ativos_significativos_p05": n_significativos,
            "n_ativos_com_dados_suficientes": n_com_dados,
        })
    return pd.DataFrame(linhas).sort_values(["timeframe", "net_pct_medio"], ascending=[True, False])


def main():
    print("=" * 90)
    print("DAYTRADE WALK-FORWARD -- receita fixa, periodo de DESENVOLVIMENTO apenas")
    print(f"(holdout travado = ultimas {HOLDOUT_SEMANAS_DAYTRADE} semanas, NAO tocado aqui)")
    print("=" * 90)

    resultados = []
    for timeframe in TIMEFRAMES_WALKFORWARD:
        for ativo in UNIVERSO_DAYTRADE:
            print(f"\n>>> {ativo} {timeframe} ...")
            for candidata in CANDIDATAS:
                for usar_volume in (False, True):
                    r = rodar_combo(ativo, timeframe, candidata, usar_volume)
                    if r is None:
                        print(f"    [AVISO] sem dados suficientes: {ativo} {timeframe} {candidata} vol={usar_volume}")
                        continue
                    resultados.append(r)
                    n_tr = len(r["trades"])
                    print(f"    {candidata:15s} vol={usar_volume!s:5s} -> {n_tr:4d} trades, "
                          f"net%={r['met_base']['retorno_total_pct']:+.1f}%, "
                          f"Calmar={r['met_base']['calmar']}")

    if not resultados:
        print("\nNenhum resultado -- confira conexao/cache.")
        return

    df_janelas = agregar_janelas(resultados)
    df_janelas.to_csv("daytrade_walkforward_janelas.csv", index=False)

    df_resumo = montar_resumo(resultados)
    df_resumo.to_csv("daytrade_walkforward_resumo.csv", index=False)

    df_regime_tend = relatorio_por_regime_tendencia(resultados)
    df_regime_tend.to_csv("daytrade_walkforward_regime_tendencia.csv", index=False)

    print("\n" + "=" * 90)
    print("RESUMO -- economia por trade em primeiro lugar (cenario BASE de custo)")
    print("=" * 90)
    cols_show = ["candidata", "com_filtro_volume", "timeframe", "n_trades_total",
                 "trades_por_mes_medio", "net_pct_medio", "win_rate_bruto_pct",
                 "win_rate_liquido_pct", "imposto_custo_pct_mes", "calmar_medio_base",
                 "n_ativos_significativos_p05", "n_ativos_com_dados_suficientes"]
    print(df_resumo[cols_show].to_string(index=False))

    print("\n" + "=" * 90)
    print("CORTE POR REGIME DE TENDENCIA (bull/lateral/bear) -- pedido explicito do usuario:")
    print("nao validar so no momento de mercado atual")
    print("=" * 90)
    print(df_regime_tend.to_string(index=False))

    print(f"\nSalvo: daytrade_walkforward_janelas.csv ({len(df_janelas)} linhas)")
    print(f"Salvo: daytrade_walkforward_resumo.csv  ({len(df_resumo)} linhas)")
    print(f"Salvo: daytrade_walkforward_regime_tendencia.csv ({len(df_regime_tend)} linhas)")


def main_volume_puro():
    """Candidata pedida explicitamente pelo usuario: SO forca do volume (spike
    de volume + vela de alta), sem cruzamento de medias/RSI/canal -- pensada
    pra horizonte de horas-a-dias (4h/1d), nao minutos. Reusa a MESMA
    infraestrutura (rodar_combo/agregar_janelas/montar_resumo/regime) do
    sweep principal -- so muda candidata e timeframes."""
    print("=" * 90)
    print("DAYTRADE -- CANDIDATA VOLUME PURO (spike de volume + vela de alta)")
    print("Horizonte horas-a-dias (4h/1d), sem cruzamento de medias. Periodo de DEV apenas.")
    print("=" * 90)

    resultados = []
    for timeframe in TIMEFRAMES_VOLUME_PURO:
        for ativo in UNIVERSO_DAYTRADE:
            r = rodar_combo(ativo, timeframe, "volume_puro", usar_volume=False)
            if r is None:
                print(f"[AVISO] sem dados suficientes: {ativo} {timeframe}")
                continue
            resultados.append(r)
            n_tr = len(r["trades"])
            print(f"  {ativo:14s} {timeframe:4s} -> {n_tr:4d} trades, "
                  f"net%={r['met_base']['retorno_total_pct']:+.1f}%, "
                  f"Calmar={r['met_base']['calmar']}")

    if not resultados:
        print("\nNenhum resultado -- confira conexao/cache.")
        return

    df_janelas = agregar_janelas(resultados)
    df_janelas.to_csv("daytrade_volume_puro_janelas.csv", index=False)

    df_resumo = montar_resumo(resultados)
    df_resumo.to_csv("daytrade_volume_puro_resumo.csv", index=False)

    df_regime_tend = relatorio_por_regime_tendencia(resultados)
    df_regime_tend.to_csv("daytrade_volume_puro_regime_tendencia.csv", index=False)

    print("\n" + "=" * 90)
    print("RESUMO -- VOLUME PURO (cenario BASE de custo)")
    print("=" * 90)
    cols_show = ["timeframe", "n_trades_total", "trades_por_mes_medio", "net_pct_medio",
                 "win_rate_bruto_pct", "win_rate_liquido_pct", "imposto_custo_pct_mes",
                 "calmar_medio_base", "n_ativos_significativos_p05", "n_ativos_com_dados_suficientes"]
    print(df_resumo[cols_show].to_string(index=False))

    print("\n" + "=" * 90)
    print("CORTE POR REGIME DE TENDENCIA -- VOLUME PURO")
    print("=" * 90)
    print(df_regime_tend.to_string(index=False))

    print(f"\nSalvo: daytrade_volume_puro_janelas.csv ({len(df_janelas)} linhas)")
    print(f"Salvo: daytrade_volume_puro_resumo.csv  ({len(df_resumo)} linhas)")
    print(f"Salvo: daytrade_volume_puro_regime_tendencia.csv ({len(df_regime_tend)} linhas)")


if __name__ == "__main__":
    import sys
    if "--volume-puro" in sys.argv:
        main_volume_puro()
    else:
        main()
