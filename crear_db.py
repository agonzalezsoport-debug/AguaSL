import sqlite3
import os

# 📌 Ruta absoluta (evita múltiples database.db)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

# 📌 Conexión
con = sqlite3.connect(DB_PATH)
cur = con.cursor()

print("📦 Creando base de datos en:", DB_PATH)

# ---------------- CLIENTES ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS clientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    telefono TEXT,
    direccion TEXT
)
""")

# ---------------- USUARIOS ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT UNIQUE NOT NULL,
    telefono TEXT,
    direccion TEXT,
    password TEXT NOT NULL
)
""")

# ---------------- PROMOS ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS promos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    descripcion TEXT,
    precio REAL NOT NULL,
    activa INTEGER DEFAULT 1
)
""")

# ---------------- PEDIDOS ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS pedidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER,
    promo_id INTEGER,
    fecha TEXT,
    estado TEXT DEFAULT 'Pendiente',
    FOREIGN KEY(cliente_id) REFERENCES clientes(id),
    FOREIGN KEY(promo_id) REFERENCES promos(id)
)
""")

# ---------------- PRODUCTOS ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS productos (
    id TEXT PRIMARY KEY,
    codigo TEXT UNIQUE,
    descripcion TEXT,
    litros INTEGER,
    precio REAL,
    stock INTEGER DEFAULT 0,
    fecha TEXT
)
""")

# ---------------- VENTAS ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS ventas (
    id TEXT PRIMARY KEY,
    fecha TEXT,
    total REAL,
    descuento REAL,
    total_final REAL,
    metodo_pago TEXT,
    cajero TEXT
)
""")

# ---------------- VENTA ITEMS ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS venta_items (
    id TEXT PRIMARY KEY,
    venta_id TEXT,
    producto_id TEXT,
    cantidad INTEGER,
    litros_total REAL,
    subtotal REAL
)
""")

# ---------------- DATOS INICIALES PROMOS ----------------
cur.execute("SELECT COUNT(*) FROM promos")
count = cur.fetchone()[0]

if count == 0:
    print("📥 Insertando promos iniciales...")

    cur.execute("""
        INSERT INTO promos(nombre, descripcion, precio, activa)
        VALUES (?, ?, ?, 1)
    """, ("Agua 20L", "Bidón de agua grande", 5000))

    cur.execute("""
        INSERT INTO promos(nombre, descripcion, precio, activa)
        VALUES (?, ?, ?, 1)
    """, ("Agua 10L", "Bidón de agua chico", 3000))

# ---------------- ADMIN ----------------
cur.execute("SELECT * FROM usuarios WHERE nombre = ?", ("admin",))
admin = cur.fetchone()

if not admin:
    print("👤 Creando usuario admin...")

    cur.execute("""
        INSERT INTO usuarios(nombre, telefono, direccion, password)
        VALUES (?, ?, ?, ?)
    """, ("admin", "000", "Admin", "1234"))

# ---------------- GUARDAR ----------------
con.commit()
con.close()

print("✅ Base de datos lista y funcionando correctamente")
