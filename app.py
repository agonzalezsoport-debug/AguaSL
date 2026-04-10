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
    return psycopg2.connect(
        host="aws-1-us-east-1.pooler.supabase.com",
        dbname="postgres",
        user="postgres.dkualpdmiykqhdpfxzxu",
        password="Administrator21slag",
        port=6543,
        sslmode="require"
    )


# ================== INDEX ==================
@app.route("/")
def index():
    return render_template("index.html")
@app.route("/debug")
def debug():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM productos")
    data = cur.fetchall()

    con.close()
    return str(data)

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
@app.route("/productos/agregar", methods=["GET", "POST"])
def agregar_producto():
    if not session.get("admin"):
        return redirect("/login")

    if request.method == "POST":
        codigo = request.form.get("codigo")
        descripcion = request.form.get("descripcion")
        litros = int(request.form.get("litros"))
        precio = float(request.form.get("precio"))
        stock = int(request.form.get("stock"))

        con = get_db()
        cur = con.cursor()

        try:
            cur.execute("""
                INSERT INTO productos (id, codigo, descripcion, litros, precio, stock, fecha)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                str(datetime.now().timestamp()),
                codigo,
                descripcion,
                litros,
                precio,
                stock,
                datetime.now().strftime("%Y-%m-%d")
            ))

            con.commit()

        except sqlite3.IntegrityError:
            con.close()
            return "❌ Código ya existe"

        con.close()
        return redirect("/productos/agregar")

    return render_template("agregar_producto.html")

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





# ================== VENTAS ==================
@app.route("/ventas", methods=["GET", "POST"])
def ventas():
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    if request.method == "POST":
        codigo = (request.form.get("codigo") or "").strip().upper()
        cantidad = int(request.form.get("cantidad") or 0)

        if cantidad <= 0:
            con.close()
            return "❌ Cantidad inválida"

        # 🔍 buscar producto
        cur.execute("""
            SELECT id, descripcion, litros, precio, stock
            FROM productos
            WHERE UPPER(codigo)=?
        """, (codigo,))

        prod = cur.fetchone()

        if not prod:
            con.close()
            return "❌ Producto no existe"

        producto_id, desc, litros, precio, stock = prod

        # 🚨 validar stock
        if stock < cantidad:
            con.close()
            return f"❌ Stock insuficiente (Disponible: {stock})"

        metodo_pago = request.form.get("metodo_pago")
        recargo = float(request.form.get("recargo") or 0)
        if not metodo_pago:
            con.close()
            return "❌ Debes seleccionar método de pago"

        subtotal = precio * cantidad
        litros_total = litros * cantidad
        venta_id = str(datetime.now().timestamp())

        # 💰 venta
        cur.execute("""
            INSERT INTO ventas (id, fecha, total, descuento, total_final, metodo_pago, cajero)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            venta_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            subtotal,
            0,
            subtotal,
            metodo_pago,
            "admin"
        ))

        # 📦 detalle
        cur.execute("""
            INSERT INTO venta_items (id, venta_id, producto_id, cantidad, litros_total, subtotal)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(datetime.now().timestamp()) + "i",
            venta_id,
            producto_id,
            cantidad,
            litros_total,
            subtotal
        ))

        # 🔥 descontar stock
        cur.execute("""
            UPDATE productos
            SET stock = stock - ?
            WHERE id = ?
        """, (cantidad, producto_id))

        con.commit()
        con.close()

        return f"✅ Venta realizada: ${subtotal}"

    # GET
    cur.execute("SELECT * FROM productos")
    productos = cur.fetchall()

    con.close()

    return render_template("ventas.html", productos=productos)
@app.route("/carrito/agregar", methods=["POST"])
def carrito_agregar():
    if "carrito" not in session:
        session["carrito"] = []

    codigo = (request.form.get("codigo") or "").strip().upper()
    cantidad_raw = request.form.get("cantidad")

    if not codigo:
        return "❌ Código vacío"

    if not cantidad_raw:
        return "❌ Debes ingresar cantidad"

    cantidad = int(cantidad_raw)

    con = get_db()
    cur = con.cursor()

    # ================= PROMOS =================
    if codigo.startswith("PROMO-"):
        promo_id = codigo.replace("PROMO-", "")

        cur.execute("""
            SELECT id, nombre, descripcion, precio
            FROM promos
            WHERE id=?
        """, (promo_id,))

        promo = cur.fetchone()

        con.close()

        if not promo:
            return "❌ Promo no existe"

        prod_id, nombre, desc, precio = promo

        session["carrito"].append({
            "id": "promo_" + str(prod_id),
            "desc": "🎁 " + nombre,
            "precio": float(precio),
            "cantidad": cantidad
        })

        session.modified = True
        return redirect("/ventas_ui")

    # ================= PRODUCTOS =================
    cur.execute("""
        SELECT id, descripcion, precio, stock
        FROM productos
        WHERE UPPER(codigo)=?
    """, (codigo,))

    prod = cur.fetchone()
    con.close()

    if not prod:
        return "❌ Producto no existe"

    prod_id, desc, precio, stock = prod

    if stock < cantidad:
        return "❌ Stock insuficiente"

    session["carrito"].append({
        "id": prod_id,
        "desc": desc,
        "precio": precio,
        "cantidad": cantidad
    })

    session.modified = True
    return redirect("/ventas_ui")
@app.route("/debug_promos")
def debug_promos():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM promos")
    data = cur.fetchall()

    con.close()
    return str(data)
@app.route("/ventas_ui")
def ventas_ui():
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM productos")
    productos = cur.fetchall()

    cur.execute("SELECT id, nombre, descripcion, precio FROM promos WHERE activa=1")
    promos = cur.fetchall()

    con.close()

    carrito = session.get("carrito", [])
    subtotal = sum(i["precio"] * i["cantidad"] for i in carrito)

    return render_template(
        "ventas.html",
        productos=productos,
        promos=promos,
        carrito=carrito,
        total=subtotal
    )

    
@app.route("/carrito/eliminar/<int:index>")
def carrito_eliminar(index):
    carrito = session.get("carrito", [])

    if 0 <= index < len(carrito):
        carrito.pop(index)

    session["carrito"] = carrito
    session.modified = True

    return redirect("/ventas_ui")
@app.route("/carrito/confirmar", methods=["POST"])

def carrito_confirmar():
    carrito = session.get("carrito", [])

    if not carrito:
        return "❌ Carrito vacío"

    metodo_pago = request.form.get("metodo_pago")
    metodo_pago = request.form.get("metodo_pago")
    recargo = float(request.form.get("recargo") or 0)   # 👈 AGREGAR ACÁ

    if not metodo_pago:
        return "❌ Selecciona método de pago"

    con = get_db()
    cur = con.cursor()

    venta_id = str(datetime.now().timestamp())

    subtotal = sum(i["precio"] * i["cantidad"] for i in carrito)

    recargo_valor = subtotal * recargo / 100
    total_final = subtotal + recargo_valor

    cur.execute("""
        INSERT INTO ventas (id, fecha, total, descuento, total_final, metodo_pago, cajero)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        venta_id,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        subtotal,
        recargo_valor,
        total_final,
        metodo_pago,
        "admin"
    ))

    for item in carrito:
        cur.execute("""
            INSERT INTO venta_items (id, venta_id, producto_id, cantidad, litros_total, subtotal)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(datetime.now().timestamp()) + "i",
            venta_id,
            item["id"],
            item["cantidad"],
            0,
            item["precio"] * item["cantidad"]
        ))

        # descontar stock
        cur.execute("""
            UPDATE productos
            SET stock = stock - ?
            WHERE id = ?
        """, (item["cantidad"], item["id"]))

    con.commit()
    con.close()

    session["carrito"] = []

    return redirect("/ventas_ui")
@app.route("/stock", methods=["GET", "POST"])
def stock():
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    # EDITAR PRODUCTO
    if request.method == "POST":
        producto_id = request.form.get("id")
        descripcion = request.form.get("descripcion")
        precio = float(request.form.get("precio") or 0)
        stock_val = int(request.form.get("stock") or 0)

        cur.execute("""
            UPDATE productos
            SET descripcion=?, precio=?, stock=?
            WHERE id=?
        """, (descripcion, precio, stock_val, producto_id))

        con.commit()

    cur.execute("SELECT id, codigo, descripcion, precio, stock FROM productos")
    productos = cur.fetchall()

    con.close()

    return render_template("stock.html", productos=productos)

# ================== RUN ==================
if __name__ == "__main__":
    app.run(debug=True)
