# -*- coding: utf-8 -*-
import json
from datetime import datetime

import pytest

import sombra_analise as sa

CAB = ("data_hora,ativo,interval,acao,candle_sinal,preco_backtest,preco_agora,desvio_pct,"
       "atraso_h,qty,valor_usd,stop,status,motivo,exposicao_pct\n")


def _pasta(tmp_path, linhas, precos, log=""):
    (tmp_path / "sombra_registro.csv").write_text(CAB + "".join(linhas), encoding="utf-8")
    (tmp_path / "monitor_estado.json").write_text(json.dumps(
        {"sinais": {a: {"preco": p} for a, p in precos.items()}}), encoding="utf-8")
    (tmp_path / "monitor.log").write_text(log, encoding="utf-8")
    return str(tmp_path)


def test_sem_arquivos_devolve_none(tmp_path):
    assert sa.resumo(pasta=str(tmp_path)) is None


def test_pareia_compra_venda_e_desconta_custo(tmp_path):
    linhas = [
        "2026-09-01 08:06:00,BTCUSDT,6h,BUY,2026-09-01 06:00:00,100,101,1.0,2.1,1,50,90,registrada,,10\n",
        "2026-09-02 08:06:00,DOGEUSDT,8h,SELL,2026-09-02 08:00:00,1,1,0,0.1,,,,ignorada,sem posicao,\n",
        "2026-09-05 00:06:00,BTCUSDT,6h,SELL,2026-09-05 00:00:00,110,111.1,1.0,0.1,1,55,,registrada,,\n",
        "2026-09-06 00:06:00,PEPEUSDT,4h,BUY,2026-09-06 00:00:00,2,2,0,0.1,5,10,1.5,registrada,,12\n",
    ]
    r = sa.resumo(pasta=_pasta(tmp_path, linhas, {"PEPEUSDT": 3.0, "BTCUSDT": 120}))
    custo_btc = sa.custo_por_lado("BTCUSDT")
    (f,) = r["fechados"]
    assert f["resultado_pct"] == pytest.approx((111.1 / 101 - 1 - 2 * custo_btc) * 100)
    assert f["resultado_bt_pct"] == pytest.approx((110 / 100 - 1 - 2 * custo_btc) * 100)
    assert f["resultado_usd"] == pytest.approx(50 * (111.1 / 101 - 1 - 2 * custo_btc))
    (a,) = r["abertos"]
    assert a["ativo"] == "PEPEUSDT" and a["stop"] == 1.5
    assert a["resultado_pct"] == pytest.approx((3.0 / 2 - 1 - 2 * sa.custo_por_lado("PEPEUSDT")) * 100)
    assert r["resultado_usd"] == pytest.approx(f["resultado_usd"] + a["resultado_usd"])
    assert r["acertos"] == 1
    assert len(r["registro"]) == 3  # a 'ignorada' fica de fora


def test_custo_de_execucao_tem_sinal_correto():
    import pandas as pd
    reg = pd.DataFrame({
        "acao": ["BUY", "SELL"], "desvio_pct": [1.0, 1.0],
        "atraso_h": [0.1, 5.0], "exposicao_pct": [10.0, None],
    })
    q = sa.qualidade_execucao(reg)
    # comprar 1% mais caro custa; vender 1% mais caro ajuda -> se anulam
    assert q["custo_execucao_medio_pct"] == pytest.approx(0.0)
    assert q["custo_execucao_no_horario_pct"] == pytest.approx(1.0)
    assert q["atrasadas"] == 1


def test_disponibilidade_conta_buracos(tmp_path):
    log = "".join(
        f"[2026-09-01 {h:02d}:06:00] [2026-09-01 {h:02d}:05:30] sombra: 22 ativos lidos, 0 falha(s)\n"
        for h in (10, 11, 15)
    )
    p = _pasta(tmp_path, [], {}, log)
    d = sa.disponibilidade(p + "/monitor.log", agora=datetime(2026, 9, 1, 15, 30))
    assert d["esperadas"] == 6 and d["rodadas"] == 3
    assert d["buracos"] == [{"de": datetime(2026, 9, 1, 12), "ate": datetime(2026, 9, 1, 14), "horas": 3}]
    # antes do minuto 7 a hora corrente ainda não conta
    d2 = sa.disponibilidade(p + "/monitor.log", agora=datetime(2026, 9, 1, 16, 3))
    assert d2["esperadas"] == 6
