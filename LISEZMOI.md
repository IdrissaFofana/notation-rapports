# Notation en ligne — lisibilité des rapports de sécurité

Site de notation à l'aveugle des textes de rapports mensuels (mémoire de master, hypothèse H4), ouvert à des participants
extérieurs. **Indépendant de la plateforme** : il ne contient que 30 paires de textes pseudonymisés (Client A à D), sans
aucune indication de leur origine, et les notes des participants. Aucune donnée client, aucun lien avec le serveur.

## Ce que voit un participant

1. L'accueil : l'étude en trois étapes, puis un court formulaire (code d'accès, pseudonyme facultatif, profil, expérience,
   accord).
2. 12 paires (environ 20 minutes) : 10 paires tirées parmi les moins notées, plus 2 paires de contrôle. Il peut
   s'arrêter et reprendre plus tard avec le même navigateur.
3. Un écran de remerciement, qui propose 10 paires de plus aux volontaires.

## Qualité des réponses (depuis le 22/10/2026)

- **Temps minimal de lecture** : le bouton « Enregistrer » ne s'active qu'après (mots des deux textes ÷ 6) secondes, au
  moins 10 s ; le serveur le vérifie aussi.
- **Paire piège** (5e écran, `donnees/controles.json`) : le texte B se contredit ouvertement. Préférer ce texte, ou juger
  les deux équivalents, exclut le participant.
- **Paire répétée** (11e écran) : la 2e paire revient, A et B inversés. Choisir deux fois la même lettre exclut le
  participant.
- Les paires de contrôle ne comptent jamais dans les résultats. Les notes antérieures (première vague, 30 paires sans
  contrôle) sont analysées à part.

Profils : spécialiste de la sécurité, développeur, autre métier de l'informatique, hors informatique. L'analyse est faite
par profil : H4 se lit sur les spécialistes, les autres groupes donnent un éclairage complémentaire.

## Mise en ligne sur Render (gratuit)

1. **Créer un dépôt GitHub privé** (par exemple `notation-rapports`) et y pousser ce dossier :
   ```
   cd "C:\Users\DELLµ\Documents\RAPPORT MENSUEL\notation-en-ligne"
   git init
   git add .
   git commit -m "Site de notation en ligne"
   git branch -M main
   git remote add origin https://github.com/<votre-compte>/notation-rapports.git
   git push -u origin main
   ```
2. Sur **render.com** : se connecter avec GitHub, puis **New → Blueprint**, choisir le dépôt. Render lit `render.yaml` et
   crée le site et sa base PostgreSQL.
3. À la question **CODE_ACCES**, saisir le code que vous communiquerez avec le lien (par exemple `Ruche2026`). Le laisser
   vide rend le lien ouvert à tous.
4. Attendre la fin du déploiement (quelques minutes). L'adresse du site s'affiche en haut : `https://notation-rapports-xxxx.onrender.com`.
5. Récupérer **CLE_SUIVI** : service → *Environment* → afficher la valeur générée. Elle donne accès au suivi :
   `https://…onrender.com/suivi?cle=<CLE_SUIVI>`

À savoir sur l'offre gratuite :
- le site **s'endort après 15 minutes sans visite** : le premier visiteur attend alors environ 50 secondes ;
- la base PostgreSQL gratuite **expire 30 jours après sa création** : téléchargez les réponses régulièrement (bouton du
  suivi) et clôturez la campagne avant cette échéance.

## Message à envoyer aux participants (exemple)

> Bonjour,
> Dans le cadre de mon mémoire de master, je compare deux façons de rédiger des rapports de sécurité informatique.
> Pourriez-vous m'aider en notant quelques paires de textes ? Environ 2 minutes par paire, vous pouvez vous arrêter
> quand vous voulez et reprendre plus tard.
> Lien : https://…onrender.com — code d'accès : Ruche2026
> Merci beaucoup !

## Récupérer et analyser les réponses

1. Ouvrir `/suivi?cle=<CLE_SUIVI>` et cliquer sur **Télécharger les réponses (JSON)**.
2. Sur le poste de l'auteur, depuis le dossier `plateforme` :
   ```
   python gerer.py analyser-h4-en-ligne "C:\chemin\vers\notation-AAAAMMJJ-HHMM.json"
   ```
   Résultat par vague et par profil dans `memoire/donnees/RESULTATS_H4_EN_LIGNE.md`, avec le nombre de participants
   exclus par les contrôles et de notes plus rapides que le temps de lecture.

La correspondance entre les textes et leur origine (LLM ou règles) n'existe que sur le poste de l'auteur
(`plateforme/donnees/recherche/resultats/notation_qr2_v3.json`) : ni le site ni ce dépôt ne la contiennent.

## Développement local

```
pip install -r requirements.txt
uvicorn app:app --reload          # http://127.0.0.1:8000 (base SQLite notation.db)
python essai.py                   # essai de bout en bout sur une base jetable
```

Régénérer les paires (après une nouvelle expérience), depuis `plateforme` :
`python gerer.py exporter-notation-en-ligne`
