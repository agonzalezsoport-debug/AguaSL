from flask import Flask, render_template, request, redirect, session
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = "clave_secreta"

ADMIN_PASSWORD = "1234"

# ✅ Estados corregidos (IMPORTANTE)
ESTADOS_VALIDOS = ["pendiente", "enproceso", "entregado", "cancelado"]

# 📌 DB
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

def get_db():
    return sqlite3.connect(DB_PATH)

# ================== INDEX ==================
@app.route("/")
def index():
    return render_template("index.html")

# ================== LOGIN ADMIN ==================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password")

        if password == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/dashboard")

        return "❌ Clave incorrecta"

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ================== DASHBOARD ==================
@app.route("/dashboard")
def dashboard():
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM usuarios")
    clientes = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM pedidos")
    pedidos = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM pedidos WHERE estado='pendiente'")
    pendientes = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(SUM(pr.precio), 0)
        FROM pedidos p
        JOIN promos pr ON p.promo_id = pr.id
    """)
    total = cur.fetchone()[0]

    con.close()

    return render_template("dashboard.html",
        clientes=clientes,
        pedidos=pedidos,
        pendientes=pendientes,
        total=total
    )

# ================== REGISTRO ==================
@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre = request.form.get("nombre")
        telefono = request.form.get("telefono")
        direccion = request.form.get("direccion")
        password = request.form.get("password")

        con = get_db()
        cur = con.cursor()

        try:
            cur.execute("""
                INSERT INTO usuarios(nombre, telefono, direccion, password)
                VALUES (?, ?, ?, ?)
            """, (nombre, telefono, direccion, password))
            con.commit()
        except sqlite3.IntegrityError:
            return "❌ Usuario ya existe"
        finally:
            con.close()

        return redirect("/login_cliente")

    return render_template("registro.html")

# ================== LOGIN CLIENTE ==================
@app.route("/login_cliente", methods=["GET", "POST"])
def login_cliente():
    if request.method == "POST":
        nombre = request.form.get("nombre")
        password = request.form.get("password")

        con = get_db()
        cur = con.cursor()

        cur.execute("""
            SELECT * FROM usuarios WHERE nombre=? AND password=?
        """, (nombre, password))

        user = cur.fetchone()
        con.close()

        if user:
            session["cliente_id"] = user[0]
            return redirect("/mis_pedidos")

        return "❌ Usuario o contraseña incorrecta"

    return render_template("login_cliente.html")

@app.route("/logout_cliente")
def logout_cliente():
    session.clear()
    return redirect("/")

# ================== CLIENTES ==================
@app.route("/clientes")
def clientes():
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM usuarios")
    data = cur.fetchall()

    con.close()

    return render_template("clientes.html", clientes=data)

# ================== PROMOS ==================
@app.route("/promos")
def promos():
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM promos")
    data = cur.fetchall()

    con.close()

    return render_template("promos.html", promos=data)

@app.route("/promos/agregar", methods=["POST"])
def agregar_promo():
    if not session.get("admin"):
        return redirect("/login")

    nombre = request.form.get("nombre")
    descripcion = request.form.get("descripcion")
    precio = request.form.get("precio")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO promos(nombre, descripcion, precio, activa)
        VALUES (?, ?, ?, 1)
    """, (nombre, descripcion, precio))

    con.commit()
    con.close()

    return redirect("/promos")

# ================== MIS PEDIDOS CLIENTE ==================
@app.route("/mis_pedidos")
def mis_pedidos():
    if not session.get("cliente_id"):
        return redirect("/login_cliente")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM promos WHERE activa=1")
    promos = cur.fetchall()

    cur.execute("""
        SELECT p.id, pr.nombre, p.fecha, p.estado
        FROM pedidos p
        JOIN promos pr ON p.promo_id = pr.id
        WHERE p.cliente_id=?
        ORDER BY p.id DESC
    """, (session["cliente_id"],))

    pedidos = cur.fetchall()
    con.close()

    return render_template("mis_pedidos.html", promos=promos, pedidos=pedidos)

# ================== CREAR PEDIDO ==================
@app.route("/pedidos_cliente/agregar", methods=["POST"])
def agregar_pedido_cliente():
    if not session.get("cliente_id"):
        return redirect("/login_cliente")

    promo_id = request.form.get("promo_id")
    fecha = request.form.get("fecha") or datetime.now().strftime("%Y-%m-%d")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO pedidos (cliente_id, promo_id, fecha, estado)
        VALUES (?, ?, ?, 'pendiente')
    """, (session["cliente_id"], promo_id, fecha))

    con.commit()
    con.close()

    return redirect("/mis_pedidos")

# ================== PEDIDOS ADMIN ==================
@app.route("/pedidos")
def pedidos():
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT p.id, u.nombre, pr.nombre, p.fecha, p.estado
        FROM pedidos p
        JOIN usuarios u ON p.cliente_id = u.id
        JOIN promos pr ON p.promo_id = pr.id
        ORDER BY p.id DESC
    """)

    pedidos = cur.fetchall()
    con.close()

    return render_template("pedidos.html", pedidos=pedidos)

# ================== CAMBIAR ESTADO ==================
@app.route("/pedido/estado/<int:id>/<estado>")
def cambiar_estado(id, estado):
    if not session.get("admin"):
        return redirect("/login")

    if estado not in ESTADOS_VALIDOS:
        return "❌ Estado inválido"

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        UPDATE pedidos SET estado=?
        WHERE id=?
    """, (estado, id))

    con.commit()
    con.close()

    return redirect("/pedidos")

# ================== RUN ==================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")