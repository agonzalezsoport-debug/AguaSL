from flask import Flask, render_template, request, redirect, session, flash
import os
from datetime import datetime
import psycopg2
import socket
import threading
import time
import json
import sqlite3
import uuid
print("🔥🔥🔥 ESTE ES EL ARCHIVO CORRECTO 🔥🔥🔥")

app = Flask(__name__)
app.secret_key = "clave_secreta"

ADMIN_PASSWORD = "1234"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

# ✅ Estados corregidos (IMPORTANTE)
ESTADOS_VALIDOS = ["pendiente", "enproceso", "entregado", "cancelado"]



def get_db_cloud():
    return psycopg2.connect(
        host="aws-1-us-east-1.pooler.supabase.com",
        dbname="postgres",
        user="postgres.dkualpdmiykqhdpfxzxu",
        password="Administrator21slag",
        port=6543,
        sslmode="require"
    )


def get_db_local():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row  # opcional pero recomendado
    return con
    
def sync_worker():
   
    while True:
        try:
            if not internet_ok():
                time.sleep(5)
                continue

            con = get_db_local()
            cur = con.cursor()

            cur.execute("SELECT * FROM sync_queue WHERE sync=0 LIMIT 50")
            rows = cur.fetchall()

            for row in rows:
                con_cloud = None

                try:
                    id_ = row["id"]
                    tabla = row["tabla"]
                    data = json.loads(row["data"])

                    print(f"🔄 Sync: {tabla} -> {data}")

                    con_cloud = get_db_cloud()
                    cur_cloud = con_cloud.cursor()

                    # ================= PRODUCTOS =================
                    if tabla == "productos":
                        cur_cloud.execute("""
                            INSERT INTO productos(
                                id, codigo, descripcion, litros, precio, stock, fecha, departamento
                            )
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (id) DO NOTHING
                        """, (
                            data["id"],
                            data["codigo"],
                            data["descripcion"],
                            data["litros"],
                            data["precio"],
                            data["stock"],
                            data["fecha"],
                            data.get("departamento")  # 🔥 IMPORTANTE
                        ))

                    # ================= PROMOS =================
                    elif tabla == "promos":
                        cur_cloud.execute("""
                            INSERT INTO promos(nombre, descripcion, precio, activa)
                            VALUES (%s,%s,%s,%s)
                            ON CONFLICT DO NOTHING
                        """, (
                            data["nombre"],
                            data["descripcion"],
                            data["precio"],
                            data["activa"]
                        ))

                    # ================= PEDIDOS =================
                    elif tabla == "pedidos":
                        cur_cloud.execute("""
                            INSERT INTO pedidos(cliente_id, promo_id, fecha, estado)
                            VALUES (%s,%s,%s,%s)
                            ON CONFLICT DO NOTHING
                        """, (
                            data["cliente_id"],
                            data["promo_id"],
                            data["fecha"],
                            data["estado"]
                        ))

                    # ================= VENTAS =================
                    elif tabla == "ventas":
                        cur_cloud.execute("""
                            INSERT INTO ventas(id, fecha, total,recargo, descuento, total_final, metodo_pago, cajero)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (id) DO NOTHING
                        """, (
                            data["id"],
                            data["fecha"],
                            data["total"],
                            data.get("recargo", 0),
                            data["descuento"],
                            data["total_final"],
                            data["metodo_pago"],
                            data["cajero"]
                        ))

                    # ================= VENTA ITEMS =================
                    elif tabla == "venta_items":

                        venta_id = data.get("venta_id")

                        if not venta_id:
                            print("⚠️ venta_item sin venta_id -> ignorado")
                            continue

                        cur_cloud.execute("""
                            SELECT 1 FROM venta_items WHERE id = %s
                        """, (data["id"],))

                        if cur_cloud.fetchone():
                            continue

                        cur_cloud.execute("""
                            INSERT INTO venta_items(
                                id, venta_id, producto_id, cantidad, litros_total, subtotal
                            )
                            VALUES (%s,%s,%s,%s,%s,%s)
                        """, (
                            data["id"],
                            venta_id,
                            data["producto_id"],
                            data["cantidad"],
                            data["litros_total"],
                            data["subtotal"]
                        ))

                        cur_cloud.execute("""
                            UPDATE productos
                            SET stock = stock - %s
                            WHERE id = %s
                        """, (
                            data["cantidad"],
                            data["producto_id"]
                        ))

                        

                    else:
                        print(f"⚠️ tabla no manejada: {tabla}")
                        continue

                    con_cloud.commit()

                    # marcar sync OK
                    cur.execute("UPDATE sync_queue SET sync=1 WHERE id=?", (id_,))
                    con.commit()

                    print(f"✅ OK: {tabla}")

                except Exception as e:
                    print("❌ ERROR SYNC:", e)

                    if con_cloud:
                        try:
                            con_cloud.rollback()
                        except:
                            pass

                finally:
                    if con_cloud:
                        con_cloud.close()

            con.close()

        except Exception as e:
            print("🔥 ERROR GLOBAL SYNC:", e)

        time.sleep(5)
def get_db():
    if internet_ok():
        try:
            return psycopg2.connect(
                host="aws-1-us-east-1.pooler.supabase.com",
                dbname="postgres",
                user="postgres.dkualpdmiykqhdpfxzxu",
                password="Administrator21slag",
                port=6543,
                sslmode="require"
            )
        except:
            pass

    # 🔴 OFFLINE fallback
    return sqlite3.connect(DB_PATH)
def ejecutar(cur, conn, query, params=None):
    if isinstance(conn, sqlite3.Connection):
        query = query.replace("%s", "?")

    if params is None:
        cur.execute(query)
    else:
        cur.execute(query, params)
def save_offline(tabla, accion, data):
    con = get_db_local()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO sync_queue(tabla, accion, data, sync)
        VALUES (?, ?, ?, 0)
    """, (tabla, accion, json.dumps(data)))  # ✅ FIX

    con.commit()
    con.close()
import requests

def internet_ok():
    try:
        import urllib.request
        urllib.request.urlopen("https://www.google.com", timeout=3)
        print("🌐 INTERNET OK")
        return True
    except Exception as e:
        print("❌ SIN INTERNET:", e)
        return False
def sync_producto_to_cloud(id, codigo, descripcion, litros, precio, stock, fecha, departamento):
    try:
        con = get_db_cloud()
        cur = con.cursor()

        ejecutar(cur, con, """
            INSERT INTO productos(
                id, codigo, descripcion, litros, precio, stock, fecha, departamento
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            id,
            codigo,
            descripcion,
            litros,
            precio,
            stock,
            fecha,
            departamento
        ))

        con.commit()
        con.close()

        print("✅ Producto sincronizado")

    except Exception as e:
        print("⚠️ Error sync producto:", e)

        # ✅ GUARDAR OFFLINE
        save_offline("productos", "insert", {
            "id": id,
            "codigo": codigo,
            "descripcion": descripcion,
            "litros": litros,
            "precio": precio,
            "stock": stock,
            "fecha": fecha,
            "departamento": departamento   # 🔥 IMPORTANTE
        })
def sync_promo_to_cloud(nombre, descripcion, precio):
    try:
        con = get_db_cloud()
        cur = con.cursor()

        ejecutar(cur, con, """
            INSERT INTO promos(nombre, descripcion, precio, activa)
            VALUES (%s, %s, %s, 1)
        """, (nombre, descripcion, precio))

        con.commit()
        con.close()

        print("✅ Promo sincronizada")

    except Exception as e:
        print("⚠️ Error sync promo:", e)

        save_offline("promos", "insert", {
            "nombre": nombre,
            "descripcion": descripcion,
            "precio": precio,
            "activa": 1
        })

def sync_venta_to_cloud(venta_id, fecha, total, recargo, descuento, total_final, metodo_pago, cajero, items):
    try:
        con = get_db_cloud()
        cur = con.cursor()

        cur.execute("""
            INSERT INTO ventas(
                id, fecha, total, recargo, descuento, total_final, metodo_pago, cajero
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            venta_id,
            fecha,
            total,
            recargo,      # ✔ ahora coincide
            descuento,
            total_final,
            metodo_pago,
            cajero
        ))

        # ================= ITEMS + STOCK =================
        for item in items:

            venta_item_id = item.get("id")
            producto_id = item.get("producto_id")
            cantidad = item.get("cantidad", 0)
            litros_total = item.get("litros_total", 0)
            subtotal = item.get("subtotal", 0)

            # 🔴 FIX CRÍTICO: validar datos antes de insertar
            if not venta_item_id or not producto_id:
                print("⚠️ Item inválido (sin id o producto_id), ignorado")
                continue

            # ================= INSERT ITEM =================
            cur.execute("""
                INSERT INTO venta_items(
                    id, venta_id, producto_id, cantidad, litros_total, subtotal
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (
                venta_item_id,
                venta_id,
                producto_id,
                cantidad,
                litros_total,
                subtotal
            ))

            

        con.commit()
        con.close()

        print("✅ Venta sincronizada completa (ventas + items + stock)")

    except Exception as e:
        print("⚠️ Error sync venta:", e)

        # fallback offline
        save_offline("ventas", "insert", {
            "id": venta_id,
            "fecha": fecha,
            "total": total,
            "descuento": descuento,
            "total_final": total_final,
            "metodo_pago": metodo_pago,
            "cajero": cajero
        })

        for item in items:
            item_fixed = dict(item)
            item_fixed["venta_id"] = venta_id

            save_offline("venta_items", "insert", item_fixed)
# ================== INDEX ==================
@app.route("/")
def index():
    return render_template("index.html")
@app.route("/debug")
def debug():
    con = get_db()
    cur = con.cursor()

    ejecutar(cur, con, "SELECT * FROM productos")
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
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ No tenés permiso para agregar productos"

    con = get_db()
    cur = con.cursor()

    # ✅ CLIENTES
    ejecutar(cur, con, "SELECT COUNT(*) FROM usuarios")
    clientes = cur.fetchone()[0]

    # ✅ PEDIDOS
    ejecutar(cur, con, "SELECT COUNT(*) FROM pedidos")
    pedidos = cur.fetchone()[0]

    # ✅ PENDIENTES
    ejecutar(cur, con, "SELECT COUNT(*) FROM pedidos WHERE estado='pendiente'")
    pendientes = cur.fetchone()[0]

    # ✅ TOTAL
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(pr.precio), 0)
        FROM pedidos p
        JOIN promos pr ON p.promo_id = pr.id
    """)
    total = cur.fetchone()[0]

    con.close()

    return render_template(
        "dashboard.html",
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
            ejecutar(cur, con, """
                INSERT INTO usuarios(nombre, telefono, direccion, password)
                VALUES (%s, %s, %s, %s)
            """, (nombre, telefono, direccion, password))

            con.commit()

        except sqlite3.IntegrityError:
            con.close()
            return "❌ Usuario ya existe"

        except Exception as e:
            con.close()
            return f"❌ Error: {e}"

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

        ejecutar(cur, con, """
            SELECT * FROM usuarios 
            WHERE nombre=%s AND password=%s
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

    ejecutar(cur, con, "SELECT * FROM usuarios")
    data = cur.fetchall()

    con.close()

    return render_template("clientes.html", clientes=data)
# ================== PROMOS ==================
@app.route("/promos")
def promos():
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ No tenés permiso para agregar productos"

    con = get_db()
    cur = con.cursor()

    ejecutar(cur, con, "SELECT * FROM promos")
    data = cur.fetchall()

    con.close()

    return render_template("promos.html", promos=data)

@app.route("/promos/agregar", methods=["POST"])
def agregar_promo():
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ No tenés permiso para agregar productos"

    nombre = request.form.get("nombre")
    descripcion = request.form.get("descripcion")
    precio = request.form.get("precio")

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, """
            INSERT INTO promos(nombre, descripcion, precio, activa)
            VALUES (%s, %s, %s, 1)
        """, (nombre, descripcion, precio))

        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error al guardar promo: {e}"

    con.close()

    # 🔁 SINCRONIZAR A SUPABASE
    sync_promo_to_cloud(nombre, descripcion, precio)

    return redirect("/promos")
@app.route("/productos/agregar", methods=["GET", "POST"])
def agregar_producto():
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ No tenés permiso para agregar productos"

    if request.method == "POST":
        try:
            codigo = (request.form.get("codigo") or "").strip().upper()
            descripcion = request.form.get("descripcion")
            litros = int(request.form.get("litros") or 0)
            precio = float(request.form.get("precio") or 0)
            stock = int(request.form.get("stock") or 0)

            # 🔥 NUEVO CAMPO
            departamento = request.form.get("departamento")

            if not codigo:
                return "❌ Código vacío"

            producto_id = str(datetime.now().timestamp())
            fecha = datetime.now().strftime("%Y-%m-%d")

            con = get_db()
            cur = con.cursor()

            # ================= LOCAL =================
            ejecutar(cur, con, """
                INSERT INTO productos (
                    id, codigo, descripcion, litros, precio, stock, fecha, departamento
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                producto_id,
                codigo,
                descripcion,
                litros,
                precio,
                stock,
                fecha,
                departamento
            ))

            con.commit()
            con.close()

            # ================= CLOUD =================
            try:
                sync_producto_to_cloud(
                    producto_id,
                    codigo,
                    descripcion,
                    litros,
                    precio,
                    stock,
                    fecha,
                    departamento
                )
            except Exception as e:
                print("⚠️ Error sync producto:", e)

            return redirect("/productos/agregar")

        except sqlite3.IntegrityError:
            return "❌ Código ya existe"

        except Exception as e:
            return f"❌ Error: {e}"

    return render_template("agregar_producto.html")
# ================== MIS PEDIDOS CLIENTE ==================
@app.route("/mis_pedidos")
def mis_pedidos():
    if not session.get("cliente_id"):
        return redirect("/login_cliente")

    con = get_db()
    cur = con.cursor()

    try:
        # ================= PROMOS =================
        ejecutar(cur, con, "SELECT * FROM promos WHERE activa=1")
        promos = cur.fetchall()

        # ================= PEDIDOS =================
        ejecutar(cur, con, """
            SELECT p.id, pr.nombre, p.fecha, p.estado
            FROM pedidos p
            JOIN promos pr ON p.promo_id = pr.id
            WHERE p.cliente_id=%s
            ORDER BY p.id DESC
        """, (session["cliente_id"],))

        pedidos = cur.fetchall()

    except Exception as e:
        con.close()
        return f"❌ Error al cargar pedidos: {e}"

    con.close()

    return render_template(
        "mis_pedidos.html",
        promos=promos,
        pedidos=pedidos
    )
# ================== CREAR PEDIDO ==================
@app.route("/pedidos_cliente/agregar", methods=["POST"])
def agregar_pedido_cliente():
    if not session.get("cliente_id"):
        return redirect("/login_cliente")

    promo_id = request.form.get("promo_id")
    fecha = request.form.get("fecha") or datetime.now().strftime("%Y-%m-%d")

    if not promo_id:
        return "❌ Debes seleccionar una promo"

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, """
            INSERT INTO pedidos (cliente_id, promo_id, fecha, estado)
            VALUES (%s, %s, %s, 'pendiente')
        """, (session["cliente_id"], promo_id, fecha))

        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error al crear pedido: {e}"

    con.close()

    return redirect("/mis_pedidos")

# ================== PEDIDOS ADMIN ==================
@app.route("/pedidos")
def pedidos():
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ No tenés permiso para agregar productos"

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, """
            SELECT p.id, u.nombre, pr.nombre, p.fecha, p.estado
            FROM pedidos p
            JOIN usuarios u ON p.cliente_id = u.id
            JOIN promos pr ON p.promo_id = pr.id
            ORDER BY p.id DESC
        """)

        pedidos = cur.fetchall()

    except Exception as e:
        con.close()
        return f"❌ Error al cargar pedidos: {e}"

    con.close()

    return render_template("pedidos.html", pedidos=pedidos)
@app.route("/permisos_cajero/<int:id>", methods=["GET", "POST"])
def permisos_cajero(id):
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    # =========================
    # GUARDAR PERMISOS (POST)
    # =========================
    if request.method == "POST":
        vender = 1 if "vender" in request.form else 0
        pedidos = 1 if "pedidos" in request.form else 0
        reportes = 1 if "reportes" in request.form else 0
        stock = 1 if "stock" in request.form else 0
        agregar = 1 if "agregar_productos" in request.form else 0

        ejecutar(cur, con, """
            UPDATE cajeros
            SET puede_vender=%s,
                puede_ver_pedidos=%s,
                puede_ver_reportes=%s,
                puede_ver_stock=%s,
                puede_agregar_productos=%s
            WHERE id=%s
        """, (vender, pedidos, reportes, stock, agregar, id))

        con.commit()

    # =========================
    # CARGAR DATOS CAJERO
    # =========================
    ejecutar(cur, con, """
        SELECT id, usuario, rol,
               puede_vender,
               puede_ver_pedidos,
               puede_ver_reportes,
               puede_ver_stock,
               puede_agregar_productos
        FROM cajeros
        WHERE id=%s
    """, (id,))

    cajero = cur.fetchone()

    con.close()

    return render_template("permisos_cajero.html", cajero=cajero)

# ================== CAMBIAR ESTADO ==================
@app.route("/pedido/estado/<int:id>/<estado>")
def cambiar_estado(id, estado):
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ No tenés permiso para agregar productos"

    if estado not in ESTADOS_VALIDOS:
        return "❌ Estado inválido"

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, """
            UPDATE pedidos 
            SET estado=%s
            WHERE id=%s
        """, (estado, id))

        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error al actualizar estado: {e}"

    con.close()

    return redirect("/pedidos")
    @app.route("/ventas_por_cajero")
def ventas_por_cajero():
    if not session.get("admin") and not session.get("puede_ver_reportes"):
        return "❌ Sin permiso"

    cajero_filtro = request.args.get("cajero")

    con = get_db()
    cur = con.cursor()

    # ================= CAJEROS =================
    ejecutar(cur, con, """
        SELECT DISTINCT cajero FROM ventas
    """)
    cajeros = cur.fetchall()

    # ================= WHERE DINÁMICO =================
    where = ""
    params = ()

    if cajero_filtro:
        where = "WHERE cajero=%s"
        params = (cajero_filtro,)

    # ================= RESUMEN =================
    ejecutar(cur, con, f"""
        SELECT COUNT(*), COALESCE(SUM(total_final),0)
        FROM ventas
        {where}
    """, params)

    total_ventas, total_dinero = cur.fetchone()

    # ================= PRODUCTOS =================
    ejecutar(cur, con, f"""
        SELECT p.descripcion,
               COALESCE(SUM(v.cantidad),0),
               COALESCE(SUM(v.subtotal),0)
        FROM venta_items v
        JOIN productos p ON v.producto_id = p.id
        JOIN ventas ve ON ve.id = v.venta_id
        {where.replace("cajero", "ve.cajero")}
        GROUP BY p.descripcion
        ORDER BY SUM(v.cantidad) DESC
    """, params)

    productos = cur.fetchall()

    # ================= MÉTODOS =================
    ejecutar(cur, con, f"""
        SELECT metodo_pago,
               COUNT(*),
               COALESCE(SUM(total_final),0)
        FROM ventas
        {where}
        GROUP BY metodo_pago
    """, params)

    metodos = cur.fetchall()

    # ================= AUDITORIA =================
    ejecutar(cur, con, """
        SELECT p.descripcion,
               p.stock,
               COALESCE(SUM(v.cantidad),0)
        FROM productos p
        LEFT JOIN venta_items v ON v.producto_id = p.id
        GROUP BY p.id
    """)

    auditoria = cur.fetchall()

    con.close()

    return render_template(
        "ventas_por_cajero.html",
        cajeros=cajeros,
        productos=productos,
        metodos=metodos,
        auditoria=auditoria,
        total_ventas=total_ventas,
        total_dinero=total_dinero
    )


# ================== VENTAS ==================
@app.route("/ventas", methods=["GET", "POST"])
def ventas():
    if not (session.get("admin") or session.get("cajero_id")):
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    if request.method == "POST":
        try:
            codigo = (request.form.get("codigo") or "").strip().upper()
            cantidad = int(request.form.get("cantidad") or 0)

            recargo = float(request.form.get("recargo") or 0)
            descuento = float(request.form.get("descuento") or 0)
            metodo_pago = request.form.get("metodo_pago")

            if cantidad <= 0:
                return "❌ Cantidad inválida"

            ejecutar(cur, con, """
                SELECT id, descripcion, litros, precio, stock
                FROM productos
                WHERE UPPER(codigo)=%s
            """, (codigo,))

            prod = cur.fetchone()
            if not prod:
                return "❌ Producto no existe"

            # compatibilidad sqlite / postgres
            if isinstance(prod, sqlite3.Row):
                producto_id = prod["id"]
                desc = prod["descripcion"]
                litros = prod["litros"]
                precio = prod["precio"]
                stock = prod["stock"]
            else:
                producto_id, desc, litros, precio, stock = prod

            if stock < cantidad:
                return f"❌ Stock insuficiente (Disponible: {stock})"

            if not metodo_pago:
                return "❌ Debes seleccionar método de pago"

            # ================= CÁLCULOS =================
            subtotal = precio * cantidad
            total_final = subtotal + recargo - descuento
            litros_total = litros * cantidad

            venta_id = str(uuid.uuid4())
            item_id = venta_id + "_i"
            fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # ================= CAJERO =================
            if session.get("admin"):
                cajero_id = None
                cajero_nombre = "admin"
            else:
                cajero_id = session.get("cajero_id")
                cajero_nombre = session.get("nombre_cajero")

            # ================= VENTA =================
            ejecutar(cur, con, """
                INSERT INTO ventas (
                    id, fecha, total, recargo, descuento,
                    total_final, metodo_pago, cajero_id, cajero
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                venta_id,
                fecha,
                subtotal,
                recargo,
                descuento,
                total_final,
                metodo_pago,
                cajero_id,
                cajero_nombre
            ))

            # ================= ITEM =================
            ejecutar(cur, con, """
                INSERT INTO venta_items (
                    id, venta_id, producto_id, cantidad, litros_total, subtotal
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                item_id,
                venta_id,
                producto_id,
                cantidad,
                litros_total,
                subtotal
            ))

            # ================= STOCK =================
            ejecutar(cur, con, """
                UPDATE productos
                SET stock = stock - %s
                WHERE id = %s
            """, (cantidad, producto_id))

            con.commit()

            # ================= SYNC CLOUD =================
            sync_venta_to_cloud(
                venta_id,
                fecha,
                subtotal,
                recargo,
                descuento,
                total_final,
                metodo_pago,
                cajero_nombre,
                [{
                    "id": item_id,
                    "producto_id": producto_id,
                    "cantidad": cantidad,
                    "litros_total": litros_total,
                    "subtotal": subtotal
                }]
            )

            return f"✅ Venta realizada. Total: ${total_final}"

        except Exception as e:
            con.rollback()
            return f"❌ Error en venta: {e}"

        finally:
            con.close()

    # ================= GET =================
    ejecutar(cur, con, "SELECT * FROM productos")
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

        ejecutar(cur, con, """
            SELECT id, nombre, descripcion, precio
            FROM promos
            WHERE id=%s
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
    ejecutar(cur, con, """
        SELECT id, descripcion, precio, stock
        FROM productos
        WHERE UPPER(codigo)=%s
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

@app.route("/reporte_ventas")
def reporte_ventas():
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ No tenés permiso para ver reportes"

    con = get_db()
    cur = con.cursor()

    # ================= VENTAS GENERALES =================
    ejecutar(cur, con, """
        SELECT COUNT(*), COALESCE(SUM(total_final),0)
        FROM ventas
    """)
    total_ventas, total_dinero = cur.fetchone()

    # ================= UTILIDAD =================
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(total_final),0)
        FROM ventas
    """)
    utilidad = cur.fetchone()[0]

    # ================= VENTAS POR DÍA =================
    ejecutar(cur, con, """
        SELECT DATE(fecha),
               COUNT(*),
               COALESCE(SUM(total_final),0)
        FROM ventas
        GROUP BY DATE(fecha)
        ORDER BY DATE(fecha) DESC
        LIMIT 10
    """)
    ventas_dia = cur.fetchall()

    # ================= MÉTODOS DE PAGO =================
    ejecutar(cur, con, """
        SELECT 
            metodo_pago,
            COUNT(*) as cantidad,
            COALESCE(SUM(total),0) as subtotal,
            COALESCE(SUM(recargo),0) as recargo_total,
            COALESCE(SUM(descuento),0) as descuento_total,
            COALESCE(SUM(total_final),0) as total_final
        FROM ventas
        GROUP BY metodo_pago
        ORDER BY cantidad DESC
    """)
    metodos = cur.fetchall()

    # ================= LITROS VENDIDOS =================
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(vi.litros_total),0)
        FROM venta_items vi
    """)
    litros_vendidos = cur.fetchone()[0]

    # ================= STOCK ACTUAL =================
    ejecutar(cur, con, "SELECT COALESCE(SUM(stock),0) FROM productos")
    stock_actual = cur.fetchone()[0]

    # ================= AUDITORÍA STOCK =================
    ejecutar(cur, con, """
        SELECT p.descripcion,
               p.stock,
               COALESCE(SUM(v.cantidad),0) as vendidos
        FROM productos p
        LEFT JOIN venta_items v ON v.producto_id = p.id
        GROUP BY p.id
        ORDER BY vendidos DESC
    """)
    auditoria_stock = cur.fetchall()

    # ================= PRODUCTOS MÁS VENDIDOS =================
    ejecutar(cur, con, """
        SELECT 
            p.descripcion,
            COALESCE(SUM(v.cantidad),0) as total_unidades,
            COALESCE(SUM(v.subtotal),0) as total_vendido
        FROM venta_items v
        JOIN productos p ON v.producto_id = p.id
        GROUP BY p.descripcion
        ORDER BY total_unidades DESC
        LIMIT 10
    """)
    productos_vendidos = cur.fetchall()

    # ================= VENTAS POR DEPARTAMENTO =================
    ejecutar(cur, con, """
        SELECT 
            COALESCE(p.departamento, 'Sin asignar') AS departamento,
            COUNT(DISTINCT v.id) AS ventas,
            COALESCE(SUM(v.total_final), 0) AS total_vendido
        FROM ventas v
        JOIN venta_items vi ON v.id = vi.venta_id
        JOIN productos p ON vi.producto_id = p.id
        GROUP BY p.departamento
        ORDER BY total_vendido DESC
    """)

    ventas_departamento = cur.fetchall()  # ✅ CORRECTO

    # ================= HISTORIAL POR ITEMS =================
    ejecutar(cur, con, """
        SELECT 
            v.fecha,

            CASE 
                WHEN vi.producto_id LIKE 'promo_%' 
                    THEN pr.nombre
                ELSE 
                    p.descripcion
            END AS producto,

            vi.cantidad,
            vi.subtotal,
            v.metodo_pago,
            v.cajero

        FROM ventas v
        JOIN venta_items vi ON v.id = vi.venta_id

        LEFT JOIN productos p 
            ON vi.producto_id = p.id

        LEFT JOIN promos pr 
            ON vi.producto_id = 'promo_' || pr.id

        ORDER BY v.fecha DESC
    """)

    historial_items = cur.fetchall()  # ✅ CORRECTO

   
   

    con.close()

    return render_template(
        "reporte_ventas.html",
        total_ventas=total_ventas,
        total_dinero=total_dinero,
        utilidad=utilidad,
        ventas_dia=ventas_dia,
        metodos=metodos,
        litros_vendidos=litros_vendidos,
        stock_actual=stock_actual,
        auditoria_stock=auditoria_stock,
        productos_vendidos=productos_vendidos,
        ventas_departamento=ventas_departamento,
        historial_items=historial_items
    )
@app.route("/promo/agregar_producto", methods=["POST"])
def agregar_producto_a_promo():
    if not session.get("admin"):
        return "❌ Sin permiso"

    promo_id = request.form.get("promo_id")
    codigo = (request.form.get("codigo") or "").strip().upper()
    cantidad = int(request.form.get("cantidad") or 0)

    if cantidad <= 0:
        return "❌ Cantidad inválida"

    con = get_db()
    cur = con.cursor()

    # Buscar producto
    ejecutar(cur, con, """
        SELECT id, stock FROM productos WHERE UPPER(codigo)=%s
    """, (codigo,))

    prod = cur.fetchone()

    if not prod:
        con.close()
        return "❌ Producto no existe"

    producto_id, stock = prod

    if stock < cantidad:
        con.close()
        return f"❌ Stock insuficiente ({stock})"

    # Guardar relación
    ejecutar(cur, con, """
        INSERT INTO promo_items (promo_id, producto_id, cantidad)
        VALUES (%s, %s, %s)
    """, (promo_id, producto_id, cantidad))

    con.commit()
    con.close()

    return redirect("/promos")
@app.route("/debug_promos")
def debug_promos():
    con = get_db()
    cur = con.cursor()

    ejecutar(cur, con, "SELECT * FROM promos")
    data = cur.fetchall()

    con.close()
    return str(data)
@app.route("/ventas_ui")
def ventas_ui():
    cajero_nombre = session.get("nombre_cajero", "admin")
    print("DEBUG CAJERO_ID:", session.get("cajero_id"))
    print("DEBUG CAJERO_NOMBRE:", session.get("nombre_cajero"))

    con = get_db()
    cur = con.cursor()

    # ================= PRODUCTOS =================
    ejecutar(cur, con, "SELECT * FROM productos")
    productos = cur.fetchall()

    # ================= PROMOS =================
    ejecutar(cur, con, """
        SELECT id, nombre, descripcion, precio 
        FROM promos 
        WHERE activa=1
    """)
    promos = cur.fetchall()

    con.close()

    # ================= CARRITO =================
    carrito = session.get("carrito", [])

    subtotal = sum(
        float(i["precio"]) * int(i["cantidad"])
        for i in carrito
    )

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
    recargo = float(request.form.get("recargo") or 0)
    descuento = float(request.form.get("descuento") or 0)

    if not metodo_pago:
        return "❌ Selecciona método de pago"

    
   # ================= CAJERO =================
    if session.get("admin"):
        cajero_id = None
        cajero_nombre = "admin"
    else:
        cajero_id = session.get("cajero_id")
        cajero_nombre = session.get("nombre_cajero")

    # 🔥 validación solo para cajeros
    if not session.get("admin") and not cajero_nombre:
        return "❌ Error: sesión de cajero perdida"

    con = get_db()
    cur = con.cursor()

    venta_id = str(datetime.now().timestamp())
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ================= CALCULOS =================
    subtotal = sum(float(i["precio"]) * int(i["cantidad"]) for i in carrito)

    recargo_valor = subtotal * recargo / 100
    total_con_recargo = subtotal + recargo_valor

    descuento_valor = total_con_recargo * descuento / 100
    total_final = total_con_recargo - descuento_valor

    # ================= VENTA =================
    ejecutar(cur, con, """
        INSERT INTO ventas (
            id, fecha, total,
            recargo,
            descuento,
            total_final, metodo_pago, cajero_id, cajero
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        venta_id,
        fecha,
        subtotal,
        recargo_valor,
        descuento_valor,
        total_final,
        metodo_pago,
        cajero_id,
        cajero_nombre
    ))

    items_cloud = []

    # ================= ITEMS =================
    for item in carrito:

        cantidad_carrito = int(item["cantidad"])

        # ================= PROMOS =================
        if str(item["id"]).startswith("promo_"):

            promo_id = item["id"].replace("promo_", "")

            ejecutar(cur, con, """
                SELECT producto_id, cantidad
                FROM promo_items
                WHERE promo_id=%s
            """, (promo_id,))

            productos_promo = cur.fetchall()

            for prod in productos_promo:
                producto_id = prod[0]
                cantidad_real = prod[1] * cantidad_carrito

                ejecutar(cur, con, """
                    UPDATE productos
                    SET stock = stock - %s
                    WHERE id = %s
                """, (cantidad_real, producto_id))

                ejecutar(cur, con, """
                    INSERT INTO venta_items (
                        id, venta_id, producto_id, cantidad, litros_total, subtotal
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    str(uuid.uuid4()),
                    venta_id,
                    producto_id,
                    cantidad_real,
                    0,
                    0
                ))

            continue

        # ================= PRODUCTOS NORMALES =================
        item_id = str(uuid.uuid4())

        cantidad = int(item["cantidad"])
        precio = float(item["precio"])
        subtotal_item = precio * cantidad

        ejecutar(cur, con, """
            INSERT INTO venta_items (id, venta_id, producto_id, cantidad, litros_total, subtotal)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            item_id,
            venta_id,
            item["id"],
            cantidad,
            0,
            subtotal_item
        ))

        ejecutar(cur, con, """
            UPDATE productos
            SET stock = stock - %s
            WHERE id = %s
        """, (cantidad, item["id"]))

        items_cloud.append({
            "id": item_id,
            "producto_id": item["id"],
            "cantidad": cantidad,
            "litros_total": 0,
            "subtotal": subtotal_item
        })

    con.commit()
    con.close()

    # ================= SYNC =================
    sync_venta_to_cloud(
        venta_id,
        fecha,
        subtotal,
        recargo_valor,
        descuento_valor,
        total_final,
        metodo_pago,
        cajero_nombre,
        items_cloud
    )

    # 🧹 limpiar carrito
    session["carrito"] = []

    return redirect("/ventas_ui")
@app.route("/login_cajero", methods=["GET", "POST"])
def login_cajero():
    if request.method == "POST":
        nombre = request.form.get("nombre")
        password = request.form.get("password")

        con = get_db()
        cur = con.cursor()

        ejecutar(cur, con, """
            SELECT id, usuario, rol,
                   puede_vender,
                   puede_ver_pedidos,
                   puede_ver_reportes,
                   puede_ver_stock,
                   puede_agregar_productos
            FROM cajeros 
            WHERE usuario=%s AND password=%s
        """, (nombre, password))

        cajero = cur.fetchone()
        con.close()

        if cajero:
            session["cajero_id"] = cajero[0]
            session["nombre_cajero"] = cajero[1]
            session["rol"] = "cajero"

            # 🔥 GUARDAR PERMISOS
            session["puede_vender"] = bool(cajero[3] or 0)
            session["puede_ver_pedidos"] = bool(cajero[4] or 0)
            session["puede_ver_reportes"] = bool(cajero[5] or 0)
            session["puede_ver_stock"] = bool(cajero[6] or 0)
            session["puede_agregar_productos"] = bool(cajero[7] or 0)

            print("PERMISOS:",
                  session["puede_vender"],
                  session["puede_ver_pedidos"],
                  session["puede_ver_reportes"],
                  session["puede_ver_stock"],
                  session["puede_agregar_productos"])

            return redirect("/dashboard_cajero")

        return "❌ Datos incorrectos"

    return render_template("login_cajero.html")

@app.route("/crear_cajero", methods=["GET", "POST"])
def crear_cajero():
    if not session.get("admin"):
        return redirect("/login")

    if request.method == "POST":
        usuario = request.form.get("nombre")
        password = request.form.get("password")

        if not usuario or not password:
            return "❌ Datos incompletos"

        con = get_db()
        cur = con.cursor()

        try:
            # ✅ USAR ejecutar (CLAVE)
            ejecutar(cur, con, """
                INSERT INTO cajeros (usuario, password, rol)
                VALUES (%s, %s, 'cajero')
            """, (usuario, password))

            con.commit()
            flash("✅ Cajero creado con éxito")

        except Exception as e:
            con.close()
            return f"❌ Error al crear cajero ya existe: {e}"

        con.close()
        return redirect("/dashboard")

    return render_template("crear_cajero.html")
@app.route("/cajeros")
def cajeros():
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    # ✅ corregido: usuario + ejecutar
    ejecutar(cur, con, "SELECT id, usuario, rol FROM cajeros")
    data = cur.fetchall()

    con.close()

    return render_template("cajeros.html", cajeros=data)
@app.route("/dashboard_cajero")
def dashboard_cajero():
    if not session.get("cajero_id"):
        return redirect("/login_cajero")

    con = get_db()
    cur = con.cursor()

    # ✅ usar ejecutar (por consistencia total)
    ejecutar(cur, con, "SELECT COUNT(*) FROM productos")
    productos = cur.fetchone()[0]

    ejecutar(cur, con, "SELECT COUNT(*) FROM pedidos")
    pedidos = cur.fetchone()[0]

    con.close()

    return render_template(
        "dashboard_cajero.html",
        productos=productos,
        pedidos=pedidos,
        nombre=session.get("nombre_cajero")
    )
@app.route("/stock", methods=["GET", "POST"])
def stock():
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ No tenés permiso"

    con = get_db()
    cur = con.cursor()

    # ================= EDITAR PRODUCTO =================
    if request.method == "POST":
        producto_id = request.form.get("id")
        descripcion = request.form.get("descripcion")
        precio = float(request.form.get("precio") or 0)
        stock_val = int(request.form.get("stock") or 0)

        if not producto_id:
            con.close()
            return "❌ ID inválido"

        try:
            ejecutar(cur, con, """
                UPDATE productos
                SET descripcion=%s,
                    precio=%s,
                    stock=%s
                WHERE id=%s
            """, (descripcion, precio, stock_val, producto_id))

            con.commit()

            # 🔥 OPCIONAL: sincronizar cambio a la nube
            if internet_ok():
                try:
                    con_cloud = get_db_cloud()
                    cur_cloud = con_cloud.cursor()

                    cur_cloud.execute("""
                        UPDATE productos
                        SET descripcion=%s,
                            precio=%s,
                            stock=%s
                        WHERE id=%s
                    """, (descripcion, precio, stock_val, producto_id))

                    con_cloud.commit()
                    con_cloud.close()

                except Exception as e:
                    print("⚠️ Error sync update producto:", e)

        except Exception as e:
            con.close()
            return f"❌ Error al actualizar: {e}"

    # ================= LISTAR PRODUCTOS =================
    ejecutar(cur, con, """
        SELECT id, codigo, descripcion, precio, stock
        FROM productos
        ORDER BY descripcion
    """)
    productos = cur.fetchall()

    con.close()

    return render_template("stock.html", productos=productos)

# ================== RUN ==================
if __name__ == "__main__":
    threading.Thread(target=sync_worker, daemon=True).start()
    app.run(debug=True)
   
