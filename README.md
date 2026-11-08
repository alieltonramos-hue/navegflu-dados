# navegflu-dados

Coleta pública da altura dos rios e dos Avisos aos Navegantes da hidrovia Paraguai-Paraná (trecho Ladário–Assunção). Roda no GitHub Actions e grava o arquivo `hidro.json`.

**Status: agendado.** A Marinha publica a altura do rio entre 06:45 e 08:30 (hora de Cuiabá, UTC-4), e o coletor roda de 06:47 a 08:47, em minutos quebrados (o cron do GitHub atrasa ou pula a hora cheia). Ao meio-dia (12:07) e às 18h (18:07) roda também para DMH-PY e AVURNAV. O workflow aceita execução manual pelo botão "Run workflow".

## Fontes oficiais

- **CHN-6 (Marinha do Brasil):** BDARN diário (alturas das estações brasileiras e avisos em vigor) e Avisos-Radio em Vigor (revisão mensal).
- **DMH-PY (Dirección de Meteorología e Hidrología, Paraguai):** páginas com o nível das estações paraguaias (e, como fallback, de algumas brasileiras) e a tabela dos últimos dias, usada no histórico.
- **CHN-6, grade mensal:** um PDF por régua brasileira (dias × meses). Entra só como histórico de 7 dias; uma estação que aparece só nele entra com o valor de hoje ou de ontem.

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

- `verificado_em_utc` e `verificado_em_local` (UTC-4): horário da última verificação, ou seja, desta coleta. Muda a cada rodada que termina com pelo menos uma fonte respondendo.
- `gerado_em_utc` e `gerado_em_local`: quando os dados mudaram pela última vez. Se nada além dos campos de horário mudou, eles ficam com o valor da coleta anterior; mudou algum dado (estação, aviso, fonte), passam a ser o horário desta coleta.
- `estacoes`: lista de `nome`, `valor_m` (altura em metros), `data_leitura`, `fonte`, `variacao_24h` (quando a fonte informa), `atual` e `serie`.
  - `atual`: `true` se o valor saiu nesta coleta; `false` se a estação não trouxe valor agora e o registro é a última leitura boa da coleta anterior. Para estação que veio só da grade do CHN-6, `true` apenas se o valor for de hoje.
  - `serie`: até 7 datas distintas, cada uma com `data` (AAAA-MM-DD) e `valor_m`, um valor por data. Serve para o gráfico. Quando a série guardada tem menos de 7 datas, ela é completada com o histórico da DMH-PY e da grade do CHN-6 (uma busca por régua por rodada; sem essa busca, a série guardada fica como está). Na mesma data, vale a leitura que já estava na série (BDARN); o histórico de DMH-PY perde para o da grade.
  - Estação que nunca teve valor não aparece na lista.
- `avurnav`: lista de posições de aviso (um aviso com tabela de sinais tem uma posição por sinal), com `numero`, `texto` (prévia: os primeiros 200 caracteres do texto, terminando em "…" quando cortado; em posição de sinal, a linha do sinal vem antes do começo do aviso), `sistema` (`CUIABA` ou `PARAGUAI`), `km` (KM citado no texto do aviso, ou `null`) e `coord` (`[lat, lon]` quando o aviso traz só coordenada, ou `null`).
- `avisos_texto`: objeto `{numero: texto}` com o texto completo de cada aviso, como lido do PDF (espaços e quebras de linha normalizados), uma entrada por número, mesmo quando o aviso tem várias posições em `avurnav`. Para ver o aviso inteiro, use `avisos_texto[numero]`.
- `avurnav_de`: data (AAAA-MM-DD) da lista de `avurnav`. Se a AVURNAV falhou na coleta, a lista é a anterior inteira, e `avurnav_de` é a data dela.
- `fontes`: para cada fonte, `ok` (true/false, se a fonte respondeu nesta coleta) e `msg` (o que aconteceu).

## Automação

O workflow `.github/workflows/coletar.yml` roda nos horários agendados (cron em UTC, com a conversão para a hora de Cuiabá comentada no arquivo) e pelo botão "Run workflow". Os minutos dos crons são quebrados de propósito (06:47, 07:02–07:47, 08:02–08:47, 12:07 e 18:07), porque o GitHub atrasa ou pula a hora cheia.

Se a busca de histórico (DMH-PY ou grade do CHN-6) falha, a coleta segue e a série guardada fica como estava. A falha aparece em `fontes` (`DMH-PY` e `CHN-6 grade`). Antes da coleta, rodam os testes de `testes/`; se eles falharem, nada é publicado. O cron do GitHub pode atrasar de 5 a 20 minutos.

O `hidro.json` é commitado em toda coleta em que pelo menos uma fonte respondeu, para que o app mostre a hora da última verificação (`verificado_em_*`). A mensagem do commit é `Dados: coleta <hora>Z` quando algum dado mudou (`gerado_em_*` novo) e `Dados: verificação <hora>Z` quando só a hora da verificação andou. Se todas as fontes falharem, nada é commitado; se não houver `hidro.json` anterior, o job termina com erro.
