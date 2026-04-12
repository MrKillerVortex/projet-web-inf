import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime


DEFAULT_CSV_URL = (
    "https://data.montreal.ca/dataset/05a9e718-6810-4e73-8bb9-5955efeb91a0/"
    "resource/7f939a08-be8a-45e1-b208-d8744dca8fc6/download/violations.csv"
)


def normalize(text: str) -> str:
    if text is None:
        return ""
    s = str(text).strip().lower()
    # Minimal accent folding, no external deps.
    s = (
        s.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("ë", "e")
        .replace("à", "a")
        .replace("â", "a")
        .replace("ä", "a")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("ô", "o")
        .replace("ö", "o")
        .replace("ù", "u")
        .replace("û", "u")
        .replace("ü", "u")
        .replace("ç", "c")
    )
    return s


def header_key(h: str) -> str:
    s = normalize(h)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s


def parse_money(value: str) -> float:
    if value is None:
        return 0.0
    s = str(value).strip()
    if not s:
        return 0.0
    cleaned = re.sub(r"[^0-9,.\-]", "", s).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_date_loose(value: str) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", raw)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    for fmt in ("%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = datetime.strptime(raw, fmt)
            return d.date().isoformat()
        except ValueError:
            continue

    return None


def get_first(record: dict, keys: list[str]) -> str:
    for k in keys:
        v = record.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def download_csv(url: str, timeout_s: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "INF5190-import-script/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_db_is_precreated(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='violations'"
    ).fetchone()
    if not row:
        raise RuntimeError(
            "La base existe mais la table 'violations' est absente. "
            "Cree la DB avec 'db/db.sql' avant d'executer l'import."
        )


def row_hash(raw_row: dict) -> str:
    """
    Hash stable base sur la ligne brute (apres normalisation des en-tetes).
    """
    payload = json.dumps(raw_row, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def import_rows(conn: sqlite3.Connection, raw_rows: list[dict]) -> tuple[int, int]:
    """
    Returns (inserted, ignored).
    """
    inserted = 0
    ignored = 0

    cur = conn.cursor()

    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(violations)").fetchall()
        if r["name"] is not None
    }

    has_owner = "owner" in cols
    has_street = "street" in cols
    has_searchable = "searchable" in cols

    if has_searchable:
        insert_sql = """
            INSERT OR IGNORE INTO violations (
              source_hash, date_iso, establishment, owner, street, category, description, amount, status, city, searchable, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
    else:
        # Fallback for older schemas: insert core fields only.
        insert_sql = """
            INSERT OR IGNORE INTO violations (
              source_hash, date_iso, establishment, category, description, amount, status, city, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

    for r in raw_rows:
        establishment = get_first(
            r,
            [
                "etablissement",
                "nom_etablissement",
                "nom_de_etablissement",
                "nom_de_letablissement",
                "etablissement_nom",
                "establishment",
                "name",
            ],
        ).strip()
        category = get_first(r, ["categorie", "category", "categorie_etablissement"]).strip()
        status = get_first(r, ["statut", "status", "statut_dossier"]).strip()
        city = get_first(r, ["ville", "city", "municipalite"]).strip()
        description = get_first(r, ["description", "infraction", "details"]).strip()
        amount = parse_money(get_first(r, ["montant", "montant_total", "amount", "amende", "fine"]))
        date_iso = parse_date_loose(get_first(r, ["date_jugement", "date_du_jugement", "date", "judgement_date"]))

        owner = get_first(
            r,
            ["proprietaire", "nom_proprietaire", "proprietaire_nom", "owner"],
        ).strip()
        street = get_first(
            r,
            ["rue", "nom_rue", "adresse", "adresse_etablissement", "adresse_complete", "street"],
        ).strip()

        h = row_hash(r)
        raw_json = json.dumps(r, ensure_ascii=True, separators=(",", ":"))
        searchable = normalize(" ".join([establishment, owner, street, category, status, city, description]))

        if has_searchable:
            cur.execute(
                insert_sql,
                (
                    h,
                    date_iso,
                    establishment,
                    owner if has_owner else "",
                    street if has_street else "",
                    category,
                    description,
                    amount,
                    status,
                    city,
                    searchable,
                    raw_json,
                ),
            )
        else:
            cur.execute(
                insert_sql,
                (h, date_iso, establishment, category, description, amount, status, city, raw_json),
            )
        if cur.rowcount == 1:
            inserted += 1
        else:
            ignored += 1

    conn.commit()
    return inserted, ignored


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Telecharge violations.csv (Montreal) et insere dans une base SQLite existante."
    )
    p.add_argument("--db", required=True, help="Chemin vers la base SQLite (doit exister).")
    p.add_argument("--url", default=DEFAULT_CSV_URL, help="URL du CSV.")
    p.add_argument("--input", default="", help="Chemin vers un CSV local (option de test; ignore --url).")
    p.add_argument("--timeout", type=int, default=30, help="Timeout HTTP en secondes.")
    p.add_argument("--limit", type=int, default=0, help="Limiter le nombre de lignes importees (0 = pas de limite).")
    args = p.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"ERREUR: base SQLite introuvable: {args.db}", file=sys.stderr)
        return 2

    if args.input:
        if not os.path.exists(args.input):
            print(f"ERREUR: CSV introuvable: {args.input}", file=sys.stderr)
            return 2
        csv_bytes = open(args.input, "rb").read()
    else:
        try:
            csv_bytes = download_csv(args.url, args.timeout)
        except Exception as e:
            print(f"ERREUR: telechargement HTTP impossible ({e}).", file=sys.stderr)
            return 3

    text = csv_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))

    try:
        headers = next(reader)
    except StopIteration:
        print("ERREUR: CSV vide.", file=sys.stderr)
        return 2

    keys = [header_key(h) for h in headers]
    raw_rows: list[dict] = []

    for i, cells in enumerate(reader, start=1):
        row = {keys[j]: (cells[j] if j < len(cells) else "") for j in range(len(keys))}
        raw_rows.append(row)
        if args.limit and i >= args.limit:
            break

    conn = connect(args.db)
    try:
        ensure_db_is_precreated(conn)
        inserted, ignored = import_rows(conn, raw_rows)
    finally:
        conn.close()

    print(f"Import termine. Lignes lues: {len(raw_rows)}. Inserees: {inserted}. Ignorees (doublons): {ignored}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
