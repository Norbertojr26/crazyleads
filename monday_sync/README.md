# Sincronização CrazyLeads → Monday (monitoramento de campanhas)

Abastece os 7 quadros de monitoramento (`boards.json`) com as interações de cada anúncio,
casando o item do Monday pelo **Meta ID** (`ad:1202…`) e pela coluna **Plataforma** (Instagram/Facebook).

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
python3 sync.py sql --modo pago        # gera estado/lotes_pago.json (lotes de 50 anúncios)
# para cada lote: rodar no CrazyLeads (test_sql) e juntar todas as linhas em estado/metricas_pago.json
python3 sync.py aplicar --modo pago -v # simulação: mostra o que mudaria
python3 sync.py aplicar --modo pago --executar
```
`estado/` guarda dados de trabalho e não vai para o git.

## Testes
```bash
cd monday_sync && python3 -m unittest -v test_sync
```
