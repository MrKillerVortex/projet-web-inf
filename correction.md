# correction

Ce fichier liste tous les points developpes et la facon de tester chacun d'eux.

## Prerequis & Installation

### Environnement Python

- Python 3.10+
- Gestionnaire de paquets: `pip`

### Installation des dependances

```powershell
pip install -r requirements.txt
```

Paquets inclus:
- Flask >= 3, < 4
- APScheduler
- jsonschema
- Werkzeug (pour gestion des mots de passe)
- itsdangerous (pour tokens HMAC)
- aiosmtpd (optionnel, pour tester l'email en local)

### Installation optionnelle pour tester E3

```powershell
pip install aiosmtpd
```

### Verifier l'installation

```powershell
python -c "import flask, apscheduler, jsonschema; print('OK')"
```

## F1

URL de l'application pour la correction:

```text
https://shawn-projet.up.railway.app
```

## A1

Point developpe:

- Import des contraventions depuis le CSV de la Ville de Montreal vers SQLite avec un script Python.
- Script SQL de creation de la base: `db/db.sql`
- Base vide fournie: `db/violations_empty.sqlite3`
- Script d'import: `import_violations.py`
- Source du CSV: https://donnees.montreal.ca/dataset/inspection-aliments-contrevenants/

Comment tester:

```powershell
Copy-Item .\db\violations_empty.sqlite3 .\db\violations_test.sqlite3
python .\import_violations.py --db .\db\violations_test.sqlite3
```

Verification:

```powershell
python -c "import sqlite3; c=sqlite3.connect('db/violations_test.sqlite3'); print(c.execute('select count(1) from violations').fetchone()[0]); c.close()"
```

Resultat attendu: Plus de 10,000 lignes importees

## A2

Point developpe:

- Application Flask pour acceder aux donnees
- Recherche par:
  - nom d'etablissement
  - proprietaire
  - rue
- Les resultats s'affichent sur une nouvelle page avec toutes les donnees disponibles de chaque contravention

Comment tester:

1. Lancer l'application:
   ```powershell
   python .\app.py
   ```
2. Ouvrir `/`
3. Utiliser le formulaire de recherche
4. Verifier que la page `/search` affiche les contraventions trouvees

## A3

Point developpe:

- Synchronisation quotidienne des donnees a minuit avec `BackgroundScheduler`
- Format de fuseau horaire: America/Toronto

Comment tester:

**Option 1: Verifier le code (rapide)**

- Verifier dans `app.py` que le scheduler lance la synchronisation quotidienne a `00:00`
- Verifier les variables d'environnement disponibles:
  - `INF5190_SCHEDULER` (defaut: "1", "0" pour desactiver)
  - `INF5190_TZ` (defaut: "America/Toronto")

**Option 2: Tester rapidement sans attendre 24h**

1. Modifier le fichier `app.py` ligne ~273 pour tester toutes les 2 minutes:
   ```python
   # Remplacer CronTrigger(hour=0, minute=0)  # Minuit
   # Par:      CronTrigger(minute='*/2')      # Chaque 2 minutes (TEST)
   ```
2. Lancer l'application: `python .\app.py`
3. Observer les logs: voir "Starting new sync job"
4. Attendre 2-3 minutes pour voir l'execution
5. **Remplacer par la vraie valeur apres le test**

Verification:

- Les logs Flask doivent afficher: "Mises a jour: X nouvelles entrees"
- Le scheduler doit etre actif au demarrage (verifier: "Scheduler configured...")

## A4

Point developpe:

- Service REST:
  - `GET /contrevenants?du=YYYY-MM-DD&au=YYYY-MM-DD`
- Dates au format ISO 8601
- Reponse en JSON
- Documentation RAML affichee sur `/doc`

Comment tester:

1. Ouvrir:
   ```text
   /contrevenants?du=2022-05-08&au=2024-05-15
   ```
2. Verifier que la reponse est du JSON
3. Ouvrir `/doc`
4. Verifier que la documentation RAML est affichee en HTML

## A5

Point developpe:

- Formulaire de recherche rapide par dates sur la page d'accueil
- Requete Ajax vers la route de A4
- Affichage d'un tableau contenant:
  - le nom de l'etablissement
  - le nombre de contraventions

Comment tester:

1. Ouvrir `/`
2. Dans "Recherche rapide (dates)", entrer deux dates
3. Lancer la recherche
4. Verifier le tableau des resultats

## A6

Point developpe:

- Recherche par nom de restaurant via une liste deroulante
- Requete Ajax vers un service REST dedie
- Affichage des differentes infractions du restaurant

Services utilises:

- `GET /restaurants`
- `GET /infractions?etablissement=...`

Comment tester:

1. Ouvrir `/`
2. Aller dans "Recherche par restaurant"
3. Choisir un restaurant dans la liste
4. Cliquer sur "Voir les infractions"
5. Verifier l'affichage des infractions

## E1

Point developpe:

- Service REST de creation de profil utilisateur:
  - `POST /utilisateurs`
- Validation du document JSON avec `json-schema`
- Service documente dans la RAML affichee sur `/doc`

Document JSON attendu:

```json
{
  "nom_complet": "Jean Tremblay",
  "courriel": "jean.tremblay@example.com",
  "etablissements_surveille": ["Restaurant Test", "Boulangerie Demo"],
  "mot_de_passe": "motdepasse123"
}
```

Comment tester:

1. Envoyer une requete `POST /utilisateurs` avec ce JSON
2. Verifier une reponse `201`
3. Envoyer un JSON invalide
4. Verifier une reponse `400`
5. Verifier la presence de ce service dans `/doc`

## Workflow complet E2 + E3 + E4

### Test de bout en bout: Inscription → Email → Desabonnement

Ce workflow teste les trois points ensemble de maniere realiste.

#### Etape 1: Preparer l'environnement

```powershell
# Terminal 1 - Serveur SMTP local
python -m aiosmtpd -n -l localhost:1026
```

```powershell
# Terminal 2 - Configurer l'application
$env:FLASK_SECRET_KEY = "dev-secret-change-me"
$env:SMTP_HOST = "localhost"
$env:SMTP_PORT = "1026"
$env:SMTP_USERNAME = ""
$env:SMTP_PASSWORD = ""
$env:SMTP_FROM = "notifications@violations.local"
$env:SMTP_USE_TLS = "0"
$env:PUBLIC_BASE_URL = "http://localhost:5000"
$env:UNSUBSCRIBE_SALT = "unsubscribe-restaurant"
$env:INF5190_SCHEDULER = "0"  # Desactiver le scheduler pour le test

python .\app.py
```

#### Etape 2: Creer un profil utilisateur (E2)

```powershell
# Terminal 3 (ou navigateur Postman/curl)
$json = @{
    nom_complet = "Prof Correcteur"
    courriel = "prof@example.com"
    etablissements_surveille = @("Restaurant Le Plateau")
    mot_de_passe = "motdepasse123"
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://localhost:5000/utilisateurs" -Method POST -Body $json -ContentType "application/json"
```

Ou via l'interface web:
1. Ouvrir `http://localhost:5000/inscription`
2. Remplir le formulaire:
   - Nom complet: "Prof Correcteur"
   - Courriel: "prof@example.com"
   - Choisir "Restaurant Le Plateau" (ou autre restaurant disponible)
   - Mot de passe: "motdepasse123"
3. Cliquer "S'inscrire"

#### Etape 3: Se connecter et tester l'envoi d'email (E3)

1. Ouvrir `http://localhost:5000/connexion`
2. Se connecter avec:
   - Courriel: prof@example.com
   - Mot de passe: motdepasse123
3. Aller a `http://localhost:5000/profil`
4. Cliquer sur "Envoyer un email de test"
5. **Verifier dans Terminal 1 (aiosmtpd):** un email doit s'afficher avec le contenu

**Email attendu:**
- De: notifications@violations.local
- A: prof@example.com
- Sujet: contient "Restaurant Le Plateau"
- Corps: contient un lien de desabonnement avec un token

#### Etape 4: Tester le desabonnement (E4)

1. Copier le lien de desabonnement de l'email (ressemble a: `/desabonnement?token=...`)
2. Ouvrir le lien dans le navigateur: `http://localhost:5000/desabonnement?token=...`
3. Verifier la page de confirmation
4. Cliquer "Confirmer le desabonnement"
5. Verifier le message "Vous avez ete desabonne avec succes"
6. Retourner au profil (`/profil`)
7. Verifier que "Restaurant Le Plateau" n'est plus dans la liste

### Configuration des variables d'environnement

#### Developpement local (aiosmtpd)

```powershell
$env:FLASK_SECRET_KEY = "dev-secret-change-me"
$env:FLASK_DEBUG = "1"
$env:SMTP_HOST = "localhost"
$env:SMTP_PORT = "1026"
$env:SMTP_USERNAME = ""
$env:SMTP_PASSWORD = ""
$env:SMTP_FROM = "dev@localhost"
$env:SMTP_USE_TLS = "0"
$env:PUBLIC_BASE_URL = "http://localhost:5000"
$env:INF5190_SCHEDULER = "1"
$env:INF5190_TZ = "America/Toronto"
```

#### Production (serveur SMTP reel)

```powershell
$env:FLASK_SECRET_KEY = "mettre-une-valeur-secure"
$env:FLASK_DEBUG = "0"
$env:SMTP_HOST = "smtp.votre-serveur.com"
$env:SMTP_PORT = "587"
$env:SMTP_USERNAME = "votre-courriel@domain.com"
$env:SMTP_PASSWORD = "votre-mot-de-passe"
$env:SMTP_FROM = "notifications@domain.com"
$env:SMTP_USE_TLS = "1"
$env:PUBLIC_BASE_URL = "https://votre-domaine.com"
$env:INF5190_SCHEDULER = "1"
$env:INF5190_TZ = "America/Toronto"
```

## E2

Point developpe:

- Page web pour invoquer le service E1:
  - `/inscription`
- Option d'authentification:
  - `/connexion`
- Page apres authentification pour modifier la liste des etablissements surveilles:
  - `/profil`
- Televersement d'une photo de profil sauvegardee dans la base
- Formats acceptes:
  - jpg
  - png

Comment tester:

1. Ouvrir `/inscription`
2. Creer un profil avec les donnees:
   - Nom: "Test User"
   - Courriel: "test@example.com"
   - Choisir au moins 1 restaurant
   - Mot de passe: "password123" (minimum 8 caracteres)
3. Ouvrir `/connexion`
4. Se connecter avec les identifiants crees
5. Ouvrir `/profil`
6. Modifier la liste des etablissements surveilles (ajouter/retirer)
7. Televerser une image JPG ou PNG (depuis votre ordinateur)
8. Verifier l'affichage de la photo dans le profil
9. Rafraichir la page et verifier que la photo persiste

## E3

Point developpe:

- Lorsqu'un nouveau contrevenant est detecte pendant la synchronisation, un courriel est envoye aux utilisateurs qui surveillent l'etablissement
- Support de multiples serveurs SMTP configurables par variables d'environnement
- Timeout de 5 secondes pour eviter les blocages Gunicorn
- Gestion des erreurs SMTP gracieuse (pas de crash worker)

Configuration requise pour tester l'envoi:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `SMTP_USE_TLS`

Comment tester (methode recommandee avec serveur SMTP local):

**Voir le workflow complet E2+E3+E4 plus haut pour les instructions detaillees**

Resumé rapide:

1. Terminal 1 - Lancer un serveur SMTP local:
   ```powershell
   python -m aiosmtpd -n -l localhost:1026
   ```

2. Terminal 2 - Configurer les variables et lancer l'application:
   ```powershell
   $env:SMTP_HOST = "localhost"
   $env:SMTP_PORT = "1026"
   $env:SMTP_USERNAME = ""
   $env:SMTP_PASSWORD = ""
   $env:SMTP_FROM = "notifications@test.local"
   $env:SMTP_USE_TLS = "0"
   $env:PUBLIC_BASE_URL = "http://localhost:5000"
   python .\\app.py
   ```

3. Interface web:
   - Creer un profil: `/inscription`
   - Se connecter: `/connexion`
   - Aller dans le profil: `/profil`
   - Cliquer "Envoyer un email de test"

Verifications:

- **Terminal 1 (aiosmtpd):** Un email doit s'afficher avec le contenu complet
- **Terminal 2 (Flask logs):** Voir le message "Email sent successfully"
- **Contenu de l'email:** Doit contenir:
  - Adresse du destinataire (prof@example.com)
  - Sujet avec le nom du restaurant surveille
  - Liste des contraventions detectees (exemple: "- Date: 2026-04-13 | Montant: 500.0")
  - **Lien de desabonnement** avec token (ce lien sera teste dans E4)

## E4

Point developpe:

- Le courriel de E3 contient un lien de desabonnement avec token HMAC signe
- Le lien ouvre une page HTML de confirmation (`/desabonnement?token=...`)
- Si l'utilisateur confirme, une requete Ajax appelle un service REST qui supprime le restaurant de la liste surveille
- Validation du token signe (expiration 30 jours, token valide une fois seulement)

Routes:

- `GET /desabonnement?token=...` - Affiche la page de confirmation
- `DELETE /api/desabonnement` - Traite le desabonnement (requete Ajax)

Configuration requise:

- `PUBLIC_BASE_URL` - Pour construire le lien dans l'email
- `UNSUBSCRIBE_SALT` - Pour signer les tokens (defaut: "unsubscribe-restaurant")

Comment tester:

**Option 1: Via le workflow E3 (recommande)**

Voir le workflow complet E2+E3+E4 plus haut.

**Option 2: Test manuel rapide**

1. Recevoir le courriel de E3
2. Copier l'URL du lien de desabonnement (format: `http://localhost:5000/desabonnement?token=...`)
3. Ouvrir le lien dans le navigateur
4. Verifier la page de confirmation:
   - Titre: "Desabonnement"
   - Affichage du restaurant surveille
   - Bouton "Confirmer le desabonnement"
5. Cliquer "Confirmer le desabonnement"
6. Verifier le message de confirmation: "Vous avez ete desabonne avec succes!"
7. Retourner au profil (`/profil`) et verifier que le restaurant n'y est plus

Verifications techniques:

- **Token valide:** Le token inclus dans l'email doit contenir user_id et establishment
- **Token signe:** Essayer de modifier le token dans l'URL devrait afficher "Token invalide ou expire"
- **Une seule utilisation:** Cliquer sur le meme lien 2 fois ne doit pas causer d'erreur (message: "Vous avez deja ete desabonne")
- **Timestamp dans token:** Verifier que le token expire apres 30 jours (voir code: `max_age=60*60*24*30`)

## Commandes utiles pour tester & depanner

### Tester l'application rapidement

```powershell
# Verifier que les dependances sont installees
pip list | findstr flask

# Lancer l'app en mode debug
python .\\app.py

# Verifier que la base de donnees existe
Test-Path .\\instance\\violations.sqlite3

# Compter les lignes de la base
python -c "import sqlite3; c=sqlite3.connect('instance/violations.sqlite3'); print(c.execute('select count(1) from violations').fetchone()[0]); c.close()"

# Verifier les utilisateurs inscrits
python -c "import sqlite3; c=sqlite3.connect('instance/violations.sqlite3'); [print(r) for r in c.execute('select id, full_name, email from users').fetchall()]; c.close()"
```

### Tester les routes API

```powershell
# Test A1: Importer les donnees
Copy-Item .\\db\\violations_empty.sqlite3 .\\db\\test.db
python .\\import_violations.py --db .\\db\\test.db

# Test A4: GET /contrevenants
curl "http://localhost:5000/contrevenants?du=2024-01-01&au=2024-12-31" -v

# Test E1: POST /utilisateurs
$body = @{
    nom_complet = "Test User"
    courriel = "test@example.com"
    etablissements_surveille = @("Restaurant Test")
    mot_de_passe = "password123"
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://localhost:5000/utilisateurs" `
  -Method POST `
  -Body $body `
  -ContentType "application/json" `
  -Verbose

# Test E4: GET /desabonnement (remplacer TOKEN par un vrai token)
curl "http://localhost:5000/desabonnement?token=TOKEN" -v
```

### Deboguer les problemes courants

**L'app crash au demarrage:**
```powershell
# Verifier les dependances
pip install -r requirements.txt

# Verifier le FLASK_SECRET_KEY
$env:FLASK_SECRET_KEY = "dev-secret"
python .\\app.py
```

**L'email n'est pas envoye:**
```powershell
# Verifier que le serveur SMTP ecoute
netstat -ano | findstr 1026

# Relancer le serveur SMTP local
python -m aiosmtpd -n -l localhost:1026
```

**La base de donnees est vide ou corrompue:**
```powershell
# Reinitialiser la base
Remove-Item .\\instance\\violations.sqlite3
python .\\import_violations.py  # Re-importer le CSV

# Ou utiliser la base test fournie
Copy-Item .\\db\\violations_test.sqlite3 .\\instance\\violations.sqlite3
```

**L'authentification ne fonctionne pas:**
```powershell
# Verifier les utilisateurs et leurs mots de passe
python -c "
import sqlite3
c = sqlite3.connect('instance/violations.sqlite3')
users = c.execute('select id, full_name, email, password_hash from users').fetchall()
for u in users:
    print(f'ID: {u[0]}, Name: {u[1]}, Email: {u[2]}, PWHash (first 20 chars): {u[3][:20] if u[3] else None}')
c.close()
"

# Creer un nouvel utilisateur via API
$body = @{
    nom_complet = "Debug User"
    courriel = "debug@test.com"
    etablissements_surveille = @()
    mot_de_passe = "debugpass123"
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://localhost:5000/utilisateurs" `
  -Method POST `
  -Body $body `
  -ContentType "application/json"
```

### Points de verification avant la remise

- [ ] Toutes les 8 routes (A1-A6, E1-E4) sont implementees
- [ ] Tous les commentaires et print sont en FRANCAIS
- [ ] Les noms de fichiers des templates sont en FRANCAIS
- [ ] Le fichier `correction.md` contient les instructions de test
- [ ] L'application accepte les variables d'environnement (SMTP_*, FLASK_*, etc.)
- [ ] La base de donnees SQLite est creee et contient des donnees
- [ ] Les tests les plus critiques passent:
  - A2: Recherche par nom d'etablissement
  - E2: Inscription et connexion
  - E3: Envoi d'email avec serveur local
  - E4: Desabonnement via lien dans l'email

