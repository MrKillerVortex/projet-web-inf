import os
import atexit
import re
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import db as dbmod

from zoneinfo import ZoneInfo

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except ModuleNotFoundError:
    BackgroundScheduler = None
    CronTrigger = None


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    # Use instance/ for sqlite (recommended by Flask).
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.config["DATABASE"] = os.environ.get(
        "INF5190_DB_PATH", os.path.join(app.instance_path, "violations.sqlite3")
    )
    app.config["CSV_CACHE"] = os.path.join(app.root_path, "data", "violations.csv")
    app.config["SCHEDULER_ENABLED"] = os.environ.get("INF5190_SCHEDULER", "1") != "0"
    app.config["SCHEDULER_TZ"] = os.environ.get("INF5190_TZ", "America/Toronto")

    def get_conn():
        return dbmod.connect(app.config["DATABASE"])

    def db_ready() -> tuple[bool, str]:
        db_path = app.config["DATABASE"]
        if not os.path.exists(db_path):
            return False, f"Base introuvable: {db_path}"
        try:
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='violations'"
                ).fetchone()
                if not row:
                    return False, "Table 'violations' absente. Cree la DB avec db/db.sql puis importe le CSV."
                # Best-effort migration for older DBs.
                dbmod.ensure_schema(conn)
                return True, ""
            finally:
                conn.close()
        except Exception as e:
            return False, str(e)

    def parse_iso_date(value: str) -> str | None:
        s = (value or "").strip()
        if not s:
            return None
        # ISO 8601 date (YYYY-MM-DD)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return None
        return s

    def sync_daily() -> None:
        """
        Daily sync job: download the CSV and refresh the SQLite table.
        """
        db_path = app.config["DATABASE"]
        if not os.path.exists(db_path):
            # Database must be created ahead of time per assignment.
            return

        cache_path = Path(app.config["CSV_CACHE"])
        dbmod.download_csv(cache_path)

        conn = get_conn()
        try:
            dbmod.refresh_from_csv(conn, cache_path)
        finally:
            conn.close()

    def start_scheduler() -> None:
        if not app.config.get("SCHEDULER_ENABLED", True):
            return
        if BackgroundScheduler is None or CronTrigger is None:
            # Dependency not installed; keep app usable.
            return

        # Avoid double-scheduling when Flask debug reloader is enabled.
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

        # Ensure clean shutdown.
        atexit.register(lambda: scheduler.shutdown(wait=False))

    start_scheduler()

    @app.get("/")
    def index():
        ready, reason = db_ready()
        return render_template("index.html", db_ready=ready, db_reason=reason)

    @app.get("/search")
    def search_page():
        ready, reason = db_ready()
        if not ready:
            return render_template("index.html", db_ready=False, db_reason=reason), 500

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
            "search_results.html",
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
        REST: GET /contrevenants?du=YYYY-MM-DD&au=YYYY-MM-DD
        Returns JSON list of contraventions between two ISO 8601 dates (inclusive).
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

    return app


if __name__ == "__main__":
    app = create_app()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=debug, host=host, port=port)
