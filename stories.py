"""Stories do PIVÔ: a cada horário da grade (9, 11, 13, 15, 17 e 19h, Brasília) posta o story do post que saiu
NAQUELE MESMO horário ontem (24 h atrás). Funciona para tudo o que está no perfil, venha do robô ou do Business Suite:
o post é achado pelo horário de publicação (GET /me/media). A imagem do story (1080x1920) é montada aqui."""
import io
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PASTA = Path(__file__).resolve().parent
BRT = timezone(timedelta(hours=-3))
SLOTS = [9, 11, 13, 15, 17, 19]   # horas de Brasília
ATRASO_MAX_H = 4                   # se o robô ficou fora do ar, ainda posta até 4 h depois do horário
TOLERANCIA_MIN = 50                # o post de ontem pode ter saído até 50 min antes/depois do horário
FRASES = ["CONFERE LÁ!", "DÁ UMA MORAL LÁ!", "JÁ VIU ESSE?", "PASSA LÁ E CONFERE!", "VALE A PENA VER!",
          "NÃO PERDE ESSE!", "OLHA SÓ ESSE!", "DÁ UMA OLHADA!", "VEM VER ESSE!", "CONFERE ESSE AQUI!"]
SUBS = ["Saiu ontem no feed", "O post de ontem no feed", "Ontem, no feed do PIVÔ"]
FUNDO = (14, 19, 71)
AMARELO = (255, 214, 80)
LILAS = (190, 185, 255)


def _fonte(nome, tam):
    return ImageFont.truetype(str(PASTA / "fonts" / nome), tam)


def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


def montar(capa, frase, sub):
    """Story 1080x1920: frase em cima, capa do post (recorte 4:5 central) com cantos redondos e sombra."""
    W, H = 1080, 1920
    im = Image.new("RGB", (W, H), FUNDO)
    d = ImageDraw.Draw(im)

    def centro(t, y, f, cor):
        d.text(((W - d.textlength(t, font=f)) / 2, y), t, font=f, fill=cor)

    f1 = _fonte("Poppins-Black.ttf", 112)
    while d.textlength(frase, font=f1) > 960:
        f1 = _fonte("Poppins-Black.ttf", f1.size - 6)
    f2 = _fonte("Poppins-SemiBold.ttf", 40)
    centro(frase, 225, f1, AMARELO)
    centro(sub, 395, f2, (255, 255, 255))
    cw = 840
    ch = int(cw * 1350 / 1080)
    # recorte 4:5 no centro (a capa dos reels é 9:16; o miolo 4:5 já é a área segura)
    w, h = capa.size
    alvo = 4 / 5
    if w / h > alvo:
        nw = int(h * alvo)
        capa = capa.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / alvo)
        capa = capa.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    capa = capa.resize((cw, ch), Image.LANCZOS)
    x, y = (W - cw) // 2, 480
    mask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, cw, ch), 40, fill=255)
    sombra = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle((x, y + 18, x + cw, y + ch + 18), 40, fill=(0, 0, 0, 150))
    im = Image.alpha_composite(im.convert("RGBA"), sombra.filter(ImageFilter.GaussianBlur(25))).convert("RGB")
    im.paste(capa, (x, y), mask)
    d = ImageDraw.Draw(im)
    centro("@pivoeconomico", y + ch + 45, f2, LILAS)
    return im


def achar_post(chamar, api, token, uid, alvo_utc):
    """O post do perfil cuja publicação é a mais próxima de alvo_utc (dentro da tolerância)."""
    campos = "id,media_type,media_product_type,media_url,thumbnail_url,timestamp,permalink"
    r = chamar(f"{api}/{uid}/media?fields={campos}&limit=50&access_token={token}")
    melhor, dif = None, None
    for m in r.get("data", []):
        if m.get("media_product_type") == "STORY":
            continue
        t = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        dd = abs((t - alvo_utc).total_seconds()) / 60
        if dd <= TOLERANCIA_MIN and (dif is None or dd < dif):
            melhor, dif = m, dd
    return melhor


def slots_devidos(agora_utc):
    """Horários de hoje (Brasília) que já chegaram e ainda estão dentro da janela de atraso."""
    agora = agora_utc.astimezone(BRT)
    out = []
    for h in SLOTS:
        s = agora.replace(hour=h, minute=0, second=0, microsecond=0)
        if s - timedelta(minutes=6) <= agora <= s + timedelta(hours=ATRASO_MAX_H):
            out.append(s)
    return out


def publicar_pendentes(chamar, esperar, api, token, uid, base_url, postados, dry):
    """Posta os stories devidos. Devolve True se mexeu em postados."""
    agora = datetime.now(timezone.utc)
    mudou = False
    reg = postados.setdefault("_stories", {})
    for s in slots_devidos(agora):
        chave = s.strftime("%Y-%m-%d_%H")
        if chave in reg and ("media_id" in reg[chave] or "pulado" in reg[chave]):
            continue
        try:
            alvo = (s - timedelta(days=1)).astimezone(timezone.utc)
            post = achar_post(chamar, api, token, uid, alvo)
            if not post:
                reg[chave] = {"pulado": "sem post de ontem nesse horário"}
                mudou = True
                print("STORY sem post de ontem:", chave, flush=True)
                continue
            url_capa = post.get("thumbnail_url") if post["media_type"] == "VIDEO" else post.get("media_url")
            if not url_capa:
                raise RuntimeError("post sem imagem de capa: " + post["id"])
            n = int(s.strftime("%j")) * 7 + s.hour
            im = montar(baixar(url_capa), FRASES[n % len(FRASES)], SUBS[(n // 3) % len(SUBS)])
            nome = f"stories/{chave}.jpg"
            (PASTA / "stories").mkdir(exist_ok=True)
            im.save(PASTA / nome, "JPEG", quality=93)
            _publicar_arquivo(nome, base_url)
            cid = chamar(f"{api}/{uid}/media", {"media_type": "STORIES", "image_url": f"{base_url}/{nome}",
                                                "access_token": token})["id"]
            esperar(cid, 18)
            falta = (s.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
            if falta > 0 and not dry:
                time.sleep(falta)
            mid = "TESTE-" + cid if dry else chamar(f"{api}/{uid}/media_publish", {"creation_id": cid, "access_token": token})["id"]
            reg[chave] = {"media_id": mid, "do_post": post["id"], "em": datetime.now(timezone.utc).isoformat()}
            postados.get("_erros", {}).pop(f"story:{chave}", None)
            print("STORY POSTADO", chave, "do post", post.get("permalink"), flush=True)
        except Exception as e:  # noqa: BLE001
            postados.setdefault("_erros", {})[f"story:{chave}"] = str(e)[:300]
            print("ERRO story", chave, e, flush=True)
        mudou = True
    return mudou


def _publicar_arquivo(nome, base_url):
    """No GitHub Actions: sobe a imagem no repositório e espera o Pages servir. Local: não faz nada."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for old in sorted((PASTA / "stories").glob("*.jpg"))[:-14]:  # mantém só as últimas 14 imagens
        old.unlink()
    subprocess.run(["git", "config", "user.name", "robo-pivo"], check=True)
    subprocess.run(["git", "config", "user.email", "robo@pivo.local"], check=True)
    subprocess.run(["git", "add", "-A", "stories"], check=True)
    subprocess.run(["git", "commit", "-m", "story " + nome], check=False)
    subprocess.run(["git", "pull", "--rebase", "--autostash", "-q"], check=False)
    subprocess.run(["git", "push", "-q"], check=True)
    for _ in range(40):  # até ~6 min
        try:
            req = urllib.request.Request(f"{base_url}/{nome}", method="HEAD")
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 200:
                    return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(10)
    raise RuntimeError("o Pages não serviu a imagem do story a tempo")
