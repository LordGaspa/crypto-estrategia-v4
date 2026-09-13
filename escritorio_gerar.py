# -*- coding: utf-8 -*-
"""Gera escritorio_omega.html com os dados reais da última rodada.

Rodado pelo `monitor_agendado.ps1` junto com o e-mail e a sombra, toda hora --
o arquivo fica sempre atualizado, igual o `RADAR.bat` já faz com os sinais.
Abra `escritorio_omega.html` (ou o atalho `ESCRITORIO.bat`) quando quiser ver
a sala.

Diferença da versão publicada no claude.ai: aqui o Gerente NÃO pensa com IA
ao vivo -- isso só funciona na página hospedada (usa um recurso do runtime de
Artifacts que um arquivo local não tem acesso). Em vez disso, ele fala um
**resumo automático**, escrito por fórmula a partir dos mesmos números reais.
Sem chave de API, sem custo, sempre correto -- decisão tomada com o usuário
em 2026-09-13 depois de pesar contra a alternativa (IA de verdade custaria
uma chamada de API por rodada).

O restante da sala (os 22 especialistas, o telão com o resultado ponderado,
o relógio, a animação de revisão) é idêntico ao da versão hospedada -- só a
fonte dos dados e a fala do Gerente mudam.
"""
import csv
import json
import os
from datetime import datetime

PASTA = os.path.dirname(os.path.abspath(__file__))
MONITOR_ESTADO = os.path.join(PASTA, "monitor_estado.json")
PESOS_CSV = os.path.join(PASTA, "portfolio_v4_pesos.csv")
TEMPLATE = os.path.join(PASTA, "escritorio_omega_template.html")
SAIDA = os.path.join(PASTA, "escritorio_omega.html")


def carregar_pesos() -> dict:
    pesos = {}
    with open(PESOS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pesos[row["Ativo"].replace("USDT", "")] = float(row["Peso_Portfolio_%"])
    return pesos


def montar_especialistas() -> tuple:
    """Lê o estado atual do monitor e devolve (lista ordenada por data do
    sinal -- mais recente primeiro --, timestamp da última atualização)."""
    pesos = carregar_pesos()
    with open(MONITOR_ESTADO, encoding="utf-8") as f:
        estado = json.load(f)

    linhas = []
    for ativo, s in estado["sinais"].items():
        nome = ativo.replace("USDT", "")
        dt = datetime.strptime(s["data_sinal"], "%d/%m/%Y %H:%M")
        var = None
        if s["posicionado"] and s.get("preco_sinal"):
            var = (s["preco"] / s["preco_sinal"] - 1) * 100
        peso = pesos.get(nome, 0.0)
        contrib = (peso / 100) * var if var is not None else 0.0
        linhas.append({
            "t": nome,
            "peso": round(peso, 2),
            "alta": bool(s["posicionado"]),
            "var": round(var, 1) if var is not None else None,
            "desde": dt.strftime("%d/%m"),
            "contrib": round(contrib, 3),
            "_dt": dt,
        })

    # mais recente primeiro; empate no mesmo horário desempata por ordem alfabética
    linhas.sort(key=lambda x: (-x["_dt"].timestamp(), x["t"]))
    for l in linhas:
        del l["_dt"]

    atualizado = datetime.fromisoformat(estado["atualizado_em"]).strftime("%d/%m %H:%M")
    return linhas, atualizado


def gerar_resumo_automatico(especialistas: list, resultado_final: float) -> str:
    """Frase determinística com os mesmos números que a sala mostra -- sem
    IA, sem custo. Nunca inventa nada que não esteja nos dados."""
    abertos = [e for e in especialistas if e["alta"]]
    n_abertos, n_total = len(abertos), len(especialistas)

    if not abertos:
        return (f"Nenhuma posição aberta agora. Resultado flutuante do portfólio: "
                f"<b>{resultado_final:+.2f}%</b>.")

    maior_alta = max(abertos, key=lambda e: e["var"])
    maior_queda = min(abertos, key=lambda e: e["var"])

    partes = [f"Resultado flutuante do portfólio: <b>{resultado_final:+.2f}%</b>."]
    partes.append(f"Maior alta: <b>{maior_alta['t']}</b> ({maior_alta['var']:+.1f}%).")
    if maior_queda["var"] < 0:
        partes.append(f"Maior queda entre as posições abertas: "
                       f"<b>{maior_queda['t']}</b> ({maior_queda['var']:+.1f}%).")
    partes.append(f"{n_abertos} de {n_total} ativos posicionados agora.")
    return " ".join(partes)


def gerar() -> None:
    especialistas, atualizado_em = montar_especialistas()
    resultado_final = round(sum(e["contrib"] for e in especialistas), 2)
    resumo = gerar_resumo_automatico(especialistas, resultado_final)

    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()

    bloco = (
        "<script>\n"
        "window.OMEGA_MODO_LOCAL = true;\n"
        f"window.OMEGA_ESPECIALISTAS = {json.dumps(especialistas, ensure_ascii=False)};\n"
        f"window.OMEGA_RESUMO_AUTOMATICO = {json.dumps(resumo, ensure_ascii=False)};\n"
        f"window.OMEGA_ATUALIZADO_EM = {json.dumps(atualizado_em, ensure_ascii=False)};\n"
        "</script>\n"
    )

    if "<!-- DADOS_OMEGA -->" not in html:
        raise RuntimeError(
            f"{TEMPLATE} não tem o marcador <!-- DADOS_OMEGA --> -- "
            "não sei onde injetar os dados."
        )
    html = html.replace("<!-- DADOS_OMEGA -->", bloco, 1)

    tmp = SAIDA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, SAIDA)  # escrita atômica -- mesmo padrão do monitor_sinais

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] escritorio_omega.html atualizado "
          f"({len(especialistas)} ativos, resultado {resultado_final:+.2f}%)")


if __name__ == "__main__":
    gerar()
