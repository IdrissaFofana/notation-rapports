"""Essai de bout en bout sur une base SQLite jetable : python essai.py"""
import json
import os
import re
import sys
import tempfile
from collections import Counter

base = os.path.join(tempfile.mkdtemp(), "essai.db")
os.environ.update(DATABASE_URL=f"sqlite:///{base}", CODE_ACCES="Ruche2026", CLE_SUIVI="cle-essai")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient  # noqa: E402

import app as site  # noqa: E402

echecs = 0


def verifier(condition, libelle):
    global echecs
    print(("  OK    " if condition else "  ÉCHEC ") + libelle)
    echecs += not condition


def jeton(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def inscrire(pseudo="", profil="dev"):
    h = TestClient(site.app)
    r = h.get("/")
    r = h.post("/commencer", data={"csrf": jeton(r.text), "code": "Ruche2026", "pseudo": pseudo, "profil": profil,
                                   "experience": "2-5", "accord": "1"}, follow_redirects=True)
    return h, r


def paire_affichee(html):
    m = re.search(r'action="/noter/([^"]+)"', html)
    return m.group(1) if m else None


COMPLET = {"clarte_a": "4", "clarte_b": "3", "utilite_a": "5", "utilite_b": "2", "preference": "A", "commentaire": " bien  "}

# --- Accueil et inscription
http = TestClient(site.app)
r = http.get("/")
verifier(r.status_code == 200 and "Code d'accès" in r.text and "12 paires" in r.text, "accueil : code d'accès, 12 paires")
verifier("Content-Security-Policy" in r.headers and r.headers["X-Frame-Options"] == "DENY", "en-têtes de sécurité")
t = jeton(r.text)
r = http.post("/commencer", data={"csrf": t, "code": "faux", "profil": "dev", "accord": "1"}, follow_redirects=True)
verifier("pas le bon" in r.text, "mauvais code refusé")
r = http.post("/commencer", data={"csrf": t, "code": "ruche2026", "profil": "", "accord": "1"}, follow_redirects=True)
verifier("Indiquez votre profil" in r.text, "profil obligatoire")
verifier(http.post("/commencer", data={"csrf": "x", "code": "ruche2026", "profil": "dev", "accord": "1"}).status_code == 400,
         "jeton CSRF vérifié")
http, r = inscrire("  Awa  ")
t = jeton(r.text)
verifier("Paire 1 sur 12" in r.text, "participant créé : 12 paires (10 + 2 contrôles)")
with site.moteur.connect() as c:
    seq = site._sequence(c, c.scalar(site.select(site.participants.c.id)))   # seul participant inscrit à ce stade
verifier(len(seq) == 12 and seq[4] == "piege" and seq[10] == "r:" + seq[1] and len(set(seq) & set(site.PAIRES)) == 10,
         "affectation : piège en 5e, 2e paire répétée en 11e, 10 paires réelles distinctes")
page = r.text.lower().split("<script")[0]
verifier(not [m for m in ("qwen", "llama", "llm", "règles", "modèle") if m in page], "aveugle : aucune indication d'origine")
verifier('data-lecture="' in r.text, "temps minimal de lecture transmis au navigateur")

# --- Temps minimal de lecture (vérifié par le serveur)
pid = paire_affichee(r.text)
r = http.post(f"/noter/{pid}", data={"csrf": t, **COMPLET}, follow_redirects=True)
verifier("Prenez le temps de lire" in r.text and paire_affichee(r.text) == pid, "note trop rapide refusée, même paire")
site.LECTURE_MIN, site.MOTS_PAR_SECONDE = 0, 10 ** 6   # la suite de l'essai ne simule pas l'attente

# --- Notation
r = http.post(f"/noter/{pid}", data={"csrf": t, "clarte_a": "4"}, follow_redirects=True)
verifier("Notez la clarté" in r.text and paire_affichee(r.text) == pid, "note incomplète refusée, même paire")
r = http.post(f"/noter/{pid}", data={"csrf": t, **COMPLET}, follow_redirects=True)
verifier("Paire 2 sur 12" in r.text and "Paire enregistrée" in r.text, "note enregistrée, confirmation, paire suivante")
http.post(f"/noter/{pid}", data={"csrf": t, **COMPLET})
verifier(http.post("/noter/inconnue", data={"csrf": t, **COMPLET}).status_code == 404, "paire hors affectation refusée")
vues = [pid]
for _ in range(11):
    r = http.get("/noter")
    x = paire_affichee(r.text)
    if x is None:
        break
    vues.append(x)
    if x == "piege":
        verifier("Client E" in r.text and "aucune menace" in r.text, "paire piège présentée")
    if x.startswith("r:"):
        base_p = site.PAIRES[x[2:]]
        verifier(base_p["texte_b"][:60] in r.text.split("Texte A")[1].split("Texte B")[0], "paire répétée : A et B inversés")
    http.post(f"/noter/{x}", data={"csrf": t, **COMPLET})
r = http.get("/noter")
verifier(vues == seq and "Merci, Awa" in r.text and "paires de plus" in r.text, "fin : 12 paires notées, suite proposée")
r = http.post("/continuer", data={"csrf": t}, follow_redirects=True)
verifier("Paire 13 sur 22" in r.text and paire_affichee(r.text) in site.PAIRES, "suite facultative : 10 paires de plus")

# --- Tirage équilibré entre participants
for k in range(5):
    inscrire(f"p{k}")
with site.moteur.connect() as c:
    aff = [a.paire for a in c.execute(site.select(site.affectations.c.paire)) if a.paire in site.PAIRES]
couverture = Counter(aff)
verifier(max(couverture.values()) - min(couverture[p] for p in site.ORDRE) <= 1,
         f"couverture équilibrée des 30 paires (écart {max(couverture.values()) - min(couverture[p] for p in site.ORDRE)})")

# --- Pages de l'auteur
verifier(http.get("/suivi").status_code == 404 and http.get("/export?cle=faux").status_code == 404, "suivi protégé par la clé")
e = http.get("/export?cle=cle-essai").json()
n = next(x for x in e["notes"] if x["paire"] == pid)
verifier(len(e["participants"]) == 6 and n["commentaire"] == "bien" and n["duree"] is not None and e["affectations"]
         and e["regles"]["piege_faux"] == "B", "export : participants, notes, durée, affectations, règles")
verifier("Awa" in http.get("/suivi?cle=cle-essai").text, "page de suivi")
r = http.post("/quitter", data={"csrf": t}, follow_redirects=True)
verifier("Participer" in r.text, "laisser la place à un autre participant")
verifier(http.get("/sante").json()["paires"] == 30, "santé")
json.dump(http.get("/export?cle=cle-essai").json(), open(os.path.join(os.path.dirname(base), "export.json"), "w"))
print("EXPORT", os.path.join(os.path.dirname(base), "export.json"))
print(f"{'OK' if not echecs else 'ÉCHEC'} : {echecs} échec(s)")
sys.exit(1 if echecs else 0)
