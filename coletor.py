#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coletor.py - coleta publica de altura de rios e Avisos aos Navegantes da hidrovia
Paraguai-Parana (trecho Ladario-Assuncao).

Fontes oficiais, todas publicas:
  - BDARN (CHN-6, Marinha do Brasil): boletim diario com as alturas das estacoes
    brasileiras e a secao de Avisos-Radio em Vigor.
  - Avisos-Radio em Vigor (CHN-6): revisao mensal dos avisos (usada quando o boletim
    do dia nao traz o aviso).
  - DMH-PY (Direccion de Meteorologia e Hidrologia, Paraguai): paginas com a tabela de
    leituras das estacoes paraguaias (e de algumas brasileiras, como fallback).

Uso:  python coletor.py [saida.json]      (padrao: hidro.json)

Sem regua e sem GPX: o KM de um aviso vem so do texto dele (regex de "KM"). Quando o
texto traz so coordenada, km = null e "coord" = [lat, lon].

Codigo de saida: 0 se pelo menos uma fonte respondeu; 1 se todas falharam.
"""
import io, os, re, sys, json, datetime, unicodedata
import urllib3, requests, pdfplumber
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CAMUFLAGEM = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36'}
TZ_LOCAL = datetime.timezone(datetime.timedelta(hours=-4))   # Cuiaba: UTC-4 sem horario de verao
HOJE_STR = ""
dados_nav = {}                                               # _publicar_avisos grava dados_nav["avurnav"]
_avisos_fontes = {"bdarn": None, "avrad": None}

ORDEM_ESTACOES = ["Cuiabá", "Cáceres", "Bela Vista Norte", "Ladário", "F. Coimbra", "P. Murtinho",
                  "Vallemi PY", "Concepción PY", "Assunção PY"]
CHAVES_BD = [("CUIABA", "Cuiabá"), ("CACERES", "Cáceres"), ("VISTA", "Bela Vista Norte"),
             ("LADARIO", "Ladário"), ("COIMBRA", "F. Coimbra"), ("MURTINHO", "P. Murtinho")]
# BR_FALLBACK: so e lido se o BDARN de hoje nao trouxe a estacao.
ALVOS_DMH = [
    {"estacao": "Vallemi PY",    "url": "https://www.meteorologia.gov.py/nivel-rio/vermas_convencional.php?code=2000086088", "tipo": "PY"},
    {"estacao": "Concepción PY", "url": "https://www.meteorologia.gov.py/nivel-rio/vermas_convencional.php?code=2000086134", "tipo": "PY"},
    {"estacao": "Assunção PY",   "url": "https://www.meteorologia.gov.py/nivel-rio/vermas_convencional.php?code=2000086218", "tipo": "PY"},
    {"estacao": "Ladário",       "url": "https://www.meteorologia.gov.py/nivel-rio/vermas_convencional.php?code=2000082001", "tipo": "BR_FALLBACK"},
    {"estacao": "Cáceres",       "url": "https://www.meteorologia.gov.py/nivel-rio/vermas_convencional.php?code=2000082003", "tipo": "BR_FALLBACK"},
    {"estacao": "P. Murtinho",   "url": "https://www.meteorologia.gov.py/nivel-rio/vermas_convencional.php?code=2000086009", "tipo": "BR_FALLBACK"},
]


def log_http(fonte, url, status, corpo, marca=""):
    """Uma linha de URL e outra de status/bytes/inicio, para o log do Actions."""
    corpo = corpo or b""
    print(f"  [{fonte}] {url}", flush=True)
    print(f"      HTTP {status} | {len(corpo)} bytes | inicio={corpo[:8]!r}"
          + (f" | {marca}" if marca else ""), flush=True)


def log_erro(fonte, url, erro):
    print(f"  [{fonte}] {url}", flush=True)
    print(f"      ERRO: {type(erro).__name__}: {erro}", flush=True)


def remover_acentos(txt):
    return ''.join(c for c in unicodedata.normalize('NFD', txt) if unicodedata.category(c) != 'Mn')


_MESES_BD = ['JAN', 'FEV', 'MAR', 'ABR', 'MAI', 'JUN', 'JUL', 'AGO', 'SET', 'OUT', 'NOV', 'DEZ']
_MESES_EXT_BD = {'JANEIRO': 1, 'FEVEREIRO': 2, 'MARCO': 3, 'ABRIL': 4, 'MAIO': 5, 'JUNHO': 6,
                 'JULHO': 7, 'AGOSTO': 8, 'SETEMBRO': 9, 'OUTUBRO': 10, 'NOVEMBRO': 11, 'DEZEMBRO': 12}
_BASE_BDARN = "https://assets.marinha.mil.br/chn-6/sites/www.marinha.mil.br.chn-6/files/"


def _baixar_bdarn(dia, camuflagem):
    """Baixa o BDARN valido para a data `dia`. Devolve (pdf_bytes, url) ou (None, None)."""
    mes_bd = _MESES_BD[dia.month - 1]               # mes do calendario (ex.: JUL)
    mes_bd_ant = _MESES_BD[(dia.month - 2) % 12]    # mes anterior (ex.: JUN) - cobre virada de ano

    # Candidatos em ordem de prioridade. O CHN-6 as vezes sobe o boletim de hoje com o
    # token do mes anterior, e o Drupal acrescenta sufixo _0, _1... quando colide com
    # o arquivo real. A validacao da data dentro do PDF (_bd_da_data) e a barreira final.
    candidatos_bd = [f"{_BASE_BDARN}BDARN_{mes_bd}_{dia.day:02d}.pdf"]
    candidatos_bd += [f"{_BASE_BDARN}BDARN_{mes_bd}_{dia.day:02d}_{i}.pdf" for i in range(9, -1, -1)]
    candidatos_bd += [f"{_BASE_BDARN}BDARN_{mes_bd_ant}_{dia.day:02d}_{i}.pdf" for i in range(9, -1, -1)]

    def _bytes_bd(url):
        try:
            r = requests.get(url, headers=camuflagem, verify=False, timeout=8)
        except Exception as e:
            log_erro("BDARN", url, e)
            return None
        ok = r.status_code == 200 and len(r.content) > 100000 and r.content[:4] == b"%PDF"
        log_http("BDARN", url, r.status_code, r.content, "PDF candidato" if ok else "descartado")
        return r.content if ok else None

    def _bd_da_data(_pdf):
        # Confirma "INFORMACOES VALIDAS PARA O DIA DD DE <MES> DE AAAA" == dia.
        try:
            with pdfplumber.open(io.BytesIO(_pdf)) as _p:
                _txt = remover_acentos(_p.pages[0].extract_text() or "").upper()
            _m = re.search(r"DIA\s+(\d{1,2})\s+DE\s+([A-Z]+)\s+DE\s+(\d{4})", _txt)
            if not _m:
                return False
            return (int(_m.group(1)) == dia.day
                    and _MESES_EXT_BD.get(_m.group(2)) == dia.month
                    and int(_m.group(3)) == dia.year)
        except Exception:
            return False

    for _u in candidatos_bd:
        _b = _bytes_bd(_u)
        if _b:
            if _bd_da_data(_b):
                print(f"      escolhido: {_u}", flush=True)
                return _b, _u
            print("      PDF baixado, mas a data interna nao e a de hoje: descartado", flush=True)

    # Rede de seguranca: se o requests nao trouxe nada, tenta curl so no candidato primario.
    try:
        import subprocess, tempfile
        tmp = os.path.join(tempfile.gettempdir(), "bdarn_tmp.pdf")
        subprocess.run(['curl', '-s', '-L', '-k', '--max-time', '20',
                        '-H', 'User-Agent: ' + CAMUFLAGEM['User-Agent'],
                        '-o', tmp, candidatos_bd[0]], capture_output=True, timeout=25)
        print(f"  [BDARN] curl (fallback) {candidatos_bd[0]}: "
              f"{os.path.getsize(tmp) if os.path.exists(tmp) else 0} bytes", flush=True)
        if os.path.exists(tmp) and os.path.getsize(tmp) > 100000:
            with open(tmp, 'rb') as f:
                _b = f.read()
            os.remove(tmp)
            if _bd_da_data(_b):
                return _b, candidatos_bd[0]
    except Exception as e:
        print(f"  [BDARN] curl (fallback) falhou: {type(e).__name__}: {e}", flush=True)
    return None, None


_RE_CAB_AVISO = re.compile(r'^HI\s+(\d{4}/\d{2})\s*[–—-]', re.M)   # inicio do texto de um aviso
_RE_ID_AVISO = re.compile(r'HI\s+(\d{4}/\d{2})')
_RE_RODAPE_PAG = re.compile(r'^\s*-\s*\d+\s+de\s+\d+\s*-\s*$', re.M)


def _ler_avisos_pdf(pdf_bytes):
    """Le a secao AVISOS-RADIO EM VIGOR de um PDF da CHN-6 (BDARN ou Avisos-Radio em
    Vigor). Devolve (ids_em_vigor, {id: texto}). Separa os avisos pelo cabecalho no
    inicio da linha ("HI 0049/26 - RIO ..."), nunca por qualquer "HI" no meio do texto."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        texto = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    texto = _RE_RODAPE_PAG.sub("", texto)
    i = remover_acentos(texto).upper().find("AVISOS-RADIO EM VIGOR")
    sec = texto[i:] if i >= 0 else texto
    cabs = list(_RE_CAB_AVISO.finditer(sec))
    ids = set(_RE_ID_AVISO.findall(sec[:cabs[0].start()] if cabs else sec))   # tabela de numeros
    textos = {}
    for k, m in enumerate(cabs):
        fim = cabs[k + 1].start() if k + 1 < len(cabs) else len(sec)
        textos[m.group(1)] = ' '.join(sec[m.start():fim].split())
    ids |= set(textos)
    return ids, textos


_RE_COORD_AVISO = re.compile(
    r'(\d{1,3})\s*[º°]\s*'
    r'(\d{1,2}(?:[.,]\s?\d+)?)\s*[\'’′`´]?'   # minutos ("41, 5528" com espaco); marca ' ’ ′ ` ´
    r'\s*\.?\s*'
    r'(?:(\d{1,2}(?:[.,]\d+)?)\s*(?:["”“″]|\'\'|’’|``)?)?'   # segundos: " ” “ ″ '' ’’ ou nenhuma
    r'\s*([SNWEO])'
)


def _coord_para_decimal(g, m, s, h):
    # Toda a hidrovia fica no hemisferio Sul e a Oeste: o sinal e sempre negativo.
    # Nao confiar na letra: em "LONG 057°37,9482' E ALTERADA..." o "E" e a conjuncao.
    v = float(g) + float(m.replace(' ', '').replace(',', '.')) / 60 + (float(s.replace(',', '.')) / 3600 if s else 0)
    return -v


def extrair_primeira_coord(texto):
    # Latitude x longitude pelo valor dos graus, nao pela letra: na hidrovia a latitude
    # fica entre 15 e 27 S e a longitude entre 54 e 60 W.
    lat = lon = None
    for mm in _RE_COORD_AVISO.finditer(texto):
        g, m, s, h = mm.groups()
        if int(g) >= 40:
            if lon is None:
                lon = _coord_para_decimal(g, m, s, h)
        elif lat is None:
            lat = _coord_para_decimal(g, m, s, h)
        if lat is not None and lon is not None:
            break
    return lat, lon


_RE_LINHA_SINAL = re.compile(
    r'(?<!\S)\d{1,3}\s+([A-ZÀ-Ú][A-ZÀ-Ú0-9 .\'/-]{2,60}?)\s+(?:BR-\d+\s+)?[DE]\s+(\d{3,4},\d)\s+'
    r'\d{4}(?:/\d{4})?\s+\S+(?:\s+(DESTRU[IÍ]D[AO]|AVARIAD[AO]|OBSTRU[IÍ]D[AO]|DESAPARECID[AO]|APAGAD[AO]|SUBMERS[AO]))?')


def sinais_da_tabela(texto):
    """[(nome, km, situacao)] das tabelas de sinais de um aviso, sem repetir sinal."""
    vistos, saida = set(), []
    for nome, km, sit in _RE_LINHA_SINAL.findall(texto):
        chave = (nome.strip(), km)
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append((nome.strip(), float(km.replace(',', '.')), sit or "INOPERANTE"))
    return saida


def _baixar_avurnav_mais_novo():
    """Baixa as revisoes do PDF Avisos-Radio em Vigor dos ultimos 3 meses e escolhe a MAIS
    NOVA pelo Last-Modified do HTTP (o sufixo _N e imprevisivel). Devolve
    (pdf_bytes, etiqueta_data, data_revisao) ou (None, '', None)."""
    base = "https://www.marinha.mil.br/chn-6/sites/www.marinha.mil.br.chn-6/files"
    nomes = ["AvRadiovigor.pdf"] + [f"AvRadiovigor_{i}.pdf" for i in range(1, 9)] \
            + ["Aviso-R%C3%A1dio%20em%20Vigor.pdf"]
    for delta in range(3):
        d = datetime.date.today().replace(day=1)
        for _ in range(delta):
            d = (d - datetime.timedelta(days=1)).replace(day=1)
        mes = d.strftime('%Y-%m')
        candidatos = []
        for nome in nomes:
            url = f"{base}/{mes}/{nome}"
            try:
                r = requests.get(url, headers=CAMUFLAGEM, verify=False, timeout=30)
            except Exception as e:
                log_erro("AVURNAV", url, e)
                continue
            ok = r.status_code == 200 and r.content[:4] == b"%PDF"
            log_http("AVURNAV", url, r.status_code, r.content, "PDF candidato" if ok else "descartado")
            if ok:
                lm = r.headers.get("Last-Modified", "")
                dt = None
                for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S GMT"):
                    try:
                        dt = datetime.datetime.strptime(lm, fmt)
                        break
                    except ValueError:
                        pass
                candidatos.append({"bytes": r.content, "dt": dt})
        if candidatos:
            # mais novo pelo Last-Modified (fallback: maior arquivo)
            candidatos.sort(key=lambda c: (c["dt"] or datetime.datetime.min, len(c["bytes"])), reverse=True)
            esc = candidatos[0]
            etiqueta = esc["dt"].strftime('%d/%m/%Y') if esc["dt"] else mes
            dia_rev = esc["dt"].date() if esc["dt"] else d
            print(f"  [AVURNAV] escolhido: revisao {etiqueta} ({len(candidatos)} disponiveis em {mes})", flush=True)
            return esc["bytes"], etiqueta, dia_rev
    return None, "", None


def _publicar_avisos():
    """Monta dados_nav["avurnav"] juntando as duas fontes (boletim do dia e revisao mensal)."""
    bd, av = _avisos_fontes["bdarn"], _avisos_fontes["avrad"]
    if bd and av and bd["dia"] < av["dia"]:
        bd = None                       # boletim mais velho que a revisao: nao manda na lista
    if bd:
        universo = bd["ids"]
        status_data = f"boletim {bd['data'][:5]}"
    elif av:
        universo = av["ids"]
        status_data = f"rev. {av['data'][:5]}"
    else:
        return

    palavras_chave = ["paraguai", "ladario", "corumba", "caceres", "miranda",
                      "murtinho", "leverger", "albuquerque", "coimbra", "olimpo",
                      "vallemi", "concepcion", "assuncao", "fluvial", "hidrovia", "rio cuiaba"]
    avisos = []
    for ident in sorted(universo, key=lambda x: (x[-2:], x[:4])):   # por ano/numero
        texto_aviso = (bd and bd["textos"].get(ident)) or (av and av["textos"].get(ident))
        if not texto_aviso:
            continue                    # so o numero, sem texto em nenhuma fonte
        texto_norm = remover_acentos(texto_aviso.lower())
        if not any(p in texto_norm for p in palavras_chave):
            continue

        # Aviso com tabela de sinais: uma entrada por sinal, cada uma no seu KM.
        # Sistema do aviso: "Rio Cuiaba" no texto -> CUIABA; senao PARAGUAI.
        sistema_av = "CUIABA" if "rio cuiaba" in texto_norm else "PARAGUAI"
        sinais = sinais_da_tabela(texto_aviso)
        if sinais:
            for nome_s, km_s, sit_s in sinais:
                resumo_s = f"{sit_s}: {nome_s} (KM {km_s:.1f}). " + texto_aviso[:120]
                avisos.append({"id": f"HI {ident}", "texto": resumo_s[:200] + ("..." if len(resumo_s) > 200 else ""),
                               "texto_completo": texto_aviso, "km": km_s, "coord": None, "sistema": sistema_av})
            continue

        # KM so do texto (KM do Cuiaba: 1 a 3 digitos; o do Paraguai: 3 a 4).
        km_encontrado = None
        padrao_km = re.search(
            r'(?:km|quilometro|quilômetro)\s*[:\-]?\s*(\d{1,3}(?:[.,]\d+)?)' if sistema_av == "CUIABA"
            else r'(?:km|quilometro|quilômetro)\s*[:\-]?\s*(\d{3,4}(?:[.,]\d+)?)', texto_norm)
        if padrao_km:
            try:
                km_encontrado = float(padrao_km.group(1).replace(',', '.'))
            except ValueError:
                pass

        # Sem KM no texto: a coordenada do aviso vai junto, para quem consome decidir o uso.
        coord = None
        if km_encontrado is None:
            la, lo = extrair_primeira_coord(texto_aviso)
            if la is not None and lo is not None:
                coord = [round(la, 5), round(lo, 5)]

        texto_resumo = texto_aviso[:160] + ("..." if len(texto_aviso) > 160 else "")
        avisos.append({"id": f"HI {ident}", "texto": texto_resumo, "texto_completo": texto_aviso,
                       "km": km_encontrado, "coord": coord, "sistema": sistema_av})
    dados_nav["avurnav"] = avisos
    n_avisos = len({a["id"] for a in avisos})
    print(f"[*] AVISOS AOS NAVEGANTES: {n_avisos} avisos para a hidrovia, {len(avisos)} posicoes ({status_data}).",
          flush=True)


def ler_bdarn(pdf_bytes, hoje):
    """Le a secao de alturas do BDARN. Devolve {estacao: (nivel, tendencia, var_bd)} so das
    estacoes lidas (estacao 'AGUARDANDO' do dia de hoje e ignorada)."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        palavras_bd = pdf.pages[0].extract_words(use_text_flow=False, keep_blank_chars=False)
    _NA = lambda t: remover_acentos(t).upper()
    top_ini, top_fim = None, 1e9
    for w in palavras_bd:
        u = _NA(w["text"])
        if top_ini is None and "ALTURA" in u:
            top_ini = w["top"]
        if top_ini is not None and w["top"] > top_ini and ("ALERTAS" in u or "AVISOS-RADIO" in u):
            top_fim = min(top_fim, w["top"])
    secao_bd = [w for w in palavras_bd if top_ini is not None and top_ini - 2 <= w["top"] < top_fim]
    labels_bd = {}
    for w in secao_bd:
        if w["x0"] >= 200:
            continue
        u = _NA(w["text"])
        for ch, nome in CHAVES_BD:
            if ch in u and nome not in labels_bd:
                labels_bd[nome] = w["top"]
    nums_bd = [w for w in secao_bd if re.match(r"^-?\d+[.,]\d+$", w["text"])]
    ms_bd = [w for w in secao_bd if w["text"].strip() in ("M", "M,")]
    val_bd = [w for w in nums_bd
              if any(abs(m["top"] - w["top"]) <= 3 and 0 <= (m["x0"] - w["x1"]) <= 12 for m in ms_bd)]
    linhas_val_bd = {}
    for w in sorted(val_bd, key=lambda w: (round(w["top"]), w["x0"])):
        k = round(w["top"])
        if k not in linhas_val_bd:
            linhas_val_bd[k] = (w["top"], float(w["text"].replace(",", ".")))

    def _texto_linha_bd(top0):
        ws = [w for w in secao_bd if abs(w["top"] - top0) <= 3]
        return _NA(" ".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"])))

    lidos = {}
    for nome, top_l in labels_bd.items():
        _prox_l = min([t for t in labels_bd.values() if t > top_l], default=1e9)
        _bloco_l = _NA(" ".join(w["text"] for w in sorted(
            [w for w in secao_bd if top_l - 2 <= w["top"] < _prox_l],
            key=lambda w: (round(w["top"]), w["x0"]))))
        if "AGUARDANDO" in _bloco_l and hoje.strftime("%d/%m/%Y") in _bloco_l:
            print(f"  [!] BDARN: {nome} aguardando dado de hoje - IGNORADO.", flush=True)
            continue
        melhor, dmin = None, 1e9
        for (tv, nivel) in linhas_val_bd.values():
            d = abs(tv - top_l)
            if d < dmin:
                dmin, melhor = d, (tv, nivel)
        if not melhor or dmin >= 18:
            print(f"  [!] BDARN: {nome} sem nivel proximo - IGNORADO.", flush=True)
            continue
        tv, nivel = melhor
        linha_txt = _texto_linha_bd(tv)
        if "SUBIU" in linha_txt or "SUBINDO" in linha_txt:
            tendencia = "SUBINDO"
        elif "BAIXOU" in linha_txt or "DESCENDO" in linha_txt:
            tendencia = "DESCENDO"
        else:
            tendencia = "ESTACIONARIO"
        _mv = re.search(r'(?:SUBIU|BAIXOU)\s+(\d+[.,]\d+)', linha_txt)
        var_bd = float(_mv.group(1).replace(",", ".")) if _mv else (0.0 if "ESTACIONAD" in linha_txt else None)
        lidos[nome] = (round(nivel, 2), tendencia, var_bd)
    return lidos


def ler_dmh(url):
    """Le a tabela de leituras de uma pagina da DMH-PY. Devolve None ou dict(nivel, data (ISO),
    hoje (bool), variacao (ou None), data_ref (ISO ou None))."""
    resp = requests.get(url, headers=CAMUFLAGEM, verify=False, timeout=15)
    tem_tabela = b"<table" in resp.content.lower()
    log_http("DMH-PY", url, resp.status_code, resp.content, "tabela encontrada" if tem_tabela else "sem <table")
    if resp.status_code != 200:
        return None
    sopa = BeautifulSoup(resp.text, 'html.parser')
    tabela = sopa.find('table')
    if not tabela:
        return None
    leituras, primeira_data, datas_leit = [], None, []
    for l in tabela.find_all('tr'):
        cols = l.find_all(['td', 'th'])
        if len(cols) >= 2:
            data_txt = cols[0].text.strip()
            if re.search(r'\d{2}-\d{2}-\d{4}', data_txt):
                if primeira_data is None:
                    primeira_data = data_txt
                for c in cols[1:]:
                    txt_c = c.text.strip()
                    if re.search(r'\d+[.,]\d+', txt_c):
                        try:
                            leituras.append(float(re.sub(r'[^\d.,\-]', '', txt_c).replace(',', '.')))
                            datas_leit.append(re.search(r'\d{2}-\d{2}-\d{4}', data_txt).group(0))
                        except Exception:
                            pass
                        break
        if len(leituras) >= 4:
            break
    if not leituras:
        return None
    iso = lambda s: datetime.datetime.strptime(s, "%d-%m-%Y").date().isoformat()
    return {"nivel": leituras[0],
            "data": iso(datas_leit[0]),
            "hoje": primeira_data == HOJE_STR,
            "variacao": (leituras[0] - leituras[1]) if len(leituras) > 1 else None,
            "data_ref": iso(datas_leit[1]) if len(datas_leit) > 1 else None}


def coletar():
    global HOJE_STR
    agora = datetime.datetime.now(TZ_LOCAL)
    hoje = agora.date()
    HOJE_STR = hoje.strftime("%d-%m-%Y")
    fontes = {}
    estacoes = {}          # nome -> registro (o mais novo vence; BDARN ganha empate)

    def oferecer(nome, nivel, data_iso, fonte, variacao):
        atual = estacoes.get(nome)
        if atual and atual["data_leitura"] >= data_iso:
            return
        reg = {"nome": nome, "valor_m": round(float(nivel), 2), "data_leitura": data_iso, "fonte": fonte}
        if variacao is not None:
            reg["variacao_24h"] = round(float(variacao), 2)
        estacoes[nome] = reg

    # 1. BDARN (CHN-6): fonte primaria BR, data validada dentro do PDF
    print("[1/3] BDARN (CHN-6)", flush=True)
    try:
        pdf_bd, url_bd = _baixar_bdarn(hoje, CAMUFLAGEM)
        if pdf_bd:
            lidos = ler_bdarn(pdf_bd, hoje)
            for nome, (nivel, tend, var_bd) in lidos.items():
                # o BDARN grava a variacao SEM sinal; o sinal vem de "BAIXOU"/"SUBIU"
                var_assinada = None if var_bd is None else (-var_bd if tend == "DESCENDO" else var_bd)
                oferecer(nome, nivel, hoje.isoformat(), "BDARN CHN-6", var_assinada)
            fontes["BDARN"] = {"ok": True, "msg": f"{url_bd.rsplit('/', 1)[-1]} ({len(pdf_bd)} bytes), data confere, {len(lidos)} estacoes"}
            ids, textos = _ler_avisos_pdf(pdf_bd)
            if ids:
                _avisos_fontes["bdarn"] = {"ids": ids, "textos": textos, "dia": hoje, "data": hoje.strftime('%d/%m/%Y')}
        else:
            fontes["BDARN"] = {"ok": False, "msg": "indisponivel hoje (nenhum candidato com data de hoje)"}
    except Exception as e:
        fontes["BDARN"] = {"ok": False, "msg": f"falha: {type(e).__name__}: {e}"}
    print(f"      -> {fontes['BDARN']['msg']}", flush=True)

    # 2. DMH-PY: Paraguai (sempre) e fallback BR (so se o BDARN nao trouxe a estacao de hoje)
    print("[2/3] DMH-PY (Paraguai e fallback)", flush=True)
    erros_dmh = []
    ok_dmh = 0
    for alvo in ALVOS_DMH:
        est = alvo["estacao"]
        if alvo["tipo"] == "BR_FALLBACK" and estacoes.get(est, {}).get("data_leitura") == hoje.isoformat():
            print(f"  [DMH-PY] {est}: pulada (BDARN ja trouxe hoje)", flush=True)
            continue
        try:
            r = ler_dmh(alvo["url"])
            if r is None:
                erros_dmh.append(f"{est}: sem tabela ou HTTP diferente de 200")
                continue
            ok_dmh += 1
            fonte = "DMH-PY" if alvo["tipo"] == "PY" else "DMH-PY (fallback BR)"
            oferecer(est, r["nivel"], r["data"], fonte, r["variacao"])
        except Exception as e:
            log_erro("DMH-PY", alvo["url"], e)
            erros_dmh.append(f"{est}: {type(e).__name__}: {e}")
    fontes["DMH-PY"] = {"ok": ok_dmh > 0 and not erros_dmh,
                        "msg": f"{ok_dmh} paginas lidas" + (("; erros: " + " | ".join(erros_dmh)) if erros_dmh else "")}
    print(f"      -> {fontes['DMH-PY']['msg']}", flush=True)

    # 3. AVURNAV: revisao mais nova das Avisos-Radio em Vigor + boletim do dia
    print("[3/3] AVURNAV (Avisos-Radio em Vigor)", flush=True)
    try:
        pdf_av, etiqueta, dia_rev = _baixar_avurnav_mais_novo()
        if pdf_av:
            ids, textos = _ler_avisos_pdf(pdf_av)
            if ids:
                _avisos_fontes["avrad"] = {"ids": ids, "textos": textos, "dia": dia_rev, "data": etiqueta}
            _publicar_avisos()
            fontes["AVURNAV"] = {"ok": True, "msg": f"revisao {etiqueta}; boletim do dia: {'sim' if _avisos_fontes['bdarn'] else 'nao'}"}
        else:
            fontes["AVURNAV"] = {"ok": False, "msg": "Avisos-Radio em Vigor nao encontrado (3 meses)"}
            if _avisos_fontes["bdarn"]:
                _publicar_avisos()
    except Exception as e:
        fontes["AVURNAV"] = {"ok": False, "msg": f"falha: {type(e).__name__}: {e}"}
    print(f"      -> {fontes['AVURNAV']['msg']}", flush=True)

    avurnav = [{"numero": a["id"], "texto": a["texto"], "sistema": a["sistema"],
                "km": (round(a["km"], 1) if a["km"] is not None else None),
                "coord": a.get("coord")}
               for a in dados_nav.get("avurnav", [])]
    return {
        "gerado_em_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gerado_em_local": agora.isoformat(timespec="seconds"),
        "estacoes": [estacoes[n] for n in ORDEM_ESTACOES if n in estacoes],
        "avurnav": avurnav,
        "fontes": fontes,
    }


def main(argv):
    saida = os.path.abspath(argv[1] if len(argv) > 1 else "hidro.json")
    print(f"[*] coletor.py | Python {sys.version.split()[0]} | "
          f"{datetime.datetime.now(TZ_LOCAL).strftime('%d/%m/%Y %H:%M')} (UTC-4)", flush=True)
    dados = coletar()
    with open(saida, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[*] gravado {saida} ({os.path.getsize(saida)} bytes): "
          f"{len(dados['estacoes'])} estacoes, {len(dados['avurnav'])} posicoes de AVURNAV", flush=True)
    for nome, f_ in dados["fontes"].items():
        print(f"    {nome}: {'OK' if f_['ok'] else 'FALHOU'} - {f_['msg']}", flush=True)
    if not any(f_["ok"] for f_ in dados["fontes"].values()):
        print("[!] TODAS as fontes falharam: saida 1.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
