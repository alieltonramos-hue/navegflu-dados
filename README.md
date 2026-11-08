# navegflu-dados

Coleta pública da altura dos rios e dos Avisos aos Navegantes da hidrovia Paraguai-Paraná (trecho Ladário–Assunção). Roda no GitHub Actions e grava o arquivo `hidro.json`.

**Status: teste.** O objetivo atual é verificar se os runners do GitHub conseguem baixar as fontes oficiais. O agendamento está desligado; a coleta roda só manualmente.

## Fontes oficiais

- **CHN-6 (Marinha do Brasil):** BDARN diário (alturas das estações brasileiras e avisos em vigor) e Avisos-Radio em Vigor (revisão mensal).
- **DMH-PY (Dirección de Meteorología e Hidrología, Paraguai):** páginas com o nível das estações paraguaias.

Todos os dados são públicos. Este repositório não é fonte oficial de navegação: para navegar, consulte os documentos oficiais.

Por escolha do coletor, as requisições não verificam o certificado TLS.

## Uso local

```
pip install -r requirements.txt
python coletor.py hidro.json
```

O programa imprime, por fonte, a URL, o status HTTP, o tamanho e o início da resposta. Sai com código 0 se pelo menos uma fonte respondeu e com código 1 se todas falharam.

## Formato do `hidro.json`

- `gerado_em_utc` e `gerado_em_local`: horário da coleta.
- `estacoes`: lista de `nome`, `valor_m` (altura em metros), `data_leitura`, `fonte` e `variacao_24h` (quando a fonte informa).
- `avurnav`: lista de avisos, com `numero`, `texto` (resumo), `sistema` (`CUIABA` ou `PARAGUAI`), `km` (KM citado no texto do aviso, ou `null`) e `coord` (`[lat, lon]` quando o aviso traz só coordenada, ou `null`).
- `fontes`: para cada fonte, `ok` (true/false) e `msg` (o que aconteceu).

## Automação

O workflow `.github/workflows/coletar.yml` roda pelo botão "Run workflow". O agendamento existe no arquivo, comentado. O `hidro.json` só é commitado quando algo além dos campos `gerado_em_*` muda. Se todas as fontes falharem, o job termina com erro e nada é commitado.
