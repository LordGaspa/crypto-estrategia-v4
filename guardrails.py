# -*- coding: utf-8 -*-
"""Guardrails do executor (Fase 3 do PLANO_EXECUCAO_AUTOMATICA.md).

Sete travas. Nenhuma é teórica: cada uma responde a uma falha que já aconteceu
neste projeto ou que a Fase 1/2 mostrou ser possível.

    kill switch ......... você desliga tudo em 5 segundos, de qualquer lugar
    dado velho .......... a mesma trava do monitor (cache desatualizado)
    preço absurdo ....... dado corrompido / erro de API
    ordem grande demais . bug de cálculo mandando 100x o pretendido
    perda diária ........ espiral de prejuízo num dia ruim
    disjuntor ........... falha em loop consumindo capital
    exposição agregada .. garante que continua Spot, sem margem acidental

**Este módulo não faz chamada de rede.** Recebe números prontos e devolve um
veredito. Isso é deliberado: guardrail que depende de I/O é guardrail que falha
junto com o que ele deveria proteger — e, do jeito que está, dá para testar
todos os caminhos sem tocar a corretora.

Regra geral: **na dúvida, não opera.** Perder uma entrada custa uma
oportunidade; operar com dado errado custa dinheiro.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

# Fonte única da verdade do limite de atraso — o mesmo que o monitor já usa.
# Importar em vez de redeclarar: dois números iguais em arquivos diferentes
# viram dois números diferentes na primeira vez que alguém mexe num só.
from monitor_sinais import MAX_ATRASO_HORAS

PASTA = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_PARAR = os.path.join(PASTA, "PARAR")
ESTADO_ARQUIVO = os.path.join(PASTA, "guardrails_estado.json")

# --- limites padrão ---------------------------------------------------------
DESVIO_PRECO_MAX = Decimal("0.10")     # 10% entre o preço da API e a referência
PERDA_DIARIA_MAX = Decimal("0.05")     # 5% do capital num único dia
ERROS_SEGUIDOS_MAX = 3                 # falhas consecutivas antes de parar
EXPOSICAO_MAX = Decimal("1.0")         # 100% do capital: Spot, sem margem


@dataclass(frozen=True)
class Veredito:
    permitido: bool
    guardrail: str = ""
    motivo: str = ""

    def __bool__(self) -> bool:
        return self.permitido


LIBERADO = Veredito(True)


# ---------------------------------------------------------------------------
# travas individuais
# ---------------------------------------------------------------------------
def kill_switch(caminho: str = ARQUIVO_PARAR) -> Veredito:
    """Arquivo `PARAR` na pasta do projeto => o bot não opera.

    Um arquivo, e não uma variável de ambiente ou uma flag no código, porque
    precisa ser acionável no susto: `touch PARAR` por SSH, pelo celular, sem
    reiniciar nada e sem esperar deploy. Some o arquivo, volta a operar."""
    if os.path.exists(caminho):
        return Veredito(False, "kill_switch",
                        f"arquivo {os.path.basename(caminho)} presente — operação "
                        f"suspensa manualmente. Apague o arquivo para retomar.")
    return LIBERADO


def dado_fresco(t_ultimo_candle: datetime, agora: Optional[datetime] = None,
                max_horas: float = MAX_ATRASO_HORAS) -> Veredito:
    """Candle velho => não opera. Mesma trava do `monitor_sinais`.

    O `carregar_dados` cai pro cache antigo quando a Binance falha, para o
    painel não quebrar. Num executor isso é perigoso: decidiria comprar hoje
    com o preço de anteontem."""
    agora = agora or datetime.now(timezone.utc).replace(tzinfo=None)
    atraso = (agora - t_ultimo_candle).total_seconds() / 3600
    if atraso > max_horas:
        return Veredito(False, "dado_velho",
                        f"último candle tem {atraso:.0f}h de atraso "
                        f"(limite: {max_horas:.0f}h)")
    return LIBERADO


def preco_sensato(preco, referencia, desvio_max: Decimal = DESVIO_PRECO_MAX) -> Veredito:
    """Preço da corretora muito longe do preço do candle => não opera.

    Pega dado corrompido, par errado e resposta malformada da API. O limite é
    folgado (10%) de propósito: cripto se move mesmo. A trava é contra o
    absurdo — um preço 10x fora —, não contra volatilidade normal."""
    p, ref = Decimal(str(preco)), Decimal(str(referencia))
    if p <= 0 or ref <= 0:
        return Veredito(False, "preco_absurdo", f"preço inválido (ordem={p}, referência={ref})")

    desvio = abs(p - ref) / ref
    if desvio > desvio_max:
        return Veredito(False, "preco_absurdo",
                        f"preço {p} está {desvio * 100:.1f}% longe da referência {ref} "
                        f"(limite: {desvio_max * 100:.0f}%)")
    return LIBERADO


def ordem_dentro_do_teto(valor_usd, teto_usd) -> Veredito:
    """Ordem acima do teto => não opera. Contra erro de cálculo de tamanho.

    Um bug que multiplique o valor por 100 não deve conseguir gastar a conta
    inteira antes de alguém perceber."""
    v, teto = Decimal(str(valor_usd)), Decimal(str(teto_usd))
    if v > teto:
        return Veredito(False, "ordem_grande",
                        f"ordem de US$ {v:.2f} acima do teto de US$ {teto:.2f} por ordem")
    return LIBERADO


def perda_diaria(equity_atual, equity_inicio_do_dia,
                 limite: Decimal = PERDA_DIARIA_MAX) -> Veredito:
    """Perda acumulada no dia acima do limite => para tudo até amanhã.

    Não é gestão de risco da estratégia (isso é o stop ATR) — é um freio contra
    um dia patológico: bug, flash crash, sequência de stops."""
    atual, inicio = Decimal(str(equity_atual)), Decimal(str(equity_inicio_do_dia))
    if inicio <= 0:
        return LIBERADO                      # sem referência, não dá pra julgar

    queda = (inicio - atual) / inicio
    if queda > limite:
        return Veredito(False, "perda_diaria",
                        f"queda de {queda * 100:.1f}% hoje (de US$ {inicio:.2f} para "
                        f"US$ {atual:.2f}) passou do limite de {limite * 100:.0f}%. "
                        f"Operação suspensa até o próximo dia.")
    return LIBERADO


def disjuntor(erros_seguidos: int, limite: int = ERROS_SEGUIDOS_MAX) -> Veredito:
    """N falhas consecutivas => para e avisa.

    Falha isolada é normal (rede, timeout). Falha em sequência é sintoma: chave
    revogada, IP fora da allowlist, API mudada. Insistir nesse estado só queima
    tentativas — e, no pior caso, dinheiro."""
    if erros_seguidos >= limite:
        return Veredito(False, "disjuntor",
                        f"{erros_seguidos} falhas seguidas (limite: {limite}). "
                        f"Parando até intervenção manual.")
    return LIBERADO


def exposicao_agregada(valor_posicoes, capital, teto: Decimal = EXPOSICAO_MAX) -> Veredito:
    """Soma das posições acima de 100% do capital => não abre mais nada.

    Esta existe por causa de um achado do próprio projeto: o modo Agressivo
    parecia render mais, mas passava de 100% do capital em 28,4% do tempo — ou
    seja, o ganho era ALAVANCAGEM, não estratégia (ver
    `riqueza_terminal_v6_exposicao.py` e `RELATORIO_RIQUEZA_TERMINAL.md`).
    A trava garante que a operação continua sendo Spot de verdade."""
    posicoes, cap = Decimal(str(valor_posicoes)), Decimal(str(capital))
    if cap <= 0:
        return Veredito(False, "exposicao", "capital de referência inválido (<= 0)")

    uso = posicoes / cap
    if uso > teto:
        return Veredito(False, "exposicao",
                        f"exposição de {uso * 100:.1f}% do capital passa do teto de "
                        f"{teto * 100:.0f}%. Abrir mais exigiria margem — isto é Spot.")
    return LIBERADO


# ---------------------------------------------------------------------------
# avaliação conjunta
# ---------------------------------------------------------------------------
@dataclass
class Contexto:
    """Números que o chamador já tem em mãos. Nada aqui vem de rede."""
    t_ultimo_candle: Optional[datetime] = None
    agora: Optional[datetime] = None
    preco: Optional[Decimal] = None
    preco_referencia: Optional[Decimal] = None
    valor_ordem: Optional[Decimal] = None
    teto_por_ordem: Optional[Decimal] = None
    equity_atual: Optional[Decimal] = None
    equity_inicio_do_dia: Optional[Decimal] = None
    erros_seguidos: int = 0
    valor_posicoes: Optional[Decimal] = None
    capital: Optional[Decimal] = None
    caminho_parar: str = ARQUIVO_PARAR


def avaliar(ctx: Contexto) -> Veredito:
    """Roda as travas aplicáveis e devolve a PRIMEIRA que bloquear.

    A ordem é proposital: as mais baratas e mais graves primeiro (kill switch
    antes de qualquer coisa). Campos não preenchidos no contexto pulam a trava
    correspondente — o chamador decide o que tem para oferecer."""
    v = kill_switch(ctx.caminho_parar)
    if not v:
        return v

    if ctx.erros_seguidos:
        v = disjuntor(ctx.erros_seguidos)
        if not v:
            return v

    if ctx.equity_atual is not None and ctx.equity_inicio_do_dia is not None:
        v = perda_diaria(ctx.equity_atual, ctx.equity_inicio_do_dia)
        if not v:
            return v

    if ctx.t_ultimo_candle is not None:
        v = dado_fresco(ctx.t_ultimo_candle, ctx.agora)
        if not v:
            return v

    if ctx.preco is not None and ctx.preco_referencia is not None:
        v = preco_sensato(ctx.preco, ctx.preco_referencia)
        if not v:
            return v

    if ctx.valor_ordem is not None and ctx.teto_por_ordem is not None:
        v = ordem_dentro_do_teto(ctx.valor_ordem, ctx.teto_por_ordem)
        if not v:
            return v

    if ctx.valor_posicoes is not None and ctx.capital is not None:
        v = exposicao_agregada(ctx.valor_posicoes, ctx.capital)
        if not v:
            return v

    return LIBERADO


# ---------------------------------------------------------------------------
# estado persistido (contador de erros + referência do dia)
# ---------------------------------------------------------------------------
def carregar_estado(caminho: str = ESTADO_ARQUIVO) -> dict:
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"erros_seguidos": 0, "dia": None, "equity_inicio_do_dia": None}


def salvar_estado(estado: dict, caminho: str = ESTADO_ARQUIVO) -> None:
    """Escrita atômica — mesmo padrão do `monitor_sinais`: se o processo morrer
    no meio, o arquivo antigo continua íntegro em vez de virar lixo."""
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)


def registrar_erro(estado: dict) -> dict:
    estado["erros_seguidos"] = estado.get("erros_seguidos", 0) + 1
    return estado


def registrar_sucesso(estado: dict) -> dict:
    """Zera o contador: o disjuntor conta falhas CONSECUTIVAS."""
    estado["erros_seguidos"] = 0
    return estado


def referencia_do_dia(estado: dict, equity_atual, hoje: Optional[date] = None) -> dict:
    """Fixa o equity de abertura do dia, usado pela trava de perda diária.

    Vira o dia => nova referência. Sem isso, uma perda antiga seguiria travando
    o bot para sempre."""
    hoje = hoje or datetime.now(timezone.utc).date()
    if estado.get("dia") != hoje.isoformat():
        estado["dia"] = hoje.isoformat()
        estado["equity_inicio_do_dia"] = str(equity_atual)
    return estado
