# INF5190 - Projet de session (Montreal - inspections alimentaires)

## Base de donnees (remise)

- Script SQL: `db/db.sql`
- Base SQLite vide: `db/violations_empty.sqlite3`

Creer une nouvelle base vide (si besoin):

```powershell
sqlite3 .\db\ma_base.sqlite3 ".read .\db\db.sql"
```

## Import CSV -> SQLite (script)

Le script **n'efface pas** la base et **ne cree pas** la DB. Il insere seulement (doublons evites).

```powershell
python .\import_violations.py --db .\db\violations_empty.sqlite3
```

Option de test sans reseau:

```powershell
python .\import_violations.py --db .\db\violations_empty.sqlite3 --input .\data\sample_violations.csv
```

## Application Flask (work in progress)

```powershell
python .\app.py
```

Puis ouvre `http://127.0.0.1:5000/`.

- Page: `templates/index.html`
- Statics: `static/assets/*`
- API: `GET /api/facets`, `GET /api/violations`
 - REST: `GET /contrevenants?du=YYYY-MM-DD&au=YYYY-MM-DD`
 - Doc: `GET /doc` (RAML en HTML)

Par defaut, l'app utilise `instance/violations.sqlite3`. Pour pointer vers une autre base:

```powershell
$env:INF5190_DB_PATH = ".\\db\\violations_test.sqlite3"
python .\\app.py
```

## Synchronisation quotidienne (BackgroundScheduler)

L'app demarre un `BackgroundScheduler` qui synchronise les donnees **chaque jour a minuit** (timezone par defaut: `America/Toronto`).

- Desactiver le scheduler: `$env:INF5190_SCHEDULER = "0"`
- Changer la timezone: `$env:INF5190_TZ = "America/Toronto"`
- Installer la dependance: `python -m pip install -r requirements.txt`

## Deploiement cloud

Le projet est prepare pour un deploiement simple sur `Railway`:

- Serveur WSGI: `wsgi.py`
- Commande web: `Procfile`
- Port cloud: variable `PORT` prise en charge par `app.py`
- Dependance de prod: `gunicorn`

### Etapes Railway

1. Pousser le projet sur GitHub.
2. Creer un nouveau projet Railway a partir du repo.
3. Ajouter un volume persistant monte sur `/app/instance`.
4. Definir les variables d'environnement:
   - `INF5190_DB_PATH=/app/instance/violations.sqlite3`
   - `INF5190_TZ=America/Toronto`
   - `FLASK_DEBUG=0`
5. Ouvrir un shell Railway ou lancer une commande one-off pour initialiser la base:

```bash
python -c "import sqlite3, pathlib; p=pathlib.Path('/app/instance/violations.sqlite3'); p.parent.mkdir(parents=True, exist_ok=True); conn=sqlite3.connect(p); conn.executescript(open('db/db.sql', encoding='utf-8').read()); conn.commit(); conn.close()"
```

6. Importer les donnees:

```bash
python import_violations.py --db /app/instance/violations.sqlite3
```

7. Deployer. Railway fournira une URL publique du type:

https://shawn-projet.up.railway.app
