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
        st = chamar(f"{API}/{cid}?fields=status_code,status&access_token={TOKEN}")
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


def publicar(item, uid):
    cid = criar_container(item, uid)
    espera = (datetime.fromisoformat(item["quando"]) - datetime.now(timezone.utc)).total_seconds()
    if espera > 0 and not DRY_RUN:  # container pronto antes da hora: espera e publica no minuto certo
        print("aguardando", int(espera), "s para", item["id"], flush=True)
        time.sleep(espera)
    if DRY_RUN:  # teste a seco: o item foi aceito pelo Instagram, mas NÃO é publicado
        return "TESTE-" + cid
    return chamar(f"{API}/{uid}/media_publish", {"creation_id": cid, "access_token": TOKEN})["id"]


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


ESPERA_ERRO = {}   # (item, rede) -> até quando não tentar de novo (evita martelar a API depois de um erro)
_UID = None


def uid_ig():
    global _UID
    if _UID is None:
        _UID = chamar(f"{API}/me?fields=user_id&access_token={TOKEN}")["user_id"]
    return _UID


def anotar_erro(chave, erro):
    limite = "request limit" in erro or '"code":4,' in erro
    ESPERA_ERRO[chave] = datetime.now(timezone.utc) + timedelta(minutes=20 if limite else 5)


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
    git("add", "postados.json", "stories")
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
        if t < agora - timedelta(minutes=JANELA_MIN):
            continue
        extra.append({**it, "id": it["id"] + "-trial", "quando": t.isoformat(), "trial": True, "redes": ["ig"]})
    return extra


def rodada():
    global FB_PAUSADO
    agenda = json.loads((PASTA / "agenda.json").read_text(encoding="utf-8"))
    pf = PASTA / ("teste_a_seco.json" if DRY_RUN else "postados.json")
    postados = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
    agora = datetime.now(timezone.utc)
    ate = postados.get("_fb_pausa_ate")
    FB_PAUSADO = bool(ate) and datetime.fromisoformat(ate) > agora
    agenda = agenda + trials_do_dia_anterior(agenda, postados, agora)
    feitos = 0
    for item in sorted(agenda, key=lambda x: x["quando"]):
        est = estado_do_item(postados, item["id"])
        pend = [r for r in redes_do_item(item) if r not in est]
        if not pend:
            continue
        if datetime.fromisoformat(item["quando"]) > agora + timedelta(minutes=JANELA_MIN) and not DRY_RUN:
            continue
        if feitos >= MAX_POR_RODADA:
            break
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
                est[rede] = {"media_id": mid, "em": datetime.now(timezone.utc).isoformat()}
                postados.get("_erros", {}).pop(f"{item['id']}:{rede}", None)
                print("POSTADO", rede, item["id"], mid, flush=True)
            except Exception as e:  # noqa: BLE001
                postados.setdefault("_erros", {})[f"{item['id']}:{rede}"] = str(e)[:300]
                print("ERRO", rede, item["id"], e, flush=True)
                anotar_erro((item["id"], rede), str(e))
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


def main():
    """LOOP_MINUTOS > 0: fica rodando esse tempo, conferindo a agenda a cada minuto (hora exata, sem depender do
    gatilho externo). Sem a variável, faz uma rodada só (teste a seco e uso manual)."""
    fim = time.time() + int(os.environ.get("LOOP_MINUTOS", "0") or 0) * 60
    n = 0
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


if __name__ == "__main__":
    sys.exit(main())
