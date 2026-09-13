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
from datetime import datetime, timezone

import guardrails

PASTA = os.path.dirname(os.path.abspath(__file__))
MONITOR_ESTADO = os.path.join(PASTA, "monitor_estado.json")
PESOS_CSV = os.path.join(PASTA, "portfolio_v4_pesos.csv")
SOMBRA_ESTADO = os.path.join(PASTA, "sombra_estado.json")
SOMBRA_REGISTRO = os.path.join(PASTA, "sombra_registro.csv")
TEMPLATE = os.path.join(PASTA, "escritorio_omega_template.html")
SAIDA = os.path.join(PASTA, "escritorio_omega.html")

# mesmo capital de referência que a Fase 4 usa (sombra.CAPITAL) -- duplicado
# aqui em vez de importado porque importar sombra.py puxaria o pacote binance
# inteiro só pra ler uma constante.
CAPITAL_REFERENCIA = 500


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


def avaliar_guardiao() -> dict:
    """Chama as funções REAIS de guardrails.py com o estado atual -- não
    inventa nada. Só as travas que têm um "estado parado" fazem sentido aqui
    (kill switch, dado velho, exposição agregada); teto por ordem, preço
    absurdo e disjuntor só existem no momento de avaliar UMA ordem específica,
    que não existe fora da Fase 5."""
    v_kill = guardrails.kill_switch()

    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    with open(MONITOR_ESTADO, encoding="utf-8") as f:
        atualizado_em = json.load(f)["atualizado_em"]
    ultimo = datetime.fromisoformat(atualizado_em)
    v_dado = guardrails.dado_fresco(ultimo, agora)

    valor_posicoes = 0.0
    try:
        with open(SOMBRA_ESTADO, encoding="utf-8") as f:
            posicoes = json.load(f).get("posicoes", {})
        valor_posicoes = sum(p.get("valor_usd", 0) for p in posicoes.values())
    except Exception:
        pass
    v_exp = guardrails.exposicao_agregada(valor_posicoes, CAPITAL_REFERENCIA)

    checks = [v_kill, v_dado, v_exp]
    return {
        "parado": not v_kill.permitido,
        "ok": sum(1 for v in checks if v.permitido),
        "total": len(checks),
        "dado_fresco": v_dado.permitido,
        "exposicao_pct": round(float(valor_posicoes) / CAPITAL_REFERENCIA * 100, 1),
        "motivos": [v.motivo for v in checks if not v.permitido],
    }


def gerar_fala_guardiao(g: dict) -> str:
    if g["parado"]:
        return "<b>Kill switch ativo.</b> Nenhuma ação seria permitida agora."
    partes = [f"<b>{g['ok']}/{g['total']}</b> travas OK."]
    partes.append("Dado fresco." if g["dado_fresco"] else "Dado desatualizado — cuidado.")
    partes.append(f"Exposição: <b>{g['exposicao_pct']:.1f}%</b> do capital de referência.")
    if g["motivos"]:
        partes.append(" · ".join(g["motivos"]))
    return " ".join(partes)


def avaliar_reconciliador() -> dict:
    """Lê o PRÓPRIO registro da Sombra (Fase 4) -- quantas posições ela acha
    que tem abertas e quantas decisões já registrou. Isto NÃO é reconciliação
    contra a corretora de verdade: essa exige chave de API e só entra na Fase
    5. Aqui é só a Sombra conferindo a própria memória."""
    posicoes = 0
    try:
        with open(SOMBRA_ESTADO, encoding="utf-8") as f:
            posicoes = len(json.load(f).get("posicoes", {}))
    except Exception:
        pass

    decisoes = 0
    try:
        with open(SOMBRA_REGISTRO, encoding="utf-8") as f:
            decisoes = max(sum(1 for _ in f) - 1, 0)  # -1 do cabeçalho
    except Exception:
        pass

    return {"posicoes": posicoes, "decisoes": decisoes}


def gerar_fala_reconciliador(r: dict) -> str:
    base = (f"{r['posicoes']} posição(ões) no registro da Sombra agora. "
            f"{r['decisoes']} decisões registradas ao todo.")
    return base + " Reconciliação contra a corretora real entra na Fase 5."


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

    guardiao = avaliar_guardiao()
    reconciliador = avaliar_reconciliador()
    admin = {
        "guardiao": {
            "ok": guardiao["ok"], "total": guardiao["total"], "parado": guardiao["parado"],
            "exposicaoPct": guardiao["exposicao_pct"],
            "fala": gerar_fala_guardiao(guardiao),
        },
        "reconciliador": {
            "posicoes": reconciliador["posicoes"], "decisoes": reconciliador["decisoes"],
            "fala": gerar_fala_reconciliador(reconciliador),
        },
    }

    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()

    bloco = (
        "<script>\n"
        "window.OMEGA_MODO_LOCAL = true;\n"
        f"window.OMEGA_ESPECIALISTAS = {json.dumps(especialistas, ensure_ascii=False)};\n"
        f"window.OMEGA_RESUMO_AUTOMATICO = {json.dumps(resumo, ensure_ascii=False)};\n"
        f"window.OMEGA_ADMIN = {json.dumps(admin, ensure_ascii=False)};\n"
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
