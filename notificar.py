"""Avisa por e-mail (Gmail) cada vez que o robô posta algo. Sem o segredo GMAIL_APP_SENHA não faz nada; qualquer erro aqui é ignorado
para nunca atrapalhar a postagem."""
import json
import os
import smtplib
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

REMETENTE = "joaokendrick3@gmail.com"
DESTINO = "joaokendrick3@gmail.com"
BRT = timezone(timedelta(hours=-3))


def _link(media_id, api, token):
    try:
        with urllib.request.urlopen(f"{api}/{media_id}?fields=permalink&access_token={token}", timeout=15) as r:
            return json.load(r).get("permalink", "")
    except Exception:  # noqa: BLE001
        return ""


def avisar(rotulo, media_id, api, token):
    senha = os.environ.get("GMAIL_APP_SENHA", "").replace(" ", "")
    if not senha:
        return
    try:
        hora = datetime.now(BRT).strftime("%H:%M")
        link = _link(media_id, api, token)
        msg = EmailMessage()
        msg["Subject"] = f"PIVÔ postou: {rotulo} ({hora})"
        msg["From"] = REMETENTE
        msg["To"] = DESTINO
        msg.set_content(f"{rotulo}\nHorário (Brasília): {hora}\n{link or 'sem link'}\n")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(REMETENTE, senha)
            s.send_message(msg)
    except Exception as e:  # noqa: BLE001
        print("aviso por e-mail falhou:", e, flush=True)
