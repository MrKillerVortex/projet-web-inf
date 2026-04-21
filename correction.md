# correction

Ce fichier liste tous les points developpes et la facon la plus simple de tester chacun.

## Demarrage rapide

### Option 1 - Test en ligne

URL pour la correction:

```text
https://shawn-projet.up.railway.app
```

Cette URL permet de tester rapidement:

- A2
- A4
- A5
- A6
- E1
- E2

### Option 2 - Test local complet

Cette option est recommandee pour tester aussi E3 et E4 de facon fiable.

1. Installer les dependances:

```powershell
pip install -r requirements.txt
pip install aiosmtpd
```

2. Creer une base locale de test:

```powershell
Copy-Item .\db\violations_empty.sqlite3 .\instance\violations.sqlite3
python .\import_violations.py --db .\instance\violations.sqlite3
```

3. Terminal 1 - lancer un serveur SMTP local:

```powershell
python -m aiosmtpd -n -l localhost:1026
```

4. Terminal 2 - lancer l'application:

```powershell
$env:FLASK_SECRET_KEY = "dev-secret-change-me"
$env:FLASK_DEBUG = "0"
$env:INF5190_DB_PATH = ".\instance\violations.sqlite3"
$env:SMTP_HOST = "localhost"
$env:SMTP_PORT = "1026"
$env:SMTP_USERNAME = ""
$env:SMTP_PASSWORD = ""
$env:SMTP_FROM = "notifications@violations.local"
$env:SMTP_USE_TLS = "0"
$env:PUBLIC_BASE_URL = "http://127.0.0.1:5000"
$env:INF5190_SCHEDULER = "0"
python .\app.py
```

5. Ouvrir:

```text
http://127.0.0.1:5000
```

## A1

**Developpe**

- Script Python d'import du CSV Montreal vers SQLite: `import_violations.py`
- Script SQL de creation: `db/db.sql`
- Base vide fournie: `db/violations_empty.sqlite3`

**Test**

```powershell
Copy-Item .\db\violations_empty.sqlite3 .\db\violations_test.sqlite3
python .\import_violations.py --db .\db\violations_test.sqlite3
python -c "import sqlite3; c=sqlite3.connect('db/violations_test.sqlite3'); print(c.execute('select count(1) from violations').fetchone()[0]); c.close()"
```

**Resultat attendu**

- La commande affiche un nombre de lignes superieur a 0.

## A2

**Developpe**

- Recherche Flask par nom d'etablissement, proprietaire et rue
- Resultats sur une nouvelle page

**Test**

1. Ouvrir `/`
2. Dans la section `Recherche`, saisir au moins un critere
3. Cliquer `Rechercher`

**Resultat attendu**

- La page `/search` s'affiche
- Les contraventions trouvees sont affichees
- Un etablissement peut apparaitre plus d'une fois

## A3

**Developpe**

- Synchronisation quotidienne avec `BackgroundScheduler`
- Horaire: tous les jours a minuit
- Fuseau horaire par defaut: `America/Toronto`

**Test**

- Verification par lecture du code dans `app.py`
- La tache planifiee est creee par `BackgroundScheduler`
- Le declenchement utilise `CronTrigger(hour=0, minute=0, ...)`

**Resultat attendu**

- Le code montre une synchronisation automatique quotidienne a minuit

## A4

**Developpe**

- Service REST `GET /contrevenants?du=YYYY-MM-DD&au=YYYY-MM-DD`
- Reponse JSON
- Documentation RAML visible sur `/doc`

**Test**

Ouvrir:

```text
/contrevenants?du=2022-05-08&au=2024-05-15
```

Puis ouvrir:

```text
/doc
```

**Resultat attendu**

- `/contrevenants` retourne du JSON
- `/doc` affiche la documentation RAML en HTML

## A5

**Developpe**

- Formulaire de recherche rapide par dates sur la page d'accueil
- Requete Ajax vers `/contrevenants`
- Tableau avec 2 colonnes: etablissement et nombre

**Test**

1. Ouvrir `/`
2. Aller a `Recherche rapide (dates)`
3. Entrer 2 dates
4. Cliquer `Chercher`

**Resultat attendu**

- Un tableau apparait
- Chaque ligne contient un nom d'etablissement et un nombre de contraventions

## A6

**Developpe**

- Recherche par restaurant avec liste deroulante
- Service `GET /restaurants`
- Service `GET /infractions?etablissement=...`

**Test**

1. Ouvrir `/`
2. Aller a `Recherche par restaurant`
3. Choisir un restaurant
4. Cliquer `Voir les infractions`

**Resultat attendu**

- Les infractions du restaurant s'affichent dans le tableau

## E1

**Developpe**

- Service REST `POST /utilisateurs`
- Validation JSON avec `json-schema`
- Documentation presente dans `/doc`

**Test**

Exemple avec PowerShell:

```powershell
$body = @{
    nom_complet = "Prof Correcteur"
    courriel = "prof@example.com"
    etablissements_surveille = @("Restaurant Test")
    mot_de_passe = "motdepasse123"
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://127.0.0.1:5000/utilisateurs" `
  -Method POST `
  -Body $body `
  -ContentType "application/json"
```

**Resultat attendu**

- Reponse HTTP `201`
- Le corps de reponse contient l'utilisateur cree

## E2

**Developpe**

- Page d'inscription: `/inscription`
- Page de connexion: `/connexion`
- Page de profil: `/profil`
- Modification de la liste d'etablissements surveilles
- Televersement photo JPG/PNG

**Test**

1. Ouvrir `/inscription`
2. Creer un profil
3. Ouvrir `/connexion`
4. Se connecter
5. Ouvrir `/profil`
6. Modifier la liste d'etablissements surveilles
7. Televerser une image JPG ou PNG

**Resultat attendu**

- Le profil est cree
- La connexion fonctionne
- La liste surveillee est mise a jour
- La photo de profil s'affiche apres envoi

## E3

**Developpe**

- Envoi d'un courriel aux utilisateurs qui surveillent un etablissement
- Bouton de test ajoute sur `/profil` pour verifier l'envoi localement

**Test recommande**

Utiliser la configuration locale de la section `Demarrage rapide`, puis:

1. Ouvrir `/inscription`
2. Creer un utilisateur avec un courriel de test et au moins un etablissement surveille
3. Ouvrir `/connexion`
4. Se connecter
5. Ouvrir `/profil`
6. Cliquer `Envoyer un courriel de test`

**Resultat attendu**

- Un message de confirmation apparait sur `/profil`
- Le courriel apparait dans le terminal `aiosmtpd`

## E4

**Developpe**

- Le courriel contient un lien de desabonnement
- La page `/desabonnement?token=...` demande une confirmation
- La confirmation appelle `DELETE /api/desabonnement` en Ajax

**Test recommande**

Apres le test E3:

1. Copier le lien de desabonnement affiche dans le courriel du terminal SMTP local
2. Ouvrir ce lien dans le navigateur
3. Cliquer `Confirmer le desabonnement`
4. Retourner sur `/profil`

**Resultat attendu**

- La page de confirmation s'affiche
- Le desabonnement reussit
- L'etablissement retire n'apparait plus dans la liste surveillee

## F1

**Developpe**

- Application deployee sur Railway

**URL**

```text
https://shawn-projet.up.railway.app
```
