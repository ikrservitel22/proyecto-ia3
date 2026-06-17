"""
Crea las tablas internas y carga datos de ejemplo.
Ejecutar una sola vez: python3 database/setup_tablas.py
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT NOT NULL,
    cedula      TEXT UNIQUE NOT NULL,
    email       TEXT,
    telefono    TEXT,
    plan        TEXT,
    estado      TEXT DEFAULT 'activo',
    fecha_registro TEXT
);

CREATE TABLE IF NOT EXISTS presupuesto (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    area        TEXT NOT NULL,
    concepto    TEXT NOT NULL,
    monto       REAL NOT NULL,
    ejecutado   REAL DEFAULT 0,
    periodo     TEXT,
    estado      TEXT DEFAULT 'vigente'
);

CREATE TABLE IF NOT EXISTS contratos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  INTEGER NOT NULL,
    tipo        TEXT,
    fecha_inicio TEXT,
    fecha_fin   TEXT,
    valor       REAL,
    estado      TEXT DEFAULT 'activo',
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS empleados (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT NOT NULL,
    cargo       TEXT,
    departamento TEXT,
    email       TEXT,
    extension   TEXT,
    estado      TEXT DEFAULT 'activo'
);

CREATE TABLE IF NOT EXISTS tareas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    texto       TEXT NOT NULL,
    contexto    TEXT,
    responsable TEXT,
    plazo       TEXT,
    prioridad   TEXT,
    estado      TEXT DEFAULT 'pendiente',
    creado_en   TEXT
);
"""

DATOS_EJEMPLO = {
    "usuarios": [
        ("Juan García",      "10234567", "juan.garcia@email.com",   "3101234567", "Premium",  "activo", "2024-01-15"),
        ("María López",      "87654321", "maria.lopez@email.com",   "3209876543", "Básico",   "activo", "2024-03-22"),
        ("Carlos Rincón",    "55512345", "carlos.rincon@email.com", "3154445566", "Empresarial","activo","2023-11-01"),
        ("Sofía Martínez",   "33398765", "sofia.m@email.com",       "3007778899", "Premium",  "activo", "2025-01-10"),
        ("Pedro Ramírez",    "11122334", "pedro.r@email.com",       "3112223344", "Básico",   "inactivo","2022-06-30"),
    ],
    "presupuesto": [
        ("Tecnología",   "Infraestructura servidores", 50_000_000, 35_000_000, "2026-Q1", "vigente"),
        ("Tecnología",   "Licencias software",         12_000_000,  9_500_000, "2026-Q1", "vigente"),
        ("Marketing",    "Publicidad digital",         20_000_000,  8_200_000, "2026-Q1", "vigente"),
        ("Marketing",    "Eventos y ferias",            8_000_000,  1_500_000, "2026-Q1", "vigente"),
        ("Operaciones",  "Mantenimiento equipos",       6_500_000,  4_100_000, "2026-Q1", "vigente"),
        ("Operaciones",  "Logística y transporte",      3_200_000,  2_900_000, "2026-Q1", "vigente"),
        ("RRHH",         "Capacitaciones",              5_000_000,  1_800_000, "2026-Q1", "vigente"),
    ],
    "contratos": [
        (1, "Servicio Premium anual",    "2025-01-01", "2026-01-01", 1_200_000, "activo"),
        (2, "Servicio Básico anual",     "2025-03-01", "2026-03-01",   480_000, "activo"),
        (3, "Servicio Empresarial",      "2024-11-01", "2025-11-01", 3_600_000, "activo"),
        (4, "Servicio Premium anual",    "2025-01-10", "2026-01-10", 1_200_000, "activo"),
        (5, "Servicio Básico anual",     "2022-06-30", "2023-06-30",   480_000, "vencido"),
    ],
    "empleados": [
        ("Carlos Martínez", "Agente Senior",   "Soporte Técnico", "c.martinez@empresa.com", "101", "activo"),
        ("Ana Rodríguez",   "Supervisora",     "Soporte Técnico", "a.rodriguez@empresa.com","102", "activo"),
        ("Luis Herrera",    "Agente",          "Soporte Técnico", "l.herrera@empresa.com",  "103", "activo"),
        ("Paola Gómez",     "Agente",          "Ventas",          "p.gomez@empresa.com",    "201", "activo"),
        ("Roberto Silva",   "Coordinador",     "Ventas",          "r.silva@empresa.com",    "202", "activo"),
        ("Diana Torres",    "Analista",        "Finanzas",        "d.torres@empresa.com",   "301", "activo"),
    ],
}

def setup():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.executescript(SCHEMA)

    for tabla, filas in DATOS_EJEMPLO.items():
        cursor.execute(f"SELECT COUNT(*) FROM {tabla}")
        if cursor.fetchone()[0] == 0:
            if tabla == "usuarios":
                cursor.executemany(
                    "INSERT INTO usuarios (nombre,cedula,email,telefono,plan,estado,fecha_registro) VALUES (?,?,?,?,?,?,?)",
                    filas
                )
            elif tabla == "presupuesto":
                cursor.executemany(
                    "INSERT INTO presupuesto (area,concepto,monto,ejecutado,periodo,estado) VALUES (?,?,?,?,?,?)",
                    filas
                )
            elif tabla == "contratos":
                cursor.executemany(
                    "INSERT INTO contratos (usuario_id,tipo,fecha_inicio,fecha_fin,valor,estado) VALUES (?,?,?,?,?,?)",
                    filas
                )
            elif tabla == "empleados":
                cursor.executemany(
                    "INSERT INTO empleados (nombre,cargo,departamento,email,extension,estado) VALUES (?,?,?,?,?,?)",
                    filas
                )
            print(f"  ✓ {tabla}: {len(filas)} registros insertados.")
        else:
            print(f"  · {tabla}: ya tiene datos, omitiendo.")

    conn.commit()
    conn.close()
    print("\nBase de datos lista.")

if __name__ == "__main__":
    setup()
