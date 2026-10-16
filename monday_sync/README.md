# Sincronização CrazyLeads → Monday (monitoramento de campanhas)

Abastece os 7 quadros de monitoramento (`boards.json`) com as interações de cada anúncio,
casando o item do Monday pelo **Meta ID** (só o `ad:1202…`; `cr:` e `ig:` são ignorados) e pela coluna **Plataforma** (Instagram/Facebook).

## Dois modos

| Modo | O que escreve | Colunas | Fonte | Status |
|---|---|---|---|---|
| `pago` | interações da veiculação paga, vitalício do anúncio | **Curtidas (pago)**, **Comentários (pago)**, **Views (pago)**, **Base (pago)** (criadas na 1ª execução) | API de anúncios da Meta, via CrazyLeads (`sql/pago.sql`) | pronto |
| `post` | total do post (orgânico + pago), como aparece na publicação | **Curtidas**, **Comentários**, **Visualização** (as que o time já usa) | Graph API de Páginas/Instagram | aguarda o token da Meta e a consulta `sql/post.sql` |

O modo `pago` nunca toca nas colunas atuais.

### Coluna "Base (pago)"
- `dividido`: conta com quebra por posicionamento (Picos de vendas, Low Ticket). O total do anúncio é
  repartido entre Instagram e Facebook pela proporção observada (a quebra só existe a partir de 29/07/2026).
- `sem divisão`: conta sem quebra (Perpétuo, RTG, LATAM). O total do anúncio aparece **igual** nas linhas
  Instagram e Facebook. Não some as duas.
- `soma das plataformas`: o item não tem Plataforma preenchida.

## Pré-requisitos do ambiente
- `api.monday.com` liberado em *Network access* do ambiente.
- Segredo `MONDAY_TOKEN` com um token do Monday com permissão de escrita nos quadros.

## Execução (rotina diária)
```bash
cd monday_sync
python3 sync.py listar                 # lê os quadros -> estado/itens.json, estado/ads.json
python3 sync.py sql --modo pago        # gera estado/lote_<n>.sql (até 460 anúncios; a resposta vem numa linha só)
# para cada lote: rodar no CrazyLeads (test_sql) e gravar estado/resp_<n>.txt
#   1ª linha: o valor de n · 2ª linha: o valor de d, copiado inteiro
python3 sync.py juntar                 # confere as contagens e gera estado/metricas_pago.json
python3 sync.py aplicar --modo pago -v # simulação: mostra o que mudaria
python3 sync.py aplicar --modo pago --executar
```
`estado/` guarda dados de trabalho e não vai para o git.

## Quadro "IG - Crescimento" (campanhas COOKING de seguidores) — `ig_crescimento.py`
Quadro diferente: **cada item é uma campanha num mês** (o grupo, ex. "Outubro/26"), sem Meta ID de anúncio nem Plataforma.
- **Meta ID** (`camp:<id da campanha>`): a rotina preenche sozinha casando nome + data com as campanhas do dataset
  `ppt_cooking`. Só grava quando a confiança é alta; nos outros casos avisa no resumo para preencher à mão.
  Itens duplicados da mesma campanha no mesmo mês (ex. link `/p/` e `/reel/`) ficam com `ignorar` (fica o `/reel/`).
- **Curtidas/Comentários/Views (pago)**: interações pagas vitalícias da campanha, só no item do mês mais recente dela
  (a Meta não dá esse número por mês).
- **Investimento / Seguidores pré / Seguidores pós**: gasto do mês e seguidores acumulados no impulsionamento (itens
  da mesma campanha com o mesmo Início), **só no mês atual** (e no anterior até o dia 3). O histórico não é tocado e
  a rotina nunca diminui Investimento nem Seguidores pós (o quadro pode ter sido atualizado à mão com dado mais novo).

```bash
cd monday_sync
python3 ig_crescimento.py listar
python3 ig_crescimento.py sql          # estado/ig_lote_campanhas.sql e estado/ig_lote_meses.sql
# rodar cada um no CrazyLeads (test_sql) e gravar estado/ig_resp_campanhas.txt e estado/ig_resp_meses.txt
#   1ª linha: o valor de n · 2ª linha: o valor de d, copiado inteiro
python3 ig_crescimento.py juntar
python3 ig_crescimento.py aplicar -v   # simulação
python3 ig_crescimento.py aplicar --executar
```

## Testes
```bash
cd monday_sync && python3 -m unittest -v test_sync test_ig_crescimento
```
