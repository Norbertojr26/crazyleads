#!/usr/bin/env python3
"""Quadro "IG - Crescimento" (campanhas COOKING de seguidores/engajamento no Instagram).

Diferente dos quadros de monitoramento: cada item é UMA CAMPANHA NUM MÊS (o grupo do quadro, ex. "Outubro/26"),
sem Meta ID de anúncio nem Plataforma. A ligação item ↔ campanha fica na coluna "Meta ID" (camp:<id>), que a
rotina preenche sozinha casando nome + data; itens duplicados da mesma campanha no mesmo mês recebem "ignorar".

O que é gravado:
  - Curtidas/Comentários/Views (pago): interações pagas vitalícias da campanha, só no item do mês mais recente dela.
  - Investimento e Seguidores pré/pós: gasto do mês e seguidores acumulados no impulsionamento (itens da mesma
    campanha com o mesmo Início) antes/depois do mês, só nos itens do mês atual (e do mês anterior nos 3 primeiros dias, para fechar o último dia). O histórico não é tocado.

Fluxo (rotina diária):
  python3 ig_crescimento.py listar
  python3 ig_crescimento.py sql          -> estado/ig_lote_campanhas.sql e estado/ig_lote_meses.sql
  # rodar cada um no CrazyLeads (test_sql) e gravar estado/ig_resp_campanhas.txt / estado/ig_resp_meses.txt
  # (1ª linha: n · 2ª linha: d, copiado inteiro)
  python3 ig_crescimento.py juntar
  python3 ig_crescimento.py aplicar -v   -> simulação
  python3 ig_crescimento.py aplicar --executar
"""
import argparse
import datetime as dt
import difflib
import json
import os
import re
import time
import unicodedata

import sync

AQUI = os.path.dirname(os.path.abspath(__file__))
ESTADO = os.path.join(AQUI, "estado")
MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
         "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
IGNORAR = "ignorar"


def cfg_quadro(cfg):
    return cfg["ig_crescimento"]


# ───────── regras puras (testadas em test_ig_crescimento.py) ─────────

def mes_do_grupo(titulo):
    """'Outubro/26' ou 'Dezembro/23 [GERENCIADOR]' -> '2026-10' / '2023-12'. None se não reconhecer."""
    m = re.match(r"\s*([A-Za-zçÇ]+)\s*/\s*(\d{2,4})", titulo or "")
    if not m or m.group(1).lower() not in MESES:
        return None
    ano = int(m.group(2))
    ano = ano + 2000 if ano < 100 else ano
    return f"{ano}-{MESES[m.group(1).lower()]:02d}"


def normalizar_nome(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"^\s*(seguidores|engajamento)\s*:", " ", s)
    t = re.sub(r"@\w+", " ", s)
    t = re.sub(r"\bsiga\b", " ", t)
    t = " ".join(re.sub(r"[^a-z0-9 ]", " ", t).split())
    return t or " ".join(re.sub(r"[^a-z0-9@ ]", " ", s).split())


def similaridade(a, b):
    a, b = normalizar_nome(a), normalizar_nome(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.95
    return difflib.SequenceMatcher(None, a, b).ratio()


def casar_item(nome, inicio, publico, campanhas):
    """Melhor campanha para um item. Devolve (campanha, confiança 'alta'|'média'|'baixa', similaridade)."""
    if not campanhas or not inicio:
        return None, "baixa", 0.0
    d = dt.date.fromisoformat(inicio)
    eng_item = "INTERA" in nome.upper()
    fem_item = "FEM" in nome.upper() or "FEM" in (publico or "").upper()
    notas = []
    for c in campanhas:
        s = similaridade(nome, c["nome"])
        ini, fim = dt.date.fromisoformat(c["ini"]), dt.date.fromisoformat(c["fim"])
        dias = 0 if ini <= d <= fim else min(abs((d - ini).days), abs((d - fim).days))
        nota = s - 0.03 * min(dias, 10)
        if c["nome"].lower().startswith("engajamento") != eng_item:
            nota -= 0.15
        if ("FEMININO" in c["nome"].upper()) != fem_item:
            nota -= 0.1
        notas.append((nota, s, dias, c))
    notas.sort(key=lambda x: -x[0])
    melhor = notas[0]
    folga = melhor[0] - (notas[1][0] if len(notas) > 1 else -1)
    if melhor[1] >= 0.8 and melhor[2] <= 3 and folga >= 0.15:
        conf = "alta"
    elif melhor[1] >= 0.6 and folga >= 0.08:
        conf = "média"
    else:
        conf = "baixa"
    return melhor[3], conf, round(melhor[1], 2)


def preferencia(item):
    """Entre itens duplicados da mesma campanha e mês, fica o de link /reel/, depois o que tem investimento."""
    link = (item.get("link") or "").lower()
    return (2 if "/reel/" in link else 0) + (1 if (item.get("investimento") or "").strip() else 0)


def janela_meses(hoje):
    """Meses cujos itens recebem Investimento/Seguidores: o atual e, até o dia 3, o anterior."""
    atual = f"{hoje.year}-{hoje.month:02d}"
    if hoje.day > 3:
        return {atual}
    ant = hoje.replace(day=1) - dt.timedelta(days=1)
    return {atual, f"{ant.year}-{ant.month:02d}"}


def ler_campanhas(n, d):
    campanhas = []
    for e in (d or "").split(" ## "):
        if not e.strip():
            continue
        p = e.split("|", 6)
        if len(p) != 7:
            raise SystemExit(f"Entrada inválida em campanhas: {e[:80]!r}")
        campanhas.append({"id": p[0], "ini": p[1], "fim": p[2], "curtidas": int(p[3]), "comentarios": int(p[4]),
                          "views": int(p[5]), "nome": p[6].strip()})
    if len(campanhas) != int(n):
        raise SystemExit(f"Campanhas: vieram {len(campanhas)}, esperado {n}. Copie a resposta de novo.")
    return campanhas


def ler_meses(n, d):
    meses = {}
    for e in (d or "").split():
        p = e.split("|")
        if len(p) != 4 or not re.fullmatch(r"\d{4}-\d{2}", p[1]):
            raise SystemExit(f"Entrada inválida em meses: {e!r}")
        meses[(p[0], p[1])] = (float(p[2]), int(p[3]))
    if len(meses) != int(n):
        raise SystemExit(f"Meses: vieram {len(meses)}, esperado {n}. Copie a resposta de novo.")
    return meses


def seguidores(meses, camp, mes, meses_anteriores):
    """(pré, pós): seguidores acumulados no impulsionamento antes do mês e até o fim do mês.
    meses_anteriores: meses dos outros itens do mesmo impulsionamento (mesma campanha e mesmo Início)."""
    pre = sum(meses.get((camp, m), (0, 0))[1] for m in meses_anteriores if m < mes)
    return pre, pre + meses.get((camp, mes), (0, 0))[1]


def planejar(itens, campanhas, meses, hoje):
    """Decide Meta IDs novos e valores de cada item. Puro: recebe itens já normalizados
    ({id, nome, mes, inicio, publico, link, meta, investimento, ...}) e devolve (metas, valores, avisos)."""
    por_id = {c["id"]: c for c in campanhas}
    metas, avisos = {}, []
    # 1. casar itens sem Meta ID (só de meses com campanhas COOKING)
    desde = min((c["ini"][:7] for c in campanhas), default="9999-99")
    for it in itens:
        if it["meta"] or not it["mes"] or it["mes"] < desde:
            continue
        camp, conf, s = casar_item(it["nome"], it["inicio"], it["publico"], campanhas)
        if camp and conf == "alta":
            metas[it["id"]] = "camp:" + camp["id"]
        elif it["mes"] in janela_meses(hoje):
            sugestao = f" (talvez '{camp['nome'][:50]}', semelhança {s})" if camp else ""
            avisos.append(f"Sem campanha certa para '{it['nome'][:60]}' ({it['mes']}){sugestao}: preencha o Meta ID à mão.")
    # 2. duplicados: mesma campanha no mesmo mês -> fica um, os outros viram 'ignorar'
    efetivo = {it["id"]: metas.get(it["id"], it["meta"]) for it in itens}
    grupos = {}
    for it in itens:
        m = efetivo[it["id"]]
        if m and m.startswith("camp:"):
            grupos.setdefault((m, it["mes"]), []).append(it)
    for (m, mes), lista in grupos.items():
        if len(lista) > 1:
            lista.sort(key=lambda x: (-preferencia(x), x["id"]))
            for it in lista[1:]:
                metas[it["id"]] = IGNORAR
                efetivo[it["id"]] = IGNORAR
    # 3. valores
    ultimo = {}
    for it in itens:
        m = efetivo[it["id"]]
        if m and m.startswith("camp:") and it["mes"]:
            ultimo[m] = max(ultimo.get(m, ""), it["mes"])
    janela = janela_meses(hoje)
    impuls = {}
    for it in itens:
        m = efetivo[it["id"]]
        if m and m.startswith("camp:") and it["mes"]:
            impuls.setdefault((m, it["inicio"]), set()).add(it["mes"])
    valores = {}
    for it in itens:
        m = efetivo[it["id"]]
        if not (m and m.startswith("camp:")):
            continue
        cid = m[5:]
        c = por_id.get(cid)
        if not c:
            avisos.append(f"Meta ID {m} de '{it['nome'][:50]}' não está entre as campanhas COOKING do CrazyLeads.")
            continue
        v = {}
        if it["mes"] == ultimo[m]:
            v.update(curtidas=c["curtidas"], comentarios=c["comentarios"], views=c["views"])
        if it["mes"] in janela:
            pre, pos = seguidores(meses, cid, it["mes"], impuls[(m, it["inicio"])])
            v.update(investimento=f"{meses.get((cid, it['mes']), (0, 0))[0]:.2f}", seg_pre=pre, seg_pos=pos)
        if v:
            valores[it["id"]] = v
    return metas, valores, avisos


def diff(atual, novo):
    """Compara texto do Monday com o valor novo, ignorando formatação de número."""
    def n(x):
        x = str(x if x is not None else "").strip().replace(",", ".")
        try:
            return f"{float(x):.2f}"
        except ValueError:
            return x
    return n(atual) != n(novo)


def rebaixa(campo, atual, novo):
    """No mês corrente gasto e seguidores só crescem: um valor menor que o do quadro é dado do CrazyLeads
    ainda não sincronizado (o quadro pode ter sido atualizado à mão mais cedo). Não grava nesse caso."""
    if campo not in ("investimento", "seg_pos"):
        return False
    try:
        return float(str(novo)) < float(str(atual).replace(",", "."))
    except ValueError:
        return False


# ───────── comandos ─────────

def _ler_resp(nome):
    caminho = os.path.join(ESTADO, nome)
    if not os.path.exists(caminho):
        raise SystemExit(f"Falta estado/{nome}.")
    with open(caminho, encoding="utf-8") as f:
        partes = f.read().strip().split("\n", 1)
    d = partes[1].strip() if len(partes) > 1 else ""
    return partes[0].strip(), (None if d in ("", "null", "None") else d)


def cmd_listar(args, cfg):
    q = cfg_quadro(cfg)
    api = sync.Monday(os.environ.get("MONDAY_TOKEN"))
    b = api.colunas(q["id"])
    cols = {}
    for chave, titulo in q["colunas"].items():
        c = sync.achar_coluna(b["columns"], titulo)
        if not c:
            raise SystemExit(f"Quadro '{b['name']}' sem a coluna '{titulo}'.")
        cols[chave] = c["id"]
    itens = api.itens(q["id"], list(cols.values()))
    os.makedirs(ESTADO, exist_ok=True)
    with open(os.path.join(ESTADO, "ig_itens.json"), "w", encoding="utf-8") as f:
        json.dump({"nome": b["name"], "colunas": cols, "itens": itens}, f, ensure_ascii=False)
    print(f"{b['name']}: {len(itens)} itens -> estado/ig_itens.json")


def cmd_sql(args, cfg):
    for nome in ("campanhas", "meses"):
        with open(os.path.join(AQUI, "sql", f"cooking_{nome}.sql"), encoding="utf-8") as f:
            sql = "\n".join(l for l in f.read().splitlines() if not l.startswith("--")).strip()
        with open(os.path.join(ESTADO, f"ig_lote_{nome}.sql"), "w", encoding="utf-8") as f:
            f.write(sql)
    print("estado/ig_lote_campanhas.sql e estado/ig_lote_meses.sql prontos. Rode cada um no CrazyLeads (test_sql) e "
          "grave estado/ig_resp_campanhas.txt e estado/ig_resp_meses.txt (1ª linha n, 2ª linha d).")


def cmd_juntar(args, cfg):
    campanhas = ler_campanhas(*_ler_resp("ig_resp_campanhas.txt"))
    meses = ler_meses(*_ler_resp("ig_resp_meses.txt"))
    with open(os.path.join(ESTADO, "ig_dados.json"), "w", encoding="utf-8") as f:
        json.dump({"campanhas": campanhas, "meses": [[c, m, g, s] for (c, m), (g, s) in meses.items()]},
                  f, ensure_ascii=False)
    print(f"{len(campanhas)} campanhas, {len(meses)} campanha-mês -> estado/ig_dados.json")


def normalizar_itens(bruto):
    cols = bruto["colunas"]
    out = []
    for it in bruto["itens"]:
        v = {c["id"]: c["text"] for c in it["column_values"]}
        out.append({"id": it["id"], "nome": it["name"], "mes": mes_do_grupo(it["group"]["title"]),
                    "inicio": v.get(cols["inicio"]) or "", "publico": v.get(cols.get("publico", "")) or "",
                    "link": v.get(cols["link"]) or "", "meta": (v.get(cols["meta_id"]) or "").strip(),
                    "investimento": v.get(cols["investimento"]) or "", "atual": {k: v.get(cid) for k, cid in cols.items()}})
    return out


def cmd_aplicar(args, cfg):
    q = cfg_quadro(cfg)
    with open(os.path.join(ESTADO, "ig_itens.json"), encoding="utf-8") as f:
        bruto = json.load(f)
    with open(os.path.join(ESTADO, "ig_dados.json"), encoding="utf-8") as f:
        dados = json.load(f)
    meses = {(c, m): (g, s) for c, m, g, s in dados["meses"]}
    hoje = dt.date.fromisoformat(args.hoje) if args.hoje else dt.date.today()
    itens = normalizar_itens(bruto)
    metas, valores, avisos = planejar(itens, dados["campanhas"], meses, hoje)
    cols = bruto["colunas"]
    api = sync.Monday(os.environ.get("MONDAY_TOKEN")) if args.executar else None
    gravados = 0
    for it in itens:
        muda = {}
        if it["id"] in metas and metas[it["id"]] != it["meta"]:
            muda[cols["meta_id"]] = metas[it["id"]]
        for campo, valor in valores.get(it["id"], {}).items():
            if diff(it["atual"].get(campo), valor) and not rebaixa(campo, it["atual"].get(campo), valor):
                muda[cols[campo]] = str(valor)
        if not muda:
            continue
        gravados += 1
        if args.executar:
            api.gravar(q["id"], it["id"], muda)
            time.sleep(0.25)
        elif args.verbose:
            nomes = {cid: k for k, cid in cols.items()}
            print(f"  {it['mes']} · {it['nome'][:55]}: " + ", ".join(f"{nomes[c]}={v}" for c, v in muda.items()))
    novos = sum(1 for m in metas.values() if m != IGNORAR)
    print(f"{bruto['nome']}: {gravados} itens {'gravados' if args.executar else 'a gravar (simulação)'} · "
          f"{novos} Meta IDs novos · {sum(1 for m in metas.values() if m == IGNORAR)} marcados 'ignorar' · "
          f"meses com investimento/seguidores: {', '.join(sorted(janela_meses(hoje)))}")
    for a in avisos:
        print("! " + a)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar")
    sub.add_parser("sql")
    sub.add_parser("juntar")
    a = sub.add_parser("aplicar")
    a.add_argument("--executar", action="store_true", help="escreve no Monday (sem isso é só simulação)")
    a.add_argument("--hoje", help="data de referência AAAA-MM-DD (padrão: hoje)")
    a.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    cfg = sync.carregar_config(args.config)
    {"listar": cmd_listar, "sql": cmd_sql, "juntar": cmd_juntar, "aplicar": cmd_aplicar}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
