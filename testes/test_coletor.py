"""Testes do coletor.py sem rede: as funcoes de busca sao trocadas por stubs.

Rodar, a partir da raiz do repositorio:  python -m unittest discover -s testes -v
"""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import coletor  # noqa: E402

# funcoes reais, guardadas antes dos stubs do setUp (os testes de parser usam estas)
_ler_dmh_real = coletor.ler_dmh
_grade_historico_real = coletor._grade_chn6_historico
ALVO_VALLEMI = next(a["url"] for a in coletor.ALVOS_DMH if a["estacao"] == "Vallemi PY")

UM_DIA = datetime.timedelta(days=1)
AVISO_TXT = ("HI 0049/26 - RIO PARAGUAI - ALTERADA A SINALIZACAO. "
             "Paraguai km 1234,5 - AVISO DE TESTE.")
VALORES_ANTERIORES = {"Cuiabá": 0.95, "Cáceres": 1.30, "Bela Vista Norte": 0.70, "Ladário": 0.90,
                      "F. Coimbra": 1.10, "P. Murtinho": 2.40, "Vallemi PY": 2.40,
                      "Concepción PY": 1.95, "Assunção PY": 2.35}


def sem_atual(reg):
    return {k: v for k, v in reg.items() if k != "atual"}


class ColetorTeste(unittest.TestCase):

    def setUp(self):
        self.hoje = datetime.datetime.now(coletor.TZ_LOCAL).date()
        self.ontem = self.hoje - UM_DIA
        coletor.dados_nav.clear()
        coletor._avisos_fontes.update({"bdarn": None, "avrad": None})
        # cenario padrao: BDARN e revisao respondem; DMH responde nas 3 paginas paraguaias
        self.bdarn_ok = True
        self.av_ok = True
        self.texto_aviso = AVISO_TXT
        self.lidos_bdarn = {"Cuiabá": (0.86, "DESCENDO", 0.09), "Cáceres": (1.18, "DESCENDO", 0.05),
                            "Ladário": (0.82, "DESCENDO", 0.03), "P. Murtinho": (2.27, "DESCENDO", 0.02)}
        self.dmh = {"Vallemi PY": self._dmh(2.33, self.hoje, -0.04),
                    "Concepción PY": self._dmh(1.89, self.hoje, -0.03),
                    "Assunção PY": self._dmh(2.30, self.hoje, -0.03)}
        # grade mensal do CHN-6: nome do arquivo -> {data ISO: valor} (ou excecao); ausente = sem PDF
        self.grade = {}
        self.chamadas_grade = []
        for nome, fn in [("_baixar_bdarn", self._fake_bdarn), ("ler_bdarn", self._fake_ler_bdarn),
                         ("_ler_avisos_pdf", self._fake_avisos),
                         ("_baixar_avurnav_mais_novo", self._fake_avurnav), ("ler_dmh", self._fake_dmh),
                         ("_achar_grade_chn6", self._fake_achar_grade),
                         ("_grade_chn6_historico", self._fake_hist_grade)]:
            p = mock.patch.object(coletor, nome, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    # ---- stubs -------------------------------------------------------------
    def _fake_bdarn(self, dia, camuflagem):
        return (b"%PDF-falso", "BDARN_TESTE.pdf") if self.bdarn_ok else (None, None)

    def _fake_ler_bdarn(self, pdf, hoje):
        return dict(self.lidos_bdarn)

    def _fake_avisos(self, pdf):
        return {"0049/26"}, {"0049/26": self.texto_aviso}

    def _fake_avurnav(self):
        return (b"%PDF-falso", "07/11/2026", self.hoje - UM_DIA) if self.av_ok else (None, "", None)

    def _fake_dmh(self, url):
        est = next(a["estacao"] for a in coletor.ALVOS_DMH if a["url"] == url)
        r = self.dmh.get(est)
        if isinstance(r, Exception):
            raise r
        return r

    def _fake_achar_grade(self, nome_arq, hoje, camuflagem):
        self.chamadas_grade.append(nome_arq)
        val = self.grade.get(nome_arq)
        if isinstance(val, Exception):
            raise val
        return nome_arq.encode() if val is not None else None

    def _fake_hist_grade(self, pdf, hoje):
        return dict(self.grade[pdf.decode()])

    # ---- auxiliares --------------------------------------------------------
    def _dmh(self, nivel, data, variacao):
        return {"nivel": nivel, "data": data.isoformat(), "hoje": True, "variacao": variacao, "data_ref": None}

    def reg_ant(self, nome, dias=3, data=None, fonte="BDARN CHN-6"):
        """Registro do hidro.json anterior, com serie de `dias` datas terminando em `data`."""
        data = data or self.ontem
        valor = VALORES_ANTERIORES[nome]
        serie = [{"data": (data - datetime.timedelta(days=i)).isoformat(), "valor_m": valor}
                 for i in reversed(range(dias))]
        return {"nome": nome, "valor_m": valor, "data_leitura": data.isoformat(), "fonte": fonte,
                "variacao_24h": -0.01, "serie": serie}

    def anterior(self, nomes=None, dias=3, avurnav=None, avurnav_de=None):
        nomes = nomes or list(VALORES_ANTERIORES)
        return {"gerado_em_utc": "2026-11-07T14:00:00Z", "gerado_em_local": "2026-11-07T10:00:00-04:00",
                "estacoes": [self.reg_ant(n, dias=dias) for n in nomes],
                "avurnav": avurnav or [], "avurnav_de": avurnav_de, "fontes": {}}

    def rodar(self, anterior):
        with contextlib.redirect_stdout(io.StringIO()):
            return coletor.coletar(anterior)

    # ---- testes ------------------------------------------------------------
    def test_a_rodada_normal_sem_anterior(self):
        d = self.rodar(None)
        est = {e["nome"]: e for e in d["estacoes"]}
        self.assertEqual(set(est), {"Cuiabá", "Cáceres", "Ladário", "P. Murtinho",
                                    "Vallemi PY", "Concepción PY", "Assunção PY"})
        self.assertTrue(all(e["atual"] for e in est.values()))
        self.assertEqual(est["Cuiabá"]["valor_m"], 0.86)
        self.assertEqual(est["Cuiabá"]["serie"], [{"data": self.hoje.isoformat(), "valor_m": 0.86}])
        self.assertTrue(d["fontes"]["BDARN"]["ok"])
        self.assertTrue(d["fontes"]["AVURNAV"]["ok"])
        self.assertEqual(d["avurnav_de"], self.hoje.isoformat())
        self.assertEqual(d["avurnav"][0]["numero"], "HI 0049/26")

    def test_b_bdarn_falha_mantem_as_estacoes_do_bdarn(self):
        self.bdarn_ok = False
        ant = self.anterior()
        antigo = {e["nome"]: e for e in ant["estacoes"]}
        est = {e["nome"]: e for e in self.rodar(ant)["estacoes"]}
        for n in ["Cuiabá", "Cáceres", "Ladário", "P. Murtinho"]:
            self.assertFalse(est[n]["atual"], n)
            self.assertEqual(sem_atual(est[n]), sem_atual(antigo[n]), n)
        for n in ["Vallemi PY", "Concepción PY", "Assunção PY"]:
            self.assertTrue(est[n]["atual"], n)
            self.assertEqual(est[n]["data_leitura"], self.hoje.isoformat(), n)
        self.assertFalse(self.rodar(ant)["fontes"]["BDARN"]["ok"])

    def test_c_aguardando_em_uma_estacao_so_ela_fica_mantida(self):
        del self.lidos_bdarn["Cáceres"]          # BDARN com AGUARDANDO: o leitor nao devolve a estacao
        ant = self.anterior()
        antigo = {e["nome"]: e for e in ant["estacoes"]}
        d = self.rodar(ant)
        est = {e["nome"]: e for e in d["estacoes"]}
        self.assertFalse(est["Cáceres"]["atual"])
        self.assertEqual(sem_atual(est["Cáceres"]), sem_atual(antigo["Cáceres"]))
        for n in ["Cuiabá", "Ladário", "P. Murtinho", "Vallemi PY"]:
            self.assertTrue(est[n]["atual"], n)
        self.assertFalse(est["Bela Vista Norte"]["atual"])   # sem valor nesta rodada, mas tinha antes
        self.assertTrue(d["fontes"]["BDARN"]["ok"])

    def test_d_avurnav_falha_mantem_lista_anterior(self):
        self.av_ok = False
        lista = [{"numero": "HI 0001/26", "texto": "aviso antigo", "sistema": "CUIABA", "km": 10.0, "coord": None}]
        ant = self.anterior(avurnav=lista, avurnav_de="2026-10-15")
        d = self.rodar(ant)
        self.assertEqual(d["avurnav"], lista)
        self.assertEqual(d["avurnav_de"], "2026-10-15")
        self.assertFalse(d["fontes"]["AVURNAV"]["ok"])
        self.assertIn("lista anterior mantida", d["fontes"]["AVURNAV"]["msg"])
        self.assertTrue(d["fontes"]["BDARN"]["ok"])

    def test_avisos_geo_sai_com_as_posicoes_do_aviso(self):
        self.texto_aviso = AVISO_TXT + " UNO – PONTO TESTE: LAT. 19º35'54\"S LONG. 057º26'29\"W."
        d = self.rodar(None)
        self.assertEqual(list(d["avisos_geo"]), ["HI 0049/26"])
        g = d["avisos_geo"]["HI 0049/26"][0]
        self.assertEqual(g["tipo"], "PONTO")
        self.assertEqual(g["coords"], [[-19.59833, -57.44139]])
        self.assertLessEqual(len(g["nome"]), 90)

    def test_parser_que_falha_nao_derruba_a_coleta(self):
        self.texto_aviso = AVISO_TXT + " LAT. 19º35'54\"S LONG. 057º26'29\"W."
        with mock.patch.object(coletor, "extrair_avisos_geo", side_effect=ValueError("teste")):
            d = self.rodar(None)
        self.assertEqual(d["avisos_geo"], {})
        self.assertEqual(len(d["avurnav"]), 1)
        self.assertTrue(d["fontes"]["AVURNAV"]["ok"])

    def test_avurnav_falha_mantem_avisos_geo_anterior(self):
        self.av_ok = False
        geo = {"HI 0001/26": [{"nome": "UNO", "tipo": "PONTO", "coords": [[-19.0, -57.0]]}]}
        ant = self.anterior(avurnav=[{"numero": "HI 0001/26", "texto": "x", "sistema": "CUIABA",
                                      "km": 10.0, "coord": None}], avurnav_de="2026-10-15")
        ant["avisos_geo"] = geo
        d = self.rodar(ant)
        self.assertEqual(d["avisos_geo"], geo)

    def test_e_tudo_falha_com_anterior_sai_zero_e_nada_se_perde(self):
        self.bdarn_ok = False
        self.av_ok = False
        self.dmh = {}                              # todas as paginas DMH sem tabela
        lista = [{"numero": "HI 0001/26", "texto": "aviso antigo", "sistema": "CUIABA", "km": 10.0, "coord": None}]
        ant = self.anterior(avurnav=lista, avurnav_de="2026-10-15")
        antigo = {e["nome"]: e for e in ant["estacoes"]}
        with tempfile.TemporaryDirectory() as tmp:
            caminho = os.path.join(tmp, "hidro.json")
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(ant, f, ensure_ascii=False)
            with contextlib.redirect_stdout(io.StringIO()):
                codigo = coletor.main(["coletor.py", caminho])
            with open(caminho, encoding="utf-8") as f:
                novo = json.load(f)
        self.assertEqual(codigo, 0)
        self.assertEqual({e["nome"] for e in novo["estacoes"]}, set(antigo))
        for e in novo["estacoes"]:
            self.assertFalse(e["atual"], e["nome"])
            self.assertEqual(sem_atual(e), sem_atual(antigo[e["nome"]]), e["nome"])
        self.assertEqual(novo["avurnav"], lista)
        self.assertEqual(novo["avurnav_de"], "2026-10-15")

    def test_f_tudo_falha_sem_anterior_sai_um(self):
        self.bdarn_ok = False
        self.av_ok = False
        self.dmh = {}
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                codigo = coletor.main(["coletor.py", os.path.join(tmp, "inexistente.json")])
        self.assertEqual(codigo, 1)

    def test_g_serie_um_valor_por_data_e_sete_datas(self):
        serie = []
        for i in range(8):                       # 8 dias seguidos
            dia = self.hoje - datetime.timedelta(days=7 - i)
            serie = coletor._serie_atualizada(serie, dia.isoformat(), 1.0 + i / 100)
        self.assertEqual(len(serie), 7)
        datas = [p["data"] for p in serie]
        self.assertEqual(datas, sorted(set(datas)))
        self.assertEqual(datas[0], (self.hoje - datetime.timedelta(days=6)).isoformat())
        self.assertEqual(datas[-1], self.hoje.isoformat())
        serie = coletor._serie_atualizada(serie, datas[-1], 9.99)   # mesma data: vale o valor novo
        self.assertEqual(len(serie), 7)
        self.assertEqual(serie[-1]["valor_m"], 9.99)

    def test_g2_serie_no_coletor_empurra_o_dia_mais_antigo(self):
        ant = self.anterior(dias=7)              # 7 datas terminando ontem
        est = {e["nome"]: e for e in self.rodar(ant)["estacoes"]}
        datas = [p["data"] for p in est["Cuiabá"]["serie"]]
        self.assertEqual(len(datas), 7)
        self.assertEqual(datas[0], (self.hoje - datetime.timedelta(days=6)).isoformat())
        self.assertEqual(datas[-1], self.hoje.isoformat())
        self.assertEqual(est["Cuiabá"]["serie"][-1]["valor_m"], 0.86)

    def test_h_leitura_mais_antiga_nao_substitui_a_guardada(self):
        ant = self.anterior(nomes=["Vallemi PY"])
        ant["estacoes"][0]["data_leitura"] = self.hoje.isoformat()
        ant["estacoes"][0]["valor_m"] = 9.9
        self.dmh["Vallemi PY"] = self._dmh(2.0, self.ontem, None)   # DMH devolve data mais antiga
        self.bdarn_ok = False
        est = {e["nome"]: e for e in self.rodar(ant)["estacoes"]}
        self.assertEqual(est["Vallemi PY"]["valor_m"], 9.9)
        self.assertFalse(est["Vallemi PY"]["atual"])

    def test_i_carregar_anterior_arquivo_invalido_ou_ausente(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruim = os.path.join(tmp, "ruim.json")
            with open(ruim, "w", encoding="utf-8") as f:
                f.write("{nao e json")
            self.assertIsNone(coletor.carregar_anterior(ruim))
            self.assertIsNone(coletor.carregar_anterior(os.path.join(tmp, "nao_existe.json")))

    def test_j_previa_curta_e_texto_completo_em_avisos_texto(self):
        longo = AVISO_TXT + " " + "trecho de teste " * 40 + "FIM."
        self.assertGreater(len(longo), 500)
        self.texto_aviso = longo
        d = self.rodar(None)
        previa = d["avurnav"][0]["texto"]
        self.assertLessEqual(len(previa), 201)
        self.assertEqual(previa, longo[:200] + "…")
        self.assertEqual(d["avisos_texto"], {"HI 0049/26": longo})

    def test_m_aviso_com_tres_sinais_tem_uma_entrada_em_avisos_texto(self):
        sinais = [("POSTE A", 1.0, "DESTRUIDO"), ("POSTE B", 2.5, "INOPERANTE"), ("POSTE C", 3.0, "INOPERANTE")]
        with mock.patch.object(coletor, "sinais_da_tabela", return_value=sinais):
            d = self.rodar(None)
        self.assertEqual(len(d["avurnav"]), 3)
        for a in d["avurnav"]:
            self.assertLessEqual(len(a["texto"]), 201)
        self.assertTrue(d["avurnav"][0]["texto"].startswith("DESTRUIDO: POSTE A (KM 1.0)."))
        self.assertEqual(d["avisos_texto"], {"HI 0049/26": AVISO_TXT})

    def test_k_verificado_muda_a_cada_rodada(self):
        t1 = datetime.datetime(2026, 11, 8, 10, 45, 0, tzinfo=datetime.timezone.utc)
        t2 = t1 + datetime.timedelta(minutes=15)
        with mock.patch.object(coletor, "_agora_utc", side_effect=[t1, t2]):
            d1 = self.rodar(None)
            d2 = self.rodar(json.loads(json.dumps(d1)))
        self.assertEqual(d1["verificado_em_utc"], "2026-11-08T10:45:00Z")
        self.assertEqual(d1["verificado_em_local"], "2026-11-08T06:45:00-04:00")
        self.assertEqual(d2["verificado_em_utc"], "2026-11-08T11:00:00Z")
        self.assertEqual(d2["verificado_em_local"], "2026-11-08T07:00:00-04:00")

    def test_l_gerado_em_so_muda_quando_os_dados_mudam(self):
        t1 = datetime.datetime(2026, 11, 8, 10, 45, 0, tzinfo=datetime.timezone.utc)
        t2 = t1 + datetime.timedelta(minutes=15)
        t3 = t2 + datetime.timedelta(minutes=15)
        with mock.patch.object(coletor, "_agora_utc", side_effect=[t1, t2, t3]):
            d1 = self.rodar(None)
            d2 = self.rodar(json.loads(json.dumps(d1)))          # mesmos dados
            self.lidos_bdarn["Cuiabá"] = (0.80, "DESCENDO", 0.06)  # um valor mudou
            d3 = self.rodar(json.loads(json.dumps(d2)))
        self.assertEqual(d1["gerado_em_utc"], "2026-11-08T10:45:00Z")
        self.assertEqual(d1["gerado_em_local"], "2026-11-08T06:45:00-04:00")
        # sem mudanca de dado: gerado_em fica, verificado_em anda
        self.assertEqual(d2["gerado_em_utc"], d1["gerado_em_utc"])
        self.assertEqual(d2["gerado_em_local"], d1["gerado_em_local"])
        self.assertEqual(d2["verificado_em_utc"], "2026-11-08T11:00:00Z")
        # com mudanca de dado: gerado_em e verificado_em sao da rodada que mudou
        self.assertEqual(d3["gerado_em_utc"], "2026-11-08T11:15:00Z")
        self.assertEqual(d3["gerado_em_local"], "2026-11-08T07:15:00-04:00")
        self.assertEqual(d3["verificado_em_utc"], "2026-11-08T11:15:00Z")

    def test_n_dmh_le_sete_datas_da_tabela(self):
        linhas = ""
        for i in range(9):
            data = (self.hoje - UM_DIA * i).strftime("%d-%m-%Y")
            valor = f"{2.0 - i / 10:.2f}".replace(".", ",")
            linhas += f"<tr><td>{data}</td><td>{valor} M</td></tr>"
        html = f"<html><body><table>{linhas}</table></body></html>"
        resp = mock.Mock(status_code=200, content=html.encode(), text=html)
        with mock.patch.object(coletor.requests, "get", return_value=resp):
            with contextlib.redirect_stdout(io.StringIO()):
                r = _ler_dmh_real(ALVO_VALLEMI)
        self.assertEqual(len(r["serie"]), 7)
        self.assertEqual([p["data"] for p in r["serie"]],
                         [(self.hoje - UM_DIA * i).isoformat() for i in range(7)])
        self.assertEqual(r["serie"][0]["valor_m"], 2.0)
        self.assertEqual(r["serie"][-1]["valor_m"], 1.4)
        self.assertEqual(r["nivel"], 2.0)
        self.assertAlmostEqual(r["variacao"], 0.1)

    def test_o_mescla_bdarn_tem_prioridade_na_mesma_data(self):
        ant = self.anterior(nomes=["Cuiabá"], dias=3)           # datas ontem-2, ontem-1, ontem; valor 0.95
        ant["estacoes"][0]["serie"][-1]["valor_m"] = 0.50        # ontem: valor de BDARN guardado
        cinco_antes = (self.ontem - UM_DIA * 5).isoformat()
        self.grade["CUIABA"] = {self.ontem.isoformat(): 0.99, cinco_antes: 1.11}
        est = {e["nome"]: e for e in self.rodar(ant)["estacoes"]}
        serie = {p["data"]: p["valor_m"] for p in est["Cuiabá"]["serie"]}
        self.assertEqual(serie[self.ontem.isoformat()], 0.50)    # guardado vence o PDF
        self.assertEqual(serie[cinco_antes], 1.11)                # data nova vem do PDF
        self.assertEqual(serie[self.hoje.isoformat()], 0.86)      # leitura de hoje
        self.assertEqual(len(serie), 5)

    def test_p_falha_do_historico_mantem_a_serie(self):
        ant = self.anterior(dias=3)
        antigo = {e["nome"]: e for e in ant["estacoes"]}
        self.grade["CUIABA"] = RuntimeError("PDF fora do ar")
        self.dmh["Vallemi PY"] = RuntimeError("DMH fora do ar")
        d = self.rodar(ant)
        est = {e["nome"]: e for e in d["estacoes"]}
        self.assertFalse(d["fontes"]["CHN-6 grade"]["ok"])
        self.assertEqual([p["data"] for p in est["Cuiabá"]["serie"]],
                         [p["data"] for p in antigo["Cuiabá"]["serie"]] + [self.hoje.isoformat()])
        self.assertFalse(est["Vallemi PY"]["atual"])
        self.assertEqual(est["Vallemi PY"]["serie"], antigo["Vallemi PY"]["serie"])
        self.assertTrue(d["fontes"]["BDARN"]["ok"])

    def test_q_estacao_so_no_pdf_mensal_passa_a_existir(self):
        self.grade["BELA VISTA DO NORTE"] = {self.ontem.isoformat(): 0.77,
                                             (self.ontem - UM_DIA).isoformat(): 0.80}
        self.grade["FORTE COIMBRA"] = {self.hoje.isoformat(): 1.01}
        est = {e["nome"]: e for e in self.rodar(None)["estacoes"]}
        b = est["Bela Vista Norte"]
        self.assertEqual(b["data_leitura"], self.ontem.isoformat())
        self.assertEqual(b["valor_m"], 0.77)
        self.assertEqual(b["fonte"], coletor.FONTE_GRADE_CHN6)
        self.assertFalse(b["atual"])                              # valor de ontem: nao e atual
        self.assertEqual([p["data"] for p in b["serie"]],
                         [(self.ontem - UM_DIA).isoformat(), self.ontem.isoformat()])
        self.assertEqual(b["variacao_24h"], -0.03)                # ontem 0,77 - anteontem 0,80
        self.assertEqual(b["variacao_fonte"], "serie")
        self.assertTrue(est["F. Coimbra"]["atual"])               # valor de hoje: atual

    def test_t_variacao_da_serie_sem_dia_anterior_fica_ausente(self):
        self.grade["BELA VISTA DO NORTE"] = {self.ontem.isoformat(): 0.77}   # so ontem na serie
        est = {e["nome"]: e for e in self.rodar(None)["estacoes"]}
        b = est["Bela Vista Norte"]
        self.assertNotIn("variacao_24h", b)
        self.assertNotIn("variacao_fonte", b)
        self.assertEqual(est["Cuiabá"]["variacao_24h"], -0.09)    # fonte principal (BDARN) nao muda
        self.assertNotIn("variacao_fonte", est["Cuiabá"])

    def test_r_nao_busca_pdf_com_serie_completa_e_uma_vez_por_rodada(self):
        self.rodar(self.anterior(dias=7))
        self.assertEqual(self.chamadas_grade, [])
        self.rodar(self.anterior(dias=3))
        self.assertEqual(sorted(self.chamadas_grade), sorted(set(self.chamadas_grade)))
        self.assertIn("CUIABA", self.chamadas_grade)

    def test_s_mes_anterior_so_nos_primeiros_dias_e_mesmo_pdf(self):
        chamadas = []

        def fake_coluna(pdf, mes, ano):
            chamadas.append(mes)
            if mes == "OUT":
                return {30: 2.0, 31: 2.5}
            if mes == "NOV":
                return {1: 1.0, 5: 9.0}
            return {}
        with mock.patch.object(coletor, "_coluna_mes_grade", side_effect=fake_coluna):
            saida = _grade_historico_real(b"%PDF", datetime.date(2026, 11, 3))
            self.assertEqual(saida, {"2026-10-30": 2.0, "2026-10-31": 2.5, "2026-11-01": 1.0})
            self.assertEqual(chamadas, ["NOV", "OUT"])
            chamadas.clear()
            saida = _grade_historico_real(b"%PDF", datetime.date(2026, 11, 16))
            self.assertEqual(chamadas, ["NOV"])
            self.assertEqual(saida, {"2026-11-01": 1.0, "2026-11-05": 9.0})


def _fixture(nome):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", nome), encoding="utf-8") as f:
        return f.read()


class AvisosGeoTeste(unittest.TestCase):
    """Parser das posicoes dos avisos (avisos_geo), sem rede: so funcoes puras."""

    def test_hi_0037_26_onze_areas_com_o_uno_de_42_posicoes(self):
        grupos, descartadas = coletor.extrair_avisos_geo(_fixture("hi_0037_26.txt"))
        self.assertEqual(len(grupos), 11)
        self.assertEqual({g["tipo"] for g in grupos}, {"AREA"})
        self.assertTrue(grupos[0]["nome"].startswith("UNO"))
        self.assertEqual(len(grupos[0]["coords"]), 42)
        self.assertEqual(descartadas, 0)
        for g in grupos:
            self.assertLessEqual(len(g["nome"]), 260)
            for lat, lon in g["coords"]:
                self.assertTrue(-28 <= lat <= -14 and -62 <= lon <= -54)

    def test_hi_0037_26_nome_do_dez_mantem_o_texto_depois_do_dois_pontos(self):
        grupos, _ = coletor.extrair_avisos_geo(_fixture("hi_0037_26.txt"))
        dez = [g for g in grupos if g["nome"].startswith("DEZ")]
        self.assertEqual(len(dez), 1)
        self.assertEqual(dez[0]["nome"],
                         "DEZ – CARTA 3344 - PROXIMIDADES DA ILHA COVA DA ONÇA OU YAGUARETÊ (SUL DA ILHA): "
                         "BAIXAS PROFUNDIDADES E CONTORNO APROXIMADO DA ISOBATIMÉTRICA DE 3M NA PARTE SUL DA ILHA")

    def test_rotulo_de_posicao_depois_do_dois_pontos_sai_e_o_resto_fica(self):
        self.assertEqual(coletor._geo_nome("NAS SEGUINTES COORDENADAS: LAT/LONG "),
                         "NAS SEGUINTES COORDENADAS")
        self.assertEqual(coletor._geo_nome("INSERIR AS SEGUINTES PROFUNDIDADES: A. "),
                         "INSERIR AS SEGUINTES PROFUNDIDADES")
        self.assertEqual(coletor._geo_nome("MENOR PROFUNDIDADE ENCONTRADA: 3,1M "),
                         "MENOR PROFUNDIDADE ENCONTRADA: 3,1M")

    def test_hi_0067_09_pontos_com_quatro_posicoes(self):
        grupos, _ = coletor.extrair_avisos_geo(_fixture("hi_0067_09.txt"))
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]["tipo"], "PONTOS")
        self.assertEqual(len(grupos[0]["coords"]), 4)

    def test_texto_sem_coordenada_nao_gera_entrada(self):
        self.assertEqual(coletor.extrair_avisos_geo("HI 0001/26 - RIO PARAGUAI - AVISO SEM POSICAO. km 1234,5."),
                         ([], 0))

    def test_coordenada_fora_da_bacia_e_descartada_e_contada(self):
        texto = "HI 0002/26 - RIO PARAGUAI - BOIA NA POSICAO LAT. 45º00'00\"S LONG. 057º00'00\"W."
        grupos, descartadas = coletor.extrair_avisos_geo(texto)
        self.assertEqual(grupos, [])
        self.assertEqual(descartadas, 1)

    def test_letra_e_conjuncao_nao_inverte_o_sinal(self):
        texto = ("HI 0026/23 - BRASIL - REPOSICIONADA NAS COORDENADAS LAT 18°59,8640'S E LONG 057°37,9482' "
                 "E ALTERADA SIMBOLOGIA PARA H/O.")
        grupos, descartadas = coletor.extrair_avisos_geo(texto)
        self.assertEqual(grupos[0]["coords"], [[-18.99773, -57.63247]])
        self.assertEqual(descartadas, 0)

    def test_nome_tem_no_maximo_260_caracteres_e_corta_em_palavra(self):
        texto = "HI 0003/26 - RIO PARAGUAI - UNO – " + "PROXIMIDADES " * 30 + "LAT. 19º35'54\"S LONG. 057º26'29\"W."
        grupos, _ = coletor.extrair_avisos_geo(texto)
        self.assertEqual(len(grupos), 1)
        nome = grupos[0]["nome"]
        self.assertLessEqual(len(nome), 260)
        self.assertTrue(nome.startswith("UNO"))
        self.assertTrue(nome.endswith("…"))
        self.assertEqual(nome[:-1].split()[-1], "PROXIMIDADES")

    def test_hi_0083_20_dois_grupos_de_quatro_posicoes_sem_separar_por_letra(self):
        grupos, descartadas = coletor.extrair_avisos_geo(_fixture("hi_0083_20.txt"))
        self.assertEqual([(g["tipo"], len(g["coords"])) for g in grupos], [("AREA", 4), ("AREA", 4)])
        self.assertEqual(descartadas, 0)

    def test_hi_0083_20_nome_traz_o_motivo_e_preserva_palavras_de_uma_letra(self):
        grupos, _ = coletor.extrair_avisos_geo(_fixture("hi_0083_20.txt"))
        self.assertEqual(grupos[0]["nome"],
                         "1) RECOMENDA-SE CAUTELA AO NAVEGAR A MONTANTE DA ILHA MARGARITA, ENTRE OS KM 1000,0 E 1001,1, "
                         "DEVIDO AO SURGIMENTO DE BANCO DE AREIA, COM PROFUNDIDADES MENORES QUE 3 METROS")
        self.assertTrue(grupos[1]["nome"].startswith("2) RECOMENDA-SE CAUTELA AO NAVEGAR NAS PROXIMIDADES DA ILHA MARIA"))
        self.assertFalse(any("POSIÇÕES" in g["nome"] or "SEGUINTES" in g["nome"] for g in grupos))

    def test_frase_de_ligacao_sai_do_nome_em_varias_formas(self):
        for ligacao in ("FORMADO PELA INTERLIGAÇÃO DAS SEGUINTES POSIÇÕES:", "NAS SEGUINTES POSIÇÕES:",
                        "NA POSIÇÃO:", "LIMITADA PELAS POSIÇÕES:", "NA SEGUINTE POSIÇÃO:"):
            with self.subTest(ligacao=ligacao):
                texto = ("HI 0009/26 - RIO PARAGUAI. RECOMENDA-SE CAUTELA AO NAVEGAR NA PROXIMIDADE DA ILHA X, "
                         "DEVIDO A BANCO DE AREIA, " + ligacao + " A) LAT. 19º10'00\"S LONG. 057º10'00\"W; "
                         "B) LAT. 19º11'00\"S LONG. 057º11'00\"W.")
                grupos, _ = coletor.extrair_avisos_geo(texto)
                self.assertEqual(len(grupos), 1)
                self.assertEqual(grupos[0]["nome"], "RECOMENDA-SE CAUTELA AO NAVEGAR NA PROXIMIDADE DA ILHA X, "
                                                    "DEVIDO A BANCO DE AREIA")
                self.assertEqual(len(grupos[0]["coords"]), 2)

    def test_linha_formada_com_hifen_da_quebra_do_pdf_aberta_e_linha(self):
        pos = ("LAT. 19º10'00\"S LONG. 057º10'00\"W; LAT. 19º11'00\"S LONG. 057º11'00\"W; "
               "LAT. 19º12'00\"S LONG. 057º12'00\"W; LAT. 19º13'00\"S LONG. 057º13'00\"W; "
               "LAT. 19º14'00\"S LONG. 057º14'00\"W.")
        texto = ("HI 0003/20 - RIO PARAGUAI - EXPANDINDO A ISOBATIMÉTRICA DE 3 METROS DA CARTA NÁUTICA EM VIGOR "
                 "PARA A LINHA FORMA- DA PELA INTERLIGAÇÃO DAS SEGUINTES POSIÇÕES: " + pos)
        grupos, _ = coletor.extrair_avisos_geo(texto)
        self.assertEqual(len(grupos), 1)
        self.assertEqual(len(grupos[0]["coords"]), 5)
        self.assertEqual(grupos[0]["tipo"], "LINHA")

    def test_linha_formada_com_pontos_fechados_continua_area(self):
        pos = ("LAT. 19º10'00\"S LONG. 057º10'00\"W; LAT. 19º11'00\"S LONG. 057º11'00\"W; "
               "LAT. 19º12'00\"S LONG. 057º12'00\"W; LAT. 19º13'00\"S LONG. 057º13'00\"W; "
               "LAT. 19º10'00\"S LONG. 057º10'00\"W.")
        texto = ("HI 0003/20 - RIO PARAGUAI - EXPANDINDO A ISOBATIMÉTRICA DE 3 METROS DA CARTA NÁUTICA EM VIGOR "
                 "PARA A LINHA FORMA- DA PELA INTERLIGAÇÃO DAS SEGUINTES POSIÇÕES: " + pos)
        grupos, _ = coletor.extrair_avisos_geo(texto)
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]["tipo"], "AREA")

    def test_area_delimitada_aberta_continua_area(self):
        pos = ("LAT. 19º10'00\"S LONG. 057º10'00\"W; LAT. 19º11'00\"S LONG. 057º11'00\"W; "
               "LAT. 19º12'00\"S LONG. 057º12'00\"W; LAT. 19º13'00\"S LONG. 057º13'00\"W; "
               "LAT. 19º14'00\"S LONG. 057º14'00\"W.")
        texto = "HI 0004/20 - RIO PARAGUAI - ÁREA DELIMITADA PELAS POSIÇÕES: " + pos
        grupos, _ = coletor.extrair_avisos_geo(texto)
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]["tipo"], "AREA")


if __name__ == "__main__":
    unittest.main()
