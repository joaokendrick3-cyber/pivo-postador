"""Respostas automáticas a comentários do Instagram (chamado pelo postar.py; qualquer falha aqui só é registrada).
Só responde elogio simples, nos posts das últimas 48 h. Pergunta, crítica, política e tudo que for duvidoso fica
para o João responder na mão."""
import hashlib
import re
from datetime import datetime, timedelta, timezone

INTERVALO_MIN = 10        # confere comentários a cada 10 minutos (a rodada roda a cada 1)
JANELA_H = 48             # só posts publicados nas últimas 48 h
MAX_POR_CONFERENCIA = 5
ESPERA_SEM_PERMISSAO_H = 6
_ULTIMA = None

ELOGIO = re.compile(r"\b(parab[eé]ns|excelente|[oó]timo|[oó]tima|show|top|boa|muito bom|muito boa|gostei|"
                    r"obrigad[oa]|[uú]til|verdade|conteudo bom|conte[uú]do bom|foda|incr[ií]vel|sensacional|"
                    r"perfeito|did[aá]tico|esclarecedor|aprendi)\b", re.I)
EMOJI_SO = re.compile(r"^[\s❤✅✨\U0001F300-\U0001FAFF☀-➿️]+$")
EVITAR = re.compile(r"\?|lula|bolsonaro|fl[aá]vio|\bpt\b|\bpl\b|esquerd|direit|golpe|corrup|ladr|mentir|fake|"
                    r"burro|idiota|lixo|porra|merda|http|www\.|\.com|@|golpista|pix|whats|chama no|\bdm\b|"
                    r"seguidor|sorteio", re.I)

RESPOSTAS = [
    "Valeu demais, {u}! 🙌 Manda pra quem precisa ver isso.",
    "Obrigado, {u}! Que bom que ajudou. Manda pra um amigo que ainda não entendeu isso.",
    "Valeu, {u}! 💜 Amanhã tem mais.",
    "Opa, {u}! Fico feliz que curtiu. Segue aqui pra não perder a continuação.",
    "Muito obrigado, {u}! Compartilha com quem vive falando disso 😉",
    "Valeu, {u}! É esse tipo de conta que a gente quer fazer toda semana.",
]


def _resposta(comentario):
    n = int(hashlib.sha1(comentario["id"].encode()).hexdigest(), 16)
    return RESPOSTAS[n % len(RESPOSTAS)].format(u="@" + comentario["username"])


def elegivel(c, meu_usuario):
    texto = (c.get("text") or "").strip()
    if not texto or len(texto) > 140:
        return False
    if c.get("username", "").lower() == meu_usuario.lower():
        return False
    if (c.get("replies") or {}).get("data"):
        return False
    if EVITAR.search(texto):
        return False
    return bool(ELOGIO.search(texto) or EMOJI_SO.match(texto))


def responder_pendentes(chamar, api, token, uid, estado):
    """estado = postados.json (dict). Devolve True se algo mudou nele."""
    agora = datetime.now(timezone.utc)
    reg = estado.setdefault("_comentarios", {})
    global _ULTIMA
    if _ULTIMA and agora - _ULTIMA < timedelta(minutes=INTERVALO_MIN):
        return False
    _ULTIMA = agora   # só em memória: não gera commit a cada conferência
    meu = chamar(f"{api}/me?fields=username&access_token={token}")["username"]
    posts = chamar(f"{api}/{uid}/media?fields=id,timestamp,comments_count&limit=15&access_token={token}")["data"]
    feitos = 0
    for p in posts:
        t = datetime.strptime(p["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        if agora - t > timedelta(hours=JANELA_H) or not p.get("comments_count"):
            continue
        try:
            cs = chamar(f"{api}/{p['id']}/comments?fields=id,text,username,timestamp,replies{{id}}"
                        f"&limit=30&access_token={token}")
        except RuntimeError as e:
            if "permission" in str(e).lower() or '"code":10' in str(e) or '"code":200' in str(e):
                _ULTIMA = agora + timedelta(hours=ESPERA_SEM_PERMISSAO_H, minutes=-INTERVALO_MIN)  # tenta de novo em 6 h
            raise
        for c in cs.get("data", []):
            if c["id"] in reg or not elegivel(c, meu):
                continue
            if feitos >= MAX_POR_CONFERENCIA:
                return True   # há mudança a gravar
            try:
                r = chamar(f"{api}/{c['id']}/replies", {"message": _resposta(c), "access_token": token})
                reg[c["id"]] = {"resposta": r.get("id"), "em": agora.isoformat()}
                feitos += 1
                print("comentário respondido:", c["id"], flush=True)
            except RuntimeError as e:
                reg[c["id"]] = {"erro": str(e)[:150]}   # não tenta de novo o mesmo comentário
                feitos += 1
    return feitos > 0
