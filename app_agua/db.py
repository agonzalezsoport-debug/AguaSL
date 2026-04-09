import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = "data/socios.db"

# ────────── PLANES DEL GYM ──────────
PLANES_CLASES = {
    "Funcional": 12,
    "Aparatos": 9999,  # ilimitado
    "Personalizado": 8
}

# ────────── CONEXIÓN ──────────
def conectar():
    os.makedirs("data", exist_ok=True)
    return sqlite3.connect(DB_PATH)

# ────────── CREAR TABLAS ──────────
def crear_tablas():
    con = conectar()
    cur = con.cursor()

    # 🧠 TABLA SOCIOS (MEJORADA)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS socios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        plan TEXT,
        precio REAL,
        profesor TEXT,
        estado TEXT,
        vencimiento TEXT,
        foto TEXT,
        clases_disponibles INTEGER DEFAULT 0,
        clases_tomadas INTEGER DEFAULT 0,
        qr TEXT
    )
    """)

    # 📊 TABLA ASISTENCIAS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS asistencias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        socio_id INTEGER,
        fecha TEXT
    )
    """)

    # 🔍 Verificar columnas existentes
    cur.execute("PRAGMA table_info(socios)")
    columnas = [c[1] for c in cur.fetchall()]

    if "qr" not in columnas:
        cur.execute("ALTER TABLE socios ADD COLUMN qr TEXT")

    con.commit()
    con.close()

# ────────── AGREGAR SOCIO ──────────
def agregar_socio(nombre, plan, precio, profesor, foto):
    con = conectar()
    cur = con.cursor()

    clases = PLANES_CLASES.get(plan, 0)

    # 📅 vencimiento automático (30 días)
    vencimiento = (datetime.now() + timedelta(days=30)).strftime("%d/%m/%Y")

    cur.execute("""
        INSERT INTO socios 
        (nombre, plan, precio, profesor, estado, vencimiento, foto, clases_disponibles, clases_tomadas, qr)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        nombre,
        plan,
        precio,
        profesor,
        "PAGO",
        vencimiento,
        foto,
        clases,
        0,
        ""  # luego podés guardar ruta del QR
    ))

    con.commit()
    con.close()

# ────────── OBTENER ÚLTIMO SOCIO ──────────
def obtener_socio():
    con = conectar()
    cur = con.cursor()

    cur.execute("""
        SELECT id, nombre, plan, precio, profesor, estado, vencimiento, foto, clases_disponibles, clases_tomadas
        FROM socios
        ORDER BY id DESC
        LIMIT 1
    """)

    socio = cur.fetchone()
    con.close()
    return socio

# 🆕 OBTENER POR ID (CLAVE PARA QR)
def obtener_socio_por_id(socio_id):
    con = conectar()
    cur = con.cursor()

    cur.execute("""
        SELECT id, nombre, plan, precio, profesor, estado, vencimiento, foto, clases_disponibles, clases_tomadas
        FROM socios
        WHERE id = ?
    """, (socio_id,))

    socio = cur.fetchone()
    con.close()
    return socio

# ────────── REGISTRAR ASISTENCIA ──────────
def registrar_asistencia(socio_id):
    con = conectar()
    cur = con.cursor()

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 📊 guardar asistencia
    cur.execute("""
        INSERT INTO asistencias (socio_id, fecha)
        VALUES (?, ?)
    """, (socio_id, fecha))

    # 🎟 descontar clase
    cur.execute("""
        UPDATE socios
        SET 
            clases_disponibles = CASE 
                WHEN clases_disponibles > 0 THEN clases_disponibles - 1 
                ELSE 0 
            END,
            clases_tomadas = clases_tomadas + 1
        WHERE id = ?
    """, (socio_id,))

    con.commit()
    con.close()

# ────────── CARGAR CLASES ──────────
def cargar_clases(socio_id, cantidad):
    con = conectar()
    cur = con.cursor()

    cur.execute("""
        UPDATE socios
        SET clases_disponibles = clases_disponibles + ?
        WHERE id = ?
    """, (cantidad, socio_id))

    con.commit()
    con.close()

# ────────── REGISTRAR PAGO ──────────
def registrar_pago(socio_id):
    con = conectar()
    cur = con.cursor()

    nuevo_vencimiento = (datetime.now() + timedelta(days=30)).strftime("%d/%m/%Y")

    cur.execute("""
        UPDATE socios
        SET estado = 'PAGO',
            vencimiento = ?
        WHERE id = ?
    """, (nuevo_vencimiento, socio_id))

    con.commit()
    con.close()

# ────────── ESTADÍSTICAS ──────────
def total_socios():
    con = conectar()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM socios")
    total = cur.fetchone()[0]

    con.close()
    return total

def asistencias_hoy():
    con = conectar()
    cur = con.cursor()

    hoy = datetime.now().strftime("%Y-%m-%d")

    cur.execute("""
        SELECT COUNT(*) FROM asistencias
        WHERE fecha LIKE ?
    """, (f"{hoy}%",))

    total = cur.fetchone()[0]
    con.close()
    return total

# ────────── VERIFICAR TABLA ──────────
def verificar_tabla():
    con = conectar()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(socios)")
    columnas = cur.fetchall()

    con.close()
    return columnas