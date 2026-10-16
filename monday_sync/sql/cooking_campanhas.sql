-- Campanhas COOKING (seguidores/engajamento) do quadro "IG - Crescimento": período, nome e interações pagas
-- vitalícias (somando os anúncios da campanha, conta Picos de vendas). Uma linha só, entradas separadas por ' ## ':
-- id|inicio|fim|curtidas|comentarios|views|nome
WITH c AS (SELECT campaign_id, min(dia) ini, max(dia) fim, max(campaign_name) nome FROM dataset_ppt_cooking GROUP BY 1),
a AS (SELECT ad_id, campaign_id FROM fb__rtg__picos_de_vendas_ads WHERE campaign_id IN (SELECT campaign_id FROM c)),
m AS (SELECT a.campaign_id,
        sum((x->>'value')::numeric) FILTER (WHERE x->>'action_type'='post_reaction') cu,
        sum((x->>'value')::numeric) FILTER (WHERE x->>'action_type'='comment') co,
        sum((x->>'value')::numeric) FILTER (WHERE x->>'action_type'='video_view') vi
      FROM fb__rtg__picos_de_vendas_insights i JOIN a USING (ad_id), jsonb_array_elements(i.actions) x GROUP BY 1)
SELECT count(*) n, string_agg(concat_ws('|', c.campaign_id, c.ini, c.fim,
         COALESCE(m.cu,0)::bigint, COALESCE(m.co,0)::bigint, COALESCE(m.vi,0)::bigint,
         translate(regexp_replace(c.nome, '^\[\d{4}\] \[COOKING\] \| ', ''), '|#', '  ')), ' ## ' ORDER BY c.campaign_id) d
FROM c LEFT JOIN m USING (campaign_id)
