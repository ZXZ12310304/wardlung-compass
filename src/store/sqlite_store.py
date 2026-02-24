from __future__ import annotations

import os
import sqlite3
from typing import List, Optional

from src.store.schemas import (
    Assessment,
    CareCard,
    ChatSummary,
    DailyLog,
    HandoverRecord,
    Handover,
    RiskSnapshot,
    NurseAdmin,
    Patient,
    PatientCard,
    StaffAccount,
)


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        if self.db_path not in (":memory:", ""):
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS patients (
                        patient_id TEXT PRIMARY KEY,
                        ward_id TEXT,
                        bed_id TEXT,
                        sex TEXT,
                        age INTEGER,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS daily_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        patient_id TEXT NOT NULL,
                        date TEXT NOT NULL,
                        diet TEXT,
                        water_ml INTEGER,
                        sleep_hours REAL,
                        symptoms_json TEXT,
                        patient_reported_meds_json TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS nurse_admin (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        patient_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        vitals_json TEXT,
                        administered_meds_json TEXT,
                        notes TEXT,
                        nurse_id TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS assessments (
                        assessment_id TEXT PRIMARY KEY,
                        patient_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        route_tag TEXT,
                        primary_basis TEXT,
                        diagnosis_json TEXT NOT NULL,
                        audit_json TEXT NOT NULL,
                        reverse_json TEXT NOT NULL,
                        rag_evidence_json TEXT,
                        tool_trace_json TEXT,
                        gaps_json TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_summaries (
                        patient_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        role TEXT,
                        summary_text TEXT NOT NULL,
                        topic_tag TEXT,
                        key_flags_json TEXT,
                        PRIMARY KEY (patient_id, timestamp)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS patient_cards (
                        card_id TEXT PRIMARY KEY,
                        patient_id TEXT NOT NULL,
                        ward_id TEXT,
                        status TEXT NOT NULL,
                        content_md TEXT NOT NULL,
                        source_assessment_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT,
                        author_role TEXT,
                        card_type TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS handovers (
                        handover_id TEXT PRIMARY KEY,
                        patient_id TEXT NOT NULL,
                        ward_id TEXT,
                        created_at TEXT,
                        timestamp TEXT,
                        created_by_role TEXT,
                        sbar_md TEXT NOT NULL,
                        key_points_json TEXT,
                        related_snapshot_id TEXT,
                        version INTEGER
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS risk_snapshots (
                        snapshot_id TEXT PRIMARY KEY,
                        patient_id TEXT NOT NULL,
                        ward_id TEXT,
                        computed_at TEXT NOT NULL,
                        risk_level TEXT NOT NULL,
                        risk_score INTEGER NOT NULL,
                        flags_json TEXT NOT NULL,
                        next_actions_json TEXT NOT NULL,
                        rules_version TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS care_cards (
                        card_id TEXT PRIMARY KEY,
                        patient_id TEXT NOT NULL,
                        ward_id TEXT,
                        created_at TEXT NOT NULL,
                        created_by_role TEXT NOT NULL,
                        status TEXT NOT NULL,
                        card_level TEXT NOT NULL,
                        card_type TEXT,
                        language TEXT NOT NULL,
                        title TEXT NOT NULL,
                        one_liner TEXT,
                        bullets_json TEXT NOT NULL,
                        red_flags_json TEXT NOT NULL,
                        followup_json TEXT,
                        text_md TEXT,
                        audio_path TEXT,
                        source_assessment_id TEXT,
                        version INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS staff_accounts (
                        staff_id TEXT PRIMARY KEY,
                        role TEXT NOT NULL,
                        ward_id TEXT,
                        name TEXT,
                        email TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS account_credentials (
                        account_key TEXT PRIMARY KEY,
                        role TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_patients_ward ON patients(ward_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_daily_logs_patient_date ON daily_logs(patient_id, date)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_nurse_admin_patient_ts ON nurse_admin(patient_id, timestamp)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_assessments_patient_ts ON assessments(patient_id, timestamp)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_summaries_patient_ts ON chat_summaries(patient_id, timestamp)"
                )
                try:
                    conn.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_summaries_pk ON chat_summaries(patient_id, timestamp)"
                    )
                except sqlite3.Error:
                    pass
                try:
                    cols = [row[1] for row in conn.execute("PRAGMA table_info(staff_accounts)").fetchall()]
                    if "email" not in cols:
                        conn.execute("ALTER TABLE staff_accounts ADD COLUMN email TEXT")
                except sqlite3.Error:
                    pass
                try:
                    cols = [row[1] for row in conn.execute("PRAGMA table_info(chat_summaries)").fetchall()]
                    if "role" not in cols:
                        conn.execute("ALTER TABLE chat_summaries ADD COLUMN role TEXT")
                    if "topic_tag" not in cols:
                        conn.execute("ALTER TABLE chat_summaries ADD COLUMN topic_tag TEXT")
                except sqlite3.Error:
                    pass
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_patient_cards_patient_status ON patient_cards(patient_id, status, created_at)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_handovers_patient_ts ON handovers(patient_id, timestamp)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_handovers_patient_created ON handovers(patient_id, created_at)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_risk_snapshots_patient_ts ON risk_snapshots(patient_id, computed_at)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_care_cards_patient_level_status ON care_cards(patient_id, card_level, status, created_at)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_care_cards_patient_version ON care_cards(patient_id, card_level, version)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_care_cards_patient_type ON care_cards(patient_id, card_type, created_at)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_staff_accounts_ward ON staff_accounts(ward_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_account_credentials_role ON account_credentials(role)"
                )
                try:
                    cols = [row[1] for row in conn.execute("PRAGMA table_info(patient_cards)").fetchall()]
                    if "card_type" not in cols:
                        conn.execute("ALTER TABLE patient_cards ADD COLUMN card_type TEXT")
                except sqlite3.Error:
                    pass
                try:
                    cols = [row[1] for row in conn.execute("PRAGMA table_info(care_cards)").fetchall()]
                    if "card_type" not in cols:
                        conn.execute("ALTER TABLE care_cards ADD COLUMN card_type TEXT")
                    if "text_md" not in cols:
                        conn.execute("ALTER TABLE care_cards ADD COLUMN text_md TEXT")
                    if "audio_path" not in cols:
                        conn.execute("ALTER TABLE care_cards ADD COLUMN audio_path TEXT")
                except sqlite3.Error:
                    pass
                try:
                    cols = [row[1] for row in conn.execute("PRAGMA table_info(handovers)").fetchall()]
                    if "created_at" not in cols:
                        conn.execute("ALTER TABLE handovers ADD COLUMN created_at TEXT")
                    if "related_snapshot_id" not in cols:
                        conn.execute("ALTER TABLE handovers ADD COLUMN related_snapshot_id TEXT")
                    if "version" not in cols:
                        conn.execute("ALTER TABLE handovers ADD COLUMN version INTEGER")
                except sqlite3.Error:
                    pass
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in init_db: {exc}") from exc

    def upsert_patient(self, patient: Patient) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO patients (patient_id, ward_id, bed_id, sex, age, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(patient_id) DO UPDATE SET
                        ward_id=excluded.ward_id,
                        bed_id=excluded.bed_id,
                        sex=excluded.sex,
                        age=excluded.age,
                        created_at=excluded.created_at
                    """,
                    (
                        patient.patient_id,
                        patient.ward_id,
                        patient.bed_id,
                        patient.sex,
                        patient.age,
                        patient.created_at,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in upsert_patient: {exc}") from exc

    def get_patient(self, patient_id: str) -> Optional[Patient]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM patients WHERE patient_id = ?",
                    (patient_id,),
                ).fetchone()
            return Patient.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_patient: {exc}") from exc

    def list_patients_by_ward(self, ward_id: str) -> List[Patient]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM patients WHERE ward_id = ? ORDER BY patient_id",
                    (ward_id,),
                ).fetchall()
            return [Patient.from_row(r) for r in rows]
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in list_patients_by_ward: {exc}") from exc

    def add_daily_log(self, log: DailyLog) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO daily_logs
                    (patient_id, date, diet, water_ml, sleep_hours, symptoms_json, patient_reported_meds_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        log.patient_id,
                        log.date,
                        log.diet,
                        log.water_ml,
                        log.sleep_hours,
                        log.symptoms_json,
                        log.patient_reported_meds_json,
                        log.created_at,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in add_daily_log: {exc}") from exc

    def list_daily_logs(self, patient_id: str, limit: int = 10) -> List[DailyLog]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM daily_logs
                    WHERE patient_id = ?
                    ORDER BY date DESC
                    LIMIT ?
                    """,
                    (patient_id, int(limit)),
                ).fetchall()
            return [DailyLog.from_row(r) for r in rows]
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in list_daily_logs: {exc}") from exc

    def get_latest_daily_log(self, patient_id: str) -> Optional[DailyLog]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM daily_logs
                    WHERE patient_id = ?
                    ORDER BY date DESC, created_at DESC
                    LIMIT 1
                    """,
                    (patient_id,),
                ).fetchone()
            return DailyLog.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_latest_daily_log: {exc}") from exc

    def add_nurse_admin(self, rec: NurseAdmin) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO nurse_admin
                    (patient_id, timestamp, vitals_json, administered_meds_json, notes, nurse_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rec.patient_id,
                        rec.timestamp,
                        rec.vitals_json,
                        rec.administered_meds_json,
                        rec.notes,
                        rec.nurse_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in add_nurse_admin: {exc}") from exc

    def get_latest_nurse_admin(self, patient_id: str) -> Optional[NurseAdmin]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM nurse_admin
                    WHERE patient_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """,
                    (patient_id,),
                ).fetchone()
            return NurseAdmin.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_latest_nurse_admin: {exc}") from exc

    def list_nurse_admin(self, patient_id: str, limit: int = 10) -> List[NurseAdmin]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM nurse_admin
                    WHERE patient_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (patient_id, int(limit)),
                ).fetchall()
            return [NurseAdmin.from_row(r) for r in rows]
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in list_nurse_admin: {exc}") from exc

    def add_assessment(self, a: Assessment) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO assessments
                    (assessment_id, patient_id, timestamp, route_tag, primary_basis, diagnosis_json, audit_json, reverse_json, rag_evidence_json, tool_trace_json, gaps_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        a.assessment_id,
                        a.patient_id,
                        a.timestamp,
                        a.route_tag,
                        a.primary_basis,
                        a.diagnosis_json,
                        a.audit_json,
                        a.reverse_json,
                        a.rag_evidence_json,
                        a.tool_trace_json,
                        a.gaps_json,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in add_assessment: {exc}") from exc

    def get_latest_assessment(self, patient_id: str) -> Optional[Assessment]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM assessments
                    WHERE patient_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """,
                    (patient_id,),
                ).fetchone()
            return Assessment.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_latest_assessment: {exc}") from exc

    def add_chat_summary(self, s: ChatSummary) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO chat_summaries
                    (patient_id, timestamp, role, summary_text, topic_tag, key_flags_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (s.patient_id, s.timestamp, s.role, s.summary_text, s.topic_tag, s.key_flags_json),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in add_chat_summary: {exc}") from exc

    def get_latest_chat_summary(self, patient_id: str) -> Optional[ChatSummary]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM chat_summaries
                    WHERE patient_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """,
                    (patient_id,),
                ).fetchone()
            return ChatSummary.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_latest_chat_summary: {exc}") from exc

    def list_chat_summaries(self, patient_id: str, limit: int = 5) -> List[ChatSummary]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM chat_summaries
                    WHERE patient_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (patient_id, int(limit)),
                ).fetchall()
            return [ChatSummary.from_row(r) for r in rows]
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in list_chat_summaries: {exc}") from exc

    def add_patient_card(self, card: PatientCard) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO patient_cards
                    (card_id, patient_id, ward_id, status, content_md, source_assessment_id, created_at, updated_at, author_role, card_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        card.card_id,
                        card.patient_id,
                        card.ward_id,
                        card.status,
                        card.content_md,
                        card.source_assessment_id,
                        card.created_at,
                        card.updated_at,
                        card.author_role,
                        card.card_type,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in add_patient_card: {exc}") from exc

    def get_latest_patient_card(
        self, patient_id: str, status: Optional[str] = None, card_type: Optional[str] = None
    ) -> Optional[PatientCard]:
        try:
            with self._connect() as conn:
                if status and card_type:
                    row = conn.execute(
                        """
                        SELECT * FROM patient_cards
                        WHERE patient_id = ? AND status = ? AND card_type = ?
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (patient_id, status, card_type),
                    ).fetchone()
                elif status:
                    row = conn.execute(
                        """
                        SELECT * FROM patient_cards
                        WHERE patient_id = ? AND status = ?
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (patient_id, status),
                    ).fetchone()
                elif card_type:
                    row = conn.execute(
                        """
                        SELECT * FROM patient_cards
                        WHERE patient_id = ? AND card_type = ?
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (patient_id, card_type),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT * FROM patient_cards
                        WHERE patient_id = ?
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (patient_id,),
                    ).fetchone()
            return PatientCard.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_latest_patient_card: {exc}") from exc

    def add_care_card(self, card: CareCard) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO care_cards (
                        card_id, patient_id, ward_id, created_at, created_by_role,
                        status, card_level, card_type, language, title, one_liner,
                        bullets_json, red_flags_json, followup_json, text_md, audio_path,
                        source_assessment_id, version
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        card.card_id,
                        card.patient_id,
                        card.ward_id,
                        card.created_at,
                        card.created_by_role,
                        card.status,
                        card.card_level,
                        card.card_type,
                        card.language,
                        card.title,
                        card.one_liner,
                        card.bullets_json,
                        card.red_flags_json,
                        card.followup_json,
                        card.text_md,
                        card.audio_path,
                        card.source_assessment_id,
                        card.version,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in add_care_card: {exc}") from exc

    def get_care_card(self, card_id: str) -> Optional[CareCard]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM care_cards WHERE card_id = ?",
                    (card_id,),
                ).fetchone()
            return CareCard.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_care_card: {exc}") from exc

    def get_latest_care_card(
        self, patient_id: str, card_level: str, status_filter: Optional[object] = None, card_type: Optional[str] = None
    ) -> Optional[CareCard]:
        try:
            with self._connect() as conn:
                if status_filter is None:
                    query = """
                        SELECT * FROM care_cards
                        WHERE patient_id = ? AND card_level = ?
                    """
                    params = [patient_id, card_level]
                    if card_type:
                        query += " AND card_type = ?"
                        params.append(card_type)
                    query += " ORDER BY created_at DESC LIMIT 1"
                    row = conn.execute(
                        query,
                        params,
                    ).fetchone()
                else:
                    if isinstance(status_filter, (list, tuple, set)):
                        placeholders = ",".join(["?"] * len(status_filter))
                        query = f"""
                            SELECT * FROM care_cards
                            WHERE patient_id = ? AND card_level = ?
                              AND status IN ({placeholders})
                        """
                        params = [patient_id, card_level, *status_filter]
                        if card_type:
                            query += " AND card_type = ?"
                            params.append(card_type)
                        query += " ORDER BY created_at DESC LIMIT 1"
                        row = conn.execute(query, params).fetchone()
                    else:
                        query = """
                            SELECT * FROM care_cards
                            WHERE patient_id = ? AND card_level = ? AND status = ?
                        """
                        params = [patient_id, card_level, status_filter]
                        if card_type:
                            query += " AND card_type = ?"
                            params.append(card_type)
                        query += " ORDER BY created_at DESC LIMIT 1"
                        row = conn.execute(query, params).fetchone()
            return CareCard.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_latest_care_card: {exc}") from exc

    def list_care_cards(
        self,
        patient_id: str,
        limit: int = 10,
        card_type: Optional[str] = None,
        status_filter: Optional[object] = None,
    ) -> List[CareCard]:
        try:
            with self._connect() as conn:
                query = "SELECT * FROM care_cards WHERE patient_id = ?"
                params: list = [patient_id]
                if card_type:
                    query += " AND card_type = ?"
                    params.append(card_type)
                if status_filter is not None:
                    if isinstance(status_filter, (list, tuple, set)):
                        placeholders = ",".join(["?"] * len(status_filter))
                        query += f" AND status IN ({placeholders})"
                        params.extend(list(status_filter))
                    else:
                        query += " AND status = ?"
                        params.append(status_filter)
                query += " ORDER BY created_at DESC LIMIT ?"
                params.append(int(limit))
                rows = conn.execute(query, params).fetchall()
            return [CareCard.from_row(r) for r in rows]
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in list_care_cards: {exc}") from exc

    def update_care_card_status(self, card_id: str, status: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE care_cards SET status = ? WHERE card_id = ?",
                    (status, card_id),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in update_care_card_status: {exc}") from exc

    def update_care_card_content(
        self,
        card_id: str,
        title: str,
        one_liner: str,
        bullets_json: str,
        red_flags_json: str,
        followup_json: Optional[str],
        text_md: Optional[str] = None,
    ) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE care_cards
                    SET title = ?, one_liner = ?, bullets_json = ?, red_flags_json = ?, followup_json = ?, text_md = ?
                    WHERE card_id = ?
                    """,
                    (title, one_liner, bullets_json, red_flags_json, followup_json, text_md, card_id),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in update_care_card_content: {exc}") from exc

    def update_care_card_audio(self, card_id: str, audio_path: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE care_cards SET audio_path = ? WHERE card_id = ?",
                    (audio_path, card_id),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in update_care_card_audio: {exc}") from exc

    def get_latest_care_card_version(self, patient_id: str, card_level: str) -> int:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT version FROM care_cards
                    WHERE patient_id = ? AND card_level = ?
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                    (patient_id, card_level),
                ).fetchone()
            if not row:
                return 0
            return int(row["version"] or 0)
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_latest_care_card_version: {exc}") from exc

    def add_handover(self, h: HandoverRecord) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO handovers
                    (handover_id, patient_id, ward_id, created_at, timestamp, created_by_role, sbar_md, key_points_json, related_snapshot_id, version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        h.handover_id,
                        h.patient_id,
                        h.ward_id,
                        h.created_at,
                        h.created_at,
                        h.created_by_role,
                        h.sbar_md,
                        h.key_points_json,
                        h.related_snapshot_id,
                        h.version,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in add_handover: {exc}") from exc

    def get_latest_handover(self, patient_id: str) -> Optional[HandoverRecord]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM handovers
                    WHERE patient_id = ?
                    ORDER BY COALESCE(created_at, timestamp) DESC
                    LIMIT 1
                    """,
                    (patient_id,),
                ).fetchone()
            return HandoverRecord.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_latest_handover: {exc}") from exc

    def list_handovers(self, patient_id: str, limit: int = 10) -> List[HandoverRecord]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM handovers
                    WHERE patient_id = ?
                    ORDER BY COALESCE(created_at, timestamp) DESC
                    LIMIT ?
                    """,
                    (patient_id, int(limit)),
                ).fetchall()
            return [HandoverRecord.from_row(r) for r in rows]
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in list_handovers: {exc}") from exc

    def get_latest_handover_version(self, patient_id: str) -> int:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT version FROM handovers
                    WHERE patient_id = ?
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                    (patient_id,),
                ).fetchone()
            if not row:
                return 0
            return int(row["version"] or 0)
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_latest_handover_version: {exc}") from exc

    def add_risk_snapshot(self, snapshot: RiskSnapshot) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO risk_snapshots
                    (snapshot_id, patient_id, ward_id, computed_at, risk_level, risk_score, flags_json, next_actions_json, rules_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.patient_id,
                        snapshot.ward_id,
                        snapshot.computed_at,
                        snapshot.risk_level,
                        snapshot.risk_score,
                        snapshot.flags_json,
                        snapshot.next_actions_json,
                        snapshot.rules_version,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in add_risk_snapshot: {exc}") from exc

    def get_latest_risk_snapshot(self, patient_id: str) -> Optional[RiskSnapshot]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM risk_snapshots
                    WHERE patient_id = ?
                    ORDER BY computed_at DESC
                    LIMIT 1
                    """,
                    (patient_id,),
                ).fetchone()
            return RiskSnapshot.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_latest_risk_snapshot: {exc}") from exc

    def list_risk_snapshots(self, patient_id: str, limit: int = 10) -> List[RiskSnapshot]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM risk_snapshots
                    WHERE patient_id = ?
                    ORDER BY computed_at DESC
                    LIMIT ?
                    """,
                    (patient_id, int(limit)),
                ).fetchall()
            return [RiskSnapshot.from_row(r) for r in rows]
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in list_risk_snapshots: {exc}") from exc

    def upsert_staff_account(self, staff: StaffAccount) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO staff_accounts (staff_id, role, ward_id, name, email, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(staff_id) DO UPDATE SET
                        role=excluded.role,
                        ward_id=excluded.ward_id,
                        name=excluded.name,
                        email=excluded.email,
                        created_at=excluded.created_at
                    """,
                    (
                        staff.staff_id,
                        staff.role,
                        staff.ward_id,
                        staff.name,
                        staff.email,
                        staff.created_at,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in upsert_staff_account: {exc}") from exc

    def get_staff_account(self, staff_id: str) -> Optional[StaffAccount]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM staff_accounts WHERE staff_id = ?",
                    (staff_id,),
                ).fetchone()
            return StaffAccount.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_staff_account: {exc}") from exc

    def get_staff_by_staff_id(self, staff_id: str) -> Optional[StaffAccount]:
        return self.get_staff_account(staff_id)

    def get_staff_by_email(self, email: str) -> Optional[StaffAccount]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM staff_accounts WHERE email = ?",
                    (email,),
                ).fetchone()
            return StaffAccount.from_row(row) if row else None
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in get_staff_by_email: {exc}") from exc

    def list_staff_by_ward(self, ward_id: str) -> List[StaffAccount]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM staff_accounts WHERE ward_id = ? ORDER BY staff_id",
                    (ward_id,),
                ).fetchall()
            return [StaffAccount.from_row(r) for r in rows]
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite error in list_staff_by_ward: {exc}") from exc
