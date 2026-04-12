import csv
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import urllib.request
from dataclasses import dataclass
from pathlib import Path


CSV_URL = (
    "https://donnees.montreal.ca/dataset/inspection-aliments-contrevenants/"
    "resource/7f939a08-be8a-45e1-b208-d8744dca8fc6/download/violations.csv"
)


def _normalize(text: str) -> str:
    if text is None:
        return ""
    s = str(text).strip().lower()
    # Remove basic accents without extra deps.
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


def _header_key(h: str) -> str:
    s = _normalize(h)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s


def _parse_money(value: str) -> float:
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


def _parse_date_loose(value: str) -> str | None:
    """
    Return ISO date YYYY-MM-DD, or None if unknown.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    # yyyy-mm-dd (or with time)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # dd/mm/yyyy
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", raw)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    # Last attempt: let datetime parse common formats.
    for fmt in ("%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = dt.datetime.strptime(raw, fmt)
            return d.date().isoformat()
        except ValueError:
            continue

    return None


def _get_first(record: dict, keys: list[str]) -> str:
    for k in keys:
        v = record.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


@dataclass(frozen=True)
class SearchParams:
    q: str = ""
    status: str = ""
    category: str = ""
    city: str = ""
    from_date: str = ""  # YYYY-MM-DD
    to_date: str = ""  # YYYY-MM-DD
    sort: str = "date_desc"
    page: int = 1
    page_size: int = 25


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    # Create base table (new installs).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS violations (
          id INTEGER PRIMARY KEY,
          source_hash TEXT NOT NULL UNIQUE,
          date_iso TEXT,
          establishment TEXT,
          owner TEXT,
          street TEXT,
          category TEXT,
          description TEXT,
          amount REAL,
          status TEXT,
          city TEXT,
          searchable TEXT NOT NULL,
          raw_json TEXT
        )
        """
    )

    # Create indexes best-effort. Old DBs may not have columns yet; migrations handle that.
    for stmt in (
        "CREATE INDEX IF NOT EXISTS idx_violations_date ON violations(date_iso)",
        "CREATE INDEX IF NOT EXISTS idx_violations_status ON violations(status)",
        "CREATE INDEX IF NOT EXISTS idx_violations_category ON violations(category)",
        "CREATE INDEX IF NOT EXISTS idx_violations_city ON violations(city)",
        "CREATE INDEX IF NOT EXISTS idx_violations_establishment ON violations(establishment)",
        "CREATE INDEX IF NOT EXISTS idx_violations_owner ON violations(owner)",
        "CREATE INDEX IF NOT EXISTS idx_violations_street ON violations(street)",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass


def ensure_schema(conn: sqlite3.Connection) -> None:
    """
    Ensure the DB schema is compatible with the current app version.

    SQLite doesn't support ALTERing many constraints; we do a best-effort migration:
    add missing columns and indexes so old databases keep working.
    """
    init_schema(conn)

    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(violations)").fetchall()
        if r["name"] is not None
    }

    def add_col(sql: str) -> None:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            # Column already exists or not supported in this context.
            pass

    if "source_hash" not in cols:
        add_col("ALTER TABLE violations ADD COLUMN source_hash TEXT")
    if "owner" not in cols:
        add_col("ALTER TABLE violations ADD COLUMN owner TEXT")
    if "street" not in cols:
        add_col("ALTER TABLE violations ADD COLUMN street TEXT")
    if "searchable" not in cols:
        # Must be NOT NULL because code assumes presence; default keeps migration safe.
        add_col("ALTER TABLE violations ADD COLUMN searchable TEXT NOT NULL DEFAULT ''")

    # Indexes for added columns (safe to run repeatedly).
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_violations_establishment ON violations(establishment)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_violations_owner ON violations(owner)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_violations_street ON violations(street)")
    except sqlite3.OperationalError:
        pass

    # Backfill searchable if empty.
    try:
        conn.execute(
            """
            UPDATE violations
            SET searchable = LOWER(
              TRIM(
                COALESCE(establishment,'') || ' ' ||
                COALESCE(owner,'') || ' ' ||
                COALESCE(street,'') || ' ' ||
                COALESCE(category,'') || ' ' ||
                COALESCE(status,'') || ' ' ||
                COALESCE(city,'') || ' ' ||
                COALESCE(description,'')
              )
            )
            WHERE searchable IS NULL OR TRIM(searchable) = ''
            """
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()


def table_has_data(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(1) AS n FROM violations").fetchone()
    return int(row["n"]) > 0


def download_csv(cache_path: Path) -> Path:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".tmp")
    with urllib.request.urlopen(CSV_URL, timeout=30) as resp, open(tmp_path, "wb") as f:
        f.write(resp.read())
    os.replace(tmp_path, cache_path)
    return cache_path


def import_csv(conn: sqlite3.Connection, csv_path: Path, *, commit: bool = True) -> int:
    """
    Import the CSV into SQLite. Returns inserted row count.
    """
    ensure_schema(conn)

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return 0

        keys = [_header_key(h) for h in headers]
        inserted = 0
        cur = conn.cursor()

        for cells in reader:
            record = {keys[i]: (cells[i] if i < len(cells) else "") for i in range(len(keys))}

            establishment = _get_first(
                record,
                [
                    "etablissement",
                    "nom_etablissement",
                    "nom_de_etablissement",
                    "nom_de_letablissement",
                    "etablissement_nom",
                    "establishment",
                    "name",
                ],
            )
            category = _get_first(record, ["categorie", "category", "categorie_etablissement"])
            status = _get_first(record, ["statut", "status", "statut_dossier"])
            city = _get_first(record, ["ville", "city", "municipalite"])
            description = _get_first(record, ["description", "infraction", "details"])
            amount = _parse_money(_get_first(record, ["montant", "montant_total", "amount", "amende", "fine"]))
            date_iso = _parse_date_loose(
                _get_first(record, ["date_jugement", "date_du_jugement", "date", "judgement_date"])
            )
            owner = _get_first(record, ["proprietaire", "nom_proprietaire", "proprietaire_nom", "owner"])
            street = _get_first(
                record, ["rue", "nom_rue", "adresse", "adresse_etablissement", "adresse_complete", "street"]
            )

            searchable = _normalize(
                " ".join(
                    [
                        establishment,
                        owner,
                        street,
                        category,
                        status,
                        city,
                        description,
                        _get_first(record, ["proprietaire", "owner"]),
                    ]
                )
            )

            payload = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            source_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

            cur.execute(
                """
                INSERT INTO violations (
                  source_hash, date_iso, establishment, owner, street, category, description, amount, status, city, searchable, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_hash) DO NOTHING
                """,
                (
                    source_hash,
                    date_iso,
                    establishment.strip(),
                    owner.strip(),
                    street.strip(),
                    category.strip(),
                    description.strip(),
                    amount,
                    status.strip(),
                    city.strip(),
                    searchable,
                    payload,
                ),
            )
            inserted += cur.execute("SELECT changes()").fetchone()[0]

    if commit:
        conn.commit()
    return inserted


def refresh_from_csv(conn: sqlite3.Connection, csv_path: Path) -> int:
    """
    Synchronize the table with the current CSV contents.

    This performs a transactional refresh: it deletes existing rows, then re-imports
    the current CSV. Readers keep seeing the old snapshot while the transaction runs
    when using WAL mode.
    """
    ensure_schema(conn)

    inserted = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM violations")
        inserted = import_csv(conn, csv_path, commit=False)
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise


def build_where(params: SearchParams) -> tuple[str, list]:
    clauses: list[str] = []
    values: list = []

    if params.q:
        clauses.append("searchable LIKE ?")
        values.append(f"%{_normalize(params.q)}%")
    if params.status:
        clauses.append("status = ?")
        values.append(params.status)
    if params.category:
        clauses.append("category = ?")
        values.append(params.category)
    if params.city:
        clauses.append("city = ?")
        values.append(params.city)
    if params.from_date:
        clauses.append("date_iso >= ?")
        values.append(params.from_date)
    if params.to_date:
        clauses.append("date_iso <= ?")
        values.append(params.to_date)

    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    return where_sql, values


def search(conn: sqlite3.Connection, params: SearchParams) -> dict:
    where_sql, values = build_where(params)

    total_row = conn.execute(f"SELECT COUNT(1) AS n FROM violations {where_sql}", values).fetchone()
    total = int(total_row["n"])

    # Stats
    stats_row = conn.execute(
        f"SELECT COUNT(1) AS n, COUNT(DISTINCT establishment) AS est, COALESCE(SUM(amount),0) AS amt FROM violations {where_sql}",
        values,
    ).fetchone()
    stats = {
        "total": int(stats_row["n"]),
        "establishments": int(stats_row["est"]),
        "amount_sum": float(stats_row["amt"]),
    }

    top_categories = [
        {"label": r["category"], "count": int(r["c"])}
        for r in conn.execute(
            f"""
            SELECT category, COUNT(1) AS c
            FROM violations
            {where_sql}
            GROUP BY category
            ORDER BY c DESC
            LIMIT 5
            """,
            values,
        ).fetchall()
        if (r["category"] or "").strip()
    ]

    sort = params.sort or "date_desc"
    if sort == "date_asc":
        order_sql = "ORDER BY date_iso ASC, id ASC"
    elif sort == "amount_asc":
        order_sql = "ORDER BY amount ASC, id ASC"
    elif sort == "amount_desc":
        order_sql = "ORDER BY amount DESC, id ASC"
    else:
        order_sql = "ORDER BY date_iso DESC, id ASC"

    page_size = max(1, min(int(params.page_size or 25), 100))
    page = max(1, int(params.page or 1))
    offset = (page - 1) * page_size

    rows = conn.execute(
        f"""
        SELECT date_iso, establishment, category, description, amount, status, city
        FROM violations
        {where_sql}
        {order_sql}
        LIMIT ? OFFSET ?
        """,
        values + [page_size, offset],
    ).fetchall()

    items = [
        {
            "date": r["date_iso"] or "",
            "establishment": r["establishment"] or "",
            "category": r["category"] or "",
            "description": r["description"] or "",
            "amount": float(r["amount"] or 0.0),
            "status": r["status"] or "",
            "city": r["city"] or "",
        }
        for r in rows
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "stats": stats,
        "top_categories": top_categories,
    }


def facets(conn: sqlite3.Connection) -> dict:
    def _col(name: str) -> list[str]:
        rows = conn.execute(
            f"SELECT DISTINCT {name} AS v FROM violations WHERE {name} IS NOT NULL AND TRIM({name}) <> '' ORDER BY v COLLATE NOCASE"
        ).fetchall()
        return [r["v"] for r in rows]

    return {
        "status": _col("status"),
        "category": _col("category"),
        "city": _col("city"),
    }


def search_for_page(
    conn: sqlite3.Connection,
    establishment: str,
    owner: str,
    street: str,
    limit: int = 200,
) -> list[dict]:
    establishment = (establishment or "").strip()
    owner = (owner or "").strip()
    street = (street or "").strip()

    clauses: list[str] = []
    values: list = []

    if establishment:
        clauses.append("LOWER(establishment) LIKE ?")
        values.append(f"%{establishment.lower()}%")
    if owner:
        clauses.append("LOWER(owner) LIKE ?")
        values.append(f"%{owner.lower()}%")
    if street:
        clauses.append("LOWER(street) LIKE ?")
        values.append(f"%{street.lower()}%")

    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    lim = max(1, min(int(limit or 200), 500))

    rows = conn.execute(
        f"""
        SELECT id, date_iso, establishment, owner, street, category, description, amount, status, city, raw_json
        FROM violations
        {where_sql}
        ORDER BY date_iso DESC, id DESC
        LIMIT ?
        """,
        values + [lim],
    ).fetchall()

    results: list[dict] = []
    for r in rows:
        raw = {}
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            raw = {"_raw_json": r["raw_json"] or ""}

        results.append(
            {
                "id": int(r["id"]),
                "date": r["date_iso"] or "",
                "establishment": r["establishment"] or "",
                "owner": r["owner"] or "",
                "street": r["street"] or "",
                "category": r["category"] or "",
                "description": r["description"] or "",
                "amount": float(r["amount"] or 0.0),
                "status": r["status"] or "",
                "city": r["city"] or "",
                "raw": raw,
            }
        )

    return results


def list_between_dates(
    conn: sqlite3.Connection,
    du: str,
    au: str,
    limit: int = 5000,
) -> list[dict]:
    """
    Return contraventions with date_iso between [du, au] (inclusive).
    Dates must be ISO 8601 date strings: YYYY-MM-DD.
    """
    lim = max(1, min(int(limit or 5000), 20000))
    rows = conn.execute(
        """
        SELECT id, date_iso, establishment, owner, street, category, description, amount, status, city, raw_json
        FROM violations
        WHERE date_iso IS NOT NULL AND date_iso >= ? AND date_iso <= ?
        ORDER BY date_iso ASC, id ASC
        LIMIT ?
        """,
        (du, au, lim),
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        raw = {}
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            raw = {"_raw_json": r["raw_json"] or ""}

        out.append(
            {
                "id": int(r["id"]),
                "date": r["date_iso"] or "",
                "establishment": r["establishment"] or "",
                "owner": r["owner"] or "",
                "street": r["street"] or "",
                "category": r["category"] or "",
                "description": r["description"] or "",
                "amount": float(r["amount"] or 0.0),
                "status": r["status"] or "",
                "city": r["city"] or "",
                "raw": raw,
            }
        )

    return out


def counts_between_dates(
    conn: sqlite3.Connection,
    du: str,
    au: str,
    limit: int = 5000,
) -> list[dict]:
    """
    Return a list of {establishment, count} for violations between [du, au] inclusive.
    """
    lim = max(1, min(int(limit or 5000), 20000))
    rows = conn.execute(
        """
        SELECT
          COALESCE(NULLIF(TRIM(establishment), ''), '(inconnu)') AS establishment,
          COUNT(1) AS n
        FROM violations
        WHERE date_iso IS NOT NULL AND date_iso >= ? AND date_iso <= ?
        GROUP BY COALESCE(NULLIF(TRIM(establishment), ''), '(inconnu)')
        ORDER BY n DESC, establishment ASC
        LIMIT ?
        """,
        (du, au, lim),
    ).fetchall()

    return [{"establishment": r["establishment"], "count": int(r["n"])} for r in rows]


def list_restaurants(conn: sqlite3.Connection, limit: int = 20000) -> list[str]:
    lim = max(1, min(int(limit or 20000), 50000))
    rows = conn.execute(
        """
        SELECT DISTINCT establishment AS v
        FROM violations
        WHERE establishment IS NOT NULL AND TRIM(establishment) <> ''
        ORDER BY v COLLATE NOCASE
        LIMIT ?
        """,
        (lim,),
    ).fetchall()
    return [r["v"] for r in rows]


def infractions_for_restaurant(
    conn: sqlite3.Connection,
    establishment: str,
    limit: int = 2000,
) -> list[dict]:
    est = (establishment or "").strip()
    lim = max(1, min(int(limit or 2000), 10000))

    rows = conn.execute(
        """
        SELECT id, date_iso, establishment, owner, street, category, description, amount, status, city, raw_json
        FROM violations
        WHERE TRIM(establishment) <> '' AND establishment = ? COLLATE NOCASE
        ORDER BY date_iso DESC, id DESC
        LIMIT ?
        """,
        (est, lim),
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        raw = {}
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            raw = {"_raw_json": r["raw_json"] or ""}

        out.append(
            {
                "id": int(r["id"]),
                "date": r["date_iso"] or "",
                "establishment": r["establishment"] or "",
                "owner": r["owner"] or "",
                "street": r["street"] or "",
                "category": r["category"] or "",
                "description": r["description"] or "",
                "amount": float(r["amount"] or 0.0),
                "status": r["status"] or "",
                "city": r["city"] or "",
                "raw": raw,
            }
        )

    return out
