-- Interações pagas por anúncio e plataforma, vitalício do anúncio.
-- {{IDS}} é trocado pela lista de ad_ids entre aspas simples, separados por vírgula.
-- Contas com quebra por posicionamento (Picos de vendas, Low Ticket): o total do anúncio é
-- repartido entre facebook/instagram pela proporção observada na quebra (que só existe a partir
-- de 29/07/2026). Contas sem quebra (Perpétuo, RTG, LATAM): o total vai inteiro, base = 'sem divisão'.
WITH ids AS (SELECT unnest(ARRAY[{{IDS}}]) AS ad_id),
ins AS (
  SELECT ad_id, actions FROM fb__rtg__picos_de_vendas_insights WHERE ad_id IN (SELECT ad_id FROM ids)
  UNION ALL SELECT ad_id, actions FROM fb__rtg__perp_tuo_insights WHERE ad_id IN (SELECT ad_id FROM ids)
  UNION ALL SELECT ad_id, actions FROM fb_lowticket_insights WHERE ad_id IN (SELECT ad_id FROM ids)
  UNION ALL SELECT ad_id, actions FROM fb_rtg_insights WHERE ad_id IN (SELECT ad_id FROM ids)
  UNION ALL SELECT ad_id, actions FROM fb_facebook_ads_act_1966155373983500_insights WHERE ad_id IN (SELECT ad_id FROM ids)
),
plc AS (
  SELECT ad_id, dim1, actions FROM fb__rtg__picos_de_vendas_breakdowns WHERE breakdown_type='placement' AND ad_id IN (SELECT ad_id FROM ids)
  UNION ALL SELECT ad_id, dim1, actions FROM fb_lowticket_breakdowns WHERE breakdown_type='placement' AND ad_id IN (SELECT ad_id FROM ids)
),
ax AS (
  SELECT i.ad_id, NULL::text AS plat, a->>'action_type' AS t, (a->>'value')::numeric AS v FROM ins i, jsonb_array_elements(i.actions) a
  WHERE a->>'action_type' IN ('post_reaction','comment','video_view')
  UNION ALL
  SELECT p.ad_id, p.dim1, a->>'action_type', (a->>'value')::numeric FROM plc p, jsonb_array_elements(p.actions) a
  WHERE a->>'action_type' IN ('post_reaction','comment','video_view')
),
tot AS (SELECT ad_id, t, sum(v) v FROM ax WHERE plat IS NULL GROUP BY 1,2),
pp AS (SELECT ad_id, t, plat, sum(v) v FROM ax WHERE plat IS NOT NULL GROUP BY 1,2,3),
pt AS (SELECT ad_id, t, sum(v) v FROM pp GROUP BY 1,2),
tem AS (SELECT DISTINCT ad_id FROM plc),
lin AS (
  SELECT tot.ad_id, pl.plat, tot.t,
    CASE WHEN tem.ad_id IS NULL THEN tot.v
         WHEN COALESCE(pt.v,0)=0 THEN 0
         ELSE round(tot.v * COALESCE(pp.v,0) / pt.v) END AS v,
    CASE WHEN tem.ad_id IS NULL THEN 'sem divisão' ELSE 'dividido' END AS base
  FROM tot
  CROSS JOIN (VALUES ('facebook'),('instagram')) pl(plat)
  LEFT JOIN tem ON tem.ad_id = tot.ad_id
  LEFT JOIN pt ON pt.ad_id = tot.ad_id AND pt.t = tot.t
  LEFT JOIN pp ON pp.ad_id = tot.ad_id AND pp.t = tot.t AND pp.plat = pl.plat
)
SELECT ad_id, plat AS plataforma, base,
  COALESCE(sum(v) FILTER (WHERE t='post_reaction'),0)::bigint AS curtidas,
  COALESCE(sum(v) FILTER (WHERE t='comment'),0)::bigint AS comentarios,
  COALESCE(sum(v) FILTER (WHERE t='video_view'),0)::bigint AS views
FROM lin GROUP BY 1,2,3 ORDER BY 1,2
