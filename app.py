"""Site de notation à l'aveugle de textes de rapports de sécurité (mémoire de master, hypothèse H4).

Indépendant de la plateforme ESAY : il ne contient que les paires de textes pseudonymisés (donnees/paires.json), sans
aucune indication de leur origine, et les notes des participants. Aucun compte : chaque participant indique un pseudonyme
et son profil, puis note les paires dans un ordre propre à lui ; il peut s'interrompre et reprendre avec le même
navigateur.

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

with open(os.path.join(RACINE, "donnees", "paires.json"), encoding="utf-8") as f:
    _lot = json.load(f)
LOT, PAIRES = _lot["lot"], {p["id"]: p for p in _lot["paires"]}
ORDRE = list(PAIRES)


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
    Column("duree", Integer),   # secondes entre l'affichage de la paire et l'envoi (repère des réponses bâclées)
    Column("cree_le", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("participant", "paire"),
)
meta.create_all(moteur)


def _participant(pid):
    if not pid:
        return None
    with moteur.connect() as c:
        return c.execute(select(participants).where(participants.c.id == pid)).mappings().first()


def _notees(pid):
    with moteur.connect() as c:
        return set(c.scalars(select(notes.c.paire).where(notes.c.participant == pid)))


def _ordre(pid):
    """Ordre des paires propre au participant (même ordre à chaque reprise) : la fatigue ne pèse pas toujours sur les
    mêmes paires. L'ordre A/B à l'intérieur d'une paire, lui, est fixé une fois pour toutes."""
    ordre = ORDRE[:]
    random.Random(pid).shuffle(ordre)
    return ordre


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
                                                         "total": len(PAIRES), **contexte})


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
    request.session["participant"] = pid
    return RedirectResponse("/noter", 303)


@app.get("/noter", response_class=HTMLResponse)
def noter(request: Request):
    p = _participant(request.session.get("participant"))
    if not p:
        return RedirectResponse("/", 303)
    faites = _notees(p["id"])
    suivante = next((pid for pid in _ordre(p["id"]) if pid not in faites), None)
    if suivante is None:
        return _page(request, "fin.html", participant=p)
    request.session["affichee"] = {"paire": suivante, "depuis": int(time.time())}
    return _page(request, "noter.html", participant=p, paire=PAIRES[suivante], faites=len(faites),
                 erreur=request.session.pop("erreur", None))


def _note(v):
    if v not in {"1", "2", "3", "4", "5"}:
        raise ValueError
    return int(v)


@app.post("/noter/{paire}")
def enregistrer(request: Request, paire: str, csrf: str = Form(""), clarte_a: str = Form(""), clarte_b: str = Form(""),
                utilite_a: str = Form(""), utilite_b: str = Form(""), preference: str = Form(""),
                commentaire: str = Form("")):
    _verifier_csrf(request, csrf)
    p = _participant(request.session.get("participant"))
    if not p:
        return RedirectResponse("/", 303)
    if paire not in PAIRES:
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
    duree = int(time.time()) - affichee["depuis"] if affichee.get("paire") == paire else None
    if paire not in _notees(p["id"]):   # double envoi : la première note est conservée
        with moteur.begin() as c:
            c.execute(insert(notes).values(participant=p["id"], paire=paire, preference=preference, duree=duree,
                                           commentaire=" ".join(commentaire.split())[:1000], **valeurs))
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
        n = dict(c.execute(select(notes.c.participant, func.count()).group_by(notes.c.participant)).all())
        par_paire = dict(c.execute(select(notes.c.paire, func.count()).group_by(notes.c.paire)).all())
        rapides = c.scalar(select(func.count()).select_from(notes).where(notes.c.duree < 20)) or 0
    lignes = [{**dict(p), "notes": n.get(p["id"], 0)} for p in ps]
    profils = {k: {"participants": sum(1 for l in lignes if l["profil"] == k and l["notes"]),
                   "complets": sum(1 for l in lignes if l["profil"] == k and l["notes"] == len(PAIRES)),
                   "notes": sum(l["notes"] for l in lignes if l["profil"] == k)} for k in PROFILS}
    return _page(request, "suivi.html", lignes=lignes, profils=profils, PROFILS=PROFILS, EXPERIENCES=EXPERIENCES,
                 couverture=sorted(par_paire.get(pid, 0) for pid in ORDRE), rapides=rapides, cle=cle,
                 total_notes=sum(n.values()))


@app.get("/export")
def exporter(cle: str = ""):
    """Toutes les réponses, pour l'analyse sur le poste de l'auteur (gerer.py analyser-h4-en-ligne)."""
    _autoriser(cle)
    with moteur.connect() as c:
        ps = [dict(p) for p in c.execute(select(participants)).mappings()]
        ns = [dict(x) for x in c.execute(select(notes).order_by(notes.c.id)).mappings()]
    for x in ps + ns:
        x["cree_le"] = x["cree_le"].isoformat() if x.get("cree_le") else None
    nom = f"notation-{datetime.now(timezone.utc):%Y%m%d-%H%M}.json"
    return JSONResponse({"lot": LOT, "exporte_le": datetime.now(timezone.utc).isoformat(), "participants": ps, "notes": ns},
                        headers={"Content-Disposition": f'attachment; filename="{nom}"'})


@app.get("/sante")
def sante():
    with moteur.connect() as c:
        c.execute(select(1))
    return {"etat": "ok", "paires": len(PAIRES)}
