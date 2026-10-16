-- Gasto e seguidores de cada campanha COOKING por mês (o quadro "IG - Crescimento" tem um item por campanha e mês).
-- Uma linha só, entradas separadas por espaço: id|AAAA-MM|gasto|seguidores
SELECT count(*) n, string_agg(concat_ws('|', campaign_id, mes, gasto, seg), ' ' ORDER BY campaign_id, mes) d FROM (
  SELECT campaign_id, left(dia, 7) mes, round(sum(spend_api), 2) gasto, sum(follows) seg
  FROM dataset_ppt_cooking GROUP BY 1, 2) x
