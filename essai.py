"""Essai de bout en bout sur une base SQLite jetable : python essai.py"""
import json
import os
import re
import sys
import tempfile

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


http = TestClient(site.app)
r = http.get("/")
verifier(r.status_code == 200 and "Code d'accès" in r.text and "Participer" in r.text, "accueil avec code d'accès")
verifier("Content-Security-Policy" in r.headers and r.headers["X-Frame-Options"] == "DENY", "en-têtes de sécurité")
t = jeton(r.text)
r = http.post("/commencer", data={"csrf": t, "code": "faux", "profil": "dev", "accord": "1"}, follow_redirects=True)
verifier("pas le bon" in r.text, "mauvais code refusé")
r = http.post("/commencer", data={"csrf": t, "code": "ruche2026", "profil": "", "accord": "1"}, follow_redirects=True)
verifier("Indiquez votre profil" in r.text, "profil obligatoire")
verifier(http.post("/commencer", data={"csrf": "x", "code": "ruche2026", "profil": "dev", "accord": "1"}).status_code == 400,
         "jeton CSRF vérifié")
r = http.post("/commencer", data={"csrf": t, "code": "Ruche2026", "pseudo": "  Awa  ", "profil": "dev", "experience": "2-5",
                                  "accord": "1"}, follow_redirects=True)
verifier(r.status_code == 200 and "Paire 1 sur 30" in r.text, "participant créé, première paire")
page = r.text
mots = [m for m in ("qwen", "llama", "llm", "règles", "modèle") if m in page.lower().split("<script")[0]]
verifier(not mots, f"aveugle : aucune indication d'origine dans la page {mots}")
pid = re.search(r'action="/noter/([0-9a-f]+)"', page).group(1)
r = http.post(f"/noter/{pid}", data={"csrf": t, "clarte_a": "4"}, follow_redirects=True)
verifier("Notez la clarté" in r.text and f"/noter/{pid}" in r.text, "note incomplète refusée, même paire")
complet = {"csrf": t, "clarte_a": "4", "clarte_b": "3", "utilite_a": "5", "utilite_b": "2", "preference": "A", "commentaire": " bien  "}
r = http.post(f"/noter/{pid}", data=complet, follow_redirects=True)
verifier("Paire 2 sur 30" in r.text, "note enregistrée, paire suivante")
http.post(f"/noter/{pid}", data=complet)
with site.moteur.connect() as c:
    verifier(c.execute(site.select(site.func.count()).select_from(site.notes)).scalar() == 1, "double envoi ignoré")
autre = TestClient(site.app)
r = autre.get("/")
autre.post("/commencer", data={"csrf": jeton(r.text), "code": "Ruche2026", "profil": "securite", "accord": "1"})
autre.get("/noter")
ordres = {tuple(site._ordre(x)[:5]) for x in ("a" * 32, "b" * 32, "c" * 32)}
verifier(len(ordres) > 1, "ordre des paires différent d'un participant à l'autre")
verifier(http.get("/suivi").status_code == 404 and http.get("/export?cle=faux").status_code == 404, "suivi protégé par la clé")
e = http.get("/export?cle=cle-essai").json()
n = e["notes"][0]
verifier(len(e["participants"]) == 2 and n["commentaire"] == "bien" and n["duree"] is not None and n["preference"] == "A",
         "export : participants, notes, durée")
verifier("Awa" in http.get("/suivi?cle=cle-essai").text, "page de suivi")
for x in list(site.PAIRES):
    if x != pid:
        http.post(f"/noter/{x}", data=complet)
r = http.get("/noter")
verifier("Merci, Awa" in r.text, "fin : 30 paires notées")
r = http.post("/quitter", data={"csrf": t}, follow_redirects=True)
verifier("Participer" in r.text, "laisser la place à un autre participant")
verifier(http.get("/sante").json()["paires"] == 30, "santé")
json.dump(http.get("/export?cle=cle-essai").json(), open(os.path.join(os.path.dirname(base), "export.json"), "w"))
print("EXPORT", os.path.join(os.path.dirname(base), "export.json"))
print(f"{'OK' if not echecs else 'ÉCHEC'} : {echecs} échec(s)")
sys.exit(1 if echecs else 0)
