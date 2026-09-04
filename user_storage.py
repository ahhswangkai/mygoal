"""用户账号与计算器投注记录的 SQLite 持久化。"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone

from calculator_math import calculate_max_bonus

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8
    from backports.zoneinfo import ZoneInfo


PASSWORD_ITERATIONS = 260000
LOCAL_TIMEZONE = ZoneInfo('Asia/Shanghai')


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def local_date(iso_value):
    try:
        value = datetime.fromisoformat(iso_value.replace('Z', '+00:00'))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(LOCAL_TIMEZONE).date().isoformat()
    except (TypeError, ValueError):
        return str(iso_value)[:10]


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), bytes.fromhex(salt), PASSWORD_ITERATIONS
    ).hex()
    return 'pbkdf2_sha256${}${}${}'.format(PASSWORD_ITERATIONS, salt, digest)


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split('$', 3)
        if algorithm != 'pbkdf2_sha256':
            return False
        digest = hashlib.pbkdf2_hmac(
            'sha256', password.encode('utf-8'), bytes.fromhex(salt), int(iterations)
        ).hex()
        return hmac.compare_digest(digest, expected)
    except (TypeError, ValueError):
        return False


class UserStorage:
    def __init__(self, database_path):
        self.database_path = database_path
        parent = os.path.dirname(database_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('PRAGMA journal_mode = WAL')
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS calculator_bets (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    multiplier INTEGER NOT NULL,
                    pass_counts_json TEXT NOT NULL,
                    selected_items_json TEXT NOT NULL,
                    match_count INTEGER NOT NULL,
                    option_count INTEGER NOT NULL,
                    notes INTEGER NOT NULL,
                    stake REAL NOT NULL,
                    total_odds REAL NOT NULL,
                    max_bonus REAL NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_calculator_bets_user_created
                    ON calculator_bets(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_calculator_bets_user_status
                    ON calculator_bets(user_id, status);

                CREATE TABLE IF NOT EXISTS calculator_drafts (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    match_date TEXT NOT NULL,
                    selected_items_json TEXT NOT NULL,
                    pass_counts_json TEXT NOT NULL,
                    multiplier INTEGER NOT NULL DEFAULT 1,
                    match_count INTEGER NOT NULL,
                    option_count INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE(user_id, match_date, content_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_calculator_drafts_user_date
                    ON calculator_drafts(user_id, match_date, updated_at DESC);
                """
            )
            columns = {
                row['name']
                for row in conn.execute('PRAGMA table_info(calculator_bets)').fetchall()
            }
            migrations = {
                'actual_return': "ALTER TABLE calculator_bets ADD COLUMN actual_return REAL NOT NULL DEFAULT 0",
                'profit': "ALTER TABLE calculator_bets ADD COLUMN profit REAL NOT NULL DEFAULT 0",
                'winning_notes': "ALTER TABLE calculator_bets ADD COLUMN winning_notes INTEGER NOT NULL DEFAULT 0",
                'settled_at': "ALTER TABLE calculator_bets ADD COLUMN settled_at TEXT",
                'settlement_json': (
                    "ALTER TABLE calculator_bets ADD COLUMN settlement_json "
                    "TEXT NOT NULL DEFAULT '{}'"
                ),
            }
            for column, statement in migrations.items():
                if column not in columns:
                    conn.execute(statement)
            self._repair_bet_created_dates(conn)
            self._repair_max_bonuses(conn)

    @staticmethod
    def _selected_match_date(selected_items):
        """Return the dominant match date used to file a betting record.

        A ticket can contain matches crossing midnight, and old OCR data can
        occasionally contain one bad year.  Using the most common valid match
        date keeps the record attached to its football programme date without
        letting one malformed leg move the whole ticket.
        """
        counts = {}
        order = []
        for item in selected_items or []:
            if not isinstance(item, dict):
                continue
            # OCR imports that could not be matched safely use the upload day
            # for display only.  They must not be treated as verified fixture
            # dates by this migration/filing helper.
            if item.get('match_resolved') is False or item.get('date_source') == 'upload':
                continue
            match = item.get('match') if isinstance(item.get('match'), dict) else {}
            value = str(item.get('date') or match.get('date') or '')[:10]
            try:
                parsed = datetime.strptime(value, '%Y-%m-%d').date()
            except (TypeError, ValueError):
                continue
            normalized = parsed.isoformat()
            if normalized not in counts:
                counts[normalized] = 0
                order.append(normalized)
            counts[normalized] += 1
        if not counts:
            return None
        return max(order, key=lambda value: counts[value])

    @classmethod
    def _created_at_for_match_date(cls, created_at, selected_items):
        match_date = cls._selected_match_date(selected_items)
        if not match_date:
            return created_at
        try:
            value = datetime.fromisoformat(str(created_at).replace('Z', '+00:00'))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            value = datetime.now(timezone.utc).replace(microsecond=0)
        local_value = value.astimezone(LOCAL_TIMEZONE)
        target = datetime.strptime(match_date, '%Y-%m-%d')
        corrected_local = local_value.replace(
            year=target.year,
            month=target.month,
            day=target.day,
            microsecond=0,
        )
        return (
            corrected_local.astimezone(timezone.utc)
            .isoformat()
            .replace('+00:00', 'Z')
        )

    @classmethod
    def _repair_bet_created_dates(cls, conn):
        """Backfill legacy records that used the data-entry date."""
        rows = conn.execute(
            'SELECT id, selected_items_json, created_at FROM calculator_bets'
        ).fetchall()
        for row in rows:
            try:
                selected_items = json.loads(row['selected_items_json'])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            corrected = cls._created_at_for_match_date(
                row['created_at'], selected_items
            )
            if corrected and corrected != row['created_at']:
                conn.execute(
                    'UPDATE calculator_bets SET created_at = ? WHERE id = ?',
                    (corrected, row['id']),
                )

    @staticmethod
    def _repair_max_bonuses(conn):
        """修正旧版本按“总赔率×总注数”写入的错误理论奖金。"""
        rows = conn.execute(
            """
            SELECT id, multiplier, pass_counts_json, selected_items_json, max_bonus
            FROM calculator_bets
            """
        ).fetchall()
        for row in rows:
            try:
                max_bonus = calculate_max_bonus(
                    json.loads(row['selected_items_json']),
                    json.loads(row['pass_counts_json']),
                    row['multiplier'],
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if abs(max_bonus - float(row['max_bonus'])) > 0.004:
                conn.execute(
                    'UPDATE calculator_bets SET max_bonus = ? WHERE id = ?',
                    (max_bonus, row['id']),
                )

    @staticmethod
    def _public_user(row):
        if not row:
            return None
        return {
            'id': row['id'],
            'username': row['username'],
            'display_name': row['display_name'],
            'created_at': row['created_at'],
            'last_login_at': row['last_login_at'],
        }

    def create_user(self, username, display_name, password):
        now = utc_now()
        with self._connect() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO users (username, display_name, password_hash, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (username, display_name, hash_password(password), now),
                )
            except sqlite3.IntegrityError:
                return None
            row = conn.execute('SELECT * FROM users WHERE id = ?', (cursor.lastrowid,)).fetchone()
            return self._public_user(row)

    def authenticate(self, username, password):
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM users WHERE username = ? COLLATE NOCASE', (username,)
            ).fetchone()
            if not row or not verify_password(password, row['password_hash']):
                return None
            now = utc_now()
            conn.execute('UPDATE users SET last_login_at = ? WHERE id = ?', (now, row['id']))
            updated = conn.execute('SELECT * FROM users WHERE id = ?', (row['id'],)).fetchone()
            return self._public_user(updated)

    def get_user(self, user_id):
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
            return self._public_user(row)

    @staticmethod
    def _bet_from_row(row):
        if not row:
            return None
        keys = set(row.keys())
        settlement = {}
        if 'settlement_json' in keys:
            try:
                settlement = json.loads(row['settlement_json'] or '{}')
            except (TypeError, ValueError, json.JSONDecodeError):
                settlement = {}
        return {
            'id': row['id'],
            'status': row['status'],
            'multiplier': row['multiplier'],
            'pass_counts': json.loads(row['pass_counts_json']),
            'selected_items': json.loads(row['selected_items_json']),
            'match_count': row['match_count'],
            'option_count': row['option_count'],
            'notes': row['notes'],
            'stake': round(float(row['stake']), 2),
            'total_odds': round(float(row['total_odds']), 2),
            'max_bonus': round(float(row['max_bonus']), 2),
            'description': row['description'],
            'created_at': row['created_at'],
            'actual_return': round(float(row['actual_return']), 2)
            if 'actual_return' in keys else 0.0,
            'profit': round(float(row['profit']), 2) if 'profit' in keys else 0.0,
            'winning_notes': int(row['winning_notes'])
            if 'winning_notes' in keys else 0,
            'settled_at': row['settled_at'] if 'settled_at' in keys else None,
            'settlement': settlement,
        }

    def create_bet(self, user_id, bet):
        max_bonus = calculate_max_bonus(
            bet.get('selected_items'),
            bet.get('pass_counts'),
            bet.get('multiplier'),
        )
        created_at = self._created_at_for_match_date(
            bet.get('created_at'), bet.get('selected_items')
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO calculator_bets (
                    id, user_id, status, multiplier, pass_counts_json,
                    selected_items_json, match_count, option_count, notes,
                    stake, total_odds, max_bonus, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bet['id'], user_id, bet['status'], bet['multiplier'],
                    json.dumps(bet['pass_counts'], ensure_ascii=False),
                    json.dumps(bet['selected_items'], ensure_ascii=False),
                    bet['match_count'], bet['option_count'], bet['notes'],
                    bet['stake'], bet['total_odds'], max_bonus,
                    bet['description'], created_at,
                ),
            )
            row = conn.execute(
                'SELECT * FROM calculator_bets WHERE id = ? AND user_id = ?',
                (bet['id'], user_id),
            ).fetchone()
            return self._bet_from_row(row)

    def list_bets(self, user_id, limit=50, offset=0, status=None):
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    """
                    SELECT * FROM calculator_bets
                    WHERE user_id = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (user_id, status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM calculator_bets
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (user_id, limit, offset),
                ).fetchall()
            return [self._bet_from_row(row) for row in rows]

    def list_pending_bets(self, user_id=None):
        with self._connect() as conn:
            if user_id is None:
                rows = conn.execute(
                    """
                    SELECT * FROM calculator_bets
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM calculator_bets
                    WHERE user_id = ? AND status = 'pending'
                    ORDER BY created_at ASC
                    """,
                    (user_id,),
                ).fetchall()
            return [self._bet_from_row(row) for row in rows]

    def settle_bet(self, bet_id, settlement):
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE calculator_bets
                SET status = ?, actual_return = ?, profit = ?, winning_notes = ?,
                    settled_at = ?, settlement_json = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    settlement['status'],
                    settlement['actual_return'],
                    settlement['profit'],
                    settlement['winning_notes'],
                    settlement['settled_at'],
                    json.dumps(settlement.get('settlement') or {}, ensure_ascii=False),
                    bet_id,
                ),
            )
            return cursor.rowcount > 0

    def delete_bet(self, user_id, bet_id):
        with self._connect() as conn:
            cursor = conn.execute(
                'DELETE FROM calculator_bets WHERE id = ? AND user_id = ?',
                (bet_id, user_id),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _draft_from_row(row):
        if not row:
            return None
        return {
            'id': row['id'],
            'match_date': row['match_date'],
            'selected_items': json.loads(row['selected_items_json']),
            'pass_counts': json.loads(row['pass_counts_json']),
            'multiplier': int(row['multiplier']),
            'match_count': int(row['match_count']),
            'option_count': int(row['option_count']),
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    @staticmethod
    def _draft_content_hash(draft):
        items = sorted((
            {
                'match_id': str(item.get('match_id') or ''),
                'pool': str(item.get('pool') or ''),
                'opt': str(item.get('opt') or ''),
            }
            for item in (draft.get('selected_items') or [])
        ),
            key=lambda item: (
                str(item.get('match_id') or ''),
                str(item.get('pool') or ''),
                str(item.get('opt') or ''),
            ),
        )
        content = {
            'selected_items': items,
            'pass_counts': sorted(draft.get('pass_counts') or []),
            'multiplier': int(draft.get('multiplier') or 1),
        }
        encoded = json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        ).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    def save_draft(self, user_id, draft, match_date):
        """Save one current-day calculator plan, deduplicating identical plans."""
        now = utc_now()
        content_hash = self._draft_content_hash(draft)
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM calculator_drafts
                WHERE user_id = ? AND match_date = ? AND content_hash = ?
                """,
                (user_id, match_date, content_hash),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE calculator_drafts
                    SET selected_items_json = ?, pass_counts_json = ?,
                        multiplier = ?, match_count = ?, option_count = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(
                            draft.get('selected_items') or [], ensure_ascii=False
                        ),
                        json.dumps(draft.get('pass_counts') or [], ensure_ascii=False),
                        int(draft.get('multiplier') or 1),
                        int(draft.get('match_count') or 0),
                        int(draft.get('option_count') or 0),
                        now,
                        existing['id'],
                    ),
                )
                row = conn.execute(
                    'SELECT * FROM calculator_drafts WHERE id = ?',
                    (existing['id'],),
                ).fetchone()
                result = self._draft_from_row(row)
                result['deduplicated'] = True
                return result

            draft_id = str(draft.get('id') or secrets.token_hex(16))
            conn.execute(
                """
                INSERT INTO calculator_drafts (
                    id, user_id, match_date, selected_items_json,
                    pass_counts_json, multiplier, match_count, option_count,
                    content_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    user_id,
                    match_date,
                    json.dumps(draft.get('selected_items') or [], ensure_ascii=False),
                    json.dumps(draft.get('pass_counts') or [], ensure_ascii=False),
                    int(draft.get('multiplier') or 1),
                    int(draft.get('match_count') or 0),
                    int(draft.get('option_count') or 0),
                    content_hash,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                'SELECT * FROM calculator_drafts WHERE id = ?',
                (draft_id,),
            ).fetchone()
            result = self._draft_from_row(row)
            result['deduplicated'] = False
            return result

    def list_drafts(self, user_id, match_date=None):
        with self._connect() as conn:
            if match_date:
                rows = conn.execute(
                    """
                    SELECT * FROM calculator_drafts
                    WHERE user_id = ? AND match_date = ?
                    ORDER BY updated_at DESC
                    """,
                    (user_id, match_date),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM calculator_drafts
                    WHERE user_id = ?
                    ORDER BY updated_at DESC
                    """,
                    (user_id,),
                ).fetchall()
            return [self._draft_from_row(row) for row in rows]

    def delete_drafts(self, user_id, draft_ids):
        ids = sorted({str(value) for value in draft_ids if value})
        if not ids:
            return 0
        placeholders = ','.join('?' for _ in ids)
        with self._connect() as conn:
            cursor = conn.execute(
                'DELETE FROM calculator_drafts '
                'WHERE user_id = ? AND id IN ({})'.format(placeholders),
                [user_id] + ids,
            )
            return cursor.rowcount

    def delete_draft(self, user_id, draft_id):
        with self._connect() as conn:
            cursor = conn.execute(
                'DELETE FROM calculator_drafts WHERE id = ? AND user_id = ?',
                (draft_id, user_id),
            )
            return cursor.rowcount > 0

    def get_stats(self, user_id, month=None):
        where_sql = 'user_id = ?'
        params = [user_id]
        if month:
            where_sql += (
                " AND strftime('%Y-%m', datetime(created_at), '+8 hours') = ?"
            )
            params.append(month)

        with self._connect() as conn:
            summary = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total_bets,
                    COALESCE(SUM(stake), 0) AS total_stake,
                    COALESCE(SUM(max_bonus), 0) AS potential_bonus,
                    COALESCE(AVG(stake), 0) AS average_stake,
                    COALESCE(SUM(notes), 0) AS total_notes,
                    COALESCE(SUM(actual_return), 0) AS total_return,
                    COALESCE(SUM(profit), 0) AS net_profit,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_bets,
                    SUM(CASE WHEN status != 'pending' THEN 1 ELSE 0 END) AS settled_bets,
                    SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) AS won_bets,
                    SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) AS lost_bets,
                    SUM(CASE WHEN status = 'draw' THEN 1 ELSE 0 END) AS draw_bets
                FROM calculator_bets WHERE {where_sql}
                """,
                params,
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT created_at, status, stake, max_bonus, actual_return, profit,
                       pass_counts_json, selected_items_json
                FROM calculator_bets
                WHERE {where_sql}
                ORDER BY created_at ASC
                """,
                params,
            ).fetchall()
            month_rows = conn.execute(
                """
                SELECT DISTINCT
                    strftime('%Y-%m', datetime(created_at), '+8 hours') AS month
                FROM calculator_bets
                WHERE user_id = ?
                ORDER BY month DESC
                """,
                (user_id,),
            ).fetchall()

        daily_map = {}
        pass_distribution = {}
        pool_distribution = {}
        for row in rows:
            date = local_date(row['created_at'])
            daily = daily_map.setdefault(
                date, {
                    'date': date,
                    'bets': 0,
                    'settled_bets': 0,
                    'stake': 0.0,
                    'potential_bonus': 0.0,
                    'actual_return': 0.0,
                    'profit': 0.0,
                }
            )
            daily['bets'] += 1
            if row['status'] != 'pending':
                daily['settled_bets'] += 1
            daily['stake'] += float(row['stake'])
            daily['potential_bonus'] += float(row['max_bonus'])
            daily['actual_return'] += float(row['actual_return'])
            daily['profit'] += float(row['profit'])

            for count in json.loads(row['pass_counts_json']):
                label = '单关' if count == 1 else '{}关'.format(count)
                pass_distribution[label] = pass_distribution.get(label, 0) + 1
            for item in json.loads(row['selected_items_json']):
                pool = item.get('pool_name') or item.get('pool') or '其他'
                pool_distribution[pool] = pool_distribution.get(pool, 0) + 1

        daily = []
        for item in daily_map.values():
            item['stake'] = round(item['stake'], 2)
            item['potential_bonus'] = round(item['potential_bonus'], 2)
            item['actual_return'] = round(item['actual_return'], 2)
            item['profit'] = round(item['profit'], 2)
            daily.append(item)

        settled_bets = int(summary['settled_bets'] or 0)
        won_bets = int(summary['won_bets'] or 0)
        return {
            'total_bets': int(summary['total_bets']),
            'total_stake': round(float(summary['total_stake']), 2),
            'potential_bonus': round(float(summary['potential_bonus']), 2),
            'average_stake': round(float(summary['average_stake']), 2),
            'total_notes': int(summary['total_notes']),
            'total_return': round(float(summary['total_return']), 2),
            'net_profit': round(float(summary['net_profit']), 2),
            'pending_bets': int(summary['pending_bets'] or 0),
            'settled_bets': settled_bets,
            'won_bets': won_bets,
            'lost_bets': int(summary['lost_bets'] or 0),
            'draw_bets': int(summary['draw_bets'] or 0),
            'win_rate': round(won_bets / settled_bets * 100, 1) if settled_bets else 0.0,
            'selected_month': month,
            'available_months': [
                row['month'] for row in month_rows if row['month']
            ],
            'daily': daily if month else daily[-30:],
            'pass_distribution': [
                {'label': label, 'count': count}
                for label, count in sorted(pass_distribution.items())
            ],
            'pool_distribution': [
                {'label': label, 'count': count}
                for label, count in sorted(pool_distribution.items(), key=lambda item: -item[1])
            ],
        }
