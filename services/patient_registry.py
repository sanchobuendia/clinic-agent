from __future__ import annotations

import os
from dataclasses import dataclass

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from utils.logger import get_logger

logger = get_logger("PatientRegistryService")


class PatientRegistryError(RuntimeError):
    pass


@dataclass
class PatientRecord:
    id: int
    cpf: str
    full_name: str
    age: int
    sex: str
    email: str
    phone: str


def _registry_db_path() -> str:
    db_path = os.getenv("DB_PATH_CADASTROS", "").strip()
    if not db_path:
        raise PatientRegistryError("DB_PATH_CADASTROS nao configurado.")
    return db_path


async def setup_patient_registry() -> None:
    db_path = _registry_db_path()
    async with await AsyncConnection.connect(db_path) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS patients (
                    id BIGSERIAL PRIMARY KEY,
                    cpf VARCHAR(11) NOT NULL UNIQUE,
                    full_name TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    sex VARCHAR(20) NOT NULL,
                    email TEXT NOT NULL,
                    phone VARCHAR(20) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await cur.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS age INTEGER")
            await cur.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS sex VARCHAR(20)")
            await cur.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS email TEXT")
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_patients_cpf ON patients (cpf)
                """
            )
        await conn.commit()
    logger.info("Estrutura de cadastro de pacientes pronta.")


async def get_patient_by_cpf(cpf: str) -> PatientRecord | None:
    db_path = _registry_db_path()
    async with await AsyncConnection.connect(db_path, row_factory=dict_row) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, cpf, full_name, age, sex, email, phone
                FROM patients
                WHERE cpf = %s
                """,
                (cpf,),
            )
            row = await cur.fetchone()

    if not row:
        return None
    return PatientRecord(
        id=row["id"],
        cpf=row["cpf"],
        full_name=row["full_name"],
        age=row["age"],
        sex=row["sex"],
        email=row["email"],
        phone=row["phone"],
    )


async def create_patient(
    cpf: str,
    full_name: str,
    age: int,
    sex: str,
    email: str,
    phone: str,
) -> PatientRecord:
    db_path = _registry_db_path()
    async with await AsyncConnection.connect(db_path, row_factory=dict_row) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO patients (cpf, full_name, age, sex, email, phone)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (cpf) DO UPDATE
                SET full_name = EXCLUDED.full_name,
                    age = EXCLUDED.age,
                    sex = EXCLUDED.sex,
                    email = EXCLUDED.email,
                    phone = EXCLUDED.phone,
                    updated_at = NOW()
                RETURNING id, cpf, full_name, age, sex, email, phone
                """,
                (cpf, full_name, age, sex, email, phone),
            )
            row = await cur.fetchone()
        await conn.commit()

    return PatientRecord(
        id=row["id"],
        cpf=row["cpf"],
        full_name=row["full_name"],
        age=row["age"],
        sex=row["sex"],
        email=row["email"],
        phone=row["phone"],
    )


async def list_patients(limit: int = 50) -> list[PatientRecord]:
    db_path = _registry_db_path()
    async with await AsyncConnection.connect(db_path, row_factory=dict_row) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, cpf, full_name, age, sex, email, phone
                FROM patients
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cur.fetchall()

    return [
        PatientRecord(
            id=row["id"],
            cpf=row["cpf"],
            full_name=row["full_name"],
            age=row["age"],
            sex=row["sex"],
            email=row["email"],
            phone=row["phone"],
        )
        for row in rows
    ]
