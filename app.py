import os
import atexit
import hashlib
import io
import re
import smtplib
from pathlib import Path
from email.message import EmailMessage

from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from jsonschema import ValidationError, validate
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.exceptions import HTTPException

from config import AppConfig
import db as dbmod

from zoneinfo import ZoneInfo

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except ModuleNotFoundError:
    BackgroundScheduler = None
    CronTrigger = None


USER_PROFILE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nom_complet", "courriel", "etablissements_surveille", "mot_de_passe"],
    "properties": {
        "nom_complet": {"type": "string", "minLength": 1, "maxLength": 200},
        "courriel": {
            "type": "string",
            "minLength": 3,
            "maxLength": 254,
            "pattern": r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        },
        "etablissements_surveille": {
            "type": "array",
            "maxItems": 100,
            "items": {"type": "string", "minLength": 1, "maxLength": 255},
        },
        "mot_de_passe": {"type": "string", "minLength": 8, "maxLength": 200},
    },
}


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(AppConfig)
    app.secret_key = app.config["SECRET_KEY"]

    # Utilisez instance/ pour sqlite (recommandé par Flask).
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    if not app.config["DATABASE"]:
        app.config["DATABASE"] = os.path.join(app.instance_path, "violations.sqlite3")
    if not app.config["CSV_CACHE"]:
        app.config["CSV_CACHE"] = os.path.join(app.root_path, "data", "violations.csv")

    def get_conn():
        return dbmod.connect(app.config["DATABASE"])

    def current_user():
        user_id = session.get("user_id")
        if not user_id:
            return None
        conn = get_conn()
        try:
            return dbmod.get_user_by_id(conn, int(user_id))
        finally:
            conn.close()

    def token_serializer() -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(app.secret_key)

    def build_unsubscribe_token(user_id: int, establishment: str) -> str:
        return token_serializer().dumps(
            {"user_id": user_id, "establishment": establishment},
            salt=app.config["UNSUBSCRIBE_SALT"],
        )

    def parse_unsubscribe_token(token: str, max_age: int = 60 * 60 * 24 * 30) -> dict | None:
        try:
            return token_serializer().loads(
                token,
                salt=app.config["UNSUBSCRIBE_SALT"],
                max_age=max_age,
            )
        except (BadSignature, SignatureExpired):
            return None

    def send_notification_email(to_email: str, subject: str, body: str) -> bool:
        host = app.config.get("SMTP_HOST", "")
        sender = app.config.get("SMTP_FROM", "")
        if not host or not sender:
            print(f"SMTP non configuré; omission de l'envoi à {to_email}")
            return False

        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)

        port = int(app.config.get("SMTP_PORT", 587))
        username = app.config.get("SMTP_USERNAME", "")
        password = app.config.get("SMTP_PASSWORD", "")
        use_tls = bool(app.config.get("SMTP_USE_TLS", True))

        try:
            with smtplib.SMTP(host, port, timeout=5) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                if username:
                    smtp.login(username, password)
                smtp.send_message(msg)
            return True
        except Exception as e:
            print(f"Impossible d'envoyer le courriel à {to_email}: {e}")
            return False

    def notify_watchers(new_items: list[dict]) -> None:
        if not new_items:
            return

        establishments = [item["establishment"] for item in new_items if item.get("establishment")]
        conn = get_conn()
        try:
            watchers = dbmod.get_watchers_for_establishments(conn, establishments)
        finally:
            conn.close()

        grouped: dict[str, list[dict]] = {}
        for item in new_items:
            est = item.get("establishment", "").strip()
            if not est or est not in watchers:
                continue
            grouped.setdefault(est, []).append(item)

        for establishment, users in watchers.items():
            entries = grouped.get(establishment, [])
            if not entries:
                continue
            lines = []
            for item in entries[:10]:
                lines.append(
                    f"- Date: {item.get('date_iso') or '-'} | Statut: {item.get('status') or '-'} | Montant: {item.get('amount') or 0}"
                )
            if len(entries) > 10:
                lines.append(f"- ... et {len(entries) - 10} autre(s) contravention(s)")

            subject = f"Nouveau contrevenant detecte: {establishment}"
            for user in users:
                token = build_unsubscribe_token(user["user_id"], establishment)
                unsubscribe_url = ""
                if app.config.get("PUBLIC_BASE_URL"):
                    unsubscribe_url = (
                        app.config["PUBLIC_BASE_URL"].rstrip("/")
                        + url_for("unsubscribe_page", token=token)
                    )
                body = (
                    f"Bonjour {user['full_name']},\n\n"
                    f"De nouvelles contraventions ont ete detectees pour l'etablissement surveille '{establishment}'.\n\n"
                    + "\n".join(lines)
                    + (
                        f"\n\nLien de desabonnement:\n{unsubscribe_url}"
                        if unsubscribe_url
                        else "\n\nLien de desabonnement indisponible (PUBLIC_BASE_URL non configure)."
                    )
                    + "\n\nCeci est un message automatique du systeme INF5190."
                )
                try:
                    send_notification_email(user["email"], subject, body)
                except Exception as exc:
                    print(f"Envoi du courriel échoué pour {user['email']}: {exc}")

    def ensure_database_bootstrap() -> None:
        """
        Initialiser la base de données SQLite au premier démarrage.

        Railway n'expose pas de shell facile dans chaque plan/chemin UI, donc l'application peut
        s'auto-initialiser si le fichier BD ou les données de la table sont manquants.
        """
        db_path = Path(app.config["DATABASE"])
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = get_conn()
        try:
            if not db_path.exists() or db_path.stat().st_size == 0:
                schema_path = Path(app.root_path) / "db" / "db.sql"
                if schema_path.exists():
                    conn.executescript(schema_path.read_text(encoding="utf-8"))
                    conn.commit()

            dbmod.ensure_schema(conn)

            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='violations'"
            ).fetchone()
            if not row:
                schema_path = Path(app.root_path) / "db" / "db.sql"
                conn.executescript(schema_path.read_text(encoding="utf-8"))
                conn.commit()

            if not dbmod.table_has_data(conn):
                cache_path = Path(app.config["CSV_CACHE"])
                if not cache_path.exists():
                    dbmod.download_csv(cache_path)
                dbmod.import_csv(conn, cache_path)
        finally:
            conn.close()

    def db_ready() -> tuple[bool, str]:
        db_path = app.config["DATABASE"]
        try:
            ensure_database_bootstrap()
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='violations'"
                ).fetchone()
                if not row:
                    return False, "Table 'violations' absente. Cree la DB avec db/db.sql puis importe le CSV."
                # Migration au mieux pour les anciennes BDs.
                dbmod.ensure_schema(conn)
                if not dbmod.table_has_data(conn):
                    return False, "Base initialisee mais aucune donnee n'a pu etre importee."
                return True, ""
            finally:
                conn.close()
        except Exception as e:
            return False, str(e)

    def parse_iso_date(value: str) -> str | None:
        s = (value or "").strip()
        if not s:
            return None
        # Date ISO 8601 (AAAA-MM-JJ)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return None
        return s

    def sync_daily() -> None:
        """
        Daily sync job: download the CSV and refresh the SQLite table.
        """
        ensure_database_bootstrap()

        cache_path = Path(app.config["CSV_CACHE"])
        dbmod.download_csv(cache_path)

        conn = get_conn()
        try:
            new_items = dbmod.detect_new_violations(conn, cache_path)
            dbmod.refresh_from_csv(conn, cache_path)
        finally:
            conn.close()
        notify_watchers(new_items)

    def start_scheduler() -> None:
        if not app.config.get("SCHEDULER_ENABLED", True):
            return
        if BackgroundScheduler is None or CronTrigger is None:
            # Dépendance non installée; maintenir l'application utilisable.
            return

        # Éviter la double planification quand le rechargeur de debug Flask est activé.
        if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
            return

        tz = ZoneInfo(app.config.get("SCHEDULER_TZ", "America/Toronto"))
        scheduler = BackgroundScheduler(timezone=tz)
        trigger = CronTrigger(hour=0, minute=0, timezone=tz)
        scheduler.add_job(
            sync_daily,
            trigger=trigger,
            id="daily_sync",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()

        # Assurer l'arrêt propre.
        atexit.register(lambda: scheduler.shutdown(wait=False))

    start_scheduler()

    @app.get("/")
    def index():
        ready, reason = db_ready()
        return render_template("accueil.html", db_ready=ready, db_reason=reason, user=current_user())

    @app.get("/inscription")
    def signup_page():
        return render_template("inscription.html", user=current_user())

    @app.get("/connexion")
    def login_page():
        return render_template("connexion.html", error="", user=current_user())

    @app.post("/connexion")
    def login_submit():
        email = (request.form.get("courriel", "") or "").strip()
        password = request.form.get("mot_de_passe", "") or ""
        conn = get_conn()
        try:
            user = dbmod.authenticate_user(conn, email, password)
        finally:
            conn.close()
        if not user:
            flash("Courriel ou mot de passe invalide.", "error")
            return redirect(url_for("login_page"))
        session["user_id"] = int(user["id"])
        flash("Connexion reussie.", "success")
        return redirect(url_for("profile_page"))

    @app.post("/deconnexion")
    def logout_submit():
        session.clear()
        flash("Deconnexion reussie.", "success")
        return redirect(url_for("index"))

    @app.get("/profil")
    def profile_page():
        user = current_user()
        if not user:
            return redirect(url_for("login_page"))
        return render_template("profil.html", user=user)

    @app.post("/profil/watchlist")
    def profile_watchlist_submit():
        user = current_user()
        if not user:
            return redirect(url_for("login_page"))

        raw_text = request.form.get("watchlist", "") or ""
        watchlist = [line.strip() for line in raw_text.splitlines()]
        conn = get_conn()
        try:
            updated = dbmod.replace_user_watchlist(conn, int(user["id"]), watchlist)
        finally:
            conn.close()
        flash(f"{len(updated)} etablissement(s) surveille(s) enregistres.", "success")
        return redirect(url_for("profile_page"))

    @app.post("/profil/photo")
    def profile_photo_submit():
        user = current_user()
        if not user:
            return redirect(url_for("login_page"))

        uploaded = request.files.get("photo")
        if uploaded is None or uploaded.filename == "":
            flash("Aucun fichier fourni.", "error")
            return redirect(url_for("profile_page"))

        mime_type = (uploaded.mimetype or "").lower()
        if mime_type not in {"image/jpeg", "image/png"}:
            flash("Formats acceptes: JPG et PNG.", "error")
            return redirect(url_for("profile_page"))

        photo_bytes = uploaded.read()
        if not photo_bytes:
            flash("Fichier vide.", "error")
            return redirect(url_for("profile_page"))

        conn = get_conn()
        try:
            dbmod.save_user_photo(conn, int(user["id"]), photo_bytes, mime_type)
        finally:
            conn.close()
        flash("Photo de profil enregistree.", "success")
        return redirect(url_for("profile_page"))

    @app.post("/profil/test-email")
    def profile_test_email_submit():
        user = current_user()
        if not user:
            return redirect(url_for("login_page"))

        watchlist = user.get("watchlist") or []
        if not watchlist:
            flash("Ajoute d'abord au moins un etablissement a surveiller.", "error")
            return redirect(url_for("profile_page"))

        establishment = str(watchlist[0]).strip()
        token = build_unsubscribe_token(int(user["id"]), establishment)
        unsubscribe_url = ""
        if app.config.get("PUBLIC_BASE_URL"):
            unsubscribe_url = app.config["PUBLIC_BASE_URL"].rstrip("/") + url_for(
                "unsubscribe_page", token=token
            )

        subject = f"Test INF5190 - notification pour {establishment}"
        body = (
            f"Bonjour {user['full_name']},\n\n"
            f"Ceci est un courriel de test pour l'etablissement surveille '{establishment}'.\n\n"
            "Exemple de nouvelle contravention detectee:\n"
            "- Date: 2026-04-13 | Statut: Ouvert | Montant: 500.0\n"
            + (
                f"\nLien de desabonnement:\n{unsubscribe_url}\n"
                if unsubscribe_url
                else "\nLien de desabonnement indisponible (PUBLIC_BASE_URL non configure).\n"
            )
            + "\nCeci est un message de test du systeme INF5190."
        )

        try:
            sent = send_notification_email(str(user["email"]), subject, body)
        except Exception as exc:
            flash(f"Echec d'envoi du courriel de test: {exc}", "error")
            return redirect(url_for("profile_page"))

        if not sent:
            flash("SMTP non configure. Verifie les variables SMTP_* et PUBLIC_BASE_URL.", "error")
            return redirect(url_for("profile_page"))

        flash(f"Courriel de test envoye a {user['email']}.", "success")
        return redirect(url_for("profile_page"))

    @app.get("/profil/photo/<int:user_id>")
    def profile_photo_view(user_id: int):
        conn = get_conn()
        try:
            payload = dbmod.get_user_photo(conn, user_id)
        finally:
            conn.close()
        if not payload:
            return ("Photo introuvable.", 404)
        photo_bytes, mime_type = payload
        return send_file(io.BytesIO(photo_bytes), mimetype=mime_type)

    @app.get("/desabonnement")
    def unsubscribe_page():
        token = (request.args.get("token", "") or "").strip()
        payload = parse_unsubscribe_token(token)
        if not payload:
            return render_template(
                "desabonnement.html",
                valid=False,
                token="",
                establishment="",
                email="",
            ), 400

        conn = get_conn()
        try:
            user = dbmod.get_user_by_id(conn, int(payload["user_id"]))
        finally:
            conn.close()
        if not user:
            return render_template(
                "desabonnement.html",
                valid=False,
                token="",
                establishment="",
                email="",
            ), 404

        return render_template(
            "desabonnement.html",
            valid=True,
            token=token,
            establishment=payload["establishment"],
            email=user["email"],
        )

    @app.delete("/api/desabonnement")
    def unsubscribe_api():
        data = request.get_json(silent=True) or {}
        token = (data.get("token", "") or "").strip()
        payload = parse_unsubscribe_token(token)
        if not payload:
            return jsonify({"error": "Lien de desabonnement invalide ou expire."}), 400

        conn = get_conn()
        try:
            removed = dbmod.remove_watchlist_item(
                conn,
                int(payload["user_id"]),
                str(payload["establishment"]),
            )
        finally:
            conn.close()

        if not removed:
            return jsonify({"error": "Cet etablissement n'etait plus dans la liste de surveillance."}), 404
        return jsonify({"ok": True, "establishment": payload["establishment"]})

    @app.get("/search")
    def search_page():
        ready, reason = db_ready()
        if not ready:
            return render_template("accueil.html", db_ready=False, db_reason=reason), 500

        establishment = request.args.get("establishment", "").strip()
        owner = request.args.get("owner", "").strip()
        street = request.args.get("street", "").strip()

        no_criteria = not (establishment or owner or street)
        if no_criteria:
            results = []
        else:
            conn = get_conn()
            try:
                results = dbmod.search_for_page(conn, establishment, owner, street)
            finally:
                conn.close()

        return render_template(
            "resultats.html",
            establishment=establishment,
            owner=owner,
            street=street,
            results=results,
            no_criteria=no_criteria,
        )

    @app.get("/api/facets")
    def api_facets():
        ready, reason = db_ready()
        if not ready:
            return jsonify({"error": reason}), 500
        conn = get_conn()
        try:
            return jsonify(dbmod.facets(conn))
        finally:
            conn.close()

    @app.get("/api/violations")
    def api_violations():
        ready, reason = db_ready()
        if not ready:
            return jsonify({"error": reason}), 500

        params = dbmod.SearchParams(
            q=request.args.get("q", "").strip(),
            status=request.args.get("status", "").strip(),
            category=request.args.get("category", "").strip(),
            city=request.args.get("city", "").strip(),
            from_date=request.args.get("from", "").strip(),
            to_date=request.args.get("to", "").strip(),
            sort=request.args.get("sort", "date_desc").strip(),
            page=int(request.args.get("page", "1") or 1),
            page_size=int(request.args.get("page_size", request.args.get("pageSize", "25")) or 25),
        )

        conn = get_conn()
        try:
            return jsonify(dbmod.search(conn, params))
        finally:
            conn.close()

    @app.get("/contrevenants")
    def contrevenants_between_dates():
        """
        REST: GET /contrevenants?du=AAAA-MM-JJ&au=AAAA-MM-JJ
        Retourner une liste JSON de contraventions entre deux dates ISO 8601 (incluses).
        """
        ready, reason = db_ready()
        if not ready:
            return jsonify({"error": reason}), 500

        du = parse_iso_date(request.args.get("du", ""))
        au = parse_iso_date(request.args.get("au", ""))
        if not du or not au:
            return (
                jsonify(
                    {
                        "error": "Parametres invalides. Utilise du=YYYY-MM-DD&au=YYYY-MM-DD (ISO 8601).",
                        "example": "/contrevenants?du=2022-05-08&au=2024-05-15",
                    }
                ),
                400,
            )
        if du > au:
            return jsonify({"error": "La date 'du' doit etre <= a la date 'au'."}), 400

        mode = (request.args.get("mode", "") or "").strip().lower()

        limit = request.args.get("limit", "").strip()
        try:
            lim = int(limit) if limit else 5000
        except ValueError:
            return jsonify({"error": "Parametre 'limit' invalide (entier)."}), 400

        conn = get_conn()
        try:
            if mode == "counts":
                items = dbmod.counts_between_dates(conn, du, au, limit=lim)
            else:
                items = dbmod.list_between_dates(conn, du, au, limit=lim)
        finally:
            conn.close()

        return jsonify(items)

    @app.get("/doc")
    def doc_raml():
        """
        HTML representation of the RAML document describing the REST service.
        """
        raml_path = Path(app.root_path) / "raml" / "api.raml"
        if not raml_path.exists():
            return "RAML introuvable.", 500
        raml_text = raml_path.read_text(encoding="utf-8")
        return render_template("doc.html", raml_text=raml_text)

    @app.get("/restaurants")
    def restaurants_list():
        """
        REST: return the distinct list of establishments for the dropdown.
        """
        ready, reason = db_ready()
        if not ready:
            return jsonify({"error": reason}), 500

        limit = request.args.get("limit", "").strip()
        try:
            lim = int(limit) if limit else 20000
        except ValueError:
            return jsonify({"error": "Parametre 'limit' invalide (entier)."}), 400

        conn = get_conn()
        try:
            items = dbmod.list_restaurants(conn, limit=lim)
        finally:
            conn.close()
        return jsonify(items)

    @app.get("/infractions")
    def infractions_by_restaurant():
        """
        REST: GET /infractions?etablissement=...
        Returns the list of contraventions (infractions) for a selected restaurant.
        """
        ready, reason = db_ready()
        if not ready:
            return jsonify({"error": reason}), 500

        est = (request.args.get("etablissement", "") or "").strip()
        if not est:
            return jsonify({"error": "Parametre requis: etablissement"}), 400

        limit = request.args.get("limit", "").strip()
        try:
            lim = int(limit) if limit else 2000
        except ValueError:
            return jsonify({"error": "Parametre 'limit' invalide (entier)."}), 400

        conn = get_conn()
        try:
            items = dbmod.infractions_for_restaurant(conn, est, limit=lim)
        finally:
            conn.close()
        return jsonify(items)

    @app.post("/utilisateurs")
    def create_user_profile():
        """
        REST: POST /utilisateurs
        Créer un profil utilisateur à partir d'un document JSON validé avec json-schema.
        """
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "Le corps de la requete doit etre un document JSON valide."}), 400

        try:
            validate(instance=data, schema=USER_PROFILE_SCHEMA)
        except ValidationError as exc:
            path = ".".join(str(part) for part in exc.absolute_path)
            return (
                jsonify(
                    {
                        "error": "Document JSON invalide.",
                        "details": exc.message,
                        "path": path,
                    }
                ),
                400,
            )

        conn = get_conn()
        try:
            profile = dbmod.create_user_profile(
                conn,
                full_name=data["nom_complet"],
                email=data["courriel"],
                password=data["mot_de_passe"],
                watchlist=data["etablissements_surveille"],
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        finally:
            conn.close()

        return jsonify(profile), 201

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        if request.path.startswith("/api/") or request.path in {
            "/contrevenants",
            "/restaurants",
            "/infractions",
            "/utilisateurs",
        }:
            return jsonify({"error": exc.description}), exc.code
        return render_template("erreur.html", title=f"Erreur {exc.code}", message=exc.description), exc.code

    @app.errorhandler(Exception)
    def handle_unexpected_exception(exc: Exception):
        print(f"Erreur d'application non gérée: {exc}")
        if request.path.startswith("/api/") or request.path in {
            "/contrevenants",
            "/restaurants",
            "/infractions",
            "/utilisateurs",
        }:
            return jsonify({"error": "Erreur interne du serveur."}), 500
        return render_template(
            "error.html",
            title="Erreur interne du serveur",
            message="Une erreur systeme est survenue. Veuillez reessayer plus tard.",
        ), 500

    return app


if __name__ == "__main__":
    app = create_app()
    host = app.config["HOST"]
    port = app.config["PORT"]
    debug = app.config["DEBUG"]
    app.run(debug=debug, host=host, port=port)
