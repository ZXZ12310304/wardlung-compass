from __future__ import annotations

import os
from datetime import datetime

from src.store.schemas import Patient, StaffAccount
from src.store.sqlite_store import SQLiteStore


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def main() -> None:
    db_path = os.path.join("data", "ward_demo.db")
    os.makedirs("data", exist_ok=True)
    store = SQLiteStore(db_path)
    store.init_db()

    patients = [
        Patient(
            patient_id="demo_patient_001",
            ward_id="ward_a",
            bed_id="bed_01",
            sex="Male",
            age=65,
            created_at=_now_iso(),
        ),
        Patient(
            patient_id="P20260210-0001",
            ward_id="ward_a",
            bed_id="A-01",
            sex="Female",
            age=72,
            created_at=_now_iso(),
        ),
        Patient(
            patient_id="P20260210-0002",
            ward_id="ward_a",
            bed_id="A-02",
            sex="Male",
            age=58,
            created_at=_now_iso(),
        ),
        Patient(
            patient_id="P20260210-0003",
            ward_id="ward_b",
            bed_id="B-03",
            sex="Female",
            age=66,
            created_at=_now_iso(),
        ),
        Patient(
            patient_id="P20260210-0004",
            ward_id="ward_b",
            bed_id="B-04",
            sex="Male",
            age=49,
            created_at=_now_iso(),
        ),
    ]

    for p in patients:
        store.upsert_patient(p)

    staff_accounts = [
        StaffAccount(
            staff_id="N-07321",
            role="nurse",
            ward_id="ward_a",
            name="Nurse A",
            email="nurse_a@wardlung.org",
            created_at=_now_iso(),
        ),
        StaffAccount(
            staff_id="N-05288",
            role="nurse",
            ward_id="ward_b",
            name="Nurse B",
            email="nurse_b@wardlung.org",
            created_at=_now_iso(),
        ),
        StaffAccount(
            staff_id="D-01987",
            role="doctor",
            ward_id="ward_a",
            name="Doctor A",
            email="doctor_a@wardlung.org",
            created_at=_now_iso(),
        ),
        StaffAccount(
            staff_id="D-04512",
            role="doctor",
            ward_id="ward_b",
            name="Doctor B",
            email="doctor_b@wardlung.org",
            created_at=_now_iso(),
        ),
    ]
    for s in staff_accounts:
        store.upsert_staff_account(s)

    print("Seeded patients:")
    for p in patients:
        print(" -", p.patient_id, "ward:", p.ward_id, "bed:", p.bed_id)

    print("\nSeeded staff accounts:")
    for s in staff_accounts:
        print(" -", s.staff_id, "role:", s.role, "ward:", s.ward_id)


if __name__ == "__main__":
    main()
