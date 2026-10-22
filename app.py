"""Site de notation à l'aveugle de textes de rapports de sécurité (mémoire de master, hypothèse H4).

Indépendant de la plateforme ESAY : il ne contient que les paires de textes pseudonymisés (donnees/paires.json), sans
aucune indication de leur origine, et les notes des participants. Aucun compte : chaque participant indique un pseudonyme
et son profil, puis note les paires qui lui sont affectées ; il peut s'interrompre et reprendre avec le même navigateur.

Dispositif (deuxième vague, 22/10/2026), après une première vague où 28 notes sur 37 ont été données en moins de 20 s :
  - 10 paires par participant, tirées parmi les moins notées (couverture équilibrée des 30 paires) ;
  - 2 paires de contrôle : une paire piège (donnees/controles.json, un texte se contredit ouvertement) en 5e position,
    et la 2e paire du participant présentée à nouveau, A et B inversés, en 11e position ;
  - temps minimal de lecture proportionnel à la longueur de la paire, vérifié dans le navigateur et par le serveur ;
  - à la fin, 10 paires de plus proposées aux volontaires.
Les règles d'exclusion sont appliquées à l'analyse (plateforme : gerer.py analyser-h4-en-ligne), jamais ici.

    uvicorn app:app --reload                      (local : base SQLite notation.db)

Variables d'environnement :
    DATABASE_URL   base PostgreSQL (Render) ; à défaut SQLite locale
    SECRET_KEY     signature du cookie de session (obligatoire en production)
    CODE_ACCES     code demandé à l'accueil (facultatif : sans lui, le lien suffit)
    CLE_SUIVI      clé des pages /suivi et /export réservées à l'auteur
"""
import hmac
import json
import os
import random
import secrets
import time
from collections import Counter
from datetime import datetime, timezone

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import (Column, DateTime, Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine, func,
                        insert, select)
from starlette.middleware.sessions import SessionMiddleware

RACINE = os.path.dirname(os.path.abspath(__file__))
PRODUCTION = bool(os.environ.get("RENDER"))
SECRET_KEY = os.environ.get("SECRET_KEY") or ("" if PRODUCTION else "developpement-local")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY doit être défini en production.")
CODE_ACCES = os.environ.get("CODE_ACCES", "").strip()
CLE_SUIVI = os.environ.get("CLE_SUIVI", "").strip()

PROFILS = {"securite": "Spécialiste de la sécurité (SOC, RSSI, audit, pentest…)",
           "dev": "Développeur",
           "info": "Autre métier de l'informatique (système, réseau, support…)",
           "autre": "Hors informatique"}
EXPERIENCES = {"<2": "Moins de 2 ans", "2-5": "2 à 5 ans", "5-10": "5 à 10 ans", ">10": "Plus de 10 ans"}

PAR_PARTICIPANT = 10        # paires réelles par participant (et par série supplémentaire)
POSITION_PIEGE = 4          # 5e écran
POSITION_REPETITION = 10    # 11e écran : reprise de la 2e paire, A et B inversés
MOTS_PAR_SECONDE = 6        # lecture rapide (360 mots/min) : en deçà, les deux textes n'ont pas pu être lus
LECTURE_MIN = 10            # secondes, même pour les paires les plus courtes
TOLERANCE = 2               # secondes (horloge, réseau)

with open(os.path.join(RACINE, "donnees", "paires.json"), encoding="utf-8") as f:
    _lot = json.load(f)
LOT, PAIRES = _lot["lot"], {p["id"]: p for p in _lot["paires"]}
ORDRE = list(PAIRES)
with open(os.path.join(RACINE, "donnees", "controles.json"), encoding="utf-8") as f:
    PIEGE = json.load(f)["piege"]


def lecture_min(texte_a, texte_b):
    """Temps minimal de lecture d'une paire, en secondes (même formule à l'analyse)."""
    return max(LECTURE_MIN, round((len(texte_a.split()) + len(texte_b.split())) / MOTS_PAR_SECONDE))


def paire(identifiant):
    """Paire réelle, paire piège (« piege ») ou paire répétée (« r:<id> », textes A et B inversés)."""
    if identifiant == "piege":
        return PIEGE
    if identifiant.startswith("r:") and identifiant[2:] in PAIRES:
        base = PAIRES[identifiant[2:]]
        return {**base, "id": identifiant, "texte_a": base["texte_b"], "texte_b": base["texte_a"]}
    return PAIRES.get(identifiant)


# --------------------------------------------------------------------------- #
# Base
# --------------------------------------------------------------------------- #
def _url(url):
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


moteur = create_engine(_url(os.environ.get("DATABASE_URL") or f"sqlite:///{os.path.join(RACINE, 'notation.db')}"),
                       pool_pre_ping=True)
meta = MetaData()
participants = Table(
    "participants", meta,
    Column("id", String(32), primary_key=True),
    Column("pseudo", String(40), nullable=False, default=""),
    Column("profil", String(10), nullable=False),
    Column("experience", String(5), nullable=False, default=""),
    Column("cree_le", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
notes = Table(
    "notes", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("participant", String(32), nullable=False, index=True),
    Column("paire", String(20), nullable=False),
    Column("clarte_a", Integer, nullable=False), Column("clarte_b", Integer, nullable=False),
    Column("utilite_a", Integer, nullable=False), Column("utilite_b", Integer, nullable=False),
    Column("preference", String(5), nullable=False),
    Column("commentaire", Text, nullable=False, default=""),
    Column("duree", Integer),   # secondes entre l'affichage de la paire et l'envoi
    Column("cree_le", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("participant", "paire"),
)
# Deuxième vague : paires affectées à chaque participant, dans l'ordre de présentation (contrôles compris)
affectations = Table(
    "affectations", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("participant", String(32), nullable=False, index=True),
    Column("paire", String(20), nullable=False),
    Column("rang", Integer, nullable=False),
    Column("cree_le", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("participant", "rang"),
)
meta.create_all(moteur)


def _participant(pid):
    if not pid:
        return None
    with moteur.connect() as c:
        return c.execute(select(participants).where(participants.c.id == pid)).mappings().first()


def _notees(c, pid):
    return set(c.scalars(select(notes.c.paire).where(notes.c.participant == pid)))


def _sequence(c, pid):
    return list(c.scalars(select(affectations.c.paire).where(affectations.c.participant == pid)
                          .order_by(affectations.c.rang)))


def _affecter(c, pid, controles):
    """Ajoute PAR_PARTICIPANT paires au participant : celles que le moins de personnes ont notées ou reçues, jamais une
    paire qu'il a déjà vue ; ex aequo départagés au hasard. Renvoie le nombre de paires ajoutées."""
    vues = _notees(c, pid) | set(_sequence(c, pid))
    recues = {tuple(x) for x in c.execute(select(notes.c.participant, notes.c.paire))}
    recues |= {tuple(x) for x in c.execute(select(affectations.c.participant, affectations.c.paire))}
    couverture = Counter(p for _, p in recues if p in PAIRES)   # personnes ayant noté ou reçu chaque paire
    candidates = [p for p in ORDRE if p not in vues]
    rng = random.Random(f"{pid}|{len(vues)}")
    rng.shuffle(candidates)
    choisies = sorted(candidates, key=lambda p: couverture[p])[:PAR_PARTICIPANT]   # tri stable : le hasard départage
    if not choisies:
        return 0
    serie = list(choisies)
    if controles and len(serie) >= 2:
        serie.insert(min(POSITION_PIEGE, len(serie)), "piege")
        serie.insert(min(POSITION_REPETITION, len(serie)), "r:" + choisies[1])
    rang = (c.scalar(select(func.max(affectations.c.rang)).where(affectations.c.participant == pid)) or 0) + 1
    c.execute(insert(affectations), [{"participant": pid, "paire": p, "rang": rang + k} for k, p in enumerate(serie)])
    return len(choisies)


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="notation", max_age=60 * 24 * 3600,
                   same_site="lax", https_only=PRODUCTION)
app.mount("/statique", StaticFiles(directory=os.path.join(RACINE, "statique")), name="statique")
gabarits = Jinja2Templates(directory=os.path.join(RACINE, "gabarits"))
VERSION_STATIQUE = str(int(os.path.getmtime(os.path.join(RACINE, "statique", "style.css"))))


@app.middleware("http")
async def entetes(request: Request, appel):
    r = await appel(request)
    r.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' "
                                            "'unsafe-inline'; img-src 'self' data:; font-src 'self'; form-action 'self'; "
                                            "frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "same-origin"
    r.headers["X-Frame-Options"] = "DENY"
    if PRODUCTION:
        r.headers["Strict-Transport-Security"] = "max-age=31536000"
    return r


def _csrf(request):
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(24)
    return request.session["csrf"]


def _verifier_csrf(request, jeton):
    if not jeton or not hmac.compare_digest(jeton, request.session.get("csrf", "")):
        raise HTTPException(400, "Formulaire expiré : rechargez la page.")


def _page(request, gabarit, **contexte):
    return gabarits.TemplateResponse(request, gabarit, {"csrf": _csrf(request), "v": VERSION_STATIQUE,
                                                         "par_participant": PAR_PARTICIPANT, **contexte})


@app.get("/", response_class=HTMLResponse)
def accueil(request: Request):
    p = _participant(request.session.get("participant"))
    if p:
        return RedirectResponse("/noter", 303)
    return _page(request, "accueil.html", PROFILS=PROFILS, EXPERIENCES=EXPERIENCES, avec_code=bool(CODE_ACCES),
                 erreur=request.session.pop("erreur", None), saisie=request.session.pop("saisie", {}))


@app.post("/commencer")
def commencer(request: Request, csrf: str = Form(""), code: str = Form(""), pseudo: str = Form(""),
              profil: str = Form(""), experience: str = Form(""), accord: str = Form("")):
    _verifier_csrf(request, csrf)
    erreur = None
    if CODE_ACCES and not hmac.compare_digest(code.strip().lower(), CODE_ACCES.lower()):
        erreur = "Le code d'accès n'est pas le bon. Il figure dans le message qui vous a transmis ce lien."
    elif profil not in PROFILS:
        erreur = "Indiquez votre profil : il permet d'analyser les réponses par métier."
    elif not accord:
        erreur = "Merci de cocher la case d'accord pour participer."
    if erreur:
        request.session["erreur"] = erreur
        request.session["saisie"] = {"pseudo": pseudo[:40], "profil": profil, "experience": experience}
        return RedirectResponse("/", 303)
    pid = secrets.token_hex(16)
    with moteur.begin() as c:
        c.execute(insert(participants).values(id=pid, pseudo=" ".join(pseudo.split())[:40], profil=profil,
                                              experience=experience if experience in EXPERIENCES else ""))
        _affecter(c, pid, controles=True)
    request.session["participant"] = pid
    return RedirectResponse("/noter", 303)


@app.get("/noter", response_class=HTMLResponse)
def noter(request: Request):
    p = _participant(request.session.get("participant"))
    if not p:
        return RedirectResponse("/", 303)
    with moteur.begin() as c:
        sequence = _sequence(c, p["id"])
        if not sequence:   # participant de la première vague qui revient : il reçoit lui aussi une affectation
            _affecter(c, p["id"], controles=True)
            sequence = _sequence(c, p["id"])
        faites = _notees(c, p["id"])
    reste = [x for x in sequence if x not in faites]
    if not reste:
        reelles = sum(1 for x in sequence if x in PAIRES)
        encore = any(x not in faites and x not in sequence for x in ORDRE)
        return _page(request, "fin.html", participant=p, notees=reelles, encore=encore)
    suivante = reste[0]
    affichee = request.session.get("affichee") or {}
    if affichee.get("paire") != suivante:   # un rechargement de la page ne remet pas le compteur à zéro
        request.session["affichee"] = {"paire": suivante, "depuis": int(time.time())}
    q = paire(suivante)
    position = sequence.index(suivante)
    return _page(request, "noter.html", participant=p, paire=q, faites=position, total=len(sequence),
                 lecture=lecture_min(q["texte_a"], q["texte_b"]), erreur=request.session.pop("erreur", None),
                 enregistree=request.session.pop("enregistree", None))


def _note(v):
    if v not in {"1", "2", "3", "4", "5"}:
        raise ValueError
    return int(v)


@app.post("/noter/{identifiant}")
def enregistrer(request: Request, identifiant: str, csrf: str = Form(""), clarte_a: str = Form(""),
                clarte_b: str = Form(""), utilite_a: str = Form(""), utilite_b: str = Form(""),
                preference: str = Form(""), commentaire: str = Form("")):
    _verifier_csrf(request, csrf)
    p = _participant(request.session.get("participant"))
    if not p:
        return RedirectResponse("/", 303)
    q = paire(identifiant)
    with moteur.connect() as c:
        if q is None or identifiant not in _sequence(c, p["id"]):
            raise HTTPException(404)
    try:
        valeurs = {"clarte_a": _note(clarte_a), "clarte_b": _note(clarte_b), "utilite_a": _note(utilite_a),
                   "utilite_b": _note(utilite_b)}
        if preference not in ("A", "B", "egal"):
            raise ValueError
    except ValueError:
        request.session["erreur"] = "Notez la clarté et l'utilité des deux textes, puis indiquez votre préférence."
        return RedirectResponse("/noter", 303)
    affichee = request.session.get("affichee") or {}
    duree = int(time.time()) - affichee["depuis"] if affichee.get("paire") == identifiant else None
    if duree is not None and duree + TOLERANCE < lecture_min(q["texte_a"], q["texte_b"]):
        request.session["erreur"] = "Prenez le temps de lire les deux textes avant de les noter."
        return RedirectResponse("/noter", 303)
    with moteur.begin() as c:
        if identifiant not in _notees(c, p["id"]):   # double envoi : la première note est conservée
            c.execute(insert(notes).values(participant=p["id"], paire=identifiant, preference=preference, duree=duree,
                                           commentaire=" ".join(commentaire.split())[:1000], **valeurs))
    request.session["enregistree"] = True
    return RedirectResponse("/noter", 303)


@app.post("/continuer")
def continuer(request: Request, csrf: str = Form("")):
    """Volontaires : 10 paires de plus (sans nouveaux contrôles)."""
    _verifier_csrf(request, csrf)
    p = _participant(request.session.get("participant"))
    if not p:
        return RedirectResponse("/", 303)
    with moteur.begin() as c:
        _affecter(c, p["id"], controles=False)
    return RedirectResponse("/noter", 303)


@app.post("/quitter")
def quitter(request: Request, csrf: str = Form("")):
    """Nouveau participant sur le même appareil (ordinateur partagé) : les notes déjà données restent enregistrées."""
    _verifier_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/", 303)


# --------------------------------------------------------------------------- #
# Pages de l'auteur (clé CLE_SUIVI)
# --------------------------------------------------------------------------- #
def _autoriser(cle):
    if not CLE_SUIVI or not hmac.compare_digest(cle or "", CLE_SUIVI):
        raise HTTPException(404)


@app.get("/suivi", response_class=HTMLResponse)
def suivi(request: Request, cle: str = ""):
    _autoriser(cle)
    with moteur.connect() as c:
        ps = c.execute(select(participants).order_by(participants.c.cree_le)).mappings().all()
        ns = c.execute(select(notes.c.participant, notes.c.paire, notes.c.duree)).all()
        aff = c.execute(select(affectations.c.participant, affectations.c.paire)).all()
    prevues = Counter(a.participant for a in aff)
    reelles = Counter(n.participant for n in ns if n.paire in PAIRES)
    par_paire = Counter(n.paire for n in ns if n.paire in PAIRES)
    rapides = sum(1 for n in ns if n.paire in PAIRES and n.duree is not None
                  and n.duree < lecture_min(PAIRES[n.paire]["texte_a"], PAIRES[n.paire]["texte_b"]))
    lignes = [{**dict(p), "notes": reelles.get(p["id"], 0), "prevues": prevues.get(p["id"], 0),
               "faites": sum(1 for n in ns if n.participant == p["id"])} for p in ps]
    profils = {k: {"participants": sum(1 for l in lignes if l["profil"] == k and l["notes"]),
                   "complets": sum(1 for l in lignes if l["profil"] == k and l["prevues"] and l["faites"] >= l["prevues"]),
                   "notes": sum(l["notes"] for l in lignes if l["profil"] == k)} for k in PROFILS}
    return _page(request, "suivi.html", lignes=lignes, profils=profils, PROFILS=PROFILS, EXPERIENCES=EXPERIENCES,
                 couverture=sorted(par_paire.get(pid, 0) for pid in ORDRE), rapides=rapides, cle=cle,
                 total_notes=sum(reelles.values()))


@app.get("/export")
def exporter(cle: str = ""):
    """Toutes les réponses, pour l'analyse sur le poste de l'auteur (gerer.py analyser-h4-en-ligne)."""
    _autoriser(cle)
    with moteur.connect() as c:
        ps = [dict(p) for p in c.execute(select(participants)).mappings()]
        ns = [dict(x) for x in c.execute(select(notes).order_by(notes.c.id)).mappings()]
        af = [dict(x) for x in c.execute(select(affectations).order_by(affectations.c.id)).mappings()]
    for x in ps + ns + af:
        x["cree_le"] = x["cree_le"].isoformat() if x.get("cree_le") else None
    nom = f"notation-{datetime.now(timezone.utc):%Y%m%d-%H%M}.json"
    regles = {"mots_par_seconde": MOTS_PAR_SECONDE, "lecture_min": LECTURE_MIN, "piege_faux": PIEGE["faux"]}
    return JSONResponse({"lot": LOT, "exporte_le": datetime.now(timezone.utc).isoformat(), "regles": regles,
                         "participants": ps, "notes": ns, "affectations": af},
                        headers={"Content-Disposition": f'attachment; filename="{nom}"'})


@app.get("/sante")
def sante():
    with moteur.connect() as c:
        c.execute(select(1))
    return {"etat": "ok", "paires": len(PAIRES)}
