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
  - Grade mensal (CHN-6): um PDF por regua brasileira (dias x meses). So fornece o
    historico de 7 dias, e a estacao que so aparece nele entra com o valor de hoje/ontem.

Uso:  python coletor.py [saida.json]      (padrao: hidro.json)

Sem regua e sem GPX: o KM de um aviso vem so do texto dele (regex de "KM"). Quando o
texto traz so coordenada, km = null e "coord" = [lat, lon].

Ultima leitura boa: se uma estacao nao trouxe valor nesta rodada, ela fica com o registro
do hidro.json anterior e "atual": false. Cada estacao tem "serie" (ultimos 7 dias, um
valor por data), preenchida com o historico da DMH-PY e da grade do CHN-6 quando a serie
guardada ainda nao tem 7 datas. Se a AVURNAV falhar, a lista anterior inteira fica e
"avurnav_de" diz a data dela.

Codigo de saida: 0 se pelo menos uma fonte respondeu, ou se todas falharam mas existe um
hidro.json anterior (que e regravado com o que ja estava); 1 se todas falharam e nao ha
hidro.json anterior.
"""
import io, os, re, sys, json, datetime, unicodedata, urllib.parse, email.utils
import urllib3, requests, pdfplumber
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CAMUFLAGEM = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36'}
TZ_LOCAL = datetime.timezone(datetime.timedelta(hours=-4))   # Cuiaba: UTC-4 sem horario de verao
HOJE_STR = ""
dados_nav = {}                                               # _publicar_avisos grava dados_nav["avurnav"]
_avisos_fontes = {"bdarn": None, "avrad": None}
SERIE_DIAS = 7                                               # datas guardadas em "serie"
# Grade mensal do CHN-6: nome do arquivo (NOME_DD[_n].pdf, DD = dia da publicacao) de cada regua.
NOMES_GRADE_CHN6 = {"Cuiabá": "CUIABA", "Cáceres": "CACERES", "Bela Vista Norte": "BELA VISTA DO NORTE",
                    "Ladário": "LADARIO", "F. Coimbra": "FORTE COIMBRA", "P. Murtinho": "PORTO MURTINHO"}
FONTE_GRADE_CHN6 = "CHN-6 grade mensal"

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


PREVIA_CHARS = 200                                           # tamanho da previa de avurnav[].texto


def _previa(txt, n=PREVIA_CHARS):
    """Primeiros n caracteres do texto; com "…" no fim se cortou."""
    return txt if len(txt) <= n else txt[:n] + "…"


def _publicar_avisos():
    """Monta dados_nav["avurnav"] juntando as duas fontes (boletim do dia e revisao mensal).
    dados_nav["avisos_texto"] guarda o texto completo de cada aviso, uma vez por numero."""
    bd, av = _avisos_fontes["bdarn"], _avisos_fontes["avrad"]
    if bd and av and bd["dia"] < av["dia"]:
        bd = None                       # boletim mais velho que a revisao: nao manda na lista
    if bd:
        universo = bd["ids"]
        dia_lista = bd["dia"]
        status_data = f"boletim {bd['data'][:5]}"
    elif av:
        universo = av["ids"]
        dia_lista = av["dia"]
        status_data = f"rev. {av['data'][:5]}"
    else:
        return

    palavras_chave = ["paraguai", "ladario", "corumba", "caceres", "miranda",
                      "murtinho", "leverger", "albuquerque", "coimbra", "olimpo",
                      "vallemi", "concepcion", "assuncao", "fluvial", "hidrovia", "rio cuiaba"]
    avisos, textos_completos = [], {}
    for ident in sorted(universo, key=lambda x: (x[-2:], x[:4])):   # por ano/numero
        texto_aviso = (bd and bd["textos"].get(ident)) or (av and av["textos"].get(ident))
        if not texto_aviso:
            continue                    # so o numero, sem texto em nenhuma fonte
        texto_norm = remover_acentos(texto_aviso.lower())
        if not any(p in texto_norm for p in palavras_chave):
            continue
        textos_completos[f"HI {ident}"] = texto_aviso      # uma vez por aviso, inteiro

        # Aviso com tabela de sinais: uma entrada por sinal, cada uma no seu KM.
        # Sistema do aviso: "Rio Cuiaba" no texto -> CUIABA; senao PARAGUAI.
        sistema_av = "CUIABA" if "rio cuiaba" in texto_norm else "PARAGUAI"
        sinais = sinais_da_tabela(texto_aviso)
        if sinais:
            for nome_s, km_s, sit_s in sinais:
                # previa: linha do sinal + comeco do aviso (o sinal diferencia as entradas)
                resumo_s = f"{sit_s}: {nome_s} (KM {km_s:.1f})."
                avisos.append({"id": f"HI {ident}", "texto": _previa(f"{resumo_s} {texto_aviso}"),
                               "km": km_s, "coord": None, "sistema": sistema_av})
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

        avisos.append({"id": f"HI {ident}", "texto": _previa(texto_aviso),
                       "km": km_encontrado, "coord": coord, "sistema": sistema_av})
    dados_nav["avurnav"] = avisos
    dados_nav["avisos_texto"] = textos_completos
    dados_nav["avurnav_de"] = dia_lista.isoformat()
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
        if len(leituras) >= SERIE_DIAS:
            break
    if not leituras:
        return None
    iso = lambda s: datetime.datetime.strptime(s, "%d-%m-%Y").date().isoformat()
    serie, vistas = [], set()
    for ds, v in zip(datas_leit, leituras):      # uma leitura por data; a primeira linha da data vence
        try:
            d = iso(ds)
        except ValueError:
            continue
        if d not in vistas:
            vistas.add(d)
            serie.append({"data": d, "valor_m": round(v, 2)})
    return {"nivel": leituras[0],
            "data": iso(datas_leit[0]),
            "hoje": primeira_data == HOJE_STR,
            "variacao": (leituras[0] - leituras[1]) if len(leituras) > 1 else None,
            "data_ref": iso(datas_leit[1]) if len(datas_leit) > 1 else None,
            "serie": serie[:SERIE_DIAS]}


_MESES_GRADE = ['JAN', 'FEV', 'MAR', 'ABR', 'MAI', 'JUN', 'JUL', 'AGO', 'SET', 'OUT', 'NOV', 'DEZ']


def _coluna_mes_grade(pdf_bytes, mes_alvo, ano_alvo):
    """{dia: valor} da coluna do mes `mes_alvo` (abreviatura, ex.: 'JUL') na PAGINA DO ANO
    `ano_alvo` da grade do CHN-6 (linhas = dias, colunas = meses). Mesmo criterio da
    _extrair_coluna_mes_pdf do Painel. Sem pagina do ano: {} (nao chuta outra pagina)."""
    out = {}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pagina_obj = None
            for pagina in pdf.pages:
                if str(ano_alvo) in (pagina.extract_text() or ""):
                    pagina_obj = pagina
                    break
            if pagina_obj is None:
                return out
            palavras = pagina_obj.extract_words(use_text_flow=False, keep_blank_chars=False)
            if not palavras:
                return out
            linhas_vis, _atual, _ref = [], [], None
            for w in sorted(palavras, key=lambda w: (w["top"], w["x0"])):
                if _ref is None or abs(w["top"] - _ref) <= 4:
                    _atual.append(w)
                    _ref = w["top"] if _ref is None else _ref
                else:
                    linhas_vis.append(_atual)
                    _atual = [w]
                    _ref = w["top"]
            if _atual:
                linhas_vis.append(_atual)
            mes_cx = {}
            for ws in linhas_vis:
                ach = {remover_acentos(w["text"]).upper(): (w["x0"] + w["x1"]) / 2
                       for w in ws if remover_acentos(w["text"]).upper() in _MESES_GRADE}
                if len(ach) >= 6:
                    mes_cx = ach
                    break
            if mes_alvo not in mes_cx:
                return out
            cs = sorted(mes_cx.values())
            espac = min(cs[i + 1] - cs[i] for i in range(len(cs) - 1)) if len(cs) > 1 else 30
            tol_x = espac * 0.5
            for ws in linhas_vis:
                ws = sorted(ws, key=lambda w: w["x0"])
                if not ws:
                    continue
                try:
                    txt_limpo = re.sub(r'[^\d]', '', ws[0]["text"].split('\n')[0])
                    if not txt_limpo:
                        continue
                    dia = int(txt_limpo)
                    if not (1 <= dia <= 31):
                        continue
                except Exception:
                    continue
                for w in ws[1:]:
                    val_limpo = w["text"].split('\n')[0].strip()
                    if not re.match(r'^-?\d+[.,]\d+$', val_limpo):
                        continue
                    cx = (w["x0"] + w["x1"]) / 2
                    mp = min(mes_cx.items(), key=lambda kv: abs(kv[1] - cx))
                    if abs(mp[1] - cx) <= tol_x and mp[0] == mes_alvo:
                        try:
                            out[dia] = float(val_limpo.replace(",", "."))
                        except ValueError:
                            pass
    except Exception:
        return out
    return out


def _grade_chn6_historico(pdf_bytes, hoje):
    """{data ISO: valor} dos dias do mes de `hoje` lidos da grade. O mes anterior sai do MESMO
    PDF e so entra nos primeiros SERIE_DIAS dias do mes. Datas depois de hoje ficam de fora."""
    meses = [(hoje.year, hoje.month)]
    if hoje.day <= SERIE_DIAS:
        meses.append((hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12))
    saida = {}
    for ano, mes in meses:
        for dia, valor in _coluna_mes_grade(pdf_bytes, _MESES_GRADE[mes - 1], ano).items():
            try:
                d = datetime.date(ano, mes, dia)
            except ValueError:
                continue
            if d <= hoje:
                saida[d.isoformat()] = round(valor, 2)
    return saida


def _achar_grade_chn6(nome_arq, hoje, camuflagem):
    """Baixa a grade do CHN-6 de uma regua (NOME_DD[_n].pdf, DD = dia da publicacao). Varre os
    candidatos de hoje e depois de ontem; para na primeira publicacao de hoje, senao escolhe o
    de Last-Modified mais novo ("maior sufixo = mais novo" nao vale neste servidor). Devolve os
    bytes do PDF, ou None."""
    ontem = hoje - datetime.timedelta(days=1)
    sufixos = [f"_{i}.pdf" for i in range(25, -1, -1)] + [".pdf"]
    candidatos = []                          # [(Last-Modified ou None, url)] na ordem da varredura
    for dia in (hoje, ontem):
        achou_hoje = False
        for suf in sufixos:
            url = _BASE_BDARN + urllib.parse.quote(f"{nome_arq}_{dia.day:02d}{suf}")
            try:
                r = requests.get(url, headers=camuflagem, verify=False, timeout=5, stream=True)
                st = r.status_code
                tam = int(r.headers.get("Content-Length", 0) or 0)
                lm_raw = r.headers.get("Last-Modified")
                r.close()
            except Exception:
                continue
            if st != 200 or tam <= 200000:
                continue
            lm = None
            try:
                if lm_raw:
                    lm = email.utils.parsedate_to_datetime(lm_raw)
                    if lm.tzinfo is None:
                        lm = lm.replace(tzinfo=datetime.timezone.utc)
            except Exception:
                lm = None
            candidatos.append((lm, url))
            if lm is not None and lm.date() >= hoje:
                achou_hoje = True
                break
        if achou_hoje:
            break
    if not candidatos:
        return None
    com_lm = [c for c in candidatos if c[0] is not None]
    url = max(com_lm, key=lambda c: c[0])[1] if com_lm else candidatos[0][1]
    try:
        r = requests.get(url, headers=camuflagem, verify=False, timeout=20)
    except Exception as e:
        log_erro("CHN-6 grade", url, e)
        return None
    ok = r.status_code == 200 and r.content[:4] == b"%PDF"
    log_http("CHN-6 grade", url, r.status_code, r.content, "PDF candidato" if ok else "descartado")
    return r.content if ok else None


def carregar_anterior(caminho):
    """Le o hidro.json da rodada anterior. Devolve o dict, ou None se o arquivo nao existe,
    nao e JSON valido ou nao tem a lista de estacoes."""
    try:
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
    except Exception:
        return None
    if isinstance(dados, dict) and isinstance(dados.get("estacoes"), list):
        return dados
    return None


def _serie_mesclada(camadas):
    """Serie curta da estacao: um valor por data, so as ultimas SERIE_DIAS datas. Cada camada e
    uma lista de pontos {data, valor_m}, da MENOS para a MAIS prioritaria (na mesma data, a
    ultima que traz o valor vence)."""
    por_data = {}
    for camada in camadas:
        for p in camada or []:
            if isinstance(p, dict) and p.get("data") and p.get("valor_m") is not None:
                por_data[p["data"]] = round(float(p["valor_m"]), 2)
    return [{"data": d, "valor_m": por_data[d]} for d in sorted(por_data)[-SERIE_DIAS:]]


def _serie_atualizada(serie, data_iso, valor):
    """Serie do registro com uma leitura nova. Com data_iso/valor None, so reaplica o limite de dias."""
    nova = [{"data": data_iso, "valor_m": valor}] if data_iso and valor is not None else []
    return _serie_mesclada([serie, nova])


def _serie_do_anterior(velho):
    """Serie do registro anterior. Registro antigo, sem serie, comeca com o proprio valor."""
    if isinstance(velho.get("serie"), list):
        return velho["serie"]
    return [{"data": velho.get("data_leitura"), "valor_m": velho.get("valor_m")}]


def _variacao_da_serie(reg):
    """Variacao do dia pela serie: valor de data_leitura menos o valor do dia anterior, com 2
    casas. None se a serie nao tem o dia anterior."""
    por_data = {p["data"]: p["valor_m"] for p in reg.get("serie", []) if isinstance(p, dict)}
    d = reg.get("data_leitura")
    if d not in por_data:
        return None
    try:
        anterior = (datetime.date.fromisoformat(d) - datetime.timedelta(days=1)).isoformat()
    except (TypeError, ValueError):
        return None
    if anterior not in por_data:
        return None
    return round(por_data[d] - por_data[anterior], 2)


def _com_variacao(reg):
    """Sem variacao_24h da fonte principal, usa a da serie (variacao_fonte: "serie"). A da
    fonte principal nunca e trocada."""
    if reg.get("variacao_fonte") == "serie":          # recalcula a que veio de uma rodada anterior
        reg.pop("variacao_24h", None)
        reg.pop("variacao_fonte", None)
    if "variacao_24h" not in reg:
        v = _variacao_da_serie(reg)
        if v is not None:
            reg["variacao_24h"] = v
            reg["variacao_fonte"] = "serie"
    return reg


CAMPOS_TEMPO = ("gerado_em_utc", "gerado_em_local", "verificado_em_utc", "verificado_em_local")


def _agora_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def _sem_tempo(dados):
    """Conteudo do hidro.json sem os campos de horario: serve para saber se algum dado mudou."""
    return {k: v for k, v in dados.items() if k not in CAMPOS_TEMPO}


def coletar(anterior=None):
    global HOJE_STR
    agora = datetime.datetime.now(TZ_LOCAL)
    hoje = agora.date()
    ontem = hoje - datetime.timedelta(days=1)
    HOJE_STR = hoje.strftime("%d-%m-%Y")
    fontes = {}
    estacoes = {}          # nome -> registro (o mais novo vence; BDARN ganha empate)
    hist_dmh, hist_grade = {}, {}   # nome -> {data ISO: valor}: historico (menor prioridade que a leitura)
    # Ultima leitura boa de cada estacao (hidro.json anterior)
    anteriores = {e["nome"]: e for e in (anterior or {}).get("estacoes", [])
                  if isinstance(e, dict) and e.get("nome")}

    def _falta_serie(nome):
        """Estacao sem SERIE_DIAS datas na serie guardada (ou nova): vale buscar historico."""
        velho = anteriores.get(nome)
        datas = {p.get("data") for p in (_serie_do_anterior(velho) if velho else []) if isinstance(p, dict)}
        return len(datas - {None}) < SERIE_DIAS

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
    print("[2/4] DMH-PY (Paraguai e fallback)", flush=True)
    erros_dmh = []
    ok_dmh = 0
    for alvo in ALVOS_DMH:
        est = alvo["estacao"]
        pulada = alvo["tipo"] == "BR_FALLBACK" and estacoes.get(est, {}).get("data_leitura") == hoje.isoformat()
        if pulada and not _falta_serie(est):
            print(f"  [DMH-PY] {est}: pulada (BDARN ja trouxe hoje)", flush=True)
            continue
        try:
            r = ler_dmh(alvo["url"])
            if r is None:
                if not pulada:
                    erros_dmh.append(f"{est}: sem tabela ou HTTP diferente de 200")
                continue
            if r.get("serie"):
                hist_dmh[est] = {p["data"]: p["valor_m"] for p in r["serie"]}
            if pulada:
                print(f"  [DMH-PY] {est}: pulada para a leitura (BDARN ja trouxe hoje); so historico", flush=True)
                continue
            ok_dmh += 1
            fonte = "DMH-PY" if alvo["tipo"] == "PY" else "DMH-PY (fallback BR)"
            oferecer(est, r["nivel"], r["data"], fonte, r["variacao"])
        except Exception as e:
            log_erro("DMH-PY", alvo["url"], e)
            if not pulada:
                erros_dmh.append(f"{est}: {type(e).__name__}: {e}")
    fontes["DMH-PY"] = {"ok": ok_dmh > 0 and not erros_dmh,
                        "msg": f"{ok_dmh} paginas lidas" + (("; erros: " + " | ".join(erros_dmh)) if erros_dmh else "")}
    print(f"      -> {fontes['DMH-PY']['msg']}", flush=True)

    # 3. CHN-6 grade mensal: historico das reguas brasileiras. Um PDF por regua por rodada e so
    # para regua cuja serie ainda nao tem 7 datas. Falha aqui nao derruba a coleta.
    precisam = [est for est in NOMES_GRADE_CHN6 if _falta_serie(est)]
    if precisam:
        print("[3/4] CHN-6 grade mensal (historico)", flush=True)
        erros_grade, ok_grade = [], 0
        for est in precisam:
            try:
                pdf = _achar_grade_chn6(NOMES_GRADE_CHN6[est], hoje, CAMUFLAGEM)
                if not pdf:
                    erros_grade.append(f"{est}: sem PDF de hoje ou de ontem")
                    continue
                hist = _grade_chn6_historico(pdf, hoje)
                if not hist:
                    erros_grade.append(f"{est}: PDF sem valores")
                    continue
                ok_grade += 1
                hist_grade[est] = hist
                ultima = max(hist)
                if ultima in (hoje.isoformat(), ontem.isoformat()):   # so aparece com valor de hoje/ontem
                    oferecer(est, hist[ultima], ultima, FONTE_GRADE_CHN6, None)
            except Exception as e:
                erros_grade.append(f"{est}: {type(e).__name__}: {e}")
        fontes["CHN-6 grade"] = {"ok": ok_grade > 0 and not erros_grade,
                                 "msg": f"{ok_grade} reguas com historico"
                                        + (("; erros: " + " | ".join(erros_grade)) if erros_grade else "")}
        print(f"      -> {fontes['CHN-6 grade']['msg']}", flush=True)
    else:
        print("[3/4] CHN-6 grade mensal: nenhuma regua precisa de historico (series com 7 datas)", flush=True)

    # 4. AVURNAV: revisao mais nova das Avisos-Radio em Vigor + boletim do dia
    print("[4/4] AVURNAV (Avisos-Radio em Vigor)", flush=True)
    av_ok = False
    try:
        pdf_av, etiqueta, dia_rev = _baixar_avurnav_mais_novo()
        if pdf_av:
            ids, textos = _ler_avisos_pdf(pdf_av)
            if ids:
                _avisos_fontes["avrad"] = {"ids": ids, "textos": textos, "dia": dia_rev, "data": etiqueta}
            _publicar_avisos()
            av_ok = True
            fontes["AVURNAV"] = {"ok": True, "msg": f"revisao {etiqueta}; boletim do dia: {'sim' if _avisos_fontes['bdarn'] else 'nao'}"}
        else:
            fontes["AVURNAV"] = {"ok": False, "msg": "Avisos-Radio em Vigor nao encontrado (3 meses)"}
    except Exception as e:
        fontes["AVURNAV"] = {"ok": False, "msg": f"falha: {type(e).__name__}: {e}"}

    if not av_ok and anterior and isinstance(anterior.get("avurnav"), list):
        # fonte falhou e ha lista anterior: ela fica inteira
        avurnav = anterior["avurnav"]
        avisos_texto = anterior.get("avisos_texto") if isinstance(anterior.get("avisos_texto"), dict) else {}
        avurnav_de = anterior.get("avurnav_de") or (anterior.get("gerado_em_local") or "")[:10] or None
        fontes["AVURNAV"]["msg"] += f"; lista anterior mantida (de {avurnav_de})"
    else:
        # rodada com a fonte, ou sem lista anterior: so o boletim do dia, se houver
        if not av_ok and _avisos_fontes["bdarn"]:
            _publicar_avisos()
        avurnav = [{"numero": a["id"], "texto": a["texto"], "sistema": a["sistema"],
                    "km": (round(a["km"], 1) if a["km"] is not None else None),
                    "coord": a.get("coord")}
                   for a in dados_nav.get("avurnav", [])]
        avurnav_de = dados_nav.get("avurnav_de")
        avisos_texto = dict(dados_nav.get("avisos_texto") or {})
    print(f"      -> {fontes['AVURNAV']['msg']}", flush=True)

    # Ultima leitura boa: estacao sem valor nesta rodada fica com o registro anterior
    finais = []
    for nome in ORDEM_ESTACOES + [n for n in anteriores if n not in ORDEM_ESTACOES]:
        novo, velho = estacoes.get(nome), anteriores.get(nome)
        # serie: um valor por data; da menos para a mais prioritaria: historico DMH, historico
        # CHN-6, serie guardada, leitura nova. Na mesma data, vence a mais prioritaria (BDARN ganha).
        hist_d = [{"data": d, "valor_m": v} for d, v in hist_dmh.get(nome, {}).items()]
        hist_g = [{"data": d, "valor_m": v} for d, v in hist_grade.get(nome, {}).items()]
        guardada = _serie_do_anterior(velho) if velho else []
        # leitura com data mais antiga que a guardada nao substitui a guardada
        if novo and not (velho and (velho.get("data_leitura") or "") > novo["data_leitura"]):
            # estacao que veio so do PDF mensal: atual apenas se o valor for de hoje
            reg = dict(novo, atual=(novo["fonte"] != FONTE_GRADE_CHN6 or novo["data_leitura"] == hoje.isoformat()))
            leitura = [{"data": novo["data_leitura"], "valor_m": novo["valor_m"]}]
            reg["serie"] = _serie_mesclada([hist_d, hist_g, guardada, leitura])
            finais.append(_com_variacao(reg))
        elif velho:
            reg = dict(velho, atual=False)
            reg["serie"] = _serie_mesclada([hist_d, hist_g, guardada])
            finais.append(_com_variacao(reg))

    saida = {
        "estacoes": finais,
        "avurnav": avurnav,
        "avisos_texto": avisos_texto,
        "avurnav_de": avurnav_de,
        "fontes": fontes,
    }
    # verificado_em_*: esta rodada, sempre. gerado_em_*: quando os dados mudaram pela ultima vez
    # (sem mudanca de dado, mantem o valor do hidro.json anterior).
    verificado_utc = _agora_utc()
    verificado_local = verificado_utc.astimezone(TZ_LOCAL)
    if anterior and anterior.get("gerado_em_utc") and _sem_tempo(anterior) == saida:
        gerado_utc = anterior["gerado_em_utc"]
        gerado_local = anterior.get("gerado_em_local") or verificado_local.isoformat(timespec="seconds")
    else:
        gerado_utc = verificado_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        gerado_local = verificado_local.isoformat(timespec="seconds")
    return {
        "gerado_em_utc": gerado_utc,
        "gerado_em_local": gerado_local,
        "verificado_em_utc": verificado_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verificado_em_local": verificado_local.isoformat(timespec="seconds"),
        **saida,
    }


def main(argv):
    saida = os.path.abspath(argv[1] if len(argv) > 1 else "hidro.json")
    print(f"[*] coletor.py | Python {sys.version.split()[0]} | "
          f"{datetime.datetime.now(TZ_LOCAL).strftime('%d/%m/%Y %H:%M')} (UTC-4)", flush=True)
    anterior = carregar_anterior(saida)           # lido antes de gravar por cima
    print("[*] hidro.json anterior: " + ("lido" if anterior is not None else "nao ha (ou nao e valido)"),
          flush=True)
    dados = coletar(anterior)
    with open(saida, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[*] gravado {saida} ({os.path.getsize(saida)} bytes): "
          f"{len(dados['estacoes'])} estacoes, {len(dados['avurnav'])} posicoes de AVURNAV", flush=True)
    for nome, f_ in dados["fontes"].items():
        print(f"    {nome}: {'OK' if f_['ok'] else 'FALHOU'} - {f_['msg']}", flush=True)
    if not any(f_["ok"] for f_ in dados["fontes"].values()):
        if anterior is None:
            print("[!] TODAS as fontes falharam e nao ha hidro.json anterior: saida 1.", flush=True)
            return 1
        print("[!] TODAS as fontes falharam: mantida a ultima leitura boa (saida 0).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
