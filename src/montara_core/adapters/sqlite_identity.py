"""Atomic opaque account provisioning for a verified external subject."""
from uuid import uuid4

from montara_core.adapters.sqlite_db import SQLiteDatabase


class SQLiteIdentityStore(SQLiteDatabase):
    def resolve_identity(self, issuer: str, subject: str) -> str:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT account_id FROM oidc_identities WHERE issuer=? AND subject=?",
                             (issuer, subject)).fetchone()
            if row:
                return row[0]
            account = str(uuid4())
            db.execute("INSERT INTO accounts(id) VALUES (?)", (account,))
            db.execute("INSERT INTO oidc_identities VALUES (?,?,?)", (issuer, subject, account))
            return account
