import json
import os
import tempfile
import unittest
from unittest import mock

import sync


class Regras(unittest.TestCase):
    def test_extrair_ads(self):
        self.assertEqual(sync.extrair_ads("ad:120246331810840208"), ["120246331810840208"])
        self.assertEqual(sync.extrair_ads("ad:120246331810840208, ad:120249384910000000"),
                         ["120246331810840208", "120249384910000000"])
        self.assertEqual(sync.extrair_ads("ad:120246331808400208|cr:1696153251530713|ig:18096672653185976"),
                         ["120246331808400208"])
        self.assertEqual(sync.extrair_ads("cr:1696153251530713|ig:18096672653185976"), [])
        self.assertEqual(sync.extrair_ads("120246331810840208"), ["120246331810840208"])
        self.assertEqual(sync.extrair_ads(""), [])
        self.assertEqual(sync.extrair_ads(None), [])

    def test_plataforma(self):
        self.assertEqual(sync.normalizar_plataforma("Instagram"), "instagram")
        self.assertEqual(sync.normalizar_plataforma(" Facebook "), "facebook")
        self.assertIsNone(sync.normalizar_plataforma(""))

    def test_achar_coluna_ignora_caixa(self):
        cols = [{"id": "a", "title": "Meta IDs"}, {"id": "b", "title": "Curtidas (pago)"}]
        self.assertEqual(sync.achar_coluna(cols, "meta ids")["id"], "a")
        self.assertIsNone(sync.achar_coluna(cols, "Views (pago)"))

    def test_valores_por_plataforma_e_soma(self):
        idx = sync.indexar_metricas([
            {"ad_id": "1111111111", "plataforma": "facebook", "base": "dividido", "curtidas": 10, "comentarios": 1, "views": 100},
            {"ad_id": "1111111111", "plataforma": "instagram", "base": "dividido", "curtidas": "5", "comentarios": None, "views": 50},
        ])
        campos = ["curtidas", "comentarios", "views"]
        ig = sync.valores_item(["1111111111"], "instagram", idx, campos)
        self.assertEqual((ig["curtidas"], ig["comentarios"], ig["views"], ig["base"]), (5, 0, 50, "dividido"))
        ambos = sync.valores_item(["1111111111"], None, idx, campos)
        self.assertEqual((ambos["curtidas"], ambos["base"]), (15, "soma das plataformas"))
        self.assertIsNone(sync.valores_item(["9999999999"], "facebook", idx, campos))

    def test_sem_divisao_marca_base(self):
        idx = sync.indexar_metricas([
            {"ad_id": "2222222222", "plataforma": "facebook", "base": "sem divisão", "curtidas": 7, "comentarios": 0, "views": 0}])
        v = sync.valores_item(["2222222222"], "facebook", idx, ["curtidas"])
        self.assertEqual(v["base"], "sem divisão")

    def test_diff_so_o_que_mudou(self):
        mapa = {"curtidas": "c1", "comentarios": "c2", "base": "c3"}
        atual = {"c1": "20.600", "c2": "280", "c3": "dividido"}
        self.assertEqual(sync.diff_item(atual, {"curtidas": 20600, "comentarios": 281, "base": "dividido"}, mapa), {"c2": "281"})
        self.assertEqual(sync.diff_item({}, {"curtidas": 0}, {"curtidas": "c1"}), {"c1": "0"})


    def test_resposta_compacta(self):
        ads = ["111", "222", "333"]
        linhas = sync.ler_resposta("2", "0d5,1,100/7,0,50x2s3,0,9", ads)
        self.assertEqual(linhas[0], {"ad_id": "111", "plataforma": "facebook", "base": "dividido",
                                     "curtidas": 5, "comentarios": 1, "views": 100})
        self.assertEqual(linhas[1]["views"], 50)
        self.assertEqual([(l["ad_id"], l["plataforma"], l["base"], l["curtidas"]) for l in linhas[2:]],
                         [("333", "facebook", "sem divisão", 3), ("333", "instagram", "sem divisão", 3)])
        self.assertEqual(sync.ler_resposta("0", None, ads), [])
        with self.assertRaises(SystemExit):
            sync.ler_resposta("3", "0d5,1,100/7,0,50", ads)  # contagem não bate: resposta truncada

    def test_sql_compacto_tem_um_lote_com_indices(self):
        tpl = open(os.path.join(os.path.dirname(os.path.abspath(sync.__file__)), "sql", "pago.sql"), encoding="utf-8").read()
        q = sync.montar_sql(tpl, ["120246331808400208"], 460)
        self.assertIn("(n-1+460)::int idx", q)
        self.assertIn("'x' ORDER BY idx", q)
        self.assertNotIn("{{IDS}}", q)


class FluxoAplicar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.estado = os.path.join(self.tmp, "estado")
        os.makedirs(self.estado)
        quadros = [{
            "board_id": "4223103403", "nome": "VTSD", "col_meta": "meta", "col_plataforma": "plat",
            "colunas": [{"id": "meta", "title": "Meta IDs"}, {"id": "plat", "title": "Plataforma"},
                        {"id": "curt", "title": "Curtidas"}, {"id": "cp", "title": "Curtidas (pago)"}],
            "itens": [
                {"id": "10", "name": "C | [75%] Redes sociais", "column_values": [
                    {"id": "meta", "text": "ad:120246331810840208"}, {"id": "plat", "text": "Instagram"},
                    {"id": "curt", "text": "20600"}, {"id": "cp", "text": ""}]},
                {"id": "11", "name": "sem meta id", "column_values": [
                    {"id": "meta", "text": ""}, {"id": "plat", "text": "Facebook"}]},
                {"id": "12", "name": "anúncio sem dado", "column_values": [
                    {"id": "meta", "text": "ad:999999999999"}, {"id": "plat", "text": "Facebook"}]},
            ]}]
        with open(os.path.join(self.estado, "itens.json"), "w") as f:
            json.dump(quadros, f)
        with open(os.path.join(self.estado, "metricas_pago.json"), "w") as f:
            json.dump([{"ad_id": "120246331810840208", "plataforma": "instagram", "base": "dividido",
                        "curtidas": 21666, "comentarios": 300, "views": 1666044}], f)

    def rodar(self, *extra):
        with mock.patch.object(sync, "ESTADO", self.estado):
            sync.main(["aplicar", "--modo", "pago", *extra])

    def test_simulacao_nao_chama_api(self):
        with mock.patch.object(sync, "Monday") as M:
            self.rodar()
            M.assert_not_called()

    def test_executar_cria_colunas_e_grava_so_o_item_certo(self):
        fake = mock.MagicMock()
        fake.criar_coluna.side_effect = lambda b, t, ty: "nova_" + t
        with mock.patch.object(sync, "Monday", return_value=fake), \
             mock.patch.dict(os.environ, {"MONDAY_TOKEN": "x"}), mock.patch.object(sync.time, "sleep"):
            self.rodar("--executar")
        criadas = sorted(c.args[1] for c in fake.criar_coluna.call_args_list)
        self.assertEqual(criadas, ["Base (pago)", "Comentários (pago)", "Views (pago)"])  # "Curtidas (pago)" já existia
        fake.gravar.assert_called_once()
        board, item, valores = fake.gravar.call_args.args
        self.assertEqual((board, item), ("4223103403", "10"))
        self.assertEqual(valores, {"cp": "21666", "nova_Comentários (pago)": "300",
                                   "nova_Views (pago)": "1666044", "nova_Base (pago)": "dividido"})
        # a coluna "Curtidas" que o time usa não é tocada no modo pago
        self.assertNotIn("curt", valores)


if __name__ == "__main__":
    unittest.main()
