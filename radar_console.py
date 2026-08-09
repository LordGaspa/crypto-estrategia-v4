# -*- coding: utf-8 -*-
# RADAR DE SINAIS - versao console (sem navegador)
# ----------------------------------------------------------------------------
# Checagem rapida dos 22 ativos direto no terminal. Usa EXATAMENTE a mesma
# fonte da verdade do painel (estrategia_core.estado_posicao_atual com a
# RECEITA_ROBUSTA do grupo) -- nao ha como divergir do que o app mostra.
#
# Uso: clique duas vezes em SINAIS.bat, ou:
#   .venv\Scripts\python.exe radar_console.py

import sys
import warnings
import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, RECEITA_ROBUSTA, grupo_ouro, carregar_dados,
    classificar_liquidez,
)
from estrategia_core import estado_posicao_atual

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BOOTSTRAP_CSV = "walkforward_robusta_v4_resumo.csv"


def montar_df_fast(df, params):
    d = {
        "abertura": df["abertura"].values,
        "minima": df["minima"].values,
        "fechamento": df["fechamento"].values,
        "t_abert": df["t_abert"].values,
    }
    d[f"ma_{params['media_rapida']}"] = df["fechamento"].rolling(params["media_rapida"]).mean().values
    d[f"ma_{params['media_lenta']}"] = df["fechamento"].rolling(params["media_lenta"]).mean().values
    d[f"ma_f_{params['media_filtro']}"] = df["fechamento"].rolling(params["media_filtro"]).mean().values
    tr = pd.concat([
        df["maxima"] - df["minima"],
        (df["maxima"] - df["fechamento"].shift()).abs(),
        (df["minima"] - df["fechamento"].shift()).abs(),
    ], axis=1).max(axis=1)
    d[f"atr_{params['atr_periodo']}"] = tr.rolling(params["atr_periodo"]).mean().values
    return d


def formatar_preco(v):
    if v is None:
        return "-"
    if v >= 1:
        return f"{v:,.2f}"
    if v >= 0.01:
        return f"{v:.4f}"
    return f"{v:.8f}"


def main():
    try:
        boot = pd.read_csv(BOOTSTRAP_CSV).set_index("Ativo")
    except FileNotFoundError:
        boot = None

    print("=" * 78)
    print("  RADAR DE SINAIS - Estrategia Ouro Robusta")
    print("=" * 78)
    print("  Buscando dados da Binance...\n")

    linhas = []
    for ativo, interval in ATIVOS_PORTFOLIO_V4.items():
        params = RECEITA_ROBUSTA[grupo_ouro(ativo)]
        df = carregar_dados(ativo, interval)
        if df.empty:
            continue
        est = estado_posicao_atual(montar_df_fast(df, params), params, slippage=0.0)
        var = None
        if est["preco_sinal"] and est["preco_atual"] is not None:
            var = (est["preco_atual"] / est["preco_sinal"] - 1) * 100
        data_sinal = pd.Timestamp(est["data_mudanca"]).strftime("%d/%m/%Y") if est["data_mudanca"] is not None else "-"

        sig = ""
        if boot is not None and ativo in boot.index:
            p = boot.loc[ativo].get("P_value_boot")
            nj = boot.loc[ativo].get("N_Janelas")
            if pd.notna(p):
                if pd.notna(nj) and int(nj) < 4:
                    sig = "amostra curta"
                elif p < 0.05:
                    sig = "confirmado"
                else:
                    sig = "nao confirmado"

        linhas.append({
            "ativo": ativo, "posicionado": est["posicionado"],
            "preco": est["preco_atual"], "var": var, "data": data_sinal,
            "stop": est["stop_atual"], "sig": sig,
            "grupo": "veterana" if grupo_ouro(ativo) == "veterana" else "nova",
        })

    linhas.sort(key=lambda x: (not x["posicionado"], x["ativo"]))
    altas = [l for l in linhas if l["posicionado"]]

    print(f"  {'ATIVO':<16}{'SINAL':<8}{'PRECO':>14}{'VAR%':>9}  {'DESDE':<12}{'SIGNIFICANCIA':<16}")
    print("  " + "-" * 74)
    for l in linhas:
        sinal = "ALTA" if l["posicionado"] else "BAIXA"
        var_txt = f"{l['var']:+.2f}%" if l["var"] is not None else "-"
        print(f"  {l['ativo']:<16}{sinal:<8}{formatar_preco(l['preco']):>14}{var_txt:>9}  "
              f"{l['data']:<12}{l['sig']:<16}")

    print("\n" + "=" * 78)
    print(f"  {len(altas)} de {len(linhas)} ativos POSICIONADOS (ALTA)")
    if altas:
        print("\n  Posicoes abertas e seus stops:")
        for l in altas:
            if l["stop"] is not None and l["preco"]:
                dist = abs(l["stop"] / l["preco"] - 1) * 100
                print(f"    {l['ativo']:<16} stop {formatar_preco(l['stop']):>14}  "
                      f"(faltam {dist:.2f}% de queda)")
    print("=" * 78)
    print("  Sinais calculados com a RECEITA_ROBUSTA do grupo de cada ativo,")
    print("  a mesma logica do painel. Nao e recomendacao de investimento.")
    print()


if __name__ == "__main__":
    main()
    input("Pressione ENTER para fechar...")
