import csv
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sqlite3
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from werkzeug.security import check_password_hash, generate_password_hash


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


def _parse_csv_records(csv_path: Path) -> list[dict]:
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return []

        keys = [_header_key(h) for h in headers]
        return [
            {keys[i]: (cells[i] if i < len(cells) else "") for i in range(len(keys))}
            for cells in reader
        ]


def _record_to_violation(record: dict) -> dict:
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

    return {
        "source_hash": source_hash,
        "date_iso": date_iso,
        "establishment": establishment.strip(),
        "owner": owner.strip(),
        "street": street.strip(),
        "category": category.strip(),
        "description": description.strip(),
        "amount": amount,
        "status": status.strip(),
        "city": city.strip(),
        "searchable": searchable,
        "raw_json": payload,
    }


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

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY,
          full_name TEXT NOT NULL,
          email TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          profile_photo BLOB,
          profile_photo_mime TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_watchlist (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL,
          establishment TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(user_id, establishment),
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    for stmt in (
        "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
        "CREATE INDEX IF NOT EXISTS idx_user_watchlist_user_id ON user_watchlist(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_watchlist_establishment ON user_watchlist(establishment)",
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

    user_cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(users)").fetchall()
        if r["name"] is not None
    }
    if user_cols:
        if "profile_photo" not in user_cols:
            add_col("ALTER TABLE users ADD COLUMN profile_photo BLOB")
        if "profile_photo_mime" not in user_cols:
            add_col("ALTER TABLE users ADD COLUMN profile_photo_mime TEXT")

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


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if password_hash.startswith("pbkdf2:") or password_hash.startswith("scrypt:"):
        return check_password_hash(password_hash, password)
    if "$" in password_hash:
        try:
            salt, digest = password_hash.split("$", 1)
            expected = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
            return hmac.compare_digest(expected, digest)
        except ValueError:
            return False
    return False


def create_user_profile(
    conn: sqlite3.Connection,
    *,
    full_name: str,
    email: str,
    password: str,
    watchlist: list[str],
) -> dict:
    ensure_schema(conn)

    normalized_email = email.strip().lower()
    password_hash = hash_password(password)

    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            INSERT INTO users (full_name, email, password_hash)
            VALUES (?, ?, ?)
            """,
            (full_name.strip(), normalized_email, password_hash),
        )
        user_id = int(cur.lastrowid)

        cleaned_watchlist = []
        seen = set()
        for name in watchlist:
            item = str(name).strip()
            if not item:
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned_watchlist.append(item)

        for establishment in cleaned_watchlist:
            conn.execute(
                """
                INSERT INTO user_watchlist (user_id, establishment)
                VALUES (?, ?)
                """,
                (user_id, establishment),
            )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        if "users.email" in str(exc).lower() or "unique" in str(exc).lower():
            raise ValueError("Un profil avec cette adresse courriel existe deja.") from exc
        raise

    row = conn.execute(
        """
        SELECT id, full_name, email, created_at
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    ).fetchone()

    watched = [
        r["establishment"]
        for r in conn.execute(
            """
            SELECT establishment
            FROM user_watchlist
            WHERE user_id = ?
            ORDER BY establishment COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()
    ]

    return {
        "id": int(row["id"]),
        "full_name": row["full_name"],
        "email": row["email"],
        "watchlist": watched,
        "created_at": row["created_at"],
    }


def get_user_by_email(conn: sqlite3.Connection, email: str) -> dict | None:
    ensure_schema(conn)
    row = conn.execute(
        """
        SELECT id, full_name, email, password_hash, profile_photo_mime, created_at
        FROM users
        WHERE email = ?
        """,
        (email.strip().lower(),),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> dict | None:
    ensure_schema(conn)
    row = conn.execute(
        """
        SELECT id, full_name, email, profile_photo_mime, created_at
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    if not row:
        return None

    watchlist = [
        r["establishment"]
        for r in conn.execute(
            """
            SELECT establishment
            FROM user_watchlist
            WHERE user_id = ?
            ORDER BY establishment COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()
    ]
    out = dict(row)
    out["watchlist"] = watchlist
    return out


def authenticate_user(conn: sqlite3.Connection, email: str, password: str) -> dict | None:
    user = get_user_by_email(conn, email)
    if not user:
        return None
    if not verify_password(user["password_hash"], password):
        return None
    return get_user_by_id(conn, int(user["id"]))


def replace_user_watchlist(conn: sqlite3.Connection, user_id: int, watchlist: list[str]) -> list[str]:
    ensure_schema(conn)
    cleaned = []
    seen = set()
    for name in watchlist:
        item = str(name).strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM user_watchlist WHERE user_id = ?", (user_id,))
        for establishment in cleaned:
            conn.execute(
                "INSERT INTO user_watchlist (user_id, establishment) VALUES (?, ?)",
                (user_id, establishment),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return cleaned


def remove_watchlist_item(conn: sqlite3.Connection, user_id: int, establishment: str) -> bool:
    ensure_schema(conn)
    cur = conn.execute(
        """
        DELETE FROM user_watchlist
        WHERE user_id = ? AND establishment = ? COLLATE NOCASE
        """,
        (user_id, establishment.strip()),
    )
    conn.commit()
    return cur.rowcount > 0


def save_user_photo(conn: sqlite3.Connection, user_id: int, photo_bytes: bytes, mime_type: str) -> None:
    ensure_schema(conn)
    conn.execute(
        """
        UPDATE users
        SET profile_photo = ?, profile_photo_mime = ?
        WHERE id = ?
        """,
        (photo_bytes, mime_type, user_id),
    )
    conn.commit()


def get_user_photo(conn: sqlite3.Connection, user_id: int) -> tuple[bytes, str] | None:
    ensure_schema(conn)
    row = conn.execute(
        """
        SELECT profile_photo, profile_photo_mime
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    if not row or row["profile_photo"] is None or not row["profile_photo_mime"]:
        return None
    return bytes(row["profile_photo"]), str(row["profile_photo_mime"])


def get_watchers_for_establishments(conn: sqlite3.Connection, establishments: list[str]) -> dict[str, list[dict]]:
    ensure_schema(conn)
    cleaned = []
    seen = set()
    for item in establishments:
        name = str(item).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)

    if not cleaned:
        return {}

    placeholders = ",".join("?" for _ in cleaned)
    rows = conn.execute(
        f"""
        SELECT uw.establishment, u.id AS user_id, u.full_name, u.email
        FROM user_watchlist AS uw
        JOIN users AS u ON u.id = uw.user_id
        WHERE uw.establishment IN ({placeholders})
        ORDER BY uw.establishment COLLATE NOCASE, u.email COLLATE NOCASE
        """,
        cleaned,
    ).fetchall()

    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["establishment"], []).append(
            {
                "user_id": int(row["user_id"]),
                "full_name": row["full_name"],
                "email": row["email"],
            }
        )
    return out


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
    inserted = 0
    cur = conn.cursor()

    for parsed in (_record_to_violation(record) for record in _parse_csv_records(csv_path)):
        cur.execute(
            """
            INSERT INTO violations (
              source_hash, date_iso, establishment, owner, street, category, description, amount, status, city, searchable, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_hash) DO NOTHING
            """,
            (
                parsed["source_hash"],
                parsed["date_iso"],
                parsed["establishment"],
                parsed["owner"],
                parsed["street"],
                parsed["category"],
                parsed["description"],
                parsed["amount"],
                parsed["status"],
                parsed["city"],
                parsed["searchable"],
                parsed["raw_json"],
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


def detect_new_violations(conn: sqlite3.Connection, csv_path: Path) -> list[dict]:
    ensure_schema(conn)
    existing_hashes = {
        row["source_hash"]
        for row in conn.execute(
            "SELECT source_hash FROM violations WHERE source_hash IS NOT NULL AND TRIM(source_hash) <> ''"
        ).fetchall()
    }

    new_items = []
    for parsed in (_record_to_violation(record) for record in _parse_csv_records(csv_path)):
        if parsed["source_hash"] in existing_hashes:
            continue
        new_items.append(parsed)
    return new_items


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
