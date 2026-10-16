import datetime as dt
import unittest

import ig_crescimento as ig

CAMPS = [
    {"id": "111", "ini": "2026-09-01", "fim": "2026-10-05", "curtidas": 6752, "comentarios": 95, "views": 193514,
     "nome": "Seguidores: Empreenda siga @leandroladeiran"},
    {"id": "222", "ini": "2026-09-01", "fim": "2026-09-08", "curtidas": 936, "comentarios": 64, "views": 51905,
     "nome": "Seguidores: Siga @leandroladeiran"},
    {"id": "333", "ini": "2026-08-13", "fim": "2026-08-13", "curtidas": 9, "comentarios": 0, "views": 455,
     "nome": "Seguidores: Seja simples e eficiente. Só isso. Siga @leandroladeiran [PUBLICO FEMININO]"},
    {"id": "444", "ini": "2026-08-03", "fim": "2026-09-11", "curtidas": 6876, "comentarios": 21, "views": 292851,
     "nome": "Seguidores: Seja simples e eficiente. Só isso. Siga @leandroladeiran"},
    {"id": "555", "ini": "2026-08-24", "fim": "2026-09-25", "curtidas": 43804, "comentarios": 13, "views": 166273,
     "nome": "Engajamento: Eu amo trabalhar no digital"},
]
MESES = {("111", "2026-09"): (5481.36, 1929), ("111", "2026-10"): (481.82, 121), ("222", "2026-09"): (1408.86, 190)}


def item(id, nome, mes, inicio, meta="", link="", inv="", publico=""):
    return {"id": id, "nome": nome, "mes": mes, "inicio": inicio, "publico": publico, "link": link, "meta": meta,
            "investimento": inv, "atual": {}}


class Regras(unittest.TestCase):
    def test_mes_do_grupo(self):
        self.assertEqual(ig.mes_do_grupo("Outubro/26"), "2026-10")
        self.assertEqual(ig.mes_do_grupo("Dezembro/23 [GERENCIADOR]"), "2023-12")
        self.assertEqual(ig.mes_do_grupo("Março/24"), "2024-03")
        self.assertIsNone(ig.mes_do_grupo("Sem grupo"))

    def test_casar_por_nome_e_data(self):
        c, conf, _ = ig.casar_item("👤 [SEGUIDOR] Empreenda", "2026-09-01", "", CAMPS)
        self.assertEqual((c["id"], conf), ("111", "alta"))
        # nome que vira só "@leandroladeiran" depois da limpeza ainda casa
        c, conf, _ = ig.casar_item("👤 [SEGUIDOR] Siga @leandroladeiran", "2026-09-01", "", CAMPS)
        self.assertEqual(c["id"], "222")
        # público feminino separa campanhas de mesmo texto
        c, _, _ = ig.casar_item("👤 [SEGUIDOR] Simples e eficiente [FEM]", "2026-08-13", "", CAMPS)
        self.assertEqual(c["id"], "333")
        c, _, _ = ig.casar_item("👤 [SEGUIDOR] Seja simples e eficiente", "2026-08-03", "", CAMPS)
        self.assertEqual(c["id"], "444")
        # interação só casa com campanha de engajamento
        c, _, _ = ig.casar_item("💬 [INTERAÇÃO] Eu amo trabalhar no digital", "2026-08-24", "", CAMPS)
        self.assertEqual(c["id"], "555")
        # sem campanha parecida -> baixa
        _, conf, _ = ig.casar_item("Hotmart Galaxy | Parte 1", "2026-06-11", "", CAMPS)
        self.assertEqual(conf, "baixa")

    def test_janela_meses(self):
        self.assertEqual(ig.janela_meses(dt.date(2026, 10, 16)), {"2026-10"})
        self.assertEqual(ig.janela_meses(dt.date(2026, 11, 2)), {"2026-11", "2026-10"})
        self.assertEqual(ig.janela_meses(dt.date(2027, 1, 1)), {"2027-01", "2026-12"})

    def test_seguidores_acumulados_no_impulsionamento(self):
        self.assertEqual(ig.seguidores(MESES, "111", "2026-09", {"2026-09", "2026-10"}), (0, 1929))
        self.assertEqual(ig.seguidores(MESES, "111", "2026-10", {"2026-09", "2026-10"}), (1929, 2050))
        # reimpulsionamento: setembro foi outro impulsionamento (outro Início), não entra no pré de outubro
        self.assertEqual(ig.seguidores(MESES, "111", "2026-10", {"2026-10"}), (0, 121))

    def test_ler_respostas(self):
        cs = ig.ler_campanhas("2", "111|2026-09-01|2026-10-05|6752|95|193514|Seguidores: Empreenda ## "
                                   "222|2026-09-01|2026-09-08|936|64|51905|Seguidores: Siga")
        self.assertEqual([c["id"] for c in cs], ["111", "222"])
        self.assertEqual(ig.ler_meses("2", "111|2026-09|5481.36|1929 111|2026-10|481.82|121")[("111", "2026-10")],
                         (481.82, 121))
        with self.assertRaises(SystemExit):
            ig.ler_meses("3", "111|2026-09|5481.36|1929")

    def test_nao_rebaixa_gasto_nem_seguidores(self):
        self.assertTrue(ig.rebaixa("investimento", "126.13", "121.22"))
        self.assertTrue(ig.rebaixa("seg_pos", "6", 5))
        self.assertFalse(ig.rebaixa("investimento", "100", "121.22"))
        self.assertFalse(ig.rebaixa("investimento", "", "121.22"))
        self.assertFalse(ig.rebaixa("curtidas", "300", 262))

    def test_diff_ignora_formato(self):
        self.assertFalse(ig.diff("5481.36", "5481.36"))
        self.assertFalse(ig.diff("1929", 1929))
        self.assertTrue(ig.diff("", "0.00"))
        self.assertTrue(ig.diff("126.13", "121.22"))


class Planejar(unittest.TestCase):
    def test_fluxo(self):
        itens = [
            item("a", "👤 [SEGUIDOR] Empreenda", "2026-09", "2026-09-01", meta="camp:111", inv="5481.36"),
            item("b", "👤 [SEGUIDOR] Empreenda", "2026-10", "2026-09-01", link="https://www.instagram.com/reel/X/", inv="400"),
            item("c", "👤 [SEGUIDOR] Empreenda", "2026-10", "2026-09-01", link="https://www.instagram.com/p/Y/"),
            item("d", "Hotmart Galaxy | Parte 1", "2026-10", "2026-10-02"),
            item("e", "Post antigo", "2024-05", "2024-05-02"),
        ]
        metas, valores, avisos = ig.planejar(itens, CAMPS, MESES, dt.date(2026, 10, 16))
        # 'b' e 'c' casam com 111 em outubro; fica o /reel/, o /p/ vira ignorar
        self.assertEqual(metas["b"], "camp:111")
        self.assertEqual(metas["c"], ig.IGNORAR)
        self.assertNotIn("e", metas)  # antes das campanhas COOKING: nem tenta
        # interações só no mês mais recente (b); investimento/seguidores só no mês atual (b)
        self.assertNotIn("a", valores)
        self.assertEqual(valores["b"], {"curtidas": 6752, "comentarios": 95, "views": 193514,
                                        "investimento": "481.82", "seg_pre": 1929, "seg_pos": 2050})
        self.assertNotIn("c", valores)
        self.assertTrue(any("Hotmart Galaxy" in a for a in avisos))

    def test_meta_manual_e_desconhecida(self):
        itens = [item("x", "Qualquer nome", "2026-10", "2026-10-01", meta="camp:999")]
        _, valores, avisos = ig.planejar(itens, CAMPS, MESES, dt.date(2026, 10, 16))
        self.assertEqual(valores, {})
        self.assertTrue(any("camp:999" in a for a in avisos))


if __name__ == "__main__":
    unittest.main()
