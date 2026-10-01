"""Autopsia di una puntata: perché è stata fatta, com'è finita e cosa ne pensa Leo.

`python betbot.py autopsia Berrettini` (o il numero della puntata, o niente = l'ultima chiusa). Cerca nel libro dei
soldi veri e in quello del paper. Mostra: la motivazione scritta al momento della puntata, l'esito, la probabilità
giusta all'ingresso e alla chiusura, il CLV (il mercato ci ha dato ragione?) e la causa di Leo con la spiegazione.
"""
from __future__ import annotations

from .config import DB_LIVE_PATH, DB_PATH
from .store import Store

CAUSES = {"merito": "vinta con merito", "fortuna": "vinta per fortuna", "varianza": "persa per varianza",
          "smentita": "smentita dal mercato", "riferimento": "riferimento vecchio", "notizia": "movimento forte (notizia?)",
          "esecuzione": "esecuzione", "non_valutabile": "senza chiusura"}


def find(text: str | None = None, limit: int = 3) -> list[dict]:
    out = []
    for name, path in (("SOLDI VERI", DB_LIVE_PATH), ("PAPER", DB_PATH)):
        if not path.exists():
            continue
        st = Store(path)
        if text and text.isdigit():
            rows = st.query("SELECT * FROM bets WHERE id=? AND mode!='shadow'", (int(text),))
        elif text:
            rows = st.query("SELECT * FROM bets WHERE mode!='shadow' AND (label LIKE ? OR reason LIKE ?) ORDER BY id DESC LIMIT ?",
                            (f"%{text}%", f"%{text}%", limit))
        else:
            rows = st.query("SELECT * FROM bets WHERE mode!='shadow' AND status!='OPEN' ORDER BY id DESC LIMIT 1")
        for b in rows:
            try:
                les = st.query("SELECT l.* FROM coach_lessons l JOIN coach_entries e ON e.id=l.entry_id "
                               "WHERE e.src='bets' AND e.row_id=?", (b["id"],))
            except Exception:
                les = []
            out.append({"libro": name, **b, "leo": les[0] if les else None})
    return out


def text_of(b: dict) -> str:
    pct = lambda x: "n.d." if x is None else f"{x:.0%}"
    lay = str(b["selection"]).startswith("LAY:")
    L = [f"#{b['id']} [{b['libro']}] {b['label']} · {b['strategy_id']}",
         f"  quando: {b['ts'][:16].replace('T', ' ')} · {'LAY (contro), rischio' if lay else 'back, puntata'} "
         f"{b['stake']:.2f} € a quota {b['odds']:.2f}",
         f"  perché: {b.get('reason') or 'n.d.'}",
         f"  esito: {b['status']} {(b.get('pnl') or 0):+.2f} € · {b.get('settle_reason') or ''}"]
    le = b.get("leo")
    if le:
        win_entry = (1 - le["p_entry"]) if lay and le.get("p_entry") is not None else le.get("p_entry")
        win_close = (1 - le["p_close"]) if lay and le.get("p_close") is not None else le.get("p_close")
        clv = le.get("price_clv")
        L += [f"  Leo: probabilità di vincere la puntata {pct(win_entry)} all'ingresso, {pct(win_close)} alla chiusura · "
              f"CLV {'n.d.' if clv is None else format(clv, '+.1%')} → {CAUSES.get(le['cause'], le['cause'])}",
              f"  {le.get('explanation') or ''}"]
    elif b["status"] == "OPEN":
        L.append("  ancora aperta: l'autopsia arriva a partita finita")
    else:
        L.append("  Leo non l'ha ancora analizzata (succede al ciclo successivo alla chiusura)")
    return "\n".join(L)


def run(text: str | None = None, out=print) -> int:
    bets = find(text)
    if not bets:
        out("Nessuna puntata trovata" + (f" con '{text}'" if text else "") + ".")
        return 1
    for b in bets:
        out(text_of(b) + "\n")
    return 0
