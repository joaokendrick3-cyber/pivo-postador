"""Adiciona um carrossel à agenda do robô.
Uso: python adicionar_carrossel.py ID "PASTA_COM_IMAGENS" "2026-10-26T14:00:00+00:00"
(a hora é em UTC: 11h de Brasília = 14:00 UTC). As imagens são ordenadas pelo número no começo do nome
("1- capa.jpg", "2- ...") e convertidas para JPEG em imagens/ID/NN.jpg; a legenda vem de legenda.txt da pasta."""
import json
import re
import sys
from pathlib import Path
from PIL import Image

ID, PASTA, QUANDO = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
AQUI = Path(__file__).resolve().parent
arqs = sorted((p for p in PASTA.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".jfif") and re.match(r"\d+", p.name)),
              key=lambda p: int(re.match(r"\d+", p.name).group()))
if not 2 <= len(arqs) <= 10:
    sys.exit(f"o carrossel precisa de 2 a 10 imagens, achei {len(arqs)}")
destino = AQUI / "imagens" / ID
destino.mkdir(parents=True, exist_ok=True)
rel = []
for n, p in enumerate(arqs, 1):
    im = Image.open(p).convert("RGB")
    im.save(destino / f"{n:02d}.jpg", "JPEG", quality=95)
    rel.append(f"imagens/{ID}/{n:02d}.jpg")
legenda = (PASTA / "legenda.txt").read_text(encoding="utf-8").strip()
ag = AQUI / "agenda.json"
agenda = json.loads(ag.read_text(encoding="utf-8"))
agenda = [x for x in agenda if x["id"] != ID]
agenda.append({"id": ID, "tipo": "carrossel", "quando": QUANDO, "imagens": rel, "legenda": legenda})
ag.write_text(json.dumps(agenda, ensure_ascii=False, indent=1), encoding="utf-8")
print("ok", ID, len(rel), "imagens")
