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

from config_v4 import CANDLES_POR_DIA, carregar_dados, separar_periodos, classificar_liquidez
from otimizador_v4 import executar_backtest_v4

st.set_page_config(page_title="Estratégia V4 - Radar & Portfólio", layout="wide", page_icon="📡")
alt.data_transformers.disable_max_rows()  # séries de anos em 4h passam do limite padrão (5000 linhas)

RESUMO_CSV = "otimizador_v4_RESUMO_ATIVOS.csv"
PESOS_CSV = "portfolio_v4_pesos.csv"
PORTFOLIO_DEV_CSV = "portfolio_v4_resultado.csv"
PORTFOLIO_HOLDOUT_CSV = "holdout_v4_portfolio_resultado.csv"
HOLDOUT_POR_ATIVO_CSV = "holdout_v4_resultado.csv"
DSR_ALERTA_LIMITE = 5.0  # % — abaixo disso, aviso de "histórico curto"

COR_ALTA = "#22c55e"
COR_BAIXA = "#ef4444"
COR_DSR_ALERTA = "#f59e0b"
COR_LIQUIDO = "#3b82f6"
COR_MENOS_LIQUIDO = "#a855f7"
COR_ESTRATEGIA = "#34d399"
COR_BUYHOLD = "#fbbf24"

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
    m_rapida = df_fast[f"ma_{params['media_rapida']}"]
    m_lenta = df_fast[f"ma_{params['media_lenta']}"]
    m_filtro = df_fast[f"ma_f_{params['media_filtro']}"]
    abertura = df_fast["abertura"]
    minima = df_fast["minima"]
    fechamento = df_fast["fechamento"]
    atr = df_fast[f"atr_{params['atr_periodo']}"]
    t_abert = df_fast["t_abert"]
    multi_atr = params["atr_multiplicador"]
    n = len(fechamento)

    vazio = {"posicionado": False, "preco_entrada": None, "stop_atual": None, "preco_atual": None, "preco_sinal": None, "data_mudanca": None}
    if n < 5:
        return vazio

    sinais_compra = np.zeros(n, dtype=bool)
    sinais_compra[1:] = (
        (m_rapida[1:] > m_lenta[1:])
        & (m_rapida[:-1] <= m_lenta[:-1])
        & (fechamento[1:] > m_filtro[1:])
    )
    sinais_venda = np.zeros(n, dtype=bool)
    sinais_venda[1:] = (m_rapida[1:] < m_lenta[1:]) & (m_rapida[:-1] >= m_lenta[:-1])

    posicionado = False
    preco_entrada = 0.0
    stop_loss_price = 0.0
    data_mudanca = None
    preco_mudanca = None
    for i in range(1, n):
        if not posicionado and sinais_compra[i - 1]:
            preco_entrada = abertura[i]
            posicionado = True
            stop_loss_price = preco_entrada - (atr[i - 1] * multi_atr)
            data_mudanca = t_abert[i]
            preco_mudanca = abertura[i]
        elif posicionado:
            if minima[i] < stop_loss_price or sinais_venda[i - 1]:
                posicionado = False
                data_mudanca = t_abert[i]
                preco_mudanca = abertura[i]

    return {
        "posicionado": posicionado,
        "preco_entrada": preco_entrada if posicionado else None,
        "stop_atual": stop_loss_price if posicionado else None,
        "preco_atual": float(fechamento[-1]),
        "preco_sinal": float(preco_mudanca) if preco_mudanca is not None else None,
        "data_mudanca": pd.Timestamp(data_mudanca) if data_mudanca is not None else None,
    }


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
st.title("📡 Estratégia V4 — Radar de Sinais & Portfólio")
st.caption(
    "Entrada = cruzamento de médias + filtro de tendência · Saída = stop ATR fixo OU cruzamento "
    "contrário (lógica do v2/v4 — não o trailing stop do v3, que foi descartado)."
)

df_resumo = carregar_csv(RESUMO_CSV)
df_pesos = carregar_csv(PESOS_CSV)
df_portfolio_dev = carregar_csv(PORTFOLIO_DEV_CSV)
df_portfolio_holdout = carregar_csv(PORTFOLIO_HOLDOUT_CSV)

if df_resumo is None:
    st.error(f"Não encontrei {RESUMO_CSV}. Rode `python otimizador_v4.py` primeiro.")
    st.stop()

pesos_map = {}
if df_pesos is not None:
    pesos_map = df_pesos.set_index("Ativo")["Peso_Portfolio_%"].to_dict()

# ---- Resumo do portfólio no topo ----
st.header("Resumo do Portfólio")
col_dev, col_holdout = st.columns(2)

with col_dev:
    st.subheader("📈 Período de Desenvolvimento")
    st.caption("Onde os parâmetros foram escolhidos — não é validação fora da amostra.")
    if df_portfolio_dev is not None and not df_portfolio_dev.empty:
        r = df_portfolio_dev.iloc[0]
        c1, c2, c3 = st.columns(3)
        c1.metric("Retorno Anualizado", f"{r['Retorno_Anualizado_%']:.2f}%")
        c2.metric("Drawdown Máximo", f"{r['DD_%']:.2f}%")
        c3.metric("Calmar", f"{r['Calmar_Portfolio']:.3f}")
        st.caption(f"Período comum: {r['Periodo_Comum_Inicio']} a {r['Periodo_Comum_Fim']} ({int(r['N_Dias_Comuns'])} dias, {int(r['N_Ativos'])} ativos)")
    else:
        st.info(f"Rode `python portfolio_v4.py` pra gerar {PORTFOLIO_DEV_CSV}.")

with col_holdout:
    st.subheader("🔒 Holdout — VALIDAÇÃO REAL (mercado de baixa)")
    st.caption("Últimos 12 meses, nunca vistos pelo otimizador. Mede proteção de capital, não captura de alta.")
    if df_portfolio_holdout is not None and not df_portfolio_holdout.empty:
        linha_estrategia = df_portfolio_holdout[df_portfolio_holdout["Versao"] == "Estrategia_V4"].iloc[0]
        linha_bh = df_portfolio_holdout[df_portfolio_holdout["Versao"] == "Buy&Hold"].iloc[0]
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "Retorno Anualizado",
            f"{linha_estrategia['Retorno_Anualizado_%']:.2f}%",
            delta=f"{linha_estrategia['Retorno_Anualizado_%'] - linha_bh['Retorno_Anualizado_%']:.2f}% vs Buy&Hold",
        )
        c2.metric("Drawdown Máximo", f"{linha_estrategia['DD_%']:.2f}%")
        c3.metric("Calmar", f"{linha_estrategia['Calmar_Portfolio']:.3f}")
        st.caption(
            f"Buy&Hold no mesmo período: {linha_bh['Retorno_Anualizado_%']:.2f}% anualizado, "
            f"DD {linha_bh['DD_%']:.2f}% · {linha_estrategia['Periodo_Comum_Inicio']} a {linha_estrategia['Periodo_Comum_Fim']}"
        )
    else:
        st.warning(
            "Holdout ainda não validado nesta pasta. Rode `python holdout_v4.py "
            "--eu-confirmo-holdout-final` quando decidir fazer a validação final."
        )

st.divider()

# ---- Radar de sinais ----
st.header("Radar de Sinais (22 ativos)")
st.caption(
    "🟢 ALTA = posicionado agora · 🔴 BAIXA = fora do mercado · ordenado pela data do último "
    "cruzamento que gerou o sinal atual (mais recente primeiro)."
)

if "ativo_selecionado" not in st.session_state:
    st.session_state["ativo_selecionado"] = None

# pré-computa sinal de todos os ativos pra poder ordenar os cards por data
cartoes = []
for _, row in df_resumo.iterrows():
    ativo = row["Ativo"]
    params = dict(
        media_rapida=int(row["media_rapida_per"]),
        media_lenta=int(row["media_lenta_per"]),
        media_filtro=int(row["media_filtro_tendencia_per"]),
        atr_periodo=int(row["atr_periodo"]),
        atr_multiplicador=float(row["atr_multiplicador"]),
    )
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

                dsr_pct = row["DSR_%"]
                grupo_liquido = row["Grupo_Liquidez"] == "liquido"
                cor_grupo = COR_LIQUIDO if grupo_liquido else COR_MENOS_LIQUIDO
                texto_grupo = "💧 Líquido" if grupo_liquido else "🪙 Menos líquido"
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
                    <div class="stats-row">
                        <b>Calmar:</b> {row['Calmar']:.2f} &nbsp;·&nbsp; <b>DSR:</b> {dsr_pct:.2f}%
                    </div>
                    <div style="margin-top:6px;">
                        {chip_html(texto_grupo, cor_grupo)}
                        {chip_html(f"peso {peso:.2f}%" if peso is not None else "peso n/d", "#64748b")}
                    </div>
                """
                if pd.notna(dsr_pct) and dsr_pct < DSR_ALERTA_LIMITE:
                    html += f"""
                    <div class="dsr-aviso" style="background:{COR_DSR_ALERTA}22; color:{COR_DSR_ALERTA}; border:1px solid {COR_DSR_ALERTA}55;">
                        ⚠️ Histórico curto — tratar com cautela
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
    params = dict(
        media_rapida=int(row["media_rapida_per"]),
        media_lenta=int(row["media_lenta_per"]),
        media_filtro=int(row["media_filtro_tendencia_per"]),
        atr_periodo=int(row["atr_periodo"]),
        atr_multiplicador=float(row["atr_multiplicador"]),
    )
    info_liq = classificar_liquidez(ativo_sel)

    st.subheader(f"{ativo_sel} ({row['Interval']})")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Calmar (dev)", f"{row['Calmar']:.2f}")
    c2.metric("Sharpe (dev)", f"{row['Sharpe']:.2f}")
    c3.metric("DSR", f"{row['DSR_%']:.2f}%")
    c4.metric("Peso no portfólio", f"{pesos_map.get(ativo_sel, float('nan')):.2f}%")
    st.caption(
        f"Parâmetros: rápida={params['media_rapida']} · lenta={params['media_lenta']} · "
        f"filtro={params['media_filtro']} · ATR={params['atr_periodo']}x{params['atr_multiplicador']} · "
        f"Grupo: {info_liq['grupo']} (taxa {info_liq['taxa']*100:.2f}% + slippage {info_liq['slippage']*100:.3f}%)"
    )

    with st.spinner("Reconstruindo curvas de capital..."):
        df_dev, df_holdout = curva_capital(ativo_sel, row["Interval"], params, info_liq["taxa"], info_liq["slippage"])

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

    df_holdout_ativo = carregar_csv(HOLDOUT_POR_ATIVO_CSV)
    if df_holdout_ativo is not None:
        linha = df_holdout_ativo[df_holdout_ativo["Ativo"] == ativo_sel]
        if not linha.empty:
            l = linha.iloc[0]
            st.caption(
                f"Holdout oficial ({l['Periodo_Holdout_Inicio']} a {l['Periodo_Holdout_Fim']}): "
                f"Estratégia {l['Retorno_Estrategia_TESTE_%']:.2f}% vs Buy&Hold {l['Retorno_BuyHold_TESTE_%']:.2f}% "
                f"· DD {l['DD_TESTE_%']:.2f}% · {int(l['Num_Trades'])} trades"
            )
