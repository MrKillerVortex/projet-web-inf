# correction

Ce fichier liste tous les points developpes et la facon de tester chacun d'eux.

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

Comment tester:

```powershell
Copy-Item .\db\violations_empty.sqlite3 .\db\violations_test.sqlite3
python .\import_violations.py --db .\db\violations_test.sqlite3
```

Verification:

```powershell
python -c "import sqlite3; c=sqlite3.connect('db/violations_test.sqlite3'); print(c.execute('select count(1) from violations').fetchone()[0]); c.close()"
```

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

Comment tester:

- Verifier dans `app.py` que le scheduler lance la synchronisation quotidienne a `00:00`
- Variables disponibles:
  - `INF5190_SCHEDULER`
  - `INF5190_TZ`

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
2. Creer un profil
3. Ouvrir `/connexion`
4. Se connecter
5. Ouvrir `/profil`
6. Modifier la liste des etablissements surveilles
7. Televerser une image JPG ou PNG
8. Verifier l'affichage de la photo dans le profil

## E3

Point developpe:

- Lorsqu'un nouveau contrevenant est detecte pendant la synchronisation, un courriel est envoye aux utilisateurs qui surveillent l'etablissement

Configuration requise pour tester l'envoi:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `SMTP_USE_TLS`

Comment tester (avec serveur SMTP local pour developpement):

1. Lancer un serveur SMTP local:
   ```powershell
   python -m aiosmtpd -n -l localhost:1026
   ```
2. Configurer les variables SMTP pour le serveur local:
   ```powershell
   $env:SMTP_HOST = "localhost"
   $env:SMTP_PORT = "1026"
   $env:SMTP_USERNAME = ""
   $env:SMTP_PASSWORD = ""
   $env:SMTP_FROM = "test@localhost"
   $env:SMTP_USE_TLS = "0"
   $env:PUBLIC_BASE_URL = "http://localhost:5000"
   ```
3. Lancer l'application:
   ```powershell
   python .\app.py
   ```
4. Creer un profil utilisateur et surveiller un etablissement
5. Aller dans le profil et cliquer sur "Envoyer un email de test"
6. Verifier l'affichage de l'email dans le terminal du serveur SMTP (aiosmtpd)
7. Verifier aussi le message de succes dans la console Flask: "✓ Email sent successfully to ..."

## E4

Point developpe:

- Le courriel de E3 contient un lien de desabonnement
- Le lien ouvre une page HTML de confirmation
- Si l'utilisateur confirme, une requete Ajax appelle un service REST qui supprime le restaurant du profil

Routes:

- `GET /desabonnement?token=...`
- `DELETE /api/desabonnement`

Configuration requise:

- `PUBLIC_BASE_URL`

Comment tester:

1. Recevoir le courriel de notification
2. Cliquer sur le lien de desabonnement
3. Verifier la page de confirmation
4. Confirmer le desabonnement
5. Verifier que le restaurant est retire de la liste surveillee de l'utilisateur
