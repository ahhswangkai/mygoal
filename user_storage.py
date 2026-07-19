"""用户账号与计算器投注记录的 SQLite 持久化。"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone

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
                    bet['stake'], bet['total_odds'], bet['max_bonus'],
                    bet['description'], bet['created_at'],
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
