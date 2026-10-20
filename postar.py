"""Robô do PIVÔ: publica no Instagram (API oficial) os itens de agenda.json cuja hora já passou.
Roda no GitHub Actions a cada 15 minutos (sem o PC ligado). Só usa a biblioteca padrão.
Variáveis: IG_TOKEN (secret), BASE_URL (endereço público da pasta, ex.: https://usuario.github.io/pivo-postador)."""
import json
import os
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
        dados = {"media_type": "REELS", "video_url": base + item["video"], "caption": item["legenda"],
                 "share_to_feed": "true", "access_token": TOKEN}
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


def main():
    agenda = json.loads((PASTA / "agenda.json").read_text(encoding="utf-8"))
    pf = PASTA / ("teste_a_seco.json" if DRY_RUN else "postados.json")
    postados = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
    agora = datetime.now(timezone.utc)
    uid = chamar(f"{API}/me?fields=user_id&access_token={TOKEN}")["user_id"]
    feitos = 0
    for item in sorted(agenda, key=lambda x: x["quando"]):
        if item["id"] in postados:
            continue
        if datetime.fromisoformat(item["quando"]) > agora + timedelta(minutes=JANELA_MIN) and not DRY_RUN:
            continue
        if feitos >= MAX_POR_RODADA:
            break
        try:
            mid = publicar(item, uid)
            postados[item["id"]] = {"media_id": mid, "em": agora.isoformat()}
            print("POSTADO", item["id"], mid, flush=True)
        except Exception as e:  # noqa: BLE001
            postados.setdefault("_erros", {})[item["id"]] = str(e)[:300]
            print("ERRO", item["id"], e, flush=True)
        feitos += 1
        pf.write_text(json.dumps(postados, ensure_ascii=False, indent=1), encoding="utf-8")
    print("rodada ok:", feitos, "item(ns)")


if __name__ == "__main__":
    sys.exit(main())
