# navegflu-dados

Coleta pública da altura dos rios e dos Avisos aos Navegantes da hidrovia Paraguai-Paraná (trecho Ladário–Assunção). Roda no GitHub Actions e grava o arquivo `hidro.json`.

**Status: agendado.** A Marinha publica a altura do rio entre 06:45 e 08:30 (hora de Cuiabá, UTC-4), e o coletor roda nesse intervalo, com uma folga até 08:45. Ao meio-dia e às 18h roda também para DMH-PY e AVURNAV. O workflow aceita execução manual pelo botão "Run workflow".

## Fontes oficiais

- **CHN-6 (Marinha do Brasil):** BDARN diário (alturas das estações brasileiras e avisos em vigor) e Avisos-Radio em Vigor (revisão mensal).
- **DMH-PY (Dirección de Meteorología e Hidrología, Paraguai):** páginas com o nível das estações paraguaias.

Todos os dados são públicos. Este repositório não é fonte oficial de navegação: para navegar, consulte os documentos oficiais.

Por escolha do coletor, as requisições não verificam o certificado TLS.

## Uso local

```
pip install -r requirements.txt
python -m unittest discover -s testes -v   # testes sem rede
python coletor.py hidro.json
```

O programa imprime, por fonte, a URL, o status HTTP, o tamanho e o início da resposta. Se já existe um `hidro.json`, ele é lido antes da coleta e serve de "última leitura boa". Sai com código 0 se pelo menos uma fonte respondeu, ou se todas falharam mas há um `hidro.json` anterior. Sai com código 1 só se todas falharam e não há `hidro.json` anterior.

## Formato do `hidro.json`

- `gerado_em_utc` e `gerado_em_local`: horário da coleta.
- `estacoes`: lista de `nome`, `valor_m` (altura em metros), `data_leitura`, `fonte`, `variacao_24h` (quando a fonte informa), `atual` e `serie`.
  - `atual`: `true` se o valor saiu nesta coleta; `false` se a estação não trouxe valor agora e o registro é a última leitura boa da coleta anterior.
  - `serie`: até 7 datas distintas, cada uma com `data` (AAAA-MM-DD) e `valor_m`, um valor por data (o mais recente). Serve para o gráfico.
  - Estação que nunca teve valor não aparece na lista.
- `avurnav`: lista de avisos, com `numero`, `texto` (resumo), `sistema` (`CUIABA` ou `PARAGUAI`), `km` (KM citado no texto do aviso, ou `null`) e `coord` (`[lat, lon]` quando o aviso traz só coordenada, ou `null`).
- `avurnav_de`: data (AAAA-MM-DD) da lista de `avurnav`. Se a AVURNAV falhou na coleta, a lista é a anterior inteira, e `avurnav_de` é a data dela.
- `fontes`: para cada fonte, `ok` (true/false, se a fonte respondeu nesta coleta) e `msg` (o que aconteceu).

## Automação

O workflow `.github/workflows/coletar.yml` roda nos horários agendados (cron em UTC, com a conversão para a hora de Cuiabá comentada no arquivo) e pelo botão "Run workflow". Antes da coleta, rodam os testes de `testes/`; se eles falharem, nada é publicado. O cron do GitHub pode atrasar de 5 a 20 minutos.

O `hidro.json` só é commitado quando algo além dos campos `gerado_em_*` muda. Se todas as fontes falharem e não houver `hidro.json` anterior, o job termina com erro e nada é commitado.
