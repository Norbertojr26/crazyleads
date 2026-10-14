#!/usr/bin/env python3
"""Sincroniza as métricas dos anúncios (CrazyLeads) com os quadros de monitoramento do Monday.

Fluxo da rotina diária:
  1. python sync.py listar              -> lê os quadros, grava estado/itens.json e estado/ads.json
  2. python sync.py sql --modo pago     -> imprime as consultas em lotes (<= 50 anúncios = <= 100 linhas)
     A rotina roda cada lote no CrazyLeads (test_sql) e junta as linhas em estado/metricas_pago.json
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
LOTE_SQL = 50
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


def cmd_sql(args, cfg):
    with open(os.path.join(ESTADO, "ads.json")) as f:
        ads = json.load(f)
    with open(os.path.join(AQUI, "sql", f"{args.modo}.sql"), encoding="utf-8") as f:
        tpl = f.read()
    lotes = [ads[i:i + LOTE_SQL] for i in range(0, len(ads), LOTE_SQL)]
    saida = [tpl.replace("{{IDS}}", ",".join(f"'{a}'" for a in l)) for l in lotes]
    with open(os.path.join(ESTADO, f"lotes_{args.modo}.json"), "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False)
    print(f"{len(saida)} lotes de até {LOTE_SQL} anúncios -> estado/lotes_{args.modo}.json")


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
    a = sub.add_parser("aplicar")
    a.add_argument("--modo", default="pago", choices=["pago", "post"])
    a.add_argument("--dados")
    a.add_argument("--executar", action="store_true", help="escreve no Monday (sem isso é só simulação)")
    a.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    cfg = carregar_config(args.config)
    {"listar": cmd_listar, "sql": cmd_sql, "aplicar": cmd_aplicar}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
