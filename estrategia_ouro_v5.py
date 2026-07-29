# -*- coding: utf-8 -*-
# ESTRATEGIA OURO V5 - teste de parametros unicos por grupo (nao por ativo)
# ----------------------------------------------------------------------------
# Arquivo NOVO e independente. Nao altera v2, v3, v4 nem o Codigo Omega.
#
# PERGUNTA QUE ESTE SCRIPT RESPONDE:
#   Os CSVs do otimizador_v4 acharam os MELHORES parametros PARA CADA ATIVO
#   individualmente (28 mil combinacoes por ativo -> pega o topo). Isso tem
#   vies de otimizacao embutido: o resultado parece otimo porque foi escolhido
#   a dedo NAQUELE periodo. Aqui a gente faz o oposto: destila UMA "estrategia
#   ouro" por GRUPO (a partir da moda dos top-100 do grupo) e aplica a MESMA
#   receita em todos os ativos do grupo. Se aguentar, e sinal de padrao real;
#   se desabar, confirma que o resultado por-ativo era overfitting.
#
# GRUPOS (o usuario pediu para separar dominantes/velhas vs novas):
#   - "veteranas/dominantes"  = ATIVOS_LIQUIDOS do config_v4 (BTC, ETH, BNB,
#     SOL, XRP, DOGE, TRX, LINK) - as de maior cap e historico mais longo.
#   - "novas/menos_liquidas"  = o resto (memecoins recentes + altcoins menores).
#
# Roda o MESMO motor executar_backtest_v4, com o MESMO tratamento de periodo
# dev vs holdout do holdout_v4.py (indicadores calculados na fatia, sem
# warm-up cruzado) - entao os numeros sao comparaveis 1:1 com os oficiais.
#
# Como rodar:
#   python estrategia_ouro_v5.py

import sys
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4,
    ATIVOS_LIQUIDOS,
    CANDLES_POR_DIA,
    CAPITAL_INICIAL,
    carregar_dados,
    separar_periodos,
    classificar_liquidez,
)
from otimizador_v4 import executar_backtest_v4

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

RESUMO_CSV = "otimizador_v4_RESUMO_ATIVOS.csv"
PESOS_CSV = "portfolio_v4_pesos.csv"

PARAMS_COLS = {
    "media_rapida": "media_rapida_per",
    "media_lenta": "media_lenta_per",
    "media_filtro": "media_filtro_tendencia_per",
    "atr_periodo": "atr_periodo",
    "atr_multiplicador": "atr_multiplicador",
}


# --------------------------------------------------------------------------
# indicadores (replica exata de montar_df_fast / holdout_v4, sem depender de
# streamlit) - calculados SOBRE A FATIA passada, igual ao holdout oficial.
# --------------------------------------------------------------------------
def montar_df_fast(df: pd.DataFrame, params: dict) -> dict:
    df_fast = {
        "abertura": df["abertura"].values,
        "minima": df["minima"].values,
        "fechamento": df["fechamento"].values,
        "t_abert": df["t_abert"].values,
    }
    df_fast[f"ma_{params['media_rapida']}"] = df["fechamento"].rolling(params["media_rapida"]).mean().values
    df_fast[f"ma_{params['media_lenta']}"] = df["fechamento"].rolling(params["media_lenta"]).mean().values
    df_fast[f"ma_f_{params['media_filtro']}"] = df["fechamento"].rolling(params["media_filtro"]).mean().values
    tr = pd.concat(
        [
            df["maxima"] - df["minima"],
            (df["maxima"] - df["fechamento"].shift()).abs(),
            (df["minima"] - df["fechamento"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df_fast[f"atr_{params['atr_periodo']}"] = tr.rolling(params["atr_periodo"]).mean().values
    return df_fast


def buy_and_hold_pct(abertura: np.ndarray, fechamento: np.ndarray) -> float:
    if len(abertura) < 2 or abertura[0] <= 0:
        return 0.0
    return round(((fechamento[-1] - abertura[0]) / abertura[0]) * 100.0, 2)


def grupo_do_ativo(ativo: str) -> str:
    return "veterana" if ativo in ATIVOS_LIQUIDOS else "nova"


# --------------------------------------------------------------------------
# 1) DESTILAR A ESTRATEGIA OURO DE CADA GRUPO (moda dos top-100 do grupo)
# --------------------------------------------------------------------------
def destilar_ouro_por_grupo(top_n: int = 100) -> dict:
    projeto = Path(".")
    ouro = {}
    linhas_grupo = {"veterana": [], "nova": []}

    for ativo, interval in ATIVOS_PORTFOLIO_V4.items():
        csv = projeto / f"otimizador_v4_{ativo}.csv"
        if not csv.exists():
            continue
        df = pd.read_csv(csv)
        top = df.nlargest(top_n, "Calmar")
        grupo = grupo_do_ativo(ativo)
        linhas_grupo[grupo].append(top)

    for grupo, blocos in linhas_grupo.items():
        if not blocos:
            continue
        todos = pd.concat(blocos, ignore_index=True)
        params = {}
        for chave_param, col in PARAMS_COLS.items():
            moda = Counter(todos[col]).most_common(1)[0][0]
            # normaliza tipo
            if chave_param == "atr_multiplicador":
                params[chave_param] = float(moda)
            else:
                params[chave_param] = int(moda)
        ouro[grupo] = params
    return ouro


# --------------------------------------------------------------------------
# 2) BACKTEST DE UM CONJUNTO DE PARAMS EM UM ATIVO, dev + holdout separados
# --------------------------------------------------------------------------
def backtest_ativo(ativo: str, interval: str, params: dict) -> dict:
    info_liq = classificar_liquidez(ativo)
    df = carregar_dados(ativo, interval)
    if df.empty:
        return None
    periodos = separar_periodos(df["t_abert"])
    idx_dev_fim = periodos["idx_dev_fim"]
    candles_dia = CANDLES_POR_DIA[interval]

    saida = {}
    for nome, fatia in (
        ("dev", df.iloc[:idx_dev_fim].reset_index(drop=True)),
        ("holdout", df.iloc[idx_dev_fim:].reset_index(drop=True)),
    ):
        if len(fatia) < max(params["media_filtro"], 30) + 5:
            saida[nome] = None
            continue
        df_fast = montar_df_fast(fatia, params)
        res = executar_backtest_v4(
            df_fast, params, 0, len(df_fast["fechamento"]),
            info_liq["taxa"], info_liq["slippage"], candles_dia, incluir_equity=True,
        )
        res["buyhold_pct"] = buy_and_hold_pct(df_fast["abertura"], df_fast["fechamento"])
        # guarda serie diaria pra portfolio
        datas = pd.to_datetime(df_fast["t_abert"])
        eq = pd.Series(res["equity"], index=datas).resample("1D").last().dropna()
        res["ret_diario"] = eq.pct_change().dropna()
        preco = pd.Series(df_fast["fechamento"], index=datas).resample("1D").last().dropna()
        res["ret_diario_bh"] = preco.pct_change().dropna()
        saida[nome] = res
    return saida


# --------------------------------------------------------------------------
# 3) METRICAS DE PORTFOLIO (mesma logica do holdout_v4)
# --------------------------------------------------------------------------
def metricas_portfolio(ret_diario_por_ativo: dict, pesos: pd.Series) -> dict:
    ativos = [a for a in ret_diario_por_ativo if a in pesos.index and len(ret_diario_por_ativo[a]) > 0]
    if not ativos:
        return None
    datas_comuns = None
    for a in ativos:
        idx = ret_diario_por_ativo[a].index
        datas_comuns = idx if datas_comuns is None else datas_comuns.intersection(idx)
    if datas_comuns is None or len(datas_comuns) < 5:
        return None
    datas_comuns = datas_comuns.sort_values()
    df_ret = pd.DataFrame({a: ret_diario_por_ativo[a].reindex(datas_comuns) for a in ativos}).fillna(0.0)
    p = pesos.reindex(ativos)
    p = p / p.sum()
    ret_port = (df_ret * p).sum(axis=1)
    equity = (1 + ret_port).cumprod()
    retorno_total = equity.iloc[-1] - 1.0
    dias = (datas_comuns[-1] - datas_comuns[0]).days
    anos = dias / 365.25 if dias > 0 else None
    ret_anual = (1 + retorno_total) ** (1 / anos) - 1 if anos and anos > 0 else None
    dd = ((equity.cummax() - equity) / equity.cummax()).max()
    calmar = ret_anual / dd if (dd and dd > 0 and ret_anual is not None) else None
    return {
        "n_ativos": len(ativos),
        "n_dias": len(datas_comuns),
        "retorno_total_%": round(retorno_total * 100, 2),
        "retorno_anual_%": round(ret_anual * 100, 2) if ret_anual is not None else None,
        "dd_%": round(dd * 100, 2),
        "calmar": round(calmar, 3) if calmar is not None else None,
    }


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------
def main():
    print("=" * 92)
    print("ESTRATEGIA OURO V5 - parametros unicos por grupo vs otimizado por ativo")
    print("=" * 92)

    # ---- destila as estrategias ouro
    ouro = destilar_ouro_por_grupo()
    print("\n>> ESTRATEGIAS OURO DESTILADAS (moda dos top-100 de cada grupo):\n")
    for grupo, p in ouro.items():
        print(f"  [{grupo:9}] rapida={p['media_rapida']:>3}  lenta={p['media_lenta']:>3}  "
              f"filtro={p['media_filtro']:>3}  atr_per={p['atr_periodo']:>2}  atr_mult={p['atr_multiplicador']}")

    # ---- params otimizados por ativo (do RESUMO)
    df_resumo = pd.read_csv(RESUMO_CSV)
    resumo_idx = df_resumo.set_index("Ativo")
    dev_inicio = resumo_idx["Periodo_Dev_Inicio"].to_dict()

    # ---- pesos do portfolio
    try:
        df_pesos = pd.read_csv(PESOS_CSV)
        pesos = df_pesos.set_index("Ativo")["Peso_Portfolio_%"] / 100.0
    except FileNotFoundError:
        pesos = None

    linhas = []
    ret_ouro_dev, ret_ouro_hold = {}, {}
    ret_otim_dev, ret_otim_hold = {}, {}
    ret_bh_dev, ret_bh_hold = {}, {}

    for ativo, interval in ATIVOS_PORTFOLIO_V4.items():
        grupo = grupo_do_ativo(ativo)
        params_ouro = ouro[grupo]

        row = resumo_idx.loc[ativo]
        params_otim = dict(
            media_rapida=int(row["media_rapida_per"]),
            media_lenta=int(row["media_lenta_per"]),
            media_filtro=int(row["media_filtro_tendencia_per"]),
            atr_periodo=int(row["atr_periodo"]),
            atr_multiplicador=float(row["atr_multiplicador"]),
        )

        bo = backtest_ativo(ativo, interval, params_ouro)
        bt = backtest_ativo(ativo, interval, params_otim)
        if bo is None or bt is None:
            print(f"[AVISO] sem dados pra {ativo}")
            continue

        def g(res, k):
            return res[k] if res is not None else None

        linha = {
            "Ativo": ativo,
            "Grupo": grupo,
            "Dev_Inicio": dev_inicio.get(ativo, "?"),
            # OURO (params do grupo)
            "Ouro_Dev_Ret_%": g(bo["dev"], "retorno_total_pct"),
            "Ouro_Dev_Calmar": round(g(bo["dev"], "calmar"), 2) if g(bo["dev"], "calmar") else None,
            "Ouro_Dev_Trades": g(bo["dev"], "num_trades"),
            "Ouro_Hold_Ret_%": g(bo["holdout"], "retorno_total_pct"),
            "Ouro_Hold_DD_%": g(bo["holdout"], "drawdown_pct"),
            # OTIMIZADO (params por ativo)
            "Otim_Dev_Ret_%": g(bt["dev"], "retorno_total_pct"),
            "Otim_Dev_Calmar": round(g(bt["dev"], "calmar"), 2) if g(bt["dev"], "calmar") else None,
            "Otim_Hold_Ret_%": g(bt["holdout"], "retorno_total_pct"),
            # BUY & HOLD
            "BH_Dev_%": g(bo["dev"], "buyhold_pct"),
            "BH_Hold_%": g(bo["holdout"], "buyhold_pct"),
        }
        linhas.append(linha)

        if bo["dev"]:
            ret_ouro_dev[ativo] = bo["dev"]["ret_diario"]
            ret_bh_dev[ativo] = bo["dev"]["ret_diario_bh"]
        if bo["holdout"]:
            ret_ouro_hold[ativo] = bo["holdout"]["ret_diario"]
            ret_bh_hold[ativo] = bo["holdout"]["ret_diario_bh"]
        if bt["dev"]:
            ret_otim_dev[ativo] = bt["dev"]["ret_diario"]
        if bt["holdout"]:
            ret_otim_hold[ativo] = bt["holdout"]["ret_diario"]

    df_out = pd.DataFrame(linhas)
    df_out.to_csv("estrategia_ouro_v5_por_ativo.csv", index=False)

    # ---- imprime por grupo
    for grupo in ("veterana", "nova"):
        sub = df_out[df_out["Grupo"] == grupo].copy()
        if sub.empty:
            continue
        print("\n" + "=" * 92)
        print(f"GRUPO: {grupo.upper()}  (estrategia ouro = {ouro[grupo]})")
        print("=" * 92)
        cols_show = ["Ativo", "Dev_Inicio", "Ouro_Dev_Ret_%", "Ouro_Dev_Calmar", "Ouro_Dev_Trades",
                     "Otim_Dev_Ret_%", "Otim_Dev_Calmar", "Ouro_Hold_Ret_%", "Otim_Hold_Ret_%", "BH_Hold_%"]
        print(sub[cols_show].to_string(index=False))

    # ---- comparacao de portfolio (ouro vs otimizado vs buy&hold), dev e holdout
    if pesos is not None:
        def so_veteranas(dic):
            return {a: s for a, s in dic.items() if grupo_do_ativo(a) == "veterana"}

        def imprime_bloco(titulo, cenarios):
            print("\n" + "=" * 92)
            print(titulo)
            print("=" * 92)
            print(f"\n  {'Cenario':34} {'RetTotal%':>10} {'RetAnual%':>10} {'DD%':>8} {'Calmar':>8} {'Dias':>6}")
            print("  " + "-" * 84)
            for nome, dic in cenarios:
                m = metricas_portfolio(dic, pesos)
                if m is None:
                    print(f"  {nome:34} {'(sem dados)':>10}")
                    continue
                print(f"  {nome:34} {m['retorno_total_%']:>10} {str(m['retorno_anual_%']):>10} "
                      f"{m['dd_%']:>8} {str(m['calmar']):>8} {m['n_dias']:>6}")

        # HOLDOUT: todos os 22 coexistem nos ultimos 12 meses -> janela comum valida (~363 dias)
        imprime_bloco(
            "PORTFOLIO NO HOLDOUT (22 ativos, janela comum de 12 meses) - OURO vs OTIMIZADO vs BUY&HOLD",
            [
                ("HOLD - Ouro (params do grupo)  ", ret_ouro_hold),
                ("HOLD - Otimizado (por ativo)   ", ret_otim_hold),
                ("HOLD - Buy & Hold              ", ret_bh_hold),
            ],
        )

        # DEV veteranas: historico longo sobreposto (SOL a partir de 2020) -> janela comum de ~5 anos
        imprime_bloco(
            "PORTFOLIO NO DEV, SO VETERANAS (8 ativos, janela comum ~5 anos) - OURO vs OTIMIZADO vs BUY&HOLD",
            [
                ("DEV  - Ouro (params do grupo)  ", so_veteranas(ret_ouro_dev)),
                ("DEV  - Otimizado (por ativo)   ", so_veteranas(ret_otim_dev)),
                ("DEV  - Buy & Hold              ", so_veteranas(ret_bh_dev)),
            ],
        )

        # DEV todos: CAVEAT - janela comum encurtada pelas moedas novas (~224 dias, so o final do dev)
        imprime_bloco(
            "PORTFOLIO NO DEV, 22 ATIVOS [CAVEAT: janela comum ~224 dias, encurtada pelas moedas novas]",
            [
                ("DEV  - Ouro (params do grupo)  ", ret_ouro_dev),
                ("DEV  - Otimizado (por ativo)   ", ret_otim_dev),
                ("DEV  - Buy & Hold              ", ret_bh_dev),
            ],
        )

    # ---- veredito rapido
    print("\n" + "=" * 92)
    print("LEITURA RAPIDA")
    print("=" * 92)
    for grupo in ("veterana", "nova"):
        sub = df_out[df_out["Grupo"] == grupo]
        if sub.empty:
            continue
        ouro_dev = sub["Ouro_Dev_Ret_%"].mean()
        otim_dev = sub["Otim_Dev_Ret_%"].mean()
        ouro_hold = sub["Ouro_Hold_Ret_%"].mean()
        otim_hold = sub["Otim_Hold_Ret_%"].mean()
        retencao = (ouro_dev / otim_dev * 100) if otim_dev else 0
        print(f"\n  [{grupo}] {len(sub)} ativos")
        print(f"    DEV  media ret: ouro {ouro_dev:8.1f}%  vs  otimizado {otim_dev:8.1f}%  "
              f"(ouro retem {retencao:4.0f}% do otimizado)")
        print(f"    HOLD media ret: ouro {ouro_hold:8.1f}%  vs  otimizado {otim_hold:8.1f}%")

    print("\n>> arquivo salvo: estrategia_ouro_v5_por_ativo.csv")


if __name__ == "__main__":
    main()
