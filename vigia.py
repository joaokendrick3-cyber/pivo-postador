"""Vigia do robô: roda de hora em hora no GitHub Actions. Se achar problema, sai com erro (o GitHub avisa por e-mail
quando um workflow agendado falha) e, se o robô parou, já o reinicia.
Confere: (1) token do Instagram válido e longe de vencer, (2) posts atrasados, (3) erros de postagem,
(4) o ciclo do robô está rodando."""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PASTA = Path(__file__).resolve().parent
API = "https://graph.instagram.com/v23.0"
TOKEN = os.environ["IG_TOKEN"].strip()
GH_TOKEN = os.environ.get("GH_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
ATRASO_MIN = 30          # item que devia ter saído há mais de 30 min e não saiu
ATRASO_MAX_H = 24        # depois de 24 h o item deixa de contar (senão o alerta ficaria vermelho para sempre)
AVISO_TOKEN_DIAS = 14


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def checar_token(problemas):
    try:
        get(f"{API}/me?fields=user_id&access_token={TOKEN}")
    except urllib.error.HTTPError as e:
        problemas.append("TOKEN do Instagram recusado (HTTP %s): %s" % (e.code, e.read().decode()[:150]))
        return
    f = PASTA / "token_vence.txt"
    if f.exists():
        dias = (date.fromisoformat(f.read_text().strip()) - date.today()).days
        if dias <= AVISO_TOKEN_DIAS:
            problemas.append(f"TOKEN do Instagram vence em {dias} dia(s). Renovar e atualizar o secret IG_TOKEN e token_vence.txt.")


def checar_atrasos(problemas):
    agenda = json.loads((PASTA / "agenda.json").read_text(encoding="utf-8"))
    pf = PASTA / "postados.json"
    postados = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
    agora = datetime.now(timezone.utc)
    for it in agenda:
        q = datetime.fromisoformat(it["quando"])
        if not (timedelta(minutes=ATRASO_MIN) < agora - q < timedelta(hours=ATRASO_MAX_H)):
            continue
        redes = it.get("redes") or ["ig"]
        if "ig" in redes and "ig" not in (postados.get(it["id"]) or {}) and "media_id" not in (postados.get(it["id"]) or {}):
            problemas.append(f"ATRASADO: {it['id']} devia ter saído em {q.isoformat()} e não saiu.")
    for chave, msg in (postados.get("_erros") or {}).items():
        if chave.endswith(":ig") or chave.startswith("story:"):
            problemas.append(f"ERRO registrado em {chave}: {str(msg)[:150]}")


def checar_ciclo(problemas):
    """O robô é um ciclo de ~5h30 que se reinicia sozinho. Se não há nenhuma execução rodando ou na fila, reinicia."""
    if not (GH_TOKEN and REPO):
        return
    h = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    base = f"https://api.github.com/repos/{REPO}/actions/workflows/postar.yml"
    vivos = 0
    for st in ("in_progress", "queued"):
        vivos += get(f"{base}/runs?status={st}&per_page=5", h).get("total_count", 0)
    if vivos:
        return
    req = urllib.request.Request(f"{base}/dispatches", data=json.dumps({"ref": "main"}).encode(), headers=h, method="POST")
    urllib.request.urlopen(req, timeout=60)
    problemas.append("O robô estava PARADO (nenhuma execução rodando). Reiniciei agora.")


def main():
    problemas = []
    for f in (checar_token, checar_atrasos, checar_ciclo):
        try:
            f(problemas)
        except Exception as e:  # noqa: BLE001
            problemas.append(f"o próprio vigia falhou em {f.__name__}: {str(e)[:150]}")
    if problemas:
        print("\n".join("PROBLEMA: " + p for p in problemas))
        return 1
    print("tudo certo", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
