# -*- coding: utf-8 -*-
"""Testes dos guardrails (guardrails.py).

Nenhum toca a rede — o módulo é puro de propósito. Cada trava tem teste do caso
que ela libera E do caso que ela bloqueia; travas que só bloqueiam seriam
inúteis (o bot nunca operaria) e travas que só liberam seriam decorativas.
"""
import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from guardrails import (
    MAX_ATRASO_HORAS, Contexto, Veredito,
    kill_switch, dado_fresco, preco_sensato, ordem_dentro_do_teto,
    perda_diaria, disjuntor, exposicao_agregada, avaliar,
    carregar_estado, salvar_estado, registrar_erro, registrar_sucesso,
    referencia_do_dia,
)

AGORA = datetime(2026, 9, 8, 12, 0, 0)


# ---------------------------------------------------------------------------
# kill switch
# ---------------------------------------------------------------------------
def test_sem_arquivo_parar_opera(tmp_path):
    assert kill_switch(str(tmp_path / "PARAR"))


def test_arquivo_parar_bloqueia(tmp_path):
    p = tmp_path / "PARAR"
    p.write_text("")
    v = kill_switch(str(p))
    assert not v and v.guardrail == "kill_switch"


def test_kill_switch_e_reversivel(tmp_path):
    """Apagar o arquivo destrava — sem reiniciar nada."""
    p = tmp_path / "PARAR"
    p.write_text("")
    assert not kill_switch(str(p))
    p.unlink()
    assert kill_switch(str(p))


def test_arquivo_vazio_tambem_bloqueia(tmp_path):
    """O que importa é existir, não o conteúdo: `touch PARAR` tem que bastar."""
    p = tmp_path / "PARAR"
    p.write_text("")
    assert not kill_switch(str(p))


# ---------------------------------------------------------------------------
# dado velho
# ---------------------------------------------------------------------------
def test_candle_recente_passa():
    assert dado_fresco(AGORA - timedelta(hours=2), AGORA)


def test_candle_velho_bloqueia():
    v = dado_fresco(AGORA - timedelta(hours=48), AGORA)
    assert not v and v.guardrail == "dado_velho"


def test_limite_e_o_mesmo_do_monitor():
    """Se alguém mudar o limite no monitor, o executor acompanha — não pode
    haver dois números diferentes para a mesma regra."""
    assert MAX_ATRASO_HORAS == 30
    assert dado_fresco(AGORA - timedelta(hours=MAX_ATRASO_HORAS - 1), AGORA)
    assert not dado_fresco(AGORA - timedelta(hours=MAX_ATRASO_HORAS + 1), AGORA)


# ---------------------------------------------------------------------------
# preço
# ---------------------------------------------------------------------------
def test_preco_proximo_passa():
    assert preco_sensato(50000, 50500)


def test_volatilidade_normal_nao_bloqueia():
    """Cripto oscila; a trava é contra absurdo, não contra mercado."""
    assert preco_sensato(52000, 50000)      # +4%


def test_preco_absurdo_bloqueia():
    v = preco_sensato(500000, 50000)        # 10x
    assert not v and v.guardrail == "preco_absurdo"


@pytest.mark.parametrize("preco,ref", [(0, 50000), (-1, 50000), (50000, 0)])
def test_precos_invalidos_bloqueiam(preco, ref):
    assert not preco_sensato(preco, ref)


# ---------------------------------------------------------------------------
# teto por ordem
# ---------------------------------------------------------------------------
def test_ordem_dentro_do_teto_passa():
    assert ordem_dentro_do_teto(50, 100)


def test_ordem_acima_do_teto_bloqueia():
    """O caso de um bug multiplicar o tamanho por engano."""
    v = ordem_dentro_do_teto(10_000, 100)
    assert not v and v.guardrail == "ordem_grande"


def test_valor_exatamente_no_teto_passa():
    assert ordem_dentro_do_teto(100, 100)


# ---------------------------------------------------------------------------
# perda diária
# ---------------------------------------------------------------------------
def test_dia_no_lucro_passa():
    assert perda_diaria(1100, 1000)


def test_perda_pequena_passa():
    assert perda_diaria(980, 1000)          # -2%, limite 5%


def test_perda_grande_bloqueia():
    v = perda_diaria(900, 1000)             # -10%
    assert not v and v.guardrail == "perda_diaria"


def test_sem_referencia_nao_bloqueia():
    """Sem base de comparação não dá pra afirmar que houve perda — travar aí
    seria bloquear o bot por falta de dado, não por risco."""
    assert perda_diaria(900, 0)


# ---------------------------------------------------------------------------
# disjuntor
# ---------------------------------------------------------------------------
def test_falha_isolada_nao_para():
    """Timeout acontece; parar na primeira seria frágil demais."""
    assert disjuntor(1)


def test_falhas_seguidas_param():
    v = disjuntor(3)
    assert not v and v.guardrail == "disjuntor"


def test_sucesso_zera_o_contador():
    estado = registrar_erro(registrar_erro({"erros_seguidos": 0}))
    assert estado["erros_seguidos"] == 2
    assert registrar_sucesso(estado)["erros_seguidos"] == 0
    assert disjuntor(0)


# ---------------------------------------------------------------------------
# exposição agregada
# ---------------------------------------------------------------------------
def test_exposicao_parcial_passa():
    assert exposicao_agregada(500, 1000)


def test_exposicao_total_passa():
    """100% investido ainda é Spot."""
    assert exposicao_agregada(1000, 1000)


def test_exposicao_alavancada_bloqueia():
    """O achado do riqueza_terminal: 1.5x em BULL exigia margem. Esta trava é o
    que garante que a operação continua Spot."""
    v = exposicao_agregada(1500, 1000)
    assert not v and v.guardrail == "exposicao"
    assert "margem" in v.motivo


def test_capital_invalido_bloqueia():
    assert not exposicao_agregada(100, 0)


# ---------------------------------------------------------------------------
# avaliação conjunta
# ---------------------------------------------------------------------------
def contexto_saudavel(**kw):
    base = dict(
        t_ultimo_candle=AGORA - timedelta(hours=1), agora=AGORA,
        preco=Decimal("50000"), preco_referencia=Decimal("50000"),
        valor_ordem=Decimal("50"), teto_por_ordem=Decimal("100"),
        equity_atual=Decimal("1000"), equity_inicio_do_dia=Decimal("1000"),
        erros_seguidos=0,
        valor_posicoes=Decimal("500"), capital=Decimal("1000"),
        caminho_parar="/caminho/que/nao/existe/PARAR",
    )
    base.update(kw)
    return Contexto(**base)


def test_contexto_saudavel_libera():
    assert avaliar(contexto_saudavel())


def test_kill_switch_tem_prioridade(tmp_path):
    """Mesmo com tudo mais quebrado, o motivo reportado é o kill switch — é a
    informação que o usuário precisa ver primeiro."""
    p = tmp_path / "PARAR"
    p.write_text("")
    v = avaliar(contexto_saudavel(caminho_parar=str(p), erros_seguidos=99,
                                  equity_atual=Decimal("1")))
    assert not v and v.guardrail == "kill_switch"


@pytest.mark.parametrize("mudanca,esperado", [
    ({"erros_seguidos": 5}, "disjuntor"),
    ({"equity_atual": Decimal("800")}, "perda_diaria"),
    ({"t_ultimo_candle": AGORA - timedelta(hours=99)}, "dado_velho"),
    ({"preco": Decimal("5")}, "preco_absurdo"),
    ({"valor_ordem": Decimal("99999")}, "ordem_grande"),
    ({"valor_posicoes": Decimal("2000")}, "exposicao"),
])
def test_cada_trava_bloqueia_no_conjunto(mudanca, esperado):
    v = avaliar(contexto_saudavel(**mudanca))
    assert not v and v.guardrail == esperado


def test_campos_ausentes_pulam_a_trava():
    """O chamador oferece o que tem; o que faltar não inventa bloqueio."""
    assert avaliar(Contexto(caminho_parar="/nao/existe/PARAR"))


def test_veredito_e_booleano():
    assert bool(Veredito(True)) is True
    assert bool(Veredito(False, "x", "y")) is False


# ---------------------------------------------------------------------------
# estado persistido
# ---------------------------------------------------------------------------
def test_estado_ausente_devolve_padrao(tmp_path):
    e = carregar_estado(str(tmp_path / "nao_existe.json"))
    assert e["erros_seguidos"] == 0


def test_estado_corrompido_nao_quebra(tmp_path):
    """Arquivo corrompido não pode derrubar o bot — cai no padrão."""
    p = tmp_path / "estado.json"
    p.write_text("{lixo nao json")
    assert carregar_estado(str(p))["erros_seguidos"] == 0


def test_salva_e_recarrega(tmp_path):
    p = str(tmp_path / "estado.json")
    salvar_estado({"erros_seguidos": 2, "dia": "2026-09-08"}, p)
    assert carregar_estado(p)["erros_seguidos"] == 2


def test_escrita_nao_deixa_temporario(tmp_path):
    p = str(tmp_path / "estado.json")
    salvar_estado({"erros_seguidos": 0}, p)
    assert not os.path.exists(p + ".tmp")


def test_referencia_do_dia_fixa_na_primeira_vez():
    e = referencia_do_dia({}, 1000, date(2026, 9, 8))
    assert e["equity_inicio_do_dia"] == "1000"


def test_referencia_nao_muda_no_mesmo_dia():
    """Se a referência acompanhasse o equity, a trava de perda diária nunca
    dispararia — ela mediria sempre zero."""
    e = referencia_do_dia({}, 1000, date(2026, 9, 8))
    e = referencia_do_dia(e, 900, date(2026, 9, 8))
    assert e["equity_inicio_do_dia"] == "1000"


def test_novo_dia_renova_a_referencia():
    """Sem isso, uma perda de ontem travaria o bot para sempre."""
    e = referencia_do_dia({}, 1000, date(2026, 9, 8))
    e = referencia_do_dia(e, 900, date(2026, 9, 9))
    assert e["equity_inicio_do_dia"] == "900"
    assert perda_diaria(900, Decimal(e["equity_inicio_do_dia"]))
