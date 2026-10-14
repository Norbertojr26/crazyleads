#!/usr/bin/env python3
"""Sincroniza as métricas dos anúncios (CrazyLeads) com os quadros de monitoramento do Monday.

Fluxo da rotina diária:
  1. python sync.py listar              -> lê os quadros, grava estado/itens.json e estado/ads.json
  2. python sync.py sql --modo pago     -> grava estado/lote_<n>.sql (uma linha de resposta por lote)
     A rotina roda cada lote no CrazyLeads (test_sql) e grava a resposta em estado/resp_<n>.txt
     python sync.py juntar               -> monta estado/metricas_pago.json
  3. python sync.py aplicar --modo pago -> mostra o que mudaria (simulação)
     python sync.py aplicar --modo pago --executar -> escreve no Monday

O token vem de MONDAY_TOKEN (segredo do ambiente). Nunca passe o token por argumento nem grave em arquivo.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
ESTADO = os.path.join(AQUI, "estado")
API = "https://api.monday.com/v2"
LOTE_SQL = 460  # anúncios por consulta; a resposta compacta vem numa linha só
RE_AD = re.compile(r"(?<![a-z])ad:\s*(\d{10,})", re.I)
RE_NUM = re.compile(r"(\d{10,})")


def carregar_config(caminho=None):
    with open(caminho or os.path.join(AQUI, "boards.json"), encoding="utf-8") as f:
        return json.load(f)


# ───────── Monday API ─────────

class Monday:
    def __init__(self, token, tentativas=4):
        if not token:
            sys.exit("MONDAY_TOKEN não definido. Cadastre o token como segredo do ambiente.")
        self.token = token
        self.tentativas = tentativas

    def gql(self, query, variaveis=None):
        corpo = json.dumps({"query": query, "variables": variaveis or {}}).encode()
        for n in range(self.tentativas):
            req = urllib.request.Request(API, data=corpo, method="POST", headers={
                "Authorization": self.token, "Content-Type": "application/json", "API-Version": "2024-10"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    resp = json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and n < self.tentativas - 1:
                    time.sleep(2 ** (n + 1))
                    continue
                raise SystemExit(f"Monday HTTP {e.code}: {e.read()[:300]!r}")
            erros = resp.get("errors") or resp.get("error_message")
            if erros:
                txt = json.dumps(erros)
                if ("complexity" in txt.lower() or "rate" in txt.lower()) and n < self.tentativas - 1:
                    time.sleep(2 ** (n + 3))
                    continue
                raise SystemExit(f"Monday GraphQL: {txt[:500]}")
            return resp["data"]
        raise SystemExit("Monday: tentativas esgotadas")

    def colunas(self, board_id):
        d = self.gql("query($b:[ID!]){boards(ids:$b){id name columns{id title type}}}", {"b": [board_id]})
        if not d["boards"]:
            raise SystemExit(f"Quadro {board_id} não encontrado ou sem acesso.")
        return d["boards"][0]

    def itens(self, board_id, col_ids):
        q1 = ("query($b:[ID!],$c:[String!]){boards(ids:$b){items_page(limit:500){cursor "
              "items{id name group{title} column_values(ids:$c){id text}}}}}")
        qn = ("query($cur:String!,$c:[String!]){next_items_page(limit:500,cursor:$cur){cursor "
              "items{id name group{title} column_values(ids:$c){id text}}}}")
        page = self.gql(q1, {"b": [board_id], "c": col_ids})["boards"][0]["items_page"]
        out = list(page["items"])
        while page.get("cursor"):
            page = self.gql(qn, {"cur": page["cursor"], "c": col_ids})["next_items_page"]
            out.extend(page["items"])
        return out

    def criar_coluna(self, board_id, titulo, tipo):
        d = self.gql("mutation($b:ID!,$t:String!,$ty:ColumnType!){create_column(board_id:$b,title:$t,column_type:$ty){id}}",
                     {"b": board_id, "t": titulo, "ty": tipo})
        return d["create_column"]["id"]

    def gravar(self, board_id, item_id, valores):
        self.gql("mutation($b:ID!,$i:ID!,$v:JSON!){change_multiple_column_values(board_id:$b,item_id:$i,column_values:$v){id}}",
                 {"b": board_id, "i": item_id, "v": json.dumps(valores)})


# ───────── regras puras (testadas em test_sync.py) ─────────

def achar_coluna(colunas, titulo):
    alvo = titulo.strip().lower()
    for c in colunas:
        if c["title"].strip().lower() == alvo:
            return c
    return None


def extrair_ads(texto):
    """'ad:1202...|cr:...|ig:...' -> só os ad_ids. Sem nenhum prefixo 'xx:', aceita números soltos."""
    texto = texto or ""
    ads = RE_AD.findall(texto)
    if ads or re.search(r"[a-z]+:", texto, re.I):
        return ads
    return RE_NUM.findall(texto)


def normalizar_plataforma(texto):
    t = (texto or "").strip().lower()
    if "insta" in t:
        return "instagram"
    if "face" in t:
        return "facebook"
    return None  # vazio ou outra coisa: soma as duas plataformas


def indexar_metricas(linhas):
    idx = {}
    for r in linhas:
        idx[(str(r["ad_id"]), str(r["plataforma"]).lower())] = r
    return idx


def valores_item(ads, plataforma, idx, campos):
    """Soma as métricas dos ad_ids do item na plataforma do item. Devolve None se nenhum ad tem dado."""
    plats = [plataforma] if plataforma else ["facebook", "instagram"]
    total = {c: 0 for c in campos}
    bases, achou = set(), False
    for ad in ads:
        for p in plats:
            r = idx.get((ad, p))
            if not r:
                continue
            achou = True
            bases.add(r.get("base", ""))
            for c in campos:
                total[c] += int(float(r.get(c) or 0))
    if not achou:
        return None
    # sem divisão por plataforma: o mesmo total aparece nas linhas Instagram e Facebook do anúncio
    total["base"] = "sem divisão" if "sem divisão" in bases else ("dividido" if plataforma else "soma das plataformas")
    return total


def diff_item(item_cols, novos, mapa_cols):
    """item_cols: {col_id: texto atual}. Devolve {col_id: valor} só do que mudou."""
    muda = {}
    for campo, cid in mapa_cols.items():
        if campo not in novos:
            continue
        v = str(novos[campo])
        atual = (item_cols.get(cid) or "").replace(".", "").replace(",", "").strip()
        if atual != v.replace(".", "").replace(",", ""):
            muda[cid] = v
    return muda


# ───────── comandos ─────────

def cmd_listar(args, cfg):
    api = Monday(os.environ.get("MONDAY_TOKEN"))
    os.makedirs(ESTADO, exist_ok=True)
    tudo, ads = [], set()
    for q in cfg["quadros"]:
        b = api.colunas(q["id"])
        cols = b["columns"]
        cm = achar_coluna(cols, cfg["colunas_chave"]["meta_ids"])
        cp = achar_coluna(cols, cfg["colunas_chave"]["plataforma"])
        if not cm:
            print(f"! {b['name']}: sem coluna '{cfg['colunas_chave']['meta_ids']}', ignorado")
            continue
        ids = [cm["id"]] + ([cp["id"]] if cp else [])
        # também as colunas de saída, para o aplicar só regravar o que mudou
        for modo in cfg["modos"].values():
            for spec in modo["colunas"].values():
                c = achar_coluna(cols, spec["titulo"])
                if c and c["id"] not in ids:
                    ids.append(c["id"])
        itens = api.itens(q["id"], ids)
        n_ad = 0
        for it in itens:
            cv = {c["id"]: c["text"] for c in it["column_values"]}
            a = extrair_ads(cv.get(cm["id"]))
            if a:
                n_ad += 1
                ads.update(a)
        tudo.append({"board_id": q["id"], "nome": b["name"], "colunas": cols,
                     "col_meta": cm["id"], "col_plataforma": cp["id"] if cp else None, "itens": itens})
        print(f"{b['name']}: {len(itens)} itens, {n_ad} com Meta ID · colunas: " + ", ".join(c["title"] for c in cols))
    with open(os.path.join(ESTADO, "itens.json"), "w", encoding="utf-8") as f:
        json.dump(tudo, f, ensure_ascii=False)
    with open(os.path.join(ESTADO, "ads.json"), "w") as f:
        json.dump(sorted(ads), f)
    print(f"\n{len(ads)} anúncios distintos -> estado/ads.json")


def montar_sql(tpl, ads, inicio):
    """Consulta de um lote que devolve UMA linha: n (anúncios com dado) e d, no formato compacto
    'IDXd fc,fcm,fv/ic,icm,iv' (dividido) ou 'IDXs c,cm,v' (sem divisão), separados por 'x'.
    IDX é a posição do anúncio em estado/ads.json. Assim o lote cabe no limite de 100 linhas do test_sql."""
    tpl = "\n".join(l for l in tpl.splitlines() if not l.startswith("--")).rstrip()
    tpl = tpl[:-len("ORDER BY 1,2")] if tpl.endswith("ORDER BY 1,2") else tpl
    arr = ",".join(f"'{a}'" for a in ads)
    q = tpl.replace("WITH ids AS (SELECT unnest(ARRAY[{{IDS}}]) AS ad_id),",
                    f"WITH ids AS (SELECT ad_id, (n-1+{inicio})::int idx FROM unnest(ARRAY[{arr}]) WITH ORDINALITY u(ad_id,n)),")
    q = q.replace("\nSELECT ad_id, plat AS plataforma,", "\n, r AS (SELECT ad_id, plat AS plataforma,").rstrip() + ")\n"
    return q + ("SELECT count(*) n, string_agg(idx||b||CASE WHEN b='s' THEN f ELSE f||'/'||ig END, 'x' ORDER BY idx) d FROM ("
                "SELECT ids.idx, max(left(r.base,1)) b, "
                "max(r.curtidas||','||r.comentarios||','||r.views) FILTER (WHERE r.plataforma='facebook') f, "
                "max(r.curtidas||','||r.comentarios||','||r.views) FILTER (WHERE r.plataforma='instagram') ig "
                "FROM r JOIN ids USING(ad_id) GROUP BY 1) g")


def ler_resposta(n, d, ads):
    """Converte a resposta compacta de um lote em linhas {ad_id, plataforma, base, curtidas, comentarios, views}."""
    linhas = []
    for e in (d or "").split("x"):
        if not e:
            continue
        m = re.fullmatch(r"(\d+)([ds])([\d,/]+)", e)
        if not m:
            raise SystemExit(f"Entrada inválida na resposta: {e!r}")
        idx, k, v = int(m.group(1)), m.group(2), [int(x) for x in re.split("[,/]", m.group(3))]
        if len(v) != (6 if k == "d" else 3):
            raise SystemExit(f"Entrada incompleta na resposta: {e!r}")
        for plat, vv in (("facebook", v[:3]), ("instagram", v[3:] if k == "d" else v[:3])):
            linhas.append({"ad_id": ads[idx], "plataforma": plat, "base": "dividido" if k == "d" else "sem divisão",
                           "curtidas": vv[0], "comentarios": vv[1], "views": vv[2]})
    if len(linhas) != 2 * int(n):
        raise SystemExit(f"Resposta com {len(linhas) // 2} anúncios, esperado {n}. Copie a resposta de novo.")
    return linhas


def cmd_sql(args, cfg):
    with open(os.path.join(ESTADO, "ads.json")) as f:
        ads = json.load(f)
    with open(os.path.join(AQUI, "sql", f"{args.modo}.sql"), encoding="utf-8") as f:
        tpl = f.read()
    for velho in os.listdir(ESTADO):
        if re.fullmatch(r"(lote|resp)_\d+\.(sql|txt)", velho):
            os.remove(os.path.join(ESTADO, velho))
    n = 0
    for n, i in enumerate(range(0, len(ads), LOTE_SQL), 1):
        with open(os.path.join(ESTADO, f"lote_{n}.sql"), "w", encoding="utf-8") as f:
            f.write(montar_sql(tpl, ads[i:i + LOTE_SQL], i))
    print(f"{n} lotes de até {LOTE_SQL} anúncios -> estado/lote_1.sql … lote_{n}.sql")
    print("Rode cada lote no CrazyLeads (test_sql) e grave a resposta em estado/resp_<n>.txt: "
          "1ª linha o valor de n, 2ª linha o valor de d. Depois: python3 sync.py juntar")


def cmd_juntar(args, cfg):
    with open(os.path.join(ESTADO, "ads.json")) as f:
        ads = json.load(f)
    lotes = sorted(f for f in os.listdir(ESTADO) if re.fullmatch(r"lote_\d+\.sql", f))
    linhas = []
    for lote in lotes:
        resp = os.path.join(ESTADO, lote.replace("lote_", "resp_").replace(".sql", ".txt"))
        if not os.path.exists(resp):
            raise SystemExit(f"Falta {os.path.basename(resp)} (resposta de {lote}).")
        with open(resp, encoding="utf-8") as f:
            partes = f.read().strip().split("\n", 1)
        d = partes[1].strip() if len(partes) > 1 else ""
        linhas += ler_resposta(partes[0].strip(), None if d in ("", "null", "None") else d, ads)
    with open(os.path.join(ESTADO, f"metricas_{args.modo}.json"), "w", encoding="utf-8") as f:
        json.dump(linhas, f, ensure_ascii=False)
    print(f"{len(lotes)} respostas, {len(linhas) // 2} anúncios com dado -> estado/metricas_{args.modo}.json")


def cmd_aplicar(args, cfg):
    modo = cfg["modos"][args.modo]
    with open(os.path.join(ESTADO, "itens.json"), encoding="utf-8") as f:
        quadros = json.load(f)
    with open(args.dados or os.path.join(ESTADO, f"metricas_{args.modo}.json"), encoding="utf-8") as f:
        idx = indexar_metricas(json.load(f))
    api = Monday(os.environ.get("MONDAY_TOKEN")) if args.executar else None
    campos = [c for c in modo["colunas"] if c != "base"]
    resumo = []
    for q in quadros:
        mapa = {}
        for campo, spec in modo["colunas"].items():
            col = achar_coluna(q["colunas"], spec["titulo"])
            if col:
                mapa[campo] = col["id"]
            elif modo.get("criar_colunas"):
                if args.executar:
                    mapa[campo] = api.criar_coluna(q["board_id"], spec["titulo"], spec["tipo"])
                    print(f"+ {q['nome']}: coluna '{spec['titulo']}' criada")
                else:
                    mapa[campo] = f"(nova:{spec['titulo']})"
            elif not spec.get("opcional"):
                print(f"! {q['nome']}: falta a coluna '{spec['titulo']}', campo {campo} ignorado")
        mudados = sem_dado = iguais = 0
        for it in q["itens"]:
            cv = {c["id"]: c["text"] for c in it["column_values"]}
            ads = extrair_ads(cv.get(q["col_meta"]))
            if not ads:
                continue
            plat = normalizar_plataforma(cv.get(q["col_plataforma"])) if q["col_plataforma"] else None
            novos = valores_item(ads, plat, idx, campos)
            if novos is None:
                sem_dado += 1
                continue
            muda = diff_item(cv, novos, mapa)
            if not muda:
                iguais += 1
                continue
            mudados += 1
            if args.executar:
                api.gravar(q["board_id"], it["id"], muda)
                time.sleep(0.25)
            elif args.verbose:
                print(f"  {q['nome']} · {it['name'][:50]} · {plat or 'sem plataforma'}: {muda}")
        resumo.append((q["nome"], mudados, iguais, sem_dado))
    acao = "gravados" if args.executar else "a gravar (simulação)"
    for nome, m, i, s in resumo:
        print(f"{nome}: {m} itens {acao} · {i} sem mudança · {s} sem dado no CrazyLeads")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar")
    s = sub.add_parser("sql"); s.add_argument("--modo", default="pago")
    j = sub.add_parser("juntar"); j.add_argument("--modo", default="pago")
    a = sub.add_parser("aplicar")
    a.add_argument("--modo", default="pago", choices=["pago", "post"])
    a.add_argument("--dados")
    a.add_argument("--executar", action="store_true", help="escreve no Monday (sem isso é só simulação)")
    a.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    cfg = carregar_config(args.config)
    {"listar": cmd_listar, "sql": cmd_sql, "juntar": cmd_juntar, "aplicar": cmd_aplicar}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
