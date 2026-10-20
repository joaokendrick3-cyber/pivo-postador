"""Gera agenda.json (e copia videos/capas) a partir de uploads/cortes_*. Grade fixa: cortes 13h, 15h, 17h e 19h (horário de Brasília = UTC-3).
Uso: python gerar_agenda.py   (rode de novo se mudar JA_AGENDADO)"""
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
UP = RAIZ.parent / "uploads"
INICIO = datetime(2026, 10, 29)  # primeiro dia dos cortes novos
HORAS = [13, 15, 17, 19]
SERIES = [("moreira", "mr"), ("mendlowicz", "md"), ("primorico", "pr"), ("lemann", "le"), ("flavioaugusto", "fl"), ("camposneto", "cn")]
# já agendados pelo site do Instagram (não repetir): série+número
JA_AGENDADO = {"mr01", "mr02", "mr04", "mr07", "mr11", "mr12"}
FUSO = timezone(timedelta(hours=-3))

agenda = []
k = 0
for slug, pre in SERIES:
    txt = (UP / f"cortes_{slug}" / "legendas.txt").read_text(encoding="utf-8")
    legs = {}
    partes = re.split(r"^POST (\d+) — .*$", txt, flags=re.M)
    for a in range(1, len(partes) - 1, 2):
        corpo = re.split(r"\n\s*[-=]{10,}\s*(?:\n|$)", partes[a + 1].strip("\n"))[0]
        legs[int(partes[a])] = corpo.strip()
    for n in range(1, 13):
        dia = INICIO + timedelta(days=k // 4)
        hora = HORAS[k % 4]
        k += 1
        cid = f"{pre}{n:02d}"
        if cid in JA_AGENDADO:
            continue
        quando = datetime(dia.year, dia.month, dia.day, hora, 0, tzinfo=FUSO).astimezone(timezone.utc)
        (RAIZ / "videos").mkdir(exist_ok=True)
        (RAIZ / "capas").mkdir(exist_ok=True)
        shutil.copy2(UP / f"cortes_{slug}" / f"{n:02d}.mp4", RAIZ / "videos" / f"{cid}.mp4")
        shutil.copy2(UP / f"cortes_{slug}" / f"capa_{n:02d}.jpg", RAIZ / "capas" / f"{cid}.jpg")
        agenda.append({"id": cid, "tipo": "reel", "quando": quando.isoformat(), "video": f"videos/{cid}.mp4",
                       "capa": f"capas/{cid}.jpg", "legenda": legs[n]})
(RAIZ / "agenda.json").write_text(json.dumps(agenda, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(agenda), "itens; primeiro", agenda[0]["id"], agenda[0]["quando"], "último", agenda[-1]["id"], agenda[-1]["quando"])
