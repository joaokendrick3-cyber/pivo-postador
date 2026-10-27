"""Robô do PIVÔ: publica no Instagram e na Página do Facebook (APIs oficiais) os itens de agenda.json cuja hora já passou.
Roda no GitHub Actions a cada 15 minutos (sem o PC ligado). Só usa a biblioteca padrão.
Variáveis: IG_TOKEN, FB_PAGE_ID e FB_PAGE_TOKEN (secrets), BASE_URL (endereço público da pasta, ex.: https://usuario.github.io/pivo-postador)."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PASTA = Path(__file__).resolve().parent
API = "https://graph.instagram.com/v23.0"
TOKEN = os.environ["IG_TOKEN"].strip()
BASE_URL = os.environ["BASE_URL"].rstrip("/")
FB_ID = os.environ.get("FB_PAGE_ID", "").strip()
FB_TOKEN = os.environ.get("FB_PAGE_TOKEN", "").strip()
FB_API = "https://graph.facebook.com/v23.0"
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
JANELA_MIN = 6  # o gatilho externo chega ~2 min antes da hora; o robô prepara e espera
MAX_POR_RODADA = 20 if DRY_RUN else 4


def chamar(url, dados=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(dados).encode() if dados else None)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        corpo = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code}: {corpo[:400]}") from None


def esperar(cid, tentativas=60):
    """Espera o Instagram terminar de processar um container (até ~10 min)."""
    for _ in range(tentativas):
        time.sleep(10)
        try:
            st = chamar(f"{API}/{cid}?fields=status_code,status&access_token={TOKEN}")
        except RuntimeError as e:
            if '"is_transient":true' in str(e):   # limite de requisições ou erro passageiro: espera mais e continua, sem perder o container
                time.sleep(20)
                continue
            raise
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"processamento falhou: {st}")
    raise RuntimeError("processamento demorou demais")


def criar_container(item, uid):
    base = f"{BASE_URL}/"
    if item["tipo"] == "reel":
        # share_to_feed=false: o reel fica só na aba Reels (a grade principal é só de carrosséis)
        dados = {"media_type": "REELS", "video_url": base + item["video"], "caption": item["legenda"],
                 "share_to_feed": "false", "access_token": TOKEN}
        if item.get("trial"):  # reel de teste: só não seguidores veem; MANUAL = nunca vira reel normal sozinho
            dados["trial_params"] = json.dumps({"graduation_strategy": "MANUAL"})
        if item.get("capa"):
            dados["cover_url"] = base + item["capa"]
        cid = chamar(f"{API}/{uid}/media", dados)["id"]
        esperar(cid)
        return cid
    if item["tipo"] == "carrossel":  # 2 a 10 imagens JPEG; cada imagem vira um container filho
        filhos = []
        for img in item["imagens"]:
            fid = chamar(f"{API}/{uid}/media", {"image_url": base + img, "is_carousel_item": "true",
                                                "access_token": TOKEN})["id"]
            esperar(fid, 18)
            filhos.append(fid)
        cid = chamar(f"{API}/{uid}/media", {"media_type": "CAROUSEL", "children": ",".join(filhos),
                                            "caption": item["legenda"], "access_token": TOKEN})["id"]
        esperar(cid, 18)
        return cid
    raise RuntimeError("tipo não suportado: " + item["tipo"])


_CONTAINERS = {}   # id do item -> container já criado (reaproveitado nas novas tentativas; expira em 24 h no Instagram)


def publicar(item, uid):
    cid = _CONTAINERS.get(item["id"])
    try:
        if not cid:
            cid = criar_container(item, uid)
            _CONTAINERS[item["id"]] = cid
        else:
            esperar(cid, 30)
    except RuntimeError as e:
        raise RuntimeError(f"[criar/processar] {e}") from None
    espera = (datetime.fromisoformat(item["quando"]) - datetime.now(timezone.utc)).total_seconds()
    if espera > 0 and not DRY_RUN:  # container pronto antes da hora: espera e publica no minuto certo
        print("aguardando", int(espera), "s para", item["id"], flush=True)
        time.sleep(espera)
    if DRY_RUN:  # teste a seco: o item foi aceito pelo Instagram, mas NÃO é publicado
        return "TESTE-" + cid
    try:
        mid = chamar(f"{API}/{uid}/media_publish", {"creation_id": cid, "access_token": TOKEN})["id"]
    except RuntimeError as e:
        raise RuntimeError(f"[media_publish] {e}") from None
    _CONTAINERS.pop(item["id"], None)
    return mid


def esperar_hora(item):
    espera = (datetime.fromisoformat(item["quando"]) - datetime.now(timezone.utc)).total_seconds()
    if espera > 0 and not DRY_RUN:
        print("aguardando", int(espera), "s para", item["id"], flush=True)
        time.sleep(espera)


def multipart(url, campos, arquivo):
    """POST multipart/form-data com um arquivo (campo 'source')."""
    lim = "----pivo" + str(int(time.time() * 1000))
    nl = bytes([13, 10])  # CRLF
    partes = []
    for k, v in campos.items():
        partes.append(("--" + lim).encode() + nl + f'Content-Disposition: form-data; name="{k}"'.encode() + nl + nl + str(v).encode())
    partes.append(("--" + lim).encode() + nl + f'Content-Disposition: form-data; name="source"; filename="{arquivo.name}"'.encode()
                  + nl + b"Content-Type: image/jpeg" + nl + nl + arquivo.read_bytes())
    corpo = nl.join(partes) + nl + ("--" + lim + "--").encode() + nl
    req = urllib.request.Request(url, data=corpo, headers={"Content-Type": f"multipart/form-data; boundary={lim}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from None


def publicar_fb(item):
    """Publica o mesmo item na Página do Facebook (vídeo ou carrossel de fotos)."""
    base = f"{BASE_URL}/"
    if DRY_RUN:  # teste a seco: só confirma que o token enxerga a Página
        nome = chamar(f"{FB_API}/{FB_ID}?fields=name&access_token={FB_TOKEN}")["name"]
        return "TESTE-FB-" + nome
    if item["tipo"] == "reel":
        esperar_hora(item)
        r = chamar(f"{FB_API}/{FB_ID}/videos", {"file_url": base + item["video"], "description": item["legenda"],
                                                "published": "true", "access_token": FB_TOKEN})
        vid = r["id"]
        if item.get("capa"):  # capa própria (se falhar, o Facebook escolhe um quadro)
            try:
                multipart(f"{FB_API}/{vid}/thumbnails", {"is_preferred": "true", "access_token": FB_TOKEN}, PASTA / item["capa"])
            except Exception as e:  # noqa: BLE001
                print("capa do Facebook não aplicada:", e, flush=True)
        return vid
    if item["tipo"] == "carrossel":
        ids = []
        for img in item["imagens"]:
            ids.append(chamar(f"{FB_API}/{FB_ID}/photos", {"url": base + img, "published": "false",
                                                           "access_token": FB_TOKEN})["id"])
        esperar_hora(item)
        anexos = json.dumps([{"media_fbid": i} for i in ids])
        return chamar(f"{FB_API}/{FB_ID}/feed", {"message": item["legenda"], "attached_media": anexos,
                                                 "access_token": FB_TOKEN})["id"]
    raise RuntimeError("tipo não suportado: " + item["tipo"])


PAUSA_FB_HORAS = 24   # depois de um erro 368 (limite de spam) o Facebook fica quieto, para não prolongar o bloqueio
FB_PAUSADO = False


def redes_do_item(item):
    r = item.get("redes") or ["ig", "fb"]
    return [x for x in r if x == "ig" or (x == "fb" and FB_ID and FB_TOKEN and not FB_PAUSADO)]


def estado_do_item(postados, iid):
    v = postados.get(iid)
    if not v:
        return {}
    return {"ig": v} if "media_id" in v else v   # formato antigo era só do Instagram


ESPERA_GLOBAL = [datetime.min.replace(tzinfo=timezone.utc)]   # até quando não tentar postar nada (limite de requisições da API)
ESPERA_ERRO = {}   # (item, rede) -> até quando não tentar de novo (evita martelar a API depois de um erro)
_UID = None


def uid_ig():
    global _UID
    if _UID is None:
        _UID = chamar(f"{API}/me?fields=user_id&access_token={TOKEN}")["user_id"]
    return _UID


# ---- limite (não oficial) de reels de TESTE do Instagram: relatos de ~20 por dia; aqui o robô falhou no 25º teste em 24 h (26/10, 20h10 UTC:
# 24 testes entre 25/10 21h30 e 26/10 20h10, depois só erro 500 no media_publish). Por segurança: no máximo 24 testes em 24 h (o que já funcionou) e 40 min entre dois testes.
TRIAL_MAX_24H = 24
TRIAL_ESPACO_MIN = 40


def trials_recentes(postados, agora):
    """Horários (UTC) em que testes foram ao ar nas últimas 24 h."""
    ts = []
    for k, v in postados.items():
        if k.endswith("-trial") and isinstance(v, dict) and "ig" in v:
            t = datetime.fromisoformat(v["ig"]["em"])
            if t > agora - timedelta(hours=24):
                ts.append(t)
    return sorted(ts)


TRIAL_NAO_ANTES = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)   # 08h de Brasília: o Instagram descansa a noite toda antes da próxima tentativa (pedido do João)


def sem_vaga_de_teste(postados, agora):
    if agora < TRIAL_NAO_ANTES:
        return True
    ts = trials_recentes(postados, agora)
    return len(ts) >= TRIAL_MAX_24H or (ts and (agora - ts[-1]).total_seconds() < TRIAL_ESPACO_MIN * 60)


FALHAS_GERAIS = [0]   # falhas seguidas de itens diferentes (zera quando algo posta)
FALHAS_SEGUIDAS = {}   # (item, rede) -> quantas falhas seguidas (zera quando posta)


def anotar_erro(chave, erro):
    limite = "request limit" in erro or '"code":4,' in erro
    n = FALHAS_SEGUIDAS[chave] = FALHAS_SEGUIDAS.get(chave, 0) + 1
    minutos = 20 if limite else min(60, 5 * 2 ** (n - 1))
    ESPERA_ERRO[chave] = datetime.now(timezone.utc) + timedelta(minutes=minutos)


def ja_no_perfil(item, uid):
    """Trava anti-duplicata: o mesmo texto já está no perfil, publicado perto da hora do item?"""
    r = chamar(f"{API}/{uid}/media?fields=caption,timestamp&limit=30&access_token={TOKEN}")
    alvo = datetime.fromisoformat(item["quando"])
    for m in r.get("data", []):
        if (m.get("caption") or "")[:80] == item["legenda"][:80]:
            t = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            if abs((t - alvo).total_seconds()) < 6 * 3600:
                return m["id"]
    return None


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def salvar_remoto(msg="postados"):
    """No GitHub Actions: envia postados.json ao repositório logo depois de cada postagem (se a rodada for
    interrompida, o estado não se perde e nada é postado duas vezes)."""
    if DRY_RUN or os.environ.get("GITHUB_ACTIONS") != "true":
        return
    git("config", "user.name", "robo-pivo")
    git("config", "user.email", "robo@pivo.local")
    git("add", "postados.json", "stories", "videos", "capas")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return
    git("commit", "-m", msg)
    for _ in range(4):
        git("pull", "--rebase", "--autostash", "-q")
        if git("push", "-q").returncode == 0:
            return
        time.sleep(5)
    print("AVISO: não consegui enviar postados.json agora", flush=True)


def trials_do_dia_anterior(agenda, postados, agora):
    """Todo reel da agenda ganha um reel de teste (trial, só Instagram) 24 h antes da hora dele.
    Reel já postado ou com o teste já vencido (hora do teste passou) não ganha teste."""
    extra = []
    for it in agenda:
        if it["tipo"] != "reel" or it.get("trial") or "sem_trial" in it or it["id"] in postados:
            continue
        t = datetime.fromisoformat(it["quando"]) - timedelta(days=1)
        if t < agora - timedelta(hours=3):   # teste atrasado até 3 h ainda vale (se a API ficou fora do ar)
            continue
        extra.append({**it, "id": it["id"] + "-trial", "quando": t.isoformat(), "trial": True, "redes": ["ig"]})
    return extra


# ---- campeonatos de testes (regra do João, 26/10): cada série de cortes (ex.: pe = Primo Rico pós-eleição, rs = Raul Sena)
# entra como TESTE (trial). 24 h depois de ir ao ar, o robô mede views e curtidas de cada um; na data de avaliação do
# campeonato (campeonatos.json) ele escolhe os N melhores e os posta DE VERDADE (só aba Reels) nas vagas marcadas.
# Não existe mais graduação automática por 10 mil views.
MEDIR_APOS_H = 24              # idade do teste na hora de medir (todos são comparados com a mesma idade)
_ULTIMA_MEDIDA = [None]


def metricas_do_reel(mid):
    """(views, curtidas) lifetime do reel; curtidas 0 se a API não devolver."""
    views = None
    for metrica in ("views", "plays"):
        try:
            r = chamar(f"{API}/{mid}/insights?metric={metrica}&access_token={TOKEN}")
            views = int(r["data"][0]["values"][0]["value"])
            break
        except Exception as e:  # noqa: BLE001
            ultimo = str(e)
    if views is None:
        raise RuntimeError(ultimo[:200])
    try:
        r = chamar(f"{API}/{mid}/insights?metric=likes&access_token={TOKEN}")
        curtidas = int(r["data"][0]["values"][0]["value"])
    except Exception:  # noqa: BLE001
        curtidas = 0
    return views, curtidas


def campeonatos(pasta=None):
    f = (pasta or PASTA) / "campeonatos.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def testes_do_campeonato(camp, agenda):
    return [i for i in agenda if i.get("trial") and i["id"].startswith(camp["prefixo"]) and i["id"].endswith("-trial")]


def medir_testes(agenda, postados, agora):
    """Lê views/curtidas de cada teste das séries em campeonato quando ele completa 24 h no ar (uma vez só). Roda a cada 30 min."""
    if DRY_RUN or (_ULTIMA_MEDIDA[0] and (agora - _ULTIMA_MEDIDA[0]).total_seconds() < 1800):
        return False
    _ULTIMA_MEDIDA[0] = agora
    med = postados.setdefault("_medidas", {})
    mudou = False
    for camp in campeonatos():
        if camp["nome"] in postados.get("_finais", {}):
            continue
        for it in testes_do_campeonato(camp, agenda):
            est = postados.get(it["id"], {}).get("ig")
            base = it["id"][:-len("-trial")]
            if not est or "views" in med.get(base, {}):
                continue
            horas = (agora - datetime.fromisoformat(est["em"])).total_seconds() / 3600
            if horas < MEDIR_APOS_H:
                continue
            try:
                v, c = metricas_do_reel(est["media_id"])
                med[base] = {"views": v, "curtidas": c, "medido_em": agora.isoformat(), "idade_h": round(horas, 1)}
            except Exception as e:  # noqa: BLE001
                med[base] = {"erro": str(e)[:150]}
            mudou = True
    return mudou


def escolher_finais(agenda, postados, agora):
    """Na data de avaliação de cada campeonato, ordena os testes por views e curtidas (soma das duas posições) e guarda os N melhores
    com as vagas de postagem em postados['_finais']. Quem ainda não tem medida de 24 h é medido agora (idade menor, melhor que nada)."""
    mudou = False
    for camp in campeonatos():
        nome = camp["nome"]
        if nome in postados.get("_finais", {}) or agora < datetime.fromisoformat(camp["avaliar_apos"]):
            continue
        med = postados.setdefault("_medidas", {})
        cand = {}
        for it in testes_do_campeonato(camp, agenda):
            est = postados.get(it["id"], {}).get("ig")
            base = it["id"][:-len("-trial")]
            if not est:
                continue
            m = med.get(base, {})
            if "views" not in m:
                try:
                    v, c = metricas_do_reel(est["media_id"])
                    m = med[base] = {"views": v, "curtidas": c, "medido_em": agora.isoformat(), "idade_h": None}
                except Exception as e:  # noqa: BLE001
                    med[base] = {"erro": str(e)[:150]}
                    continue
            cand[base] = m
        por_v = sorted(cand, key=lambda b: -cand[b]["views"])
        por_c = sorted(cand, key=lambda b: -cand[b]["curtidas"])
        pos = {b: por_v.index(b) + por_c.index(b) for b in cand}
        ranking = sorted(cand, key=lambda b: (pos[b], -cand[b]["views"]))
        escolhidos = ranking[:len(camp["vagas"])]
        # plano B: se o Instagram não aceitou testes suficientes, completa as vagas com a ordem de preferência (corte vai direto, sem teste)
        todos = [i["id"][:-len("-trial")] for i in testes_do_campeonato(camp, agenda)]
        pref = [b for b in camp.get("preferencia", []) if b in todos] + [b for b in todos if b not in camp.get("preferencia", [])]
        sem_teste = [b for b in pref if b not in cand][:max(0, len(camp["vagas"]) - len(escolhidos))]
        itens = [{"base": b, "quando": q, "views": cand[b]["views"], "curtidas": cand[b]["curtidas"]}
                 for b, q in zip(escolhidos, camp["vagas"])]
        for b, q in zip(sem_teste, camp["vagas"][len(escolhidos):]):
            itens.append({"base": b, "quando": q, "views": None, "curtidas": None, "sem_teste": True})
        usados = {e["base"] for e in itens}
        postados.setdefault("_finais", {})[nome] = {
            "escolhido_em": agora.isoformat(), "candidatos": len(cand), "completados_sem_teste": len(sem_teste),
            "itens": itens, "perdedores": [b for b in ranking if b not in usados],
            "cancelados": [b + "-trial" for b in todos if b not in cand and b not in usados]}
        print("CAMPEONATO", nome, "decidido:", escolhidos, flush=True)
        mudou = True
    return mudou


def itens_finais(agenda, postados):
    """Os vencedores viram itens normais da agenda (reel de verdade, só Instagram, sem teste extra)."""
    por_id = {i["id"]: i for i in agenda}
    extra = []
    for nome, f in postados.get("_finais", {}).items():
        for e in f["itens"]:
            it = por_id.get(e["base"] + "-trial")
            if not it:
                continue
            real = {**it, "id": e["base"] + "-final", "quando": e["quando"], "redes": ["ig"], "sem_trial": True}
            real.pop("trial", None)
            real.pop("graduar", None)
            extra.append(real)
    return extra


def arquivos_em_uso(agenda, postados):
    """Vídeos e capas ainda necessários: de itens que faltam postar e dos testes de campeonatos ainda sem decisão."""
    em_campeonato = [c for c in campeonatos() if c["nome"] not in postados.get("_finais", {})]
    usados = set()
    for it in agenda:
        est = estado_do_item(postados, it["id"])
        falta = any(r not in est for r in redes_do_item(it)) or not redes_do_item(it)
        candidato = it.get("trial") and any(it["id"].startswith(c["prefixo"]) for c in em_campeonato)
        if falta or candidato:
            for k in ("video", "capa"):
                if it.get(k):
                    usados.add(it[k].replace("\\", "/"))
    return usados


def limpar_arquivos_sem_uso(agenda, postados):
    """Apaga de videos/ e capas/ o que não é mais preciso (já postado e sem repostagem prevista): o site do robô tem limite de 1 GB.
    Só roda no GitHub Actions e só depois que há campeonato decidido ou item postado."""
    if DRY_RUN or os.environ.get("GITHUB_ACTIONS") != "true":
        return False
    usados = arquivos_em_uso(agenda, postados)
    apagou = False
    for sub in ("videos", "capas"):
        for arq in (PASTA / sub).glob("*"):
            rel = f"{sub}/{arq.name}"
            if arq.is_file() and rel not in usados:
                arq.unlink()
                apagou = True
    return apagou


def rodada():
    global FB_PAUSADO
    agenda = json.loads((PASTA / "agenda.json").read_text(encoding="utf-8"))
    pf = PASTA / ("teste_a_seco.json" if DRY_RUN else "postados.json")
    postados = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
    agora = datetime.now(timezone.utc)
    ate = postados.get("_fb_pausa_ate")
    FB_PAUSADO = bool(ate) and datetime.fromisoformat(ate) > agora
    # pedido do João (27/10): testes avulsos: só os campeonatos pe/rs; sem os rt antigos. O teste automático 24 h antes dos reels da agenda CONTINUA
    agenda = [i for i in agenda if not (i.get("trial") and i["id"].startswith("rt"))]
    agenda = agenda + trials_do_dia_anterior(agenda, postados, agora) + itens_finais(agenda, postados)
    cancel = {c for f in postados.get("_finais", {}).values() for c in f.get("cancelados", [])}
    agenda = [i for i in agenda if i["id"] not in cancel]
    feitos = 0
    retentou = False
    for item in sorted(agenda, key=lambda x: x["quando"]):
        est = estado_do_item(postados, item["id"])
        pend = [r for r in redes_do_item(item) if r not in est]
        if not pend:
            continue
        if datetime.fromisoformat(item["quando"]) > agora + timedelta(minutes=JANELA_MIN) and not DRY_RUN:
            continue
        if feitos >= MAX_POR_RODADA:
            break
        if item.get("trial") and sem_vaga_de_teste(postados, agora):
            continue   # limite de testes do Instagram: espera abrir vaga (24 h depois do mais antigo) sem tentar publicar
        if all(ESPERA_ERRO.get((item["id"], r), agora) > agora for r in pend):
            continue   # ainda esperando depois de um erro: passa para o próximo sem gastar a cota da rodada
        if any(FALHAS_SEGUIDAS.get((item["id"], r)) for r in pend):   # item que já falhou: no máximo 1 nova tentativa por rodada, para não tirar a vez dos outros
            if retentou:
                continue
            retentou = True
        if ESPERA_GLOBAL[0] > agora:
            break      # a API está limitando as chamadas: não insiste agora
        uid = uid_ig()
        for rede in pend:  # Instagram primeiro (ele espera a hora exata); o Facebook só sai se o Instagram saiu
            if ESPERA_ERRO.get((item["id"], rede), agora) > agora:
                break
            try:
                if rede == "ig":
                    existente = None if DRY_RUN else ja_no_perfil(item, uid)
                    if existente:
                        mid = existente
                        print("já estava no perfil, não repito:", item["id"], flush=True)
                    else:
                        mid = publicar(item, uid)
                else:
                    mid = publicar_fb(item)
                FALHAS_SEGUIDAS.pop((item["id"], rede), None)
                FALHAS_GERAIS[0] = 0
                est[rede] = {"media_id": mid, "em": datetime.now(timezone.utc).isoformat()}
                postados.get("_erros", {}).pop(f"{item['id']}:{rede}", None)
                print("POSTADO", rede, item["id"], mid, flush=True)
            except Exception as e:  # noqa: BLE001
                postados.setdefault("_erros", {})[f"{item['id']}:{rede}"] = str(e)[:300]
                print("ERRO", rede, item["id"], e, flush=True)
                anotar_erro((item["id"], rede), str(e))
                if "processamento falhou" in str(e):
                    _CONTAINERS.pop(item["id"], None)
                if "request limit" in str(e) or '"code":4,' in str(e):   # limite de requisições do app: pausa geral de 10 min
                    ESPERA_GLOBAL[0] = datetime.now(timezone.utc) + timedelta(minutes=10)
                FALHAS_GERAIS[0] += 1
                if FALHAS_GERAIS[0] >= 3:   # 3 itens seguidos falhando: o Instagram está recusando tudo; espera 30 min em vez de martelar a API
                    ESPERA_GLOBAL[0] = max(ESPERA_GLOBAL[0], datetime.now(timezone.utc) + timedelta(minutes=30))
                    FALHAS_GERAIS[0] = 0
                    print("Instagram recusando publicações em série: pausa de 30 min", flush=True)
                if rede == "fb" and '"code":368' in str(e):
                    postados["_fb_pausa_ate"] = (datetime.now(timezone.utc) + timedelta(hours=PAUSA_FB_HORAS)).isoformat()
                    FB_PAUSADO = True
                if rede == "ig":
                    break
        if est:
            postados[item["id"]] = est
        feitos += 1
        pf.write_text(json.dumps(postados, ensure_ascii=False, indent=1), encoding="utf-8")
        salvar_remoto("postados")
    try:  # campeonatos: mede os testes com 24 h no ar e, na data de avaliação, escolhe os melhores para postar de verdade
        mudou = medir_testes(agenda, postados, agora)
        mudou = escolher_finais(agenda, postados, agora) or mudou
        if limpar_arquivos_sem_uso(agenda, postados):
            mudou = True
        if mudou:
            pf.write_text(json.dumps(postados, ensure_ascii=False, indent=1), encoding="utf-8")
            salvar_remoto("campeonatos")
    except Exception as e:  # noqa: BLE001
        print("ERRO nos campeonatos:", str(e)[:200], flush=True)
    try:  # stories do post de 24 h atrás (ver stories.py); falha aqui nunca derruba a rodada dos posts
        import stories
        reg = postados.get("_stories", {})
        faltam = [s for s in stories.slots_devidos(agora)
                  if not ({"media_id", "pulado"} & set(reg.get(s.strftime("%Y-%m-%d_%H"), {})))]
        if faltam and ESPERA_ERRO.get(("story", ""), agora) <= agora:
            if stories.publicar_pendentes(chamar, esperar, API, TOKEN, uid_ig(), BASE_URL, postados, DRY_RUN):
                pf.write_text(json.dumps(postados, ensure_ascii=False, indent=1), encoding="utf-8")
                salvar_remoto("postados")
            if any(k.startswith("story:") for k in postados.get("_erros", {})):
                anotar_erro(("story", ""), " ".join(postados["_erros"].values()))
    except Exception as e:  # noqa: BLE001
        print("ERRO nos stories:", e, flush=True)
    if not DRY_RUN:  # respostas a elogios nos comentários (ver comentarios.py); falha aqui nunca derruba a rodada
        try:
            import comentarios
            if comentarios.responder_pendentes(chamar, API, TOKEN, uid_ig(), postados):
                pf.write_text(json.dumps(postados, ensure_ascii=False, indent=1), encoding="utf-8")
                salvar_remoto("postados")
        except Exception as e:  # noqa: BLE001
            print("ERRO nos comentários:", str(e)[:200], flush=True)
    return feitos


def versao_do_codigo():
    import hashlib
    h = hashlib.sha1()
    for nome in ("postar.py", "stories.py", "comentarios.py"):
        f = PASTA / nome
        h.update(f.read_bytes() if f.exists() else b"")
    return h.hexdigest()


def main():
    """LOOP_MINUTOS > 0: fica rodando esse tempo, conferindo a agenda a cada minuto (hora exata, sem depender do
    gatilho externo). Sem a variável, faz uma rodada só (teste a seco e uso manual)."""
    fim = time.time() + int(os.environ.get("LOOP_MINUTOS", "0") or 0) * 60
    n = 0
    versao = versao_do_codigo()
    while True:
        try:
            feitos = rodada()
            if feitos or n % 30 == 0:
                print(datetime.now(timezone.utc).strftime("%H:%M"), "rodada ok:", feitos, "item(ns)", flush=True)
        except Exception as e:  # noqa: BLE001
            print("ERRO na rodada:", e, flush=True)
        n += 1
        if time.time() + 60 > fim:
            break
        time.sleep(60)
        if os.environ.get("GITHUB_ACTIONS") == "true" and n % 3 == 0:
            git("pull", "--rebase", "--autostash", "-q")  # pega agenda nova
            if versao_do_codigo() != versao:
                print("código novo no repositório: encerrando o ciclo para recarregar (o próximo começa sozinho)", flush=True)
                break


if __name__ == "__main__":
    sys.exit(main())
