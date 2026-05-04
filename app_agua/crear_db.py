import sqlite3
import os

# 📌 Ruta absoluta
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

print(" Creando base de datos en:", DB_PATH)

con = sqlite3.connect(DB_PATH)
cur = con.cursor()

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

# ---------------- CAJEROS ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS cajeros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    rol TEXT,

    puede_vender INTEGER DEFAULT 1,
    puede_ver_pedidos INTEGER DEFAULT 0,
    puede_ver_reportes INTEGER DEFAULT 0,
    puede_ver_stock INTEGER DEFAULT 0,
    puede_agregar_productos INTEGER DEFAULT 0
)
""")

# ---------------- PROMOS ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS promos (
     id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    descripcion TEXT,
    precio REAL NOT NULL,
    activa INTEGER DEFAULT 1
)
""")


# ---------------- PEDIDOS ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS pedidos (
    id TEXT PRIMARY KEY,  -- <--- CAMBIADO A TEXT
    cliente_id INTEGER,
    promo_id TEXT,        -- <--- CAMBIADO A TEXT
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
    fecha TEXT,
    departamento TEXT,
    foto TEXT
)
""")

# ---------------- PROMO ITEMS ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS promo_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    promo_id INTEGER,
    producto_id TEXT,
    cantidad INTEGER
)
""")

# ---------------- CAJA ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS caja (
    id TEXT PRIMARY KEY,
    cajero TEXT,
    fecha_apertura TEXT,
    fecha_cierre TEXT,
    estado TEXT,
    monto_inicial REAL,
    cierre REAL,
    diferencia REAL
)
""")

# ---------------- VENTAS ----------------

cur.execute("""
    CREATE TABLE IF NOT EXISTS ventas (
        id TEXT PRIMARY KEY,
        fecha TEXT,
        total REAL,
        recargo REAL DEFAULT 0,
        descuento REAL DEFAULT 0,
        total_final REAL,
        metodo_pago TEXT,
        cajero_id INTEGER,
        cajero TEXT,
        caja_id TEXT,
        FOREIGN KEY(caja_id) REFERENCES caja(id) -- <--- ESTO ASEGURA EL VÍNCULO
    )
    """)


#  ASEGURAR COLUMNAS SI LA TABLA YA EXISTÍA
try:
    cur.execute("ALTER TABLE ventas ADD COLUMN recargo REAL DEFAULT 0")
except:
    pass

try:
    cur.execute("ALTER TABLE ventas ADD COLUMN descuento REAL DEFAULT 0")
except:
    pass

try:
    cur.execute("ALTER TABLE ventas ADD COLUMN caja_id TEXT")
except:
    pass

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
# ---------------- LITROS CONTROL ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS litros_control (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    litros REAL NOT NULL,
    fecha TEXT NOT NULL
)
""")


# ---------------- SYNC ----------------
cur.execute("""
CREATE TABLE IF NOT EXISTS sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tabla TEXT,
    accion TEXT,
    data TEXT,
    sync INTEGER DEFAULT 0,
    retries INTEGER DEFAULT 0,
    last_error TEXT,
    updated_at TEXT
)
""")

# ---------------- DATOS INICIALES ----------------
cur.execute("SELECT COUNT(*) FROM promos")
count = cur.fetchone()[0]

if count == 0:
    print(" Insertando promos iniciales...")

    cur.execute("""
        INSERT INTO promos(nombre, descripcion, precio, activa)
        VALUES (?, ?, ?, 1)
    """, ("Agua 20L", "Bidón grande", 5000))

    cur.execute("""
        INSERT INTO promos(nombre, descripcion, precio, activa)
        VALUES (?, ?, ?, 1)
    """, ("Agua 10L", "Bidón chico", 3000))

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

print(" Base de datos lista y funcionando correctamente")
