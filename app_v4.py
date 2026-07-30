# -*- coding: utf-8 -*-
# APP V4 - Radar de sinais + portfólio (Streamlit)
# ----------------------------------------------------------------------------
# App novo e independente. NÃO altera nada do Código Ômega original (backend/
# frontend), do v2, do v3, nem dos scripts de otimização/validação do v4
# (config_v4.py, otimizador_v4.py, portfolio_v4.py, holdout_v4.py) — só LÊ os
# CSVs que eles já geraram e reusa executar_backtest_v4 (sem modificá-lo) pra
# desenhar os gráficos de capital.
#
# O cálculo do "sinal atual" (ALTA/BAIXA) e a data desse sinal são feitos por
# uma função local deste arquivo (estado_atual_posicao) que espelha a MESMA
# lógica de entrada/saída do v4 (cruzamento de médias + filtro de tendência;
# stop ATR fixo OU cruzamento contrário — NÃO o trailing stop do v3, que foi
# descartado) — ela não reaproveita o motor de backtest porque precisamos
# saber se a posição está aberta AGORA, no candle mais recente, e desde
# quando, o que otimizador_v4.py não expõe.
#
# Esta revisão só mexe em apresentação (tema escuro, cards em HTML/CSS,
# gráficos de área semi-transparente, ordenação por data do sinal) — nenhuma
# lógica de cálculo mudou.
#
# Pré-requisitos (rodar antes de abrir o app):
#   python otimizador_v4.py
#   python portfolio_v4.py
#   python holdout_v4.py --eu-confirmo-holdout-final   (opcional — o app
#       funciona sem isso, só não mostra a seção de holdout)
#
# Como rodar:
#   streamlit run app_v4.py

import os
import numpy as np
import pandas as pd
import altair as alt
import streamlit as st

from config_v4 import (
    CANDLES_POR_DIA,
    CAPITAL_INICIAL,
    ATIVOS_PORTFOLIO_V4,
    RECEITA_ROBUSTA,
    grupo_ouro,
    carregar_dados,
    separar_periodos,
    classificar_liquidez,
)
from otimizador_v4 import executar_backtest_v4
from estrategia_core import estado_posicao_atual

st.set_page_config(page_title="Estratégia V4 - Radar & Portfólio", layout="wide", page_icon="📡")
alt.data_transformers.disable_max_rows()  # séries de anos em 4h passam do limite padrão (5000 linhas)

RESUMO_CSV = "otimizador_v4_RESUMO_ATIVOS.csv"  # manifesto: lista de ativos, interval, grupo, DSR
DSR_ALERTA_LIMITE = 5.0  # % — abaixo disso, aviso de "sem significância estatística"
# (os antigos CSVs de portfólio/pesos não são mais lidos: tudo é calculado ao
#  vivo pela receita robusta em computar_portfolio_robusto)

COR_ALTA = "#22c55e"
COR_BAIXA = "#ef4444"
COR_DSR_ALERTA = "#f59e0b"
COR_LIQUIDO = "#3b82f6"
COR_MENOS_LIQUIDO = "#a855f7"
COR_ESTRATEGIA = "#34d399"
COR_BUYHOLD = "#fbbf24"

# RECEITA_ROBUSTA e grupo_ouro agora vivem no config_v4 (fonte única da verdade,
# importados acima) — antes estavam duplicados aqui.

# ----------------------------------------------------------------------------
# CSS - tema escuro tipo dashboard (o base=dark já vem de .streamlit/config.toml)
# ----------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; padding-bottom: 3rem; }
    div[data-testid="stMetric"] {
        background: #161b26;
        border: 1px solid #262d3d;
        border-radius: 10px;
        padding: 10px 14px;
    }
    .sinal-card {
        border-radius: 12px;
        padding: 14px 16px 10px 16px;
        margin-bottom: 6px;
    }
    .sinal-badge {
        display: inline-block;
        padding: 3px 12px;
        border-radius: 999px;
        font-weight: 700;
        font-size: 0.82rem;
        letter-spacing: 0.02em;
    }
    .chip {
        display: inline-block;
        padding: 2px 9px;
        border-radius: 999px;
        font-size: 0.74rem;
        font-weight: 600;
        margin-right: 4px;
    }
    .dsr-aviso {
        margin-top: 8px;
        padding: 5px 9px;
        border-radius: 7px;
        font-size: 0.78rem;
        font-weight: 600;
    }
    .preco-atual {
        font-size: 1.25rem;
        font-weight: 700;
        color: #f8fafc;
        font-variant-numeric: tabular-nums;
    }
    .variacao-chip {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 700;
        margin-left: 8px;
        font-variant-numeric: tabular-nums;
        vertical-align: middle;
    }
    .meta-sinal {
        font-size: 0.76rem;
        color: #94a3b8;
        margin-top: 2px;
    }
    .stop-row {
        margin-top: 7px;
        font-size: 0.8rem;
        color: #e2e8f0;
        font-variant-numeric: tabular-nums;
    }
    .stats-row {
        margin-top: 10px;
        font-size: 0.85rem;
        color: #cbd5e1;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# CARREGAMENTO DE CSVs JÁ GERADOS (não roda nenhuma otimização/validação nova)
# ----------------------------------------------------------------------------
@st.cache_data(ttl=1800)
def carregar_csv(caminho):
    if not os.path.exists(caminho):
        return None
    return pd.read_csv(caminho)


# ----------------------------------------------------------------------------
# SINAL ATUAL - mesma lógica de entrada/saída do v4 (cruzamento + filtro +
# stop ATR fixo OU cruzamento contrário), calculada localmente pra saber se a
# posição está aberta no candle mais recente, e desde quando (data do último
# cruzamento que mudou o estado — usada só pra ordenar/exibir os cards).
# ----------------------------------------------------------------------------
def estado_atual_posicao(df_fast: dict, params: dict) -> dict:
    """Wrapper fino sobre estrategia_core.estado_posicao_atual — a MESMA lógica
    de entrada/saída/stop que o backtest (executar_backtest_v4) usa, agora numa
    fonte única. O radar não pode mais divergir do que a estratégia testou.
    Só embrulha data_mudanca em pd.Timestamp para o resto do app formatar."""
    d = estado_posicao_atual(df_fast, params, slippage=0.0)
    if d["data_mudanca"] is not None:
        d["data_mudanca"] = pd.Timestamp(d["data_mudanca"])
    return d


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


@st.cache_data(ttl=1800)
def calcular_sinal(ativo: str, interval_str: str, params: dict) -> dict:
    df = carregar_dados(ativo, interval_str)
    if df.empty:
        return {"posicionado": False, "preco_entrada": None, "stop_atual": None, "preco_atual": None, "preco_sinal": None, "data_mudanca": None}
    df_fast = montar_df_fast(df, params)
    return estado_atual_posicao(df_fast, params)


@st.cache_data(ttl=1800)
def curva_capital(ativo: str, interval_str: str, params: dict, taxa: float, slippage: float):
    """Devolve (df_dev, df_holdout) com colunas Estrategia/Buy&Hold indexadas
    por data, cada um recomeçando em 1000 no início do seu próprio período —
    exatamente como otimizador_v4.py e holdout_v4.py calculam oficialmente."""
    df = carregar_dados(ativo, interval_str)
    if df.empty:
        return None, None
    periodos = separar_periodos(df["t_abert"])
    idx_dev_fim = periodos["idx_dev_fim"]
    candles_dia = CANDLES_POR_DIA[interval_str]

    resultados = {}
    for nome, fatia in (("dev", df.iloc[:idx_dev_fim]), ("holdout", df.iloc[idx_dev_fim:])):
        fatia = fatia.reset_index(drop=True)
        if len(fatia) < 10:
            resultados[nome] = None
            continue
        df_fast = montar_df_fast(fatia, params)
        res = executar_backtest_v4(
            df_fast, params, 0, len(df_fast["fechamento"]), taxa, slippage, candles_dia, incluir_equity=True
        )
        datas = pd.to_datetime(df_fast["t_abert"])
        equity_estrategia = pd.Series(res["equity"], index=datas)
        preco = fatia["fechamento"].values
        equity_buyhold = pd.Series(preco / preco[0] * 1000.0, index=datas)
        df_curva = pd.DataFrame({"Estrategia": equity_estrategia, "Buy&Hold": equity_buyhold})
        # resample pra granularidade diária só pra exibição no gráfico (mais
        # legível e evita o limite de linhas do Altair em séries de anos em 4h)
        resultados[nome] = df_curva.resample("1D").last().dropna()

    return resultados.get("dev"), resultados.get("holdout")


@st.cache_data(ttl=1800)
def curva_capital_intervalo(ativo: str, interval_str: str, params: dict, taxa: float,
                            slippage: float, data_inicio, data_fim):
    """Curva de capital + métricas recalculadas SOMENTE na janela [data_inicio,
    data_fim] escolhida no calendário. Os indicadores são calculados sobre o
    histórico COMPLETO (warm-up), e só a simulação/capital recomeça em 1000 no
    início da janela — ou seja: "se você tivesse começado a operar a estratégia
    nesta data, com os indicadores já aquecidos, o que teria acontecido até a
    data final?". Buy&Hold também recomeça em 1000 no início da janela."""
    df = carregar_dados(ativo, interval_str)
    if df.empty:
        return None, None
    candles_dia = CANDLES_POR_DIA[interval_str]
    df_fast = montar_df_fast(df, params)  # warm-up no histórico inteiro
    datas = pd.to_datetime(df["t_abert"]).reset_index(drop=True)
    ini_ts = pd.Timestamp(data_inicio)
    fim_ts = pd.Timestamp(data_fim) + pd.Timedelta(days=1)  # inclui o dia final
    mask = (datas >= ini_ts) & (datas < fim_ts)
    idxs = np.where(mask.values)[0]
    if len(idxs) < 10:
        return None, None
    ini, fim = int(idxs[0]), int(idxs[-1]) + 1
    res = executar_backtest_v4(
        df_fast, params, ini, fim, taxa, slippage, candles_dia, incluir_equity=True
    )
    datas_janela = datas.iloc[ini:fim].reset_index(drop=True)
    eq = pd.Series(res["equity"], index=datas_janela)
    preco = df["fechamento"].values[ini:fim]
    bh = pd.Series(preco / preco[0] * CAPITAL_INICIAL, index=datas_janela)
    df_curva = pd.DataFrame({"Estrategia": eq, "Buy&Hold": bh}).resample("1D").last().dropna()
    bh_ret = (preco[-1] / preco[0] - 1) * 100.0
    metricas = {
        "retorno_estrategia_%": res["retorno_total_pct"],
        "retorno_buyhold_%": round(bh_ret, 2),
        "dd_%": res["drawdown_pct"],
        "calmar": res["calmar"],
        "num_trades": res["num_trades"],
        "data_ini": datas_janela.iloc[0],
        "data_fim": datas_janela.iloc[-1],
    }
    return df_curva, metricas


# ----------------------------------------------------------------------------
# PORTFÓLIO DA ESTRATÉGIA OURO ROBUSTA (calculado ao vivo, mesmo motor e mesmos
# pesos) — substitui a leitura dos CSVs de portfólio, que eram da versão
# OTIMIZADA por ativo (que decidimos não usar mais). Assim o resumo do topo bate
# exatamente com os sinais e gráficos, todos na receita robusta por grupo.
# ----------------------------------------------------------------------------
def _ret_diario_robusto(ativo: str, interval_str: str):
    """Retornos diários da estratégia robusta (receita do grupo) em dev e
    holdout, em fatias separadas — mesmo tratamento do holdout_v4."""
    params = RECEITA_ROBUSTA[grupo_ouro(ativo)]
    info_liq = classificar_liquidez(ativo)
    df = carregar_dados(ativo, interval_str)
    if df is None or df.empty:
        return None
    periodos = separar_periodos(df["t_abert"])
    idx = periodos["idx_dev_fim"]
    candles_dia = CANDLES_POR_DIA[interval_str]
    out = {"dev": None, "holdout": None}
    for nome, fatia in (("dev", df.iloc[:idx]), ("holdout", df.iloc[idx:])):
        fatia = fatia.reset_index(drop=True)
        if len(fatia) < max(params["media_filtro"], 30) + 5:
            continue
        df_fast = montar_df_fast(fatia, params)
        res = executar_backtest_v4(
            df_fast, params, 0, len(df_fast["fechamento"]),
            info_liq["taxa"], info_liq["slippage"], candles_dia, incluir_equity=True,
        )
        datas = pd.to_datetime(df_fast["t_abert"])
        eq = pd.Series(res["equity"], index=datas).resample("1D").last().dropna()
        preco = pd.Series(df_fast["fechamento"], index=datas).resample("1D").last().dropna()
        out[nome] = {"ret": eq.pct_change().dropna(), "ret_bh": preco.pct_change().dropna(), "res": res}
    return out


def _metricas_portfolio_local(ret_por_ativo: dict, pesos: pd.Series) -> dict:
    """Combina retornos diários de vários ativos com pesos, na janela comum."""
    ativos = [a for a in ret_por_ativo if a in pesos.index and len(ret_por_ativo[a]) > 0]
    if not ativos:
        return None
    datas = None
    for a in ativos:
        idx = ret_por_ativo[a].index
        datas = idx if datas is None else datas.intersection(idx)
    if datas is None or len(datas) < 5:
        return None
    datas = datas.sort_values()
    dfret = pd.DataFrame({a: ret_por_ativo[a].reindex(datas) for a in ativos}).fillna(0.0)
    p = pesos.reindex(ativos)
    p = p / p.sum()
    retp = (dfret * p).sum(axis=1)
    eq = (1 + retp).cumprod()
    rt = eq.iloc[-1] - 1.0
    dias = (datas[-1] - datas[0]).days
    anos = dias / 365.25 if dias > 0 else None
    ra = (1 + rt) ** (1 / anos) - 1 if anos and anos > 0 else None
    dd = ((eq.cummax() - eq) / eq.cummax()).max()
    calmar = ra / dd if (dd and dd > 0 and ra is not None) else None
    return {
        "ret_total_%": round(rt * 100, 2),
        "ret_anual_%": round(ra * 100, 2) if ra is not None else None,
        "dd_%": round(dd * 100, 2),
        "calmar": round(calmar, 3) if calmar is not None else None,
        "ini": datas[0], "fim": datas[-1], "n_dias": len(datas), "n_ativos": len(ativos),
    }


def _pesos_inverse_vol(ret_dev_por_ativo: dict) -> dict:
    """Pesos por volatilidade inversa (mesma metodologia do portfolio_v4.py):
    vol diária de cada ativo na janela comum a TODOS, peso_i = (1/vol_i)/soma.
    Aqui sobre os retornos da estratégia ROBUSTA (não a otimizada)."""
    datas = None
    for s in ret_dev_por_ativo.values():
        idx = s.index
        datas = idx if datas is None else datas.intersection(idx)
    if datas is None or len(datas) < 5:
        return {}
    datas = datas.sort_values()
    dfret = pd.DataFrame({a: s.reindex(datas) for a, s in ret_dev_por_ativo.items()})
    vol = dfret.std(ddof=1)
    inv = 1.0 / vol
    pesos = inv / inv.sum()
    return {a: round(float(p) * 100, 2) for a, p in pesos.items()}


@st.cache_data(ttl=1800, show_spinner=False)
def computar_portfolio_robusto():
    """Faz UM passo por todos os 22 ativos com a receita robusta e devolve, de
    uma vez: os pesos (inverse-vol da estratégia robusta, período de dev) e as
    métricas de portfólio — DEV só veteranas (janela longa ~5 anos) e HOLDOUT
    com os 22 (12 meses fora da amostra). Tudo com os MESMOS pesos robustos."""
    ret_dev_all, ret_dev_vet, ret_dev_vet_bh, ret_hold, ret_hold_bh = {}, {}, {}, {}, {}
    for ativo, interval in ATIVOS_PORTFOLIO_V4.items():
        r = _ret_diario_robusto(ativo, interval)
        if r is None:
            continue
        if r["dev"]:
            ret_dev_all[ativo] = r["dev"]["ret"]
            if grupo_ouro(ativo) == "veterana":
                ret_dev_vet[ativo] = r["dev"]["ret"]
                ret_dev_vet_bh[ativo] = r["dev"]["ret_bh"]
        if r["holdout"]:
            ret_hold[ativo] = r["holdout"]["ret"]
            ret_hold_bh[ativo] = r["holdout"]["ret_bh"]

    weights_pct = _pesos_inverse_vol(ret_dev_all)
    pesos = pd.Series({a: p / 100.0 for a, p in weights_pct.items()})
    return {
        "weights_pct": weights_pct,
        "dev_vet": _metricas_portfolio_local(ret_dev_vet, pesos),
        "dev_vet_bh": _metricas_portfolio_local(ret_dev_vet_bh, pesos),
        "hold": _metricas_portfolio_local(ret_hold, pesos),
        "hold_bh": _metricas_portfolio_local(ret_hold_bh, pesos),
    }


def grafico_area(df: pd.DataFrame, altura: int = 320):
    """Gráfico de área empilhada=NÃO (overlay), com preenchimento semi-
    transparente sob a linha — uma cor pra Estratégia, outra pra Buy&Hold."""
    df_long = df.reset_index(names="Data").melt("Data", var_name="Série", value_name="Capital")
    escala_cores = alt.Scale(domain=["Estrategia", "Buy&Hold"], range=[COR_ESTRATEGIA, COR_BUYHOLD])

    area = (
        alt.Chart(df_long)
        .mark_area(opacity=0.28, interpolate="monotone", line=False)
        .encode(
            x=alt.X("Data:T", title=None),
            y=alt.Y("Capital:Q", stack=None, title="Capital (USD)"),
            color=alt.Color("Série:N", scale=escala_cores, legend=alt.Legend(title=None, orient="top")),
        )
    )
    linha = (
        alt.Chart(df_long)
        .mark_line(interpolate="monotone", strokeWidth=2.2)
        .encode(
            x="Data:T",
            y=alt.Y("Capital:Q", stack=None),
            color=alt.Color("Série:N", scale=escala_cores, legend=None),
        )
    )
    return (
        (area + linha)
        .properties(height=altura, background="transparent")
        .configure_axis(gridColor="#262d3d", domainColor="#3a4256", labelColor="#9aa4b8", titleColor="#9aa4b8")
        .configure_view(strokeWidth=0)
        .configure_legend(labelColor="#e5e7eb")
    )


def badge_html(texto: str, cor: str) -> str:
    return f'<span class="sinal-badge" style="background:{cor}22; color:{cor}; border:1px solid {cor}66;">{texto}</span>'


def chip_html(texto: str, cor: str) -> str:
    return f'<span class="chip" style="background:{cor}22; color:{cor}; border:1px solid {cor}55;">{texto}</span>'


def formatar_preco(v: float) -> str:
    """Preços de cripto variam de $0.0000001 (memecoin) a $100000+ (BTC) —
    ajusta as casas decimais pra sempre mostrar algo com precisão útil."""
    if v >= 1:
        return f"{v:,.2f}"
    if v >= 0.01:
        return f"{v:.4f}"
    return f"{v:.8f}"


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.title("📡 Estratégia Ouro Robusta — Radar de Sinais & Portfólio")
st.caption(
    "Entrada = cruzamento de médias + filtro de tendência · Saída = stop ATR fixo OU cruzamento "
    "contrário. Sinais, gráficos e portfólio usam a **receita robusta por grupo** (veterana/nova) "
    "— não os parâmetros otimizados por ativo, que se mostraram overfitting."
)

df_resumo = carregar_csv(RESUMO_CSV)

if df_resumo is None:
    st.error(f"Não encontrei {RESUMO_CSV}. Rode `python otimizador_v4.py` primeiro.")
    st.stop()

# ---- Resumo do portfólio no topo (ESTRATÉGIA OURO ROBUSTA, calculado ao vivo) ----
st.header("Resumo do Portfólio — Estratégia Ouro Robusta")
st.caption(
    "Receita robusta por grupo (veterana nas líquidas/antigas, nova nas recentes) — "
    "não os parâmetros otimizados por ativo. Mesmos números que os sinais e gráficos abaixo."
)

with st.spinner("Calculando portfólio e pesos da estratégia robusta..."):
    resultado_rob = computar_portfolio_robusto()
# pesos calculados AO VIVO pela volatilidade inversa da estratégia robusta
pesos_map = resultado_rob["weights_pct"] if resultado_rob else {}

col_dev, col_holdout = st.columns(2)

with col_dev:
    st.subheader("📈 Desenvolvimento (veteranas, ~5 anos)")
    st.caption("Janela longa e confiável das 8 líquidas/antigas. A receita foi derivada aqui — não é validação cega.")
    m = resultado_rob["dev_vet"] if resultado_rob else None
    m_bh = resultado_rob["dev_vet_bh"] if resultado_rob else None
    if m is not None:
        c1, c2, c3 = st.columns(3)
        c1.metric("Retorno Anualizado", f"{m['ret_anual_%']:.2f}%")
        c2.metric("Drawdown Máximo", f"{m['dd_%']:.2f}%")
        c3.metric("Calmar", f"{m['calmar']:.3f}")
        bh_txt = (f" · Buy&Hold: {m_bh['ret_anual_%']:.1f}% anual, DD {m_bh['dd_%']:.1f}%, Calmar {m_bh['calmar']}"
                  if m_bh is not None else "")
        st.caption(f"{m['ini'].date()} a {m['fim'].date()} ({m['n_dias']} dias, {m['n_ativos']} veteranas){bh_txt}")
    else:
        st.info("Sem dados suficientes pra compor o portfólio de desenvolvimento.")

with col_holdout:
    st.subheader("🔒 Holdout — VALIDAÇÃO REAL (mercado de baixa)")
    st.caption("Últimos 12 meses, nunca vistos na derivação da receita. Mede proteção de capital, não captura de alta.")
    m = resultado_rob["hold"] if resultado_rob else None
    m_bh = resultado_rob["hold_bh"] if resultado_rob else None
    if m is not None and m_bh is not None:
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "Retorno Anualizado",
            f"{m['ret_anual_%']:.2f}%",
            delta=f"{m['ret_anual_%'] - m_bh['ret_anual_%']:.2f}% vs Buy&Hold",
        )
        c2.metric("Drawdown Máximo", f"{m['dd_%']:.2f}%")
        c3.metric("Calmar", f"{m['calmar']:.3f}")
        st.caption(
            f"Buy&Hold no mesmo período: {m_bh['ret_anual_%']:.2f}% anualizado, "
            f"DD {m_bh['dd_%']:.2f}% · {m['ini'].date()} a {m['fim'].date()} ({m['n_ativos']} ativos)"
        )
    else:
        st.info("Sem dados de holdout pra compor o portfólio.")

st.divider()

# ---- Validação Multi-Regime (Fase 1) ----
with st.expander("📊 Validação Multi-Regime — o que a estratégia faz bem (e o que não faz)", expanded=False):
    st.caption(
        "Análise walk-forward sobre 115 janelas anuais × 22 ativos no período de "
        "desenvolvimento — receita robusta fixa, sem re-otimização por janela."
    )

    df_wf = carregar_csv("walkforward_robusta_v4_janelas.csv")
    if df_wf is None:
        st.info("Rode `python walkforward_robusta_v4.py` para gerar os dados de validação.")
    else:
        col_r1, col_r2, col_r3, col_r4 = st.columns(4)
        n_total = len(df_wf)
        pct_pos = float((df_wf["Retorno_%"] > 0).mean() * 100)
        pct_bat = float((df_wf["Retorno_%"] > df_wf["BH_%"]).mean() * 100)
        med = float(df_wf["Retorno_%"].median())
        col_r1.metric("Janelas analisadas", f"{n_total}")
        col_r2.metric("Janelas positivas", f"{pct_pos:.0f}%")
        col_r3.metric("Bate Buy&Hold", f"{pct_bat:.0f}%")
        col_r4.metric("Retorno mediano/janela", f"{med:+.1f}%")

        st.markdown("#### Por regime de mercado (regime do ativo naquele ano)")
        linhas_regime = []
        for regime in ["BULL", "LATERAL", "BEAR"]:
            sub = df_wf[df_wf["Regime"] == regime]
            if sub.empty:
                continue
            rv = sub["Retorno_%"].values
            bv = sub["BH_%"].values
            linhas_regime.append({
                "Regime": regime,
                "Janelas": len(rv),
                "Strat. mediana": f"{float(np.median(rv)):+.1f}%",
                "B&H mediana": f"{float(np.median(bv)):+.1f}%",
                "% positivas": f"{float(np.mean(rv > 0) * 100):.0f}%",
                "Bate B&H": f"{float(np.mean(rv > bv) * 100):.0f}%",
            })
        if linhas_regime:
            st.dataframe(
                pd.DataFrame(linhas_regime).set_index("Regime"),
                use_container_width=True,
            )

        st.markdown(
            """
**Leitura honesta:**
- 🐻 **BEAR (queda):** estratégia perde −14% (mediana) mas **protege fortemente** vs B&H (−54%) — bate B&H em 93% das janelas.
- ↔️ **LATERAL:** estratégia ganha +28% e bate B&H em 81% das janelas — ponto forte.
- 🐂 **BULL (alta):** estratégia ganha +55%, mas B&H ganha +156% — saída por cruzamento corta os vencedores cedo. Principal fraqueza.

**O que o trailing stop mostrou:** testado nas mesmas janelas, o trailing stop **piorou** em todos os regimes (−50pp em BULL). O cruzamento + stop ATR fixo é a saída mais eficiente para esses parâmetros.

**Próximo passo (Fase 2C):** filtro de regime do BTC — em anos BULL do BTC usar 1.5× capital, em anos BEAR usar 0.5×. Simulação sobre os mesmos dados mostrou retorno total $81K → $380K no mesmo período histórico (resultado orientativo, não validado out-of-sample).
            """.strip()
        )

        # Gráfico de distribuição
        st.markdown("#### Distribuição dos retornos anuais (estratégia vs B&H)")
        df_dist = df_wf[["Retorno_%", "BH_%"]].copy()
        df_dist = df_dist.rename(columns={"Retorno_%": "Estratégia", "BH_%": "Buy&Hold"})
        df_melt = df_dist.melt(var_name="Série", value_name="Retorno (%)")
        if not df_melt.empty:
            hist = (
                alt.Chart(df_melt)
                .mark_bar(opacity=0.6, binSpacing=1)
                .encode(
                    x=alt.X("Retorno (%):Q", bin=alt.Bin(maxbins=40), title="Retorno anual (%)"),
                    y=alt.Y("count():Q", title="Nº de janelas-ativo"),
                    color=alt.Color(
                        "Série:N",
                        scale=alt.Scale(domain=["Estratégia", "Buy&Hold"], range=[COR_ESTRATEGIA, COR_BUYHOLD]),
                        legend=alt.Legend(title=None, orient="top"),
                    ),
                )
                .properties(height=220, background="transparent")
                .configure_axis(gridColor="#262d3d", labelColor="#9aa4b8")
                .configure_view(strokeWidth=0)
                .configure_legend(labelColor="#e5e7eb")
            )
            st.altair_chart(hist, use_container_width=True)

st.divider()

# ---- Radar de sinais ----
st.header("Radar de Sinais (22 ativos)")
st.caption(
    "🟢 ALTA = posicionado agora · 🔴 BAIXA = fora do mercado · ordenado pela data do último "
    "cruzamento que gerou o sinal atual (mais recente primeiro)."
)

if "ativo_selecionado" not in st.session_state:
    st.session_state["ativo_selecionado"] = None

# pré-computa sinal de todos os ativos pra poder ordenar os cards por data.
# Os sinais usam a RECEITA OURO ROBUSTA do grupo do ativo (veterana/nova) — NÃO
# os parâmetros otimizados por ativo, que decidimos não confiar (overfitting).
cartoes = []
for _, row in df_resumo.iterrows():
    ativo = row["Ativo"]
    params = RECEITA_ROBUSTA[grupo_ouro(ativo)]
    sinal = calcular_sinal(ativo, row["Interval"], params)
    cartoes.append({"row": row, "params": params, "sinal": sinal})

cartoes.sort(key=lambda c: c["sinal"]["data_mudanca"] or pd.Timestamp.min, reverse=True)

N_COLS = 4
blocos = [cartoes[i : i + N_COLS] for i in range(0, len(cartoes), N_COLS)]

for bloco in blocos:
    cols = st.columns(N_COLS)
    for col, item in zip(cols, bloco):
        row, params, sinal = item["row"], item["params"], item["sinal"]
        ativo = row["Ativo"]
        with col:
            with st.container(border=True):
                posicionado = sinal["posicionado"]
                cor_sinal = COR_ALTA if posicionado else COR_BAIXA
                texto_sinal = "🟢 ALTA" if posicionado else "🔴 BAIXA"
                data_txt = sinal["data_mudanca"].strftime("%d/%m/%Y") if sinal["data_mudanca"] is not None else "sem sinal no histórico"

                variacao_pct = None
                if sinal["preco_sinal"] and sinal["preco_atual"] is not None:
                    variacao_pct = (sinal["preco_atual"] / sinal["preco_sinal"] - 1) * 100
                variacao_chip = ""
                if variacao_pct is not None:
                    cor_variacao = COR_ALTA if variacao_pct >= 0 else COR_BAIXA
                    variacao_chip = f'<span class="variacao-chip" style="background:{cor_variacao}22; color:{cor_variacao}; border:1px solid {cor_variacao}55;">{variacao_pct:+.2f}%</span>'

                preco_txt = formatar_preco(sinal["preco_atual"]) if sinal["preco_atual"] is not None else "—"

                stop_html = ""
                if posicionado and sinal["stop_atual"] is not None and sinal["preco_atual"]:
                    distancia_pct = abs(sinal["stop_atual"] / sinal["preco_atual"] - 1) * 100
                    stop_html = (
                        f'<div class="stop-row">🛑 <b>Stop fixo desde a entrada:</b> {formatar_preco(sinal["stop_atual"])} '
                        f'<span style="color:#94a3b8;">(faltam {distancia_pct:.2f}% de queda a partir de agora pra atingi-lo)</span></div>'
                    )

                grupo_liquido = row["Grupo_Liquidez"] == "liquido"
                cor_grupo = COR_LIQUIDO if grupo_liquido else COR_MENOS_LIQUIDO
                texto_grupo = "💧 Veterana" if grupo_liquido else "🪙 Nova"
                peso = pesos_map.get(ativo)

                html = f"""
                <div class="sinal-card" style="background:{cor_sinal}14; border:1px solid {cor_sinal}40; border-left:4px solid {cor_sinal};">
                    <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:4px 8px;">
                        <span style="font-size:1.05rem; font-weight:700; color:#f1f5f9; overflow-wrap:anywhere;">{ativo}</span>
                        {badge_html(texto_sinal, cor_sinal)}
                    </div>
                    <div style="margin-top:8px;">
                        <span class="preco-atual">{preco_txt}</span>{variacao_chip}
                    </div>
                    <div class="meta-sinal">sinal desde {data_txt} · preço no sinal: {formatar_preco(sinal["preco_sinal"]) if sinal["preco_sinal"] else "—"}</div>
                    {stop_html}
                    <div style="margin-top:8px;">
                        {chip_html(texto_grupo, cor_grupo)}
                        {chip_html(f"peso {peso:.2f}%" if peso is not None else "peso n/d", "#64748b")}
                    </div>
                """
                dsr_pct = row["DSR_%"]
                if pd.notna(dsr_pct) and dsr_pct < DSR_ALERTA_LIMITE:
                    html += f"""
                    <div class="dsr-aviso" style="background:{COR_DSR_ALERTA}22; color:{COR_DSR_ALERTA}; border:1px solid {COR_DSR_ALERTA}55;">
                        ⚠️ Poucos trades no histórico — backtest sem significância estatística (DSR {dsr_pct:.1f}%); tratar com cautela
                    </div>
                    """
                html += "</div>"
                html = "\n".join(linha.strip() for linha in html.strip().splitlines())
                st.markdown(html, unsafe_allow_html=True)

                if st.button("Ver detalhes", key=f"btn_{ativo}", use_container_width=True):
                    st.session_state["ativo_selecionado"] = ativo

st.divider()

# ---- Análise detalhada por ativo ----
st.header("Análise Detalhada por Ativo")
ativo_sel = st.session_state["ativo_selecionado"]
if ativo_sel is None:
    st.info("Clique em \"Ver detalhes\" em algum card acima pra ver o gráfico de capital.")
else:
    row = df_resumo[df_resumo["Ativo"] == ativo_sel].iloc[0]
    info_liq = classificar_liquidez(ativo_sel)
    interval_sel = row["Interval"]
    grupo_a = grupo_ouro(ativo_sel)
    params = RECEITA_ROBUSTA[grupo_a]

    st.subheader(f"{ativo_sel} ({interval_sel}) — Estratégia Ouro Robusta ({grupo_a})")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Grupo", grupo_a)
    c2.metric("Peso no portfólio", f"{pesos_map.get(ativo_sel, float('nan')):.2f}%")
    c3.metric("Média lenta / filtro", f"{params['media_lenta']}/{params['media_filtro']}")
    c4.metric("ATR", f"{params['atr_periodo']}×{params['atr_multiplicador']}")
    dsr_ativo = row["DSR_%"]
    if pd.notna(dsr_ativo) and dsr_ativo < DSR_ALERTA_LIMITE:
        st.caption(f"⚠️ Poucos trades no histórico — backtest sem significância estatística "
                   f"(DSR {dsr_ativo:.1f}%). Os sinais existem, mas trate com cautela extra.")
    st.caption(
        f"Parâmetros em uso: rápida={params['media_rapida']} · lenta={params['media_lenta']} · "
        f"filtro={params['media_filtro']} · ATR={params['atr_periodo']}×{params['atr_multiplicador']} · "
        f"Grupo: {info_liq['grupo']} (taxa {info_liq['taxa']*100:.2f}% + slippage {info_liq['slippage']*100:.3f}%)"
    )

    # ---- Gráfico com CALENDÁRIO (janela personalizada) ----
    st.markdown("### 🗓️ Janela personalizada (escolha o período)")
    df_datas = carregar_dados(ativo_sel, interval_sel)
    if df_datas is None or df_datas.empty:
        st.info("Sem dados de preço pra este ativo.")
    else:
        datas_all = pd.to_datetime(df_datas["t_abert"])
        dmin, dmax = datas_all.min().date(), datas_all.max().date()
        periodos_a = separar_periodos(datas_all)
        default_ini = periodos_a["holdout_inicio"].date() if periodos_a["holdout_inicio"] is not None else dmin

        colcal1, colcal2 = st.columns([2, 1])
        with colcal1:
            intervalo = st.date_input(
                "Período de comparação",
                value=(default_ini, dmax),
                min_value=dmin,
                max_value=dmax,
                key=f"cal_{ativo_sel}",
                format="DD/MM/YYYY",
            )
        with colcal2:
            st.caption("Padrão = período de holdout (12 meses fora da amostra). "
                       "Arraste no calendário pra qualquer janela — tudo recalcula.")

        if isinstance(intervalo, (list, tuple)) and len(intervalo) == 2:
            data_ini, data_fim = intervalo
            with st.spinner("Recalculando na janela escolhida..."):
                df_janela, met = curva_capital_intervalo(
                    ativo_sel, interval_sel, params, info_liq["taxa"], info_liq["slippage"],
                    data_ini, data_fim,
                )
            if df_janela is None or met is None:
                st.warning("Janela muito curta pra simular (precisa de pelo menos ~10 candles). Escolha um período maior.")
            else:
                m1, m2, m3, m4 = st.columns(4)
                delta_vs_bh = met["retorno_estrategia_%"] - met["retorno_buyhold_%"]
                m1.metric("Estratégia (janela)", f"{met['retorno_estrategia_%']:.2f}%",
                          delta=f"{delta_vs_bh:+.2f}% vs Buy&Hold")
                m2.metric("Buy & Hold (janela)", f"{met['retorno_buyhold_%']:.2f}%")
                m3.metric("Drawdown máx", f"{met['dd_%']:.2f}%")
                m4.metric("Trades", f"{int(met['num_trades'])}")
                calmar_txt = f"{met['calmar']:.2f}" if met["calmar"] is not None else "—"
                st.caption(
                    f"Janela: {met['data_ini'].strftime('%d/%m/%Y')} a {met['data_fim'].strftime('%d/%m/%Y')} "
                    f"· Calmar {calmar_txt} · indicadores aquecidos com o histórico anterior à janela "
                    f"(capital recomeça em ${CAPITAL_INICIAL:.0f} no início do período escolhido)."
                )
                st.altair_chart(grafico_area(df_janela), use_container_width=True)
        else:
            st.info("Escolha as duas datas (início e fim) no calendário acima.")

    # ---- Janelas oficiais fixas (dev | holdout), pra referência ----
    st.markdown("### Janelas oficiais (referência) — Desenvolvimento vs Holdout")
    with st.spinner("Reconstruindo curvas de capital..."):
        df_dev, df_holdout = curva_capital(ativo_sel, interval_sel, params, info_liq["taxa"], info_liq["slippage"])

    col_g1, col_g2 = st.columns(2)
    with col_g1:
        st.markdown("**Período de Desenvolvimento** — Estratégia vs Buy&Hold")
        if df_dev is not None:
            st.altair_chart(grafico_area(df_dev), use_container_width=True)
        else:
            st.info("Sem dados suficientes de desenvolvimento pra este ativo.")
    with col_g2:
        st.markdown("**Holdout (últimos 12 meses)** — Estratégia vs Buy&Hold 🔒")
        if df_holdout is not None:
            st.altair_chart(grafico_area(df_holdout), use_container_width=True)
        else:
            st.info("Sem dados de holdout pra este ativo ainda.")

    st.caption(
        "As duas janelas acima usam a receita robusta do grupo, com indicadores calculados "
        "isoladamente em cada período (sem aquecer com o histórico anterior) — por isso podem "
        "diferir um pouco da janela personalizada do calendário, que aquece os indicadores."
    )
