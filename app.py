
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
from datetime import datetime
from flask import jsonify
from werkzeug.security import check_password_hash
import os
from dotenv import load_dotenv  # <--- AGREGÁ ESTA LÍNEA ESPECÍFICAMENTE
import psycopg2
import sqlite3

# Ahora sí podés llamarla
load_dotenv()

# Este print te va a confirmar si está leyendo bien el host o si sigue en None
print(f"🌐 Intentando conectar a: {os.getenv('DB_CLOUD_HOST')}")




app = Flask(__name__)

# 🔥 CONFIGURACIÓN DE SEGURIDAD (Esto arregla el RuntimeError)
# El segundo valor es un "plan B" por si el .env no carga
app.secret_key = os.getenv("SECRET_KEY", "clave_de_emergencia_12345")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

#  Estados corregidos (IMPORTANTE)
ESTADOS_VALIDOS = ["pendiente", "enproceso", "entregado", "cancelado"]
import os
from werkzeug.utils import secure_filename

# Configuración de carpeta para fotos (Crea la carpeta 'static/productos' si no existe)
UPLOAD_FOLDER = 'static/productos'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)




def get_db_cloud():
    # Cambié los nombres para que coincidan EXACTO con tu archivo .env
    return psycopg2.connect(
        host=os.getenv("DB_CLOUD_HOST"),
        dbname=os.getenv("DB_CLOUD_NAME"),
        user=os.getenv("DB_CLOUD_USER"),
        password=os.getenv("DB_CLOUD_PASS"),
        port=os.getenv("DB_CLOUD_PORT", 6543),
        sslmode="require"
    )




def get_db_local():
    # Agregamos timeout de 30 segundos
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row 
    # Habilitamos el modo WAL para permitir lecturas y escrituras simultáneas
    con.execute("PRAGMA journal_mode=WAL;")
    return con

    
def sync_worker():
    print("🚀 WORKER INICIADO: MODO SEGURO (PUSH PRIORITARIO + ESCUDO)")
    while True:
        try:
            if not internet_ok():
                time.sleep(10)
                continue

            # --- 1. SUBIDA (PUSH) ---
            con = get_db_local()
            cur = con.cursor()
            cur.execute("SELECT id, tabla, data FROM sync_queue WHERE sync=0 LIMIT 50")
            rows = cur.fetchall()
            con.close()

            if rows:
                print(f"📦 Subiendo {len(rows)} cambios locales...")
                con_cloud = get_db_cloud()
                cur_cloud = con_cloud.cursor()

                for row in rows:
                    id_q, tabla, data_raw = row
                    data = json.loads(data_raw)

                    if tabla == "productos":
                        # Subida con refuerzo (UPDATE directo)
                        cur_cloud.execute("""
                            INSERT INTO productos(id, codigo, descripcion, litros, precio, stock, fecha, departamento, foto)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) 
                            ON CONFLICT (id) DO UPDATE SET stock=EXCLUDED.stock, precio=EXCLUDED.precio
                        """, (data["id"], data["codigo"], data["descripcion"], data["litros"], data["precio"], data["stock"], data["fecha"], data.get("departamento"), data.get("foto")))
                        
                        cur_cloud.execute("UPDATE productos SET stock = %s WHERE id = %s", (data["stock"], data["id"]))

                    elif tabla == "ventas":
                        cur_cloud.execute("INSERT INTO ventas(id, fecha, total, total_final, caja_id) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                         (data["id"], data["fecha"], data["total"], data.get("total_final"), data.get("caja_id")))

                    # Marcar como sincronizado localmente
                    with get_db_local() as cl:
                        cl.execute("UPDATE sync_queue SET sync=1 WHERE id=?", (id_q,))
                
                con_cloud.commit()
                con_cloud.close()
                print("✅ Cambios impactados en la nube. Esperando pausa de seguridad...")
                time.sleep(5) # Pausa para que la nube estabilice el dato
                continue # Saltamos la bajada en esta vuelta

            # --- 2. BAJADA (PULL) CON ESCUDO ---
            con_cloud = get_db_cloud()
            cur_cloud = con_cloud.cursor()
            tablas = ["productos", "cajeros", "caja", "ventas", "promos", "usuarios"]

            for t in tablas:
                cur_cloud.execute(f"SELECT * FROM {t}")
                cols = [desc[0] for desc in cur_cloud.description]
                rows_cloud = cur_cloud.fetchall()

                con_loc = get_db_local()
                cur_loc = con_loc.cursor()
                
                # BUSCAR IDs PENDIENTES (ESCUDO)
                cur_loc.execute("SELECT data FROM sync_queue WHERE tabla=? AND sync=0", (t,))
                pendientes = []
                for r in cur_loc.fetchall():
                    try:
                        d_json = json.loads(r[0]) # r[0] porque fetchall devuelve tuplas
                        pendientes.append(str(d_json.get('id')))
                    except: continue

                for rc in rows_cloud:
                    row_dict = dict(zip(cols, rc))
                    rid = str(row_dict.get('id'))

                    # 🛡️ Si el ID está en la cola esperando subir, NO lo pisamos con lo de la nube
                    if rid in pendientes:
                        continue 

                    # Conversión Decimal a Float
                    rp = [float(v) if isinstance(v, decimal.Decimal) else v for v in rc]
                    query = f"INSERT OR REPLACE INTO {t} ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))})"
                    cur_loc.execute(query, tuple(rp))
                
                con_loc.commit()
                con_loc.close()
            con_cloud.close()

        except Exception as e:
            print(f"🔥 ERROR SYNC: {e}")
        
        time.sleep(30)




def get_db():
    # 1. Si estamos en RENDER (nube), conectamos a Supabase usando variables de entorno
    if os.environ.get("RENDER"):
        try:
            return psycopg2.connect(
                host=os.getenv("DB_CLOUD_HOST"),
                dbname=os.getenv("DB_CLOUD_NAME"),
                user=os.getenv("DB_CLOUD_USER"),
                password=os.getenv("DB_CLOUD_PASS"),
                port=os.getenv("DB_CLOUD_PORT", 6543),
                sslmode="require"
            )
        except Exception as e:
            print(f"❌ Error conexión Supabase en Render: {e}")
            return None

    # 2. Si estamos en PC LOCAL, usamos SQLite
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL;")
    except:
        pass
    return con


def ejecutar(cur, conn, query, params=None):
    es_sqlite = isinstance(conn, sqlite3.Connection)

    # 🔥 adaptar placeholders según DB
    if es_sqlite:
        query = query.replace("%s", "?")

    try:
        #  SIEMPRE FORZAR TUPLA SI HAY PARAMS
        if params is not None:
            if not isinstance(params, (list, tuple)):
                params = (params,)
            cur.execute(query, params)
        else:
            cur.execute(query)

    except Exception as e:
        print("\n❌ ERROR SQL")
        print("DB:", "SQLITE" if es_sqlite else "POSTGRES")
        print("QUERY:", query)
        print("PARAMS:", params)
        print("ERROR:", e)
        raise
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

def sync_venta_to_cloud(venta_id, fecha, total, recargo, descuento, total_final, metodo_pago, cajero, items):
    try:
        # 1. Recuperar el caja_id de la sesión (Sin esto Supabase rechaza la venta)
        caja_id = session.get("caja_id")
        
        con = get_db_cloud()
        cur = con.cursor()

        # 2. INSERT EN VENTAS (Agregado caja_id para que no rebote)
        cur.execute("""
            INSERT INTO ventas(
                id, fecha, total, recargo, descuento, total_final, metodo_pago, cajero, caja_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            venta_id, fecha, total, recargo, descuento,
            total_final, metodo_pago, cajero, caja_id
        ))

        # 3. INSERT EN ITEMS
        for item in items:
            venta_item_id = item.get("id")
            producto_id = item.get("producto_id")
            
            if not venta_item_id or not producto_id:
                continue

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
                item.get("cantidad", 0),
                item.get("litros_total", 0),
                item.get("subtotal", 0)
            ))
            
            # 4. ACTUALIZAR STOCK EN LA NUBE
            cur.execute("""
                UPDATE productos 
                SET stock = stock - %s 
                WHERE id = %s
            """, (item.get("cantidad", 0), producto_id))

        con.commit()
        con.close()
        print(f"✅ Venta {venta_id} sincronizada en Supabase")

    except Exception as e:
        print(f"❌ Error crítico en sync_venta_to_cloud: {e}")
        # Si falla la nube, guardamos en la cola local para reintentar luego
        datos_venta = {
            "id": venta_id,
            "fecha": fecha,
            "total": total,
            "recargo": recargo,
            "descuento": descuento,
            "total_final": total_final,
            "metodo_pago": metodo_pago,
            "cajero": cajero,
            "caja_id": session.get("caja_id")
        }
        save_offline("ventas", "insert", datos_venta)
        
        # Guardar cada item offline también
        for item in items:
            item_fixed = dict(item)
            item_fixed["venta_id"] = venta_id
            save_offline("venta_items", "insert", item_fixed)
            

            

# --- VER EL CARRITO ---
@app.route("/carrito")
def ver_carrito():
    carrito = session.get("carrito_cliente", [])
    total = sum(item['precio'] * item['cantidad'] for item in carrito)
    return render_template("carrito.html", carrito=carrito, total=total)

# --- VACIAR CARRITO ---
@app.route("/carrito/vaciar")
def vaciar_carrito():
    session.pop("carrito_cliente", None)
    return redirect("/tienda")

@app.route("/")
def index():
    return render_template("index.html")
@app.route("/tienda")
def tienda():
    con = get_db()
    cur = con.cursor()
    # Traemos todos los campos incluyendo 'foto' al final
    ejecutar(cur, con, """
        SELECT id, descripcion, precio, stock, litros, departamento, fecha, codigo, foto 
        FROM productos 
        WHERE stock > 0
    """)
    productos = cur.fetchall()
    con.close()
    return render_template("tienda.html", productos=productos)


@app.route("/clientes/agregar", methods=["POST"])
def agregar_cliente():
    if not session.get("admin"):
        return redirect("/login")

    import uuid

    nombre = request.form.get("nombre")
    telefono = request.form.get("telefono")
    direccion = request.form.get("direccion")

    # 🔥 SOLUCIÓN AL ERROR
    password = str(uuid.uuid4())[:8]

    if not nombre:
        return "❌ Nombre obligatorio"

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, """
            INSERT INTO usuarios(nombre, telefono, direccion, password)
            VALUES (%s, %s, %s, %s)
        """, (nombre, telefono, direccion, password))

        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error: {e}"

    con.close()

    # 🔁 volver a la lista
    return redirect("/clientes")
@app.route("/eliminar_cajero/<id>") # Fijate que el nombre sea igual al del HTML
def eliminar_cajero(id):
    if not session.get("admin"):
        return "❌ No tenés permiso", 403

    con = get_db_local()
    cur = con.cursor()
    
    try:
        cur.execute("DELETE FROM cajeros WHERE id = ?", (id,))
        con.commit()
        
        # Opcional: También borrarlo de la nube si usas sync
        # save_offline("cajeros", "delete", {"id": id})
        
    except Exception as e:
        return f"❌ Error al eliminar: {e}"
    finally:
        con.close()

    return redirect("/cajeros") # Asegurate que esta ruta sea la que muestra la tabla



@app.route("/clientes/eliminar/<int:id>", methods=["POST"])
def eliminar_cliente(id):
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, "DELETE FROM usuarios WHERE id=%s", (id,))
        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error al eliminar: {e}"

    con.close()
    return redirect("/clientes")
@app.route("/clientes/editar/<int:id>", methods=["GET", "POST"])
def editar_cliente(id):
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    if request.method == "POST":
        nombre = request.form.get("nombre")
        telefono = request.form.get("telefono")
        direccion = request.form.get("direccion")

        try:
            ejecutar(cur, con, """
                UPDATE usuarios
                SET nombre=%s, telefono=%s, direccion=%s
                WHERE id=%s
            """, (nombre, telefono, direccion, id))

            con.commit()

        except Exception as e:
            con.close()
            return f"❌ Error: {e}"

        con.close()
        return redirect("/clientes")

    # GET → cargar datos
    ejecutar(cur, con, "SELECT * FROM usuarios WHERE id=%s", (id,))
    cliente = cur.fetchone()
    con.close()

    return render_template("editar_cliente.html", cliente=cliente)
from decimal import Decimal

@app.route("/litros", methods=["GET"])
def dashboard_litros():
    con = get_db()
    cur = con.cursor()
    
    # 🔹 Vendidos
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(vi.litros_total), 0) 
        FROM venta_items vi
    """)
    vendidos = cur.fetchone()[0] or 0

    # 🔹 Cargados
    ejecutar(cur, con, "SELECT COALESCE(SUM(litros), 0) FROM litros_control")
    cargados = cur.fetchone()[0] or 0

    # 🔹 Historial
    ejecutar(cur, con, "SELECT litros, fecha FROM litros_control ORDER BY id DESC LIMIT 20")
    historial = cur.fetchall()

    # 🔥 NORMALIZAR (LA CLAVE)
    cargados = Decimal(str(cargados))
    vendidos = Decimal(str(vendidos))

    diferencia = cargados - vendidos

    # 🔹 Para gráfico
    historial_json = [
        {"litros": float(h[0]), "fecha": h[1]} 
        for h in historial
    ]
    historial_json.reverse()

    con.close()

    return render_template(
        "dashboard_litros.html", 
        vendidos=float(vendidos), 
        cargados=float(cargados), 
        diferencia=float(diferencia), 
        historial=historial,
        historial_json=historial_json
    )

@app.route("/litros/agregar", methods=["POST"])
def agregar_litros():
    litros = float(request.form.get("litros") or 0)
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Guardar localmente
    con = get_db_local()
    cur = con.cursor()
    cur.execute("INSERT INTO litros_control (litros, fecha) VALUES (?, ?)", (litros, fecha))
    con.commit()
    con.close()

    # 🔥 SYNC: Mandar a la cola para Supabase
    save_offline("litros_control", "insert", {
        "litros": litros,
        "fecha": fecha
    })
    
    return redirect("/litros")

@app.route("/debug")
def debug():
    con = get_db()
    cur = con.cursor()

    ejecutar(cur, con, "SELECT * FROM productos")
    data = cur.fetchall()

    con.close()
    return str(data)
@app.route("/buscar_productos")
def buscar_productos():
    q = request.args.get("q", "").upper()

    con = get_db()
    cur = con.cursor()

    ejecutar(cur, con, """
        SELECT codigo, descripcion, precio
        FROM productos
        WHERE UPPER(descripcion) LIKE %s
        LIMIT 20
    """, (f"%{q}%",))

    data = [
        {
            "codigo": r[0],
            "descripcion": r[1],
            "precio": r[2]
        }
        for r in cur.fetchall()
    ]

    con.close()
    return jsonify(data)

# ================== LOGIN ADMIN ==================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password_ingresada = request.form.get("password")

        con = get_db()
        if con is None:
            return "❌ Error de conexión con la base de datos", 500
            
        cur = con.cursor()

        # Usamos %s y la función ejecutar se encarga de convertirlo a ? si es SQLite
        query = "SELECT password FROM usuarios WHERE nombre = %s"
        
        try:
            # Usamos la función 'ejecutar' que definiste antes para que sea compatible
            ejecutar(cur, con, query, ("admin",))
            usuario = cur.fetchone()
        except Exception as e:
            print(f"🔥 Error en query de login: {e}")
            return "❌ Error interno", 500
        finally:
            con.close()

        if usuario:
            # Obtenemos el hash de forma segura (funciona para SQLite y Postgres)
            # En Postgres/psycopg2 el resultado suele ser una tupla, en SQLite un Row
            password_hash = usuario[0] if isinstance(usuario, (tuple, list)) else usuario['password']

            if check_password_hash(password_hash, password_ingresada):
                session["admin"] = True
                return redirect("/dashboard")
            else:
                return "❌ Clave incorrecta"
        else:
            return "❌ El usuario admin no existe"

    return render_template("login.html")


@app.route("/logout")
def logout():
    # 1. Obtener el ID de la caja de la sesión actual
    caja_id = session.get("caja_id")
    
    # 2. Si hay un ID en sesión, verificar su estado en la DB
    if caja_id:
        con = get_db()
        cur = con.cursor()
        
        # Usamos 'ejecutar' y '%s' para que funcione en Postgres y SQLite
        ejecutar(cur, con, "SELECT estado FROM caja WHERE id = %s", (caja_id,))
        
        caja = cur.fetchone()
        con.close()

        # Extraemos el valor del estado de forma segura
        estado = caja[0] if caja and isinstance(caja, (list, tuple)) else (caja["estado"] if caja else None)

        # 🚨 Si la caja sigue ABIERTA → NO dejar salir (solo para cajeros)
        if estado == 'ABIERTA':
            return """
            <script>
                alert("⚠️ Debes cerrar TU caja antes de salir");
                window.location.href = "/dashboard_cajero";
            </script>
            """

    # ✅ Limpiar sesión (borra admin, cajero_id, etc.)
    session.clear()

    # 🎯 CAMBIO CLAVE: Redirigir a la raíz para ver los botones de colores
    return redirect("/")


@app.route("/caja/estado")
def estado_caja():
    # 1. Obtener el ID de la caja de la sesión actual
    caja_id = session.get("caja_id")

    if not caja_id:
        return jsonify({"abierta": False})

    con = get_db()
    cur = con.cursor()

    # 2. Buscar datos de ESTA caja específica
    ejecutar(cur, con, """
        SELECT id, monto_inicial
        FROM caja
        WHERE id = %s AND estado = 'ABIERTA'
    """, (caja_id,))

    caja = cur.fetchone()

    if not caja:
        con.close()
        return jsonify({"abierta": False})

    # 3. SUMAR SOLO LAS VENTAS DE ESTA CAJA (Aquí estaba el error)
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(total_final), 0)
        FROM ventas
        WHERE caja_id = %s
    """, (caja_id,))

    ventas = cur.fetchone()[0]

    con.close()

    return jsonify({
        "abierta": True,
        "apertura": float(caja[1]),
        "ventas": float(ventas or 0),
        "total_esperado": float(caja[1]) + float(ventas or 0)
    })

@app.route("/verificar_caja")
def verificar_caja():

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT id FROM caja WHERE estado='ABIERTA'")
    caja = cur.fetchone()

    con.close()

    return {"ABIERTA": bool(caja)}

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

    editar_id = request.args.get("editar")

    con = get_db()
    cur = con.cursor()

    # Lista de clientes
    ejecutar(cur, con, "SELECT * FROM usuarios")
    data = cur.fetchall()

    cliente_editar = None

    # Si viene ?editar=ID
    if editar_id:
        ejecutar(cur, con, "SELECT * FROM usuarios WHERE id=%s", (editar_id,))
        cliente_editar = cur.fetchone()

    con.close()

    return render_template(
        "clientes.html",
        clientes=data,
        cliente_editar=cliente_editar
    )
@app.route("/clientes/actualizar/<int:id>", methods=["POST"])
def actualizar_cliente(id):
    if not session.get("admin"):
        return redirect("/login")

    nombre = request.form.get("nombre")
    telefono = request.form.get("telefono")
    direccion = request.form.get("direccion")

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, """
            UPDATE usuarios
            SET nombre=%s, telefono=%s, direccion=%s
            WHERE id=%s
        """, (nombre, telefono, direccion, id))

        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error: {e}"

    con.close()
    return redirect("/clientes")
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
from datetime import datetime

@app.route("/reporte_ventas_cajero")
def reporte_ventas_cajero():
    if not session.get("admin") and not session.get("puede_ver_reportes"):
        return "❌ Sin permiso"

    con = get_db()
    cur = con.cursor()
    

    cajero = request.args.get("cajero")
    fecha = request.args.get("fecha")

    # 📅 SI NO HAY FECHA → HOY
    if not fecha:
        fecha = datetime.now().strftime("%Y-%m-%d")

    # ================= FILTROS =================
    filtros = []
    params = []

    # 📅 FILTRO POR FECHA (SIEMPRE)
    filtros.append("DATE(v.fecha) = %s")
    params.append(fecha)

    # 👤 FILTRO POR CAJERO (OPCIONAL)
    if cajero:
        filtros.append("v.cajero = %s")
        params.append(cajero)

    where = "WHERE " + " AND ".join(filtros)

    # ================= TOTAL VENTAS =================
    ejecutar(cur, con, f"""
        SELECT 
            COUNT(*),
            COALESCE(SUM(total),0),
            COALESCE(SUM(recargo),0),
            COALESCE(SUM(descuento),0),
            COALESCE(SUM(total_final),0)
        FROM ventas v
        {where}
    """, params)

    total_ventas, total_bruto, total_recargo, total_descuento, total_dinero = cur.fetchone()

    # ================= PRODUCTOS =================
    ejecutar(cur, con, f"""
        SELECT p.descripcion, SUM(vi.cantidad), SUM(vi.subtotal)
        FROM ventas v
        JOIN venta_items vi ON v.id = vi.venta_id
        JOIN productos p ON p.id = vi.producto_id
        {where}
        GROUP BY p.descripcion
        ORDER BY SUM(vi.cantidad) DESC
    """, params)

    productos = cur.fetchall()

    # ================= MÉTODOS DE PAGO =================
    ejecutar(cur, con, f"""
        SELECT metodo_pago,
            COUNT(*),
            COALESCE(SUM(total),0),
            COALESCE(SUM(recargo),0),
            COALESCE(SUM(descuento),0),
            COALESCE(SUM(total_final),0)
        FROM ventas v
        {where}
        GROUP BY metodo_pago
    """, params)

    metodos = cur.fetchall()

    # ================= AUDITORÍA STOCK (CORREGIDA) =================
    auditoria_params = [fecha]
    auditoria_sql = """
        SELECT p.descripcion, p.stock, COALESCE(SUM(vi.cantidad),0)
        FROM productos p
        LEFT JOIN venta_items vi ON p.id = vi.producto_id
        LEFT JOIN ventas v ON v.id = vi.venta_id
        AND DATE(v.fecha) = %s
    """

    if cajero:
        auditoria_sql += " AND v.cajero = %s"
        auditoria_params.append(cajero)

    auditoria_sql += " GROUP BY p.id"

    ejecutar(cur, con, auditoria_sql, auditoria_params)
    auditoria = cur.fetchall()

    # ================= LISTA CAJEROS =================
    ejecutar(cur, con, "SELECT DISTINCT cajero FROM ventas")
    cajeros = cur.fetchall()

    con.close()

    return render_template(
        "reporte_ventas_cajero.html",
        total_ventas=total_ventas,
        total_bruto=total_bruto,
        total_recargo=total_recargo,
        total_descuento=total_descuento,
        total_dinero=total_dinero,
        productos=productos,
        metodos=metodos,
        auditoria=auditoria,
        cajeros=cajeros,
        fecha=fecha
    )
@app.route("/promos/agregar", methods=["POST"])
def agregar_promo():
    if not session.get("admin"):
        return "❌ Sin permiso"

    import uuid
    # Usamos UUID solo si tu tabla local es TEXT. 
    # Si es INTEGER, SQLite dará error. 
    promo_id = str(uuid.uuid4()) 
    nombre = request.form.get("nombre")
    descripcion = request.form.get("descripcion")
    precio = float(request.form.get("precio") or 0)

    # USAMOS LOCAL para evitar el error de tipos si get_db() intenta ir a la nube directo
    con = get_db_local()
    cur = con.cursor()

    try:
        # Si tu tabla local tiene el ID como INTEGER, cambia promo_id por None 
        # o cambia la tabla a TEXT id.
        cur.execute("""
            INSERT INTO promos(id, nombre, descripcion, precio, activa)
            VALUES (?, ?, ?, ?, 1)
        """, (promo_id, nombre, descripcion, precio))

        con.commit()

        # 🔥 SYNC: Guardar en la cola para Supabase
        save_offline("promos", "insert", {
            "id": promo_id,
            "nombre": nombre,
            "descripcion": descripcion,
            "precio": precio,
            "activa": 1
        })

    except Exception as e:
        con.rollback()
        return f"❌ Error: {e}. Verificá si el ID de la tabla promos es TEXT o INTEGER."
    finally:
        con.close()

    return redirect("/promos")


import uuid
from datetime import datetime

@app.route("/productos/agregar", methods=["GET", "POST"])
def agregar_producto():
    # 🔐 VALIDACIÓN DE PERMISOS CORREGIDA
    permisos = session.get("permisos", {})
    es_admin = session.get("admin")
    tiene_permiso = permisos.get("agregar") == 1

    if not es_admin and not tiene_permiso:
        return "❌ No tenés permiso para agregar productos", 403

    if request.method == "POST":
        try:
            # Captura de datos del formulario
            codigo = (request.form.get("codigo") or "").strip().upper()
            descripcion = request.form.get("descripcion")
            litros = int(request.form.get("litros") or 0)
            precio = float(request.form.get("precio") or 0)
            stock = int(request.form.get("stock") or 0)
            departamento = request.form.get("departamento")
            
            # --- 🔥 NUEVO: MANEJO DE FOTO ---
            foto = request.files.get('foto')
            nombre_foto = "" # Valor por defecto si no suben nada
            
            if foto and foto.filename != '':
                # Aseguramos un nombre de archivo seguro y único
                extension = os.path.splitext(foto.filename)[1]
                nombre_foto = f"{codigo}_{str(uuid.uuid4())[:8]}{extension}"
                foto.save(os.path.join(UPLOAD_FOLDER, nombre_foto))

            if not codigo:
                return "❌ Código vacío"

            # Generamos un ID único y la fecha actual
            producto_id = str(uuid.uuid4()) 
            fecha = datetime.now().strftime("%Y-%m-%d")

            # ================= LOCAL (SQLite) =================
            con = get_db_local()
            cur = con.cursor()
            
            # Agregamos la columna 'foto' al INSERT
            cur.execute("""
                INSERT INTO productos (
                    id, codigo, descripcion, litros, precio, stock, fecha, departamento, foto
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (producto_id, codigo, descripcion, litros, precio, stock, fecha, departamento, nombre_foto))

            con.commit()
            con.close()

            # ================= SYNC (Cola de sincronización) =================
            data_producto = {
                "id": producto_id,
                "codigo": codigo,
                "descripcion": descripcion,
                "litros": litros,
                "precio": precio,
                "stock": stock,
                "fecha": fecha,
                "departamento": departamento,
                "foto": nombre_foto # 🔥 Enviamos el nombre de la foto a la nube
            }
            # Guardamos para que el worker lo suba a la nube después
            save_offline("productos", "insert", data_producto)

            flash("✅ Producto guardado localmente con éxito")
            return redirect("/productos/agregar")

        except sqlite3.IntegrityError:
            return "❌ El código de producto ya existe localmente"
        except Exception as e:
            return f"❌ Error: {e}"

    # Si es GET, mostramos el formulario
    return render_template("agregar_producto.html")


# ================== MIS PEDIDOS CLIENTE ==================
@app.route("/mis_pedidos")
def mis_pedidos():
    if not session.get("cliente_id"):
        return redirect("/login_cliente")

    # Intentamos conectar a la base de datos (Cloud si hay internet, Local si no)
    con = get_db() 
    cur = con.cursor()

    try:
        # 1. Traer promociones activas
        ejecutar(cur, con, "SELECT id, nombre, descripcion, precio FROM promos WHERE activa=1")
        promos = cur.fetchall()

        # 2. Traer pedidos del cliente actual
        # IMPORTANTE: Asegúrate de que cliente_id sea el mismo en ambas DBs
        ejecutar(cur, con, """
            SELECT p.id, pr.nombre, p.fecha, p.estado
            FROM pedidos p
            JOIN promos pr ON p.promo_id = pr.id
            WHERE p.cliente_id=%s
            ORDER BY p.id DESC
        """, (session["cliente_id"],))

        pedidos = cur.fetchall()

    except Exception as e:
        # Si algo falla (ej. error de conexión a la nube a mitad de camino)
        if con: con.close()
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

    con = get_db_local()
    cur = con.cursor()

    try:
        # Usamos un ID único para evitar choques en la nube
        pedido_id = str(uuid.uuid4()) 
        cur.execute("""
            INSERT INTO pedidos (id, cliente_id, promo_id, fecha, estado)
            VALUES (?, ?, ?, ?, 'pendiente')
        """, (pedido_id, session["cliente_id"], promo_id, fecha))
        con.commit()

        # 🔥 SYNC: A la cola
        save_offline("pedidos", "insert", {
            "id": pedido_id,
            "cliente_id": session["cliente_id"],
            "promo_id": promo_id,
            "fecha": fecha,
            "estado": "pendiente"
        })

    except Exception as e:
        con.rollback()
        return f"❌ Error al crear pedido: {e}"
    finally:
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
        vender = 1 if request.form.get("vender") else 0
        pedidos = 1 if request.form.get("pedidos") else 0
        reportes = 1 if request.form.get("reportes") else 0
        stock = 1 if request.form.get("stock") else 0
        agregar = 1 if request.form.get("agregar_productos") else 0

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

    if not cajero:
        return "❌ Cajero no encontrado"

    return render_template("permisos_cajero.html", cajero=cajero)

# ================== CAMBIAR ESTADO ==================
@app.route("/pedido/estado/<id>/<estado>")
def cambiar_estado_pedido(id, estado):

    # 🔐 Permisos
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ No tenés permiso"

    # ✅ Estados válidos (los mismos que usás en el HTML)
    ESTADOS_VALIDOS = ["pendiente", "enproceso", "entregado", "cancelado"]

    if estado not in ESTADOS_VALIDOS:
        return "❌ Estado inválido"

    # 🔄 Si querés guardar con espacio en DB
    mapa_estados = {
        "pendiente": "pendiente",
        "enproceso": "en proceso",
        "entregado": "entregado",
        "cancelado": "cancelado"
    }

    estado_db = mapa_estados[estado]

    con = get_db_local()
    cur = con.cursor()

    try:
        cur.execute("""
            UPDATE pedidos 
            SET estado = ? 
            WHERE id = ?
        """, (estado_db, id))

        con.commit()

        # 🔥 SYNC (si lo usás)
        save_offline("pedidos", "update", {
            "id": id,
            "estado": estado_db
        })

    except Exception as e:
        con.rollback()
        return f"❌ Error: {e}"

    finally:
        con.close()

    return redirect("/pedidos")

# ================== VENTAS ==================
@app.route("/ventas", methods=["GET", "POST"])
def ventas():
    if not (session.get("admin") or session.get("cajero_id")):
        return redirect("/")

    if request.method == "POST":
        # CONEXIÓN MANUAL PARA EVITAR INTERFERENCIAS
        con = sqlite3.connect(DB_PATH, timeout=30)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        try:
            codigo = (request.form.get("codigo") or "").strip().upper()
            cantidad = int(request.form.get("cantidad") or 0)
            print(f"DEBUG: Buscando código [{codigo}]")

            # 1. Buscar producto
            cur.execute("SELECT * FROM productos WHERE UPPER(TRIM(codigo)) = ?", (codigo,))
            prod = cur.fetchone()
            
            if not prod:
                print("DEBUG: Producto NO encontrado")
                return f"❌ El código {codigo} no existe localmente."

            p = dict(prod)
            id_real = p['id']
            stock_anterior = p['stock']
            nuevo_stock = stock_anterior - cantidad
            print(f"DEBUG: Producto [{p['descripcion']}] | Stock: {stock_anterior} -> Nuevo: {nuevo_stock}")

            # 2. INTENTO DE DESCUENTO
            cur.execute("UPDATE productos SET stock = ? WHERE id = ?", (nuevo_stock, id_real))
            
            # 3. VERIFICAR SI SQLITE REALMENTE CAMBIÓ LA FILA
            if cur.rowcount == 0:
                print("DEBUG: El UPDATE se ejecutó pero cur.rowcount es 0 (No se modificó nada)")
                return "❌ Error: La base de datos no permitió actualizar el stock."

            # 4. FORZAR GUARDADO
            con.commit()
            
            # 5. VERIFICACIÓN POST-COMMIT (Para estar 100% seguros)
            cur.execute("SELECT stock FROM productos WHERE id = ?", (id_real,))
            confirmacion = cur.fetchone()[0]
            print(f"DEBUG: Stock en disco después del commit: {confirmacion}")

            if confirmacion != nuevo_stock:
                print("DEBUG: ¡ERROR CRÍTICO! El stock en disco no coincide con el nuevo stock.")

            # 6. ENCOLAR PARA SUPABASE
            p['stock'] = nuevo_stock
            cur.execute("INSERT INTO sync_queue (tabla, data, sync) VALUES (?, ?, 0)", 
                        ("productos", json.dumps(p), 0))
            con.commit()

            return f"✅ Venta exitosa. Stock: {nuevo_stock}"

        except Exception as e:
            con.rollback()
            print(f"DEBUG: EXCEPCIÓN CACHADA: {e}")
            return f"❌ Error crítico: {e}"
        finally:
            con.close()

    # Carga normal de la página
    con = get_db_local()
    productos = con.execute("SELECT * FROM productos ORDER BY descripcion ASC").fetchall()
    con.close()
    return render_template("ventas.html", productos=productos)



@app.route("/admin/cierres_caja")
def ver_cierres_caja():
    # 1. Quitamos la validación de admin para probar que el problema no sea la sesión
    try:
        con = get_db()
        cur = con.cursor()
        
        # 2. Usamos el SQL correcto para tu tabla
        ejecutar(cur, con, """
            SELECT id, cajero, fecha_apertura, fecha_cierre, monto_inicial, cierre, diferencia, estado
            FROM caja
            ORDER BY fecha_apertura DESC
        """)
        
        cierres = cur.fetchall()
        con.close()
        
        # 3. Forzamos el renderizado
        return render_template("admin_cierres.html", cierres=cierres)
        
    except Exception as e:
        print(f"ERROR EN REPORTE: {e}")
        return f"Error al cargar el reporte: {e}"





@app.route("/litros")
def ver_litros():

    con = get_db()   # 🔥 IMPORTANTE
    cur = con.cursor()

    # 📜 HISTORIAL
    cur.execute("SELECT litros, fecha FROM litros_control ORDER BY fecha ASC")
    historial = cur.fetchall()

    # 🔵 CARGADOS
    cargados = sum(float(row[0]) for row in historial)

    # 🟢 VENDIDOS (AHORA SÍ VA A FUNCIONAR)
    cur.execute("""
        SELECT COALESCE(SUM(litros_total), 0)
        FROM venta_items
    """)
    vendidos = cur.fetchone()[0]

    # ⚖️ DIFERENCIA
    diferencia = cargados - vendidos

    con.close()

    historial_json = [
        {"litros": float(row[0]), "fecha": row[1]}
        for row in historial
    ]

    return render_template(
        "litros_dashboard.html",
        historial=historial,
        historial_json=historial_json,
        vendidos=vendidos,
        cargados=cargados,
        diferencia=diferencia
    )
@app.route("/caja/estado")
def caja_estado():

    caja_id = session.get("caja_id")

    if not caja_id:
        return {"ABIERTA": False}

    con = get_db()
    cur = con.cursor()

    # 🔥 buscar caja actual
    ejecutar(cur, con, """
        SELECT monto_inicial
        FROM caja
        WHERE id=%s AND estado='ABIERTA'
    """, (caja_id,))

    caja = cur.fetchone()

    if not caja:
        con.close()
        return {"ABIERTA": False}

    monto_inicial = float(caja[0] or 0)

    # 🔥 SOLO ventas de ESTA caja
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(total_final),0)
        FROM ventas
        WHERE caja_id = %s
    """, (caja_id,))

    ventas = float(cur.fetchone()[0] or 0)

    con.close()

    return {
        "ABIERTA": True,
        "apertura": monto_inicial,
        "ventas": ventas,
        "total_esperado": monto_inicial + ventas
    }

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

    # ================= FILTROS =================
    desde = request.args.get("desde")
    hasta = request.args.get("hasta")

    if not desde:
        desde = datetime.now().strftime("%Y-%m-%d")

    if not hasta:
        hasta = datetime.now().strftime("%Y-%m-%d")

    where = "WHERE DATE(v.fecha) BETWEEN %s AND %s"
    params = (desde, hasta)  # 🔥 SIEMPRE TUPLA

    # ================= VENTAS GENERALES =================
    ejecutar(cur, con, f"""
        SELECT COUNT(*), COALESCE(SUM(v.total_final),0)
        FROM ventas v
        {where}
    """, params)
    total_ventas, total_dinero = cur.fetchone()

    # ================= UTILIDAD =================
    ejecutar(cur, con, f"""
        SELECT COALESCE(SUM(v.total_final),0)
        FROM ventas v
        {where}
    """, params)
    utilidad = cur.fetchone()[0]

    # ================= VENTAS POR DÍA =================
    ejecutar(cur, con, f"""
        SELECT DATE(v.fecha),
               COUNT(*),
               COALESCE(SUM(v.total_final),0)
        FROM ventas v
        {where}
        GROUP BY DATE(v.fecha)
        ORDER BY DATE(v.fecha) DESC
    """, params)
    ventas_dia = cur.fetchall()

    # ================= MÉTODOS DE PAGO =================
    ejecutar(cur, con, f"""
        SELECT 
            v.metodo_pago,
            COUNT(*),
            COALESCE(SUM(v.total),0),
            COALESCE(SUM(v.recargo),0),
            COALESCE(SUM(v.descuento),0),
            COALESCE(SUM(v.total_final),0)
        FROM ventas v
        {where}
        GROUP BY v.metodo_pago
        ORDER BY COUNT(*) DESC
    """, params)
    metodos = cur.fetchall()

    # ================= LITROS VENDIDOS =================
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(vi.litros_total),0)
        FROM venta_items vi
        JOIN ventas v ON v.id = vi.venta_id
        WHERE DATE(v.fecha) BETWEEN %s AND %s
    """, params)
    litros_vendidos = cur.fetchone()[0]

    # ================= STOCK ACTUAL =================
    ejecutar(cur, con, "SELECT COALESCE(SUM(stock),0) FROM productos")
    stock_actual = cur.fetchone()[0]

    # ================= AUDITORÍA STOCK (FIX REAL) =================
    ejecutar(cur, con, """
        SELECT 
            p.descripcion,
            p.stock,
            COALESCE(SUM(vi.cantidad),0)
        FROM productos p
        LEFT JOIN venta_items vi ON vi.producto_id = p.id
        LEFT JOIN ventas v ON v.id = vi.venta_id
        WHERE (DATE(v.fecha) BETWEEN %s AND %s OR v.fecha IS NULL)
        GROUP BY p.id
        ORDER BY COALESCE(SUM(vi.cantidad),0) DESC
    """, params)
    auditoria_stock = cur.fetchall()

    # ================= PRODUCTOS MÁS VENDIDOS =================
    ejecutar(cur, con, """
        SELECT 
            p.descripcion,
            COALESCE(SUM(vi.cantidad),0),
            COALESCE(SUM(vi.subtotal),0)
        FROM venta_items vi
        JOIN productos p ON vi.producto_id = p.id
        JOIN ventas v ON v.id = vi.venta_id
        WHERE DATE(v.fecha) BETWEEN %s AND %s
        GROUP BY p.descripcion
        ORDER BY SUM(vi.cantidad) DESC
        LIMIT 10
    """, params)
    productos_vendidos = cur.fetchall()

    # ================= VENTAS POR DEPARTAMENTO =================
    ejecutar(cur, con, """
        SELECT 
            COALESCE(p.departamento, 'Sin asignar'),
            COUNT(DISTINCT v.id),
            COALESCE(SUM(v.total_final), 0)
        FROM ventas v
        JOIN venta_items vi ON v.id = vi.venta_id
        JOIN productos p ON vi.producto_id = p.id
        WHERE DATE(v.fecha) BETWEEN %s AND %s
        GROUP BY p.departamento
        ORDER BY SUM(v.total_final) DESC
    """, params)
    ventas_departamento = cur.fetchall()

    # ================= HISTORIAL DE ITEMS (FIX PRO) =================
    ejecutar(cur, con, """
        SELECT 
            v.fecha,
            COALESCE(p.descripcion, pr.nombre),
            vi.cantidad,
            vi.subtotal,
            v.metodo_pago,
            v.cajero
        FROM ventas v
        JOIN venta_items vi ON v.id = vi.venta_id
        LEFT JOIN productos p ON vi.producto_id = p.id
        LEFT JOIN promos pr ON vi.producto_id = 'promo_' || pr.id
        WHERE DATE(v.fecha) BETWEEN %s AND %s
        ORDER BY v.fecha DESC
    """, params)
    historial_items = cur.fetchall()

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
        historial_items=historial_items,
        desde=desde,
        hasta=hasta
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
from datetime import datetime
from flask import jsonify
from werkzeug.security import check_password_hash
import os
from dotenv import load_dotenv  # <--- AGREGÁ ESTA LÍNEA ESPECÍFICAMENTE
import psycopg2
import sqlite3

# Ahora sí podés llamarla
load_dotenv()

# Este print te va a confirmar si está leyendo bien el host o si sigue en None
print(f"🌐 Intentando conectar a: {os.getenv('DB_CLOUD_HOST')}")




app = Flask(__name__)

# 🔥 CONFIGURACIÓN DE SEGURIDAD (Esto arregla el RuntimeError)
# El segundo valor es un "plan B" por si el .env no carga
app.secret_key = os.getenv("SECRET_KEY", "clave_de_emergencia_12345")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

#  Estados corregidos (IMPORTANTE)
ESTADOS_VALIDOS = ["pendiente", "enproceso", "entregado", "cancelado"]
import os
from werkzeug.utils import secure_filename

# Configuración de carpeta para fotos (Crea la carpeta 'static/productos' si no existe)
UPLOAD_FOLDER = 'static/productos'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)




def get_db_cloud():
    # Cambié los nombres para que coincidan EXACTO con tu archivo .env
    return psycopg2.connect(
        host=os.getenv("DB_CLOUD_HOST"),
        dbname=os.getenv("DB_CLOUD_NAME"),
        user=os.getenv("DB_CLOUD_USER"),
        password=os.getenv("DB_CLOUD_PASS"),
        port=os.getenv("DB_CLOUD_PORT", 6543),
        sslmode="require"
    )




def get_db_local():
    # Agregamos timeout de 30 segundos
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row 
    # Habilitamos el modo WAL para permitir lecturas y escrituras simultáneas
    con.execute("PRAGMA journal_mode=WAL;")
    return con

    
def sync_worker():
    print("🚀 WORKER INICIADO: MODO SEGURO (PUSH PRIORITARIO + ESCUDO)")
    while True:
        try:
            if not internet_ok():
                time.sleep(10)
                continue

            # --- 1. SUBIDA (PUSH) ---
            con = get_db_local()
            cur = con.cursor()
            cur.execute("SELECT id, tabla, data FROM sync_queue WHERE sync=0 LIMIT 50")
            rows = cur.fetchall()
            con.close()

            if rows:
                print(f"📦 Subiendo {len(rows)} cambios locales...")
                con_cloud = get_db_cloud()
                cur_cloud = con_cloud.cursor()

                for row in rows:
                    id_q, tabla, data_raw = row
                    data = json.loads(data_raw)

                    if tabla == "productos":
                        # Subida con refuerzo (UPDATE directo)
                        cur_cloud.execute("""
                            INSERT INTO productos(id, codigo, descripcion, litros, precio, stock, fecha, departamento, foto)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) 
                            ON CONFLICT (id) DO UPDATE SET stock=EXCLUDED.stock, precio=EXCLUDED.precio
                        """, (data["id"], data["codigo"], data["descripcion"], data["litros"], data["precio"], data["stock"], data["fecha"], data.get("departamento"), data.get("foto")))
                        
                        cur_cloud.execute("UPDATE productos SET stock = %s WHERE id = %s", (data["stock"], data["id"]))

                    elif tabla == "ventas":
                        cur_cloud.execute("INSERT INTO ventas(id, fecha, total, total_final, caja_id) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                         (data["id"], data["fecha"], data["total"], data.get("total_final"), data.get("caja_id")))

                    # Marcar como sincronizado localmente
                    with get_db_local() as cl:
                        cl.execute("UPDATE sync_queue SET sync=1 WHERE id=?", (id_q,))
                
                con_cloud.commit()
                con_cloud.close()
                print("✅ Cambios impactados en la nube. Esperando pausa de seguridad...")
                time.sleep(5) # Pausa para que la nube estabilice el dato
                continue # Saltamos la bajada en esta vuelta

            # --- 2. BAJADA (PULL) CON ESCUDO ---
            con_cloud = get_db_cloud()
            cur_cloud = con_cloud.cursor()
            tablas = ["productos", "cajeros", "caja", "ventas", "promos", "usuarios"]

            for t in tablas:
                cur_cloud.execute(f"SELECT * FROM {t}")
                cols = [desc[0] for desc in cur_cloud.description]
                rows_cloud = cur_cloud.fetchall()

                con_loc = get_db_local()
                cur_loc = con_loc.cursor()
                
                # BUSCAR IDs PENDIENTES (ESCUDO)
                cur_loc.execute("SELECT data FROM sync_queue WHERE tabla=? AND sync=0", (t,))
                pendientes = []
                for r in cur_loc.fetchall():
                    try:
                        d_json = json.loads(r[0]) # r[0] porque fetchall devuelve tuplas
                        pendientes.append(str(d_json.get('id')))
                    except: continue

                for rc in rows_cloud:
                    row_dict = dict(zip(cols, rc))
                    rid = str(row_dict.get('id'))

                    # 🛡️ Si el ID está en la cola esperando subir, NO lo pisamos con lo de la nube
                    if rid in pendientes:
                        continue 

                    # Conversión Decimal a Float
                    rp = [float(v) if isinstance(v, decimal.Decimal) else v for v in rc]
                    query = f"INSERT OR REPLACE INTO {t} ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))})"
                    cur_loc.execute(query, tuple(rp))
                
                con_loc.commit()
                con_loc.close()
            con_cloud.close()

        except Exception as e:
            print(f"🔥 ERROR SYNC: {e}")
        
        time.sleep(30)




def get_db():
    # 1. Si estamos en RENDER (nube), conectamos a Supabase usando variables de entorno
    if os.environ.get("RENDER"):
        try:
            return psycopg2.connect(
                host=os.getenv("DB_CLOUD_HOST"),
                dbname=os.getenv("DB_CLOUD_NAME"),
                user=os.getenv("DB_CLOUD_USER"),
                password=os.getenv("DB_CLOUD_PASS"),
                port=os.getenv("DB_CLOUD_PORT", 6543),
                sslmode="require"
            )
        except Exception as e:
            print(f"❌ Error conexión Supabase en Render: {e}")
            return None

    # 2. Si estamos en PC LOCAL, usamos SQLite
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL;")
    except:
        pass
    return con


def ejecutar(cur, conn, query, params=None):
    es_sqlite = isinstance(conn, sqlite3.Connection)

    # 🔥 adaptar placeholders según DB
    if es_sqlite:
        query = query.replace("%s", "?")

    try:
        #  SIEMPRE FORZAR TUPLA SI HAY PARAMS
        if params is not None:
            if not isinstance(params, (list, tuple)):
                params = (params,)
            cur.execute(query, params)
        else:
            cur.execute(query)

    except Exception as e:
        print("\n❌ ERROR SQL")
        print("DB:", "SQLITE" if es_sqlite else "POSTGRES")
        print("QUERY:", query)
        print("PARAMS:", params)
        print("ERROR:", e)
        raise
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

def sync_venta_to_cloud(venta_id, fecha, total, recargo, descuento, total_final, metodo_pago, cajero, items):
    try:
        # 1. Recuperar el caja_id de la sesión (Sin esto Supabase rechaza la venta)
        caja_id = session.get("caja_id")
        
        con = get_db_cloud()
        cur = con.cursor()

        # 2. INSERT EN VENTAS (Agregado caja_id para que no rebote)
        cur.execute("""
            INSERT INTO ventas(
                id, fecha, total, recargo, descuento, total_final, metodo_pago, cajero, caja_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            venta_id, fecha, total, recargo, descuento,
            total_final, metodo_pago, cajero, caja_id
        ))

        # 3. INSERT EN ITEMS
        for item in items:
            venta_item_id = item.get("id")
            producto_id = item.get("producto_id")
            
            if not venta_item_id or not producto_id:
                continue

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
                item.get("cantidad", 0),
                item.get("litros_total", 0),
                item.get("subtotal", 0)
            ))
            
            # 4. ACTUALIZAR STOCK EN LA NUBE
            cur.execute("""
                UPDATE productos 
                SET stock = stock - %s 
                WHERE id = %s
            """, (item.get("cantidad", 0), producto_id))

        con.commit()
        con.close()
        print(f"✅ Venta {venta_id} sincronizada en Supabase")

    except Exception as e:
        print(f"❌ Error crítico en sync_venta_to_cloud: {e}")
        # Si falla la nube, guardamos en la cola local para reintentar luego
        datos_venta = {
            "id": venta_id,
            "fecha": fecha,
            "total": total,
            "recargo": recargo,
            "descuento": descuento,
            "total_final": total_final,
            "metodo_pago": metodo_pago,
            "cajero": cajero,
            "caja_id": session.get("caja_id")
        }
        save_offline("ventas", "insert", datos_venta)
        
        # Guardar cada item offline también
        for item in items:
            item_fixed = dict(item)
            item_fixed["venta_id"] = venta_id
            save_offline("venta_items", "insert", item_fixed)
            

            

# --- VER EL CARRITO ---
@app.route("/carrito")
def ver_carrito():
    carrito = session.get("carrito_cliente", [])
    total = sum(item['precio'] * item['cantidad'] for item in carrito)
    return render_template("carrito.html", carrito=carrito, total=total)

# --- VACIAR CARRITO ---
@app.route("/carrito/vaciar")
def vaciar_carrito():
    session.pop("carrito_cliente", None)
    return redirect("/tienda")

@app.route("/")
def index():
    return render_template("index.html")
@app.route("/tienda")
def tienda():
    con = get_db()
    cur = con.cursor()
    # Traemos todos los campos incluyendo 'foto' al final
    ejecutar(cur, con, """
        SELECT id, descripcion, precio, stock, litros, departamento, fecha, codigo, foto 
        FROM productos 
        WHERE stock > 0
    """)
    productos = cur.fetchall()
    con.close()
    return render_template("tienda.html", productos=productos)


@app.route("/clientes/agregar", methods=["POST"])
def agregar_cliente():
    if not session.get("admin"):
        return redirect("/login")

    import uuid

    nombre = request.form.get("nombre")
    telefono = request.form.get("telefono")
    direccion = request.form.get("direccion")

    # 🔥 SOLUCIÓN AL ERROR
    password = str(uuid.uuid4())[:8]

    if not nombre:
        return "❌ Nombre obligatorio"

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, """
            INSERT INTO usuarios(nombre, telefono, direccion, password)
            VALUES (%s, %s, %s, %s)
        """, (nombre, telefono, direccion, password))

        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error: {e}"

    con.close()

    # 🔁 volver a la lista
    return redirect("/clientes")
@app.route("/eliminar_cajero/<id>") # Fijate que el nombre sea igual al del HTML
def eliminar_cajero(id):
    if not session.get("admin"):
        return "❌ No tenés permiso", 403

    con = get_db_local()
    cur = con.cursor()
    
    try:
        cur.execute("DELETE FROM cajeros WHERE id = ?", (id,))
        con.commit()
        
        # Opcional: También borrarlo de la nube si usas sync
        # save_offline("cajeros", "delete", {"id": id})
        
    except Exception as e:
        return f"❌ Error al eliminar: {e}"
    finally:
        con.close()

    return redirect("/cajeros") # Asegurate que esta ruta sea la que muestra la tabla



@app.route("/clientes/eliminar/<int:id>", methods=["POST"])
def eliminar_cliente(id):
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, "DELETE FROM usuarios WHERE id=%s", (id,))
        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error al eliminar: {e}"

    con.close()
    return redirect("/clientes")
@app.route("/clientes/editar/<int:id>", methods=["GET", "POST"])
def editar_cliente(id):
    if not session.get("admin"):
        return redirect("/login")

    con = get_db()
    cur = con.cursor()

    if request.method == "POST":
        nombre = request.form.get("nombre")
        telefono = request.form.get("telefono")
        direccion = request.form.get("direccion")

        try:
            ejecutar(cur, con, """
                UPDATE usuarios
                SET nombre=%s, telefono=%s, direccion=%s
                WHERE id=%s
            """, (nombre, telefono, direccion, id))

            con.commit()

        except Exception as e:
            con.close()
            return f"❌ Error: {e}"

        con.close()
        return redirect("/clientes")

    # GET → cargar datos
    ejecutar(cur, con, "SELECT * FROM usuarios WHERE id=%s", (id,))
    cliente = cur.fetchone()
    con.close()

    return render_template("editar_cliente.html", cliente=cliente)
from decimal import Decimal

@app.route("/litros", methods=["GET"])
def dashboard_litros():
    con = get_db()
    cur = con.cursor()
    
    # 🔹 Vendidos
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(vi.litros_total), 0) 
        FROM venta_items vi
    """)
    vendidos = cur.fetchone()[0] or 0

    # 🔹 Cargados
    ejecutar(cur, con, "SELECT COALESCE(SUM(litros), 0) FROM litros_control")
    cargados = cur.fetchone()[0] or 0

    # 🔹 Historial
    ejecutar(cur, con, "SELECT litros, fecha FROM litros_control ORDER BY id DESC LIMIT 20")
    historial = cur.fetchall()

    # 🔥 NORMALIZAR (LA CLAVE)
    cargados = Decimal(str(cargados))
    vendidos = Decimal(str(vendidos))

    diferencia = cargados - vendidos

    # 🔹 Para gráfico
    historial_json = [
        {"litros": float(h[0]), "fecha": h[1]} 
        for h in historial
    ]
    historial_json.reverse()

    con.close()

    return render_template(
        "dashboard_litros.html", 
        vendidos=float(vendidos), 
        cargados=float(cargados), 
        diferencia=float(diferencia), 
        historial=historial,
        historial_json=historial_json
    )

@app.route("/litros/agregar", methods=["POST"])
def agregar_litros():
    litros = float(request.form.get("litros") or 0)
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Guardar localmente
    con = get_db_local()
    cur = con.cursor()
    cur.execute("INSERT INTO litros_control (litros, fecha) VALUES (?, ?)", (litros, fecha))
    con.commit()
    con.close()

    # 🔥 SYNC: Mandar a la cola para Supabase
    save_offline("litros_control", "insert", {
        "litros": litros,
        "fecha": fecha
    })
    
    return redirect("/litros")

@app.route("/debug")
def debug():
    con = get_db()
    cur = con.cursor()

    ejecutar(cur, con, "SELECT * FROM productos")
    data = cur.fetchall()

    con.close()
    return str(data)
@app.route("/buscar_productos")
def buscar_productos():
    q = request.args.get("q", "").upper()

    con = get_db()
    cur = con.cursor()

    ejecutar(cur, con, """
        SELECT codigo, descripcion, precio
        FROM productos
        WHERE UPPER(descripcion) LIKE %s
        LIMIT 20
    """, (f"%{q}%",))

    data = [
        {
            "codigo": r[0],
            "descripcion": r[1],
            "precio": r[2]
        }
        for r in cur.fetchall()
    ]

    con.close()
    return jsonify(data)

# ================== LOGIN ADMIN ==================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password_ingresada = request.form.get("password")

        con = get_db()
        if con is None:
            return "❌ Error de conexión con la base de datos", 500
            
        cur = con.cursor()

        # Usamos %s y la función ejecutar se encarga de convertirlo a ? si es SQLite
        query = "SELECT password FROM usuarios WHERE nombre = %s"
        
        try:
            # Usamos la función 'ejecutar' que definiste antes para que sea compatible
            ejecutar(cur, con, query, ("admin",))
            usuario = cur.fetchone()
        except Exception as e:
            print(f"🔥 Error en query de login: {e}")
            return "❌ Error interno", 500
        finally:
            con.close()

        if usuario:
            # Obtenemos el hash de forma segura (funciona para SQLite y Postgres)
            # En Postgres/psycopg2 el resultado suele ser una tupla, en SQLite un Row
            password_hash = usuario[0] if isinstance(usuario, (tuple, list)) else usuario['password']

            if check_password_hash(password_hash, password_ingresada):
                session["admin"] = True
                return redirect("/dashboard")
            else:
                return "❌ Clave incorrecta"
        else:
            return "❌ El usuario admin no existe"

    return render_template("login.html")


@app.route("/logout")
def logout():
    # 1. Obtener el ID de la caja de la sesión actual
    caja_id = session.get("caja_id")
    
    # 2. Si hay un ID en sesión, verificar su estado en la DB
    if caja_id:
        con = get_db()
        cur = con.cursor()
        
        # Usamos 'ejecutar' y '%s' para que funcione en Postgres y SQLite
        ejecutar(cur, con, "SELECT estado FROM caja WHERE id = %s", (caja_id,))
        
        caja = cur.fetchone()
        con.close()

        # Extraemos el valor del estado de forma segura
        estado = caja[0] if caja and isinstance(caja, (list, tuple)) else (caja["estado"] if caja else None)

        # 🚨 Si la caja sigue ABIERTA → NO dejar salir (solo para cajeros)
        if estado == 'ABIERTA':
            return """
            <script>
                alert("⚠️ Debes cerrar TU caja antes de salir");
                window.location.href = "/dashboard_cajero";
            </script>
            """

    # ✅ Limpiar sesión (borra admin, cajero_id, etc.)
    session.clear()

    # 🎯 CAMBIO CLAVE: Redirigir a la raíz para ver los botones de colores
    return redirect("/")


@app.route("/caja/estado")
def estado_caja():
    # 1. Obtener el ID de la caja de la sesión actual
    caja_id = session.get("caja_id")

    if not caja_id:
        return jsonify({"abierta": False})

    con = get_db()
    cur = con.cursor()

    # 2. Buscar datos de ESTA caja específica
    ejecutar(cur, con, """
        SELECT id, monto_inicial
        FROM caja
        WHERE id = %s AND estado = 'ABIERTA'
    """, (caja_id,))

    caja = cur.fetchone()

    if not caja:
        con.close()
        return jsonify({"abierta": False})

    # 3. SUMAR SOLO LAS VENTAS DE ESTA CAJA (Aquí estaba el error)
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(total_final), 0)
        FROM ventas
        WHERE caja_id = %s
    """, (caja_id,))

    ventas = cur.fetchone()[0]

    con.close()

    return jsonify({
        "abierta": True,
        "apertura": float(caja[1]),
        "ventas": float(ventas or 0),
        "total_esperado": float(caja[1]) + float(ventas or 0)
    })

@app.route("/verificar_caja")
def verificar_caja():

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT id FROM caja WHERE estado='ABIERTA'")
    caja = cur.fetchone()

    con.close()

    return {"ABIERTA": bool(caja)}

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

    editar_id = request.args.get("editar")

    con = get_db()
    cur = con.cursor()

    # Lista de clientes
    ejecutar(cur, con, "SELECT * FROM usuarios")
    data = cur.fetchall()

    cliente_editar = None

    # Si viene ?editar=ID
    if editar_id:
        ejecutar(cur, con, "SELECT * FROM usuarios WHERE id=%s", (editar_id,))
        cliente_editar = cur.fetchone()

    con.close()

    return render_template(
        "clientes.html",
        clientes=data,
        cliente_editar=cliente_editar
    )
@app.route("/clientes/actualizar/<int:id>", methods=["POST"])
def actualizar_cliente(id):
    if not session.get("admin"):
        return redirect("/login")

    nombre = request.form.get("nombre")
    telefono = request.form.get("telefono")
    direccion = request.form.get("direccion")

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, """
            UPDATE usuarios
            SET nombre=%s, telefono=%s, direccion=%s
            WHERE id=%s
        """, (nombre, telefono, direccion, id))

        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error: {e}"

    con.close()
    return redirect("/clientes")
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
from datetime import datetime

@app.route("/reporte_ventas_cajero")
def reporte_ventas_cajero():
    if not session.get("admin") and not session.get("puede_ver_reportes"):
        return "❌ Sin permiso"

    con = get_db()
    cur = con.cursor()
    

    cajero = request.args.get("cajero")
    fecha = request.args.get("fecha")

    # 📅 SI NO HAY FECHA → HOY
    if not fecha:
        fecha = datetime.now().strftime("%Y-%m-%d")

    # ================= FILTROS =================
    filtros = []
    params = []

    # 📅 FILTRO POR FECHA (SIEMPRE)
    filtros.append("DATE(v.fecha) = %s")
    params.append(fecha)

    # 👤 FILTRO POR CAJERO (OPCIONAL)
    if cajero:
        filtros.append("v.cajero = %s")
        params.append(cajero)

    where = "WHERE " + " AND ".join(filtros)

    # ================= TOTAL VENTAS =================
    ejecutar(cur, con, f"""
        SELECT 
            COUNT(*),
            COALESCE(SUM(total),0),
            COALESCE(SUM(recargo),0),
            COALESCE(SUM(descuento),0),
            COALESCE(SUM(total_final),0)
        FROM ventas v
        {where}
    """, params)

    total_ventas, total_bruto, total_recargo, total_descuento, total_dinero = cur.fetchone()

    # ================= PRODUCTOS =================
    ejecutar(cur, con, f"""
        SELECT p.descripcion, SUM(vi.cantidad), SUM(vi.subtotal)
        FROM ventas v
        JOIN venta_items vi ON v.id = vi.venta_id
        JOIN productos p ON p.id = vi.producto_id
        {where}
        GROUP BY p.descripcion
        ORDER BY SUM(vi.cantidad) DESC
    """, params)

    productos = cur.fetchall()

    # ================= MÉTODOS DE PAGO =================
    ejecutar(cur, con, f"""
        SELECT metodo_pago,
            COUNT(*),
            COALESCE(SUM(total),0),
            COALESCE(SUM(recargo),0),
            COALESCE(SUM(descuento),0),
            COALESCE(SUM(total_final),0)
        FROM ventas v
        {where}
        GROUP BY metodo_pago
    """, params)

    metodos = cur.fetchall()

    # ================= AUDITORÍA STOCK (CORREGIDA) =================
    auditoria_params = [fecha]
    auditoria_sql = """
        SELECT p.descripcion, p.stock, COALESCE(SUM(vi.cantidad),0)
        FROM productos p
        LEFT JOIN venta_items vi ON p.id = vi.producto_id
        LEFT JOIN ventas v ON v.id = vi.venta_id
        AND DATE(v.fecha) = %s
    """

    if cajero:
        auditoria_sql += " AND v.cajero = %s"
        auditoria_params.append(cajero)

    auditoria_sql += " GROUP BY p.id"

    ejecutar(cur, con, auditoria_sql, auditoria_params)
    auditoria = cur.fetchall()

    # ================= LISTA CAJEROS =================
    ejecutar(cur, con, "SELECT DISTINCT cajero FROM ventas")
    cajeros = cur.fetchall()

    con.close()

    return render_template(
        "reporte_ventas_cajero.html",
        total_ventas=total_ventas,
        total_bruto=total_bruto,
        total_recargo=total_recargo,
        total_descuento=total_descuento,
        total_dinero=total_dinero,
        productos=productos,
        metodos=metodos,
        auditoria=auditoria,
        cajeros=cajeros,
        fecha=fecha
    )
@app.route("/promos/agregar", methods=["POST"])
def agregar_promo():
    if not session.get("admin"):
        return "❌ Sin permiso"

    import uuid
    # Usamos UUID solo si tu tabla local es TEXT. 
    # Si es INTEGER, SQLite dará error. 
    promo_id = str(uuid.uuid4()) 
    nombre = request.form.get("nombre")
    descripcion = request.form.get("descripcion")
    precio = float(request.form.get("precio") or 0)

    # USAMOS LOCAL para evitar el error de tipos si get_db() intenta ir a la nube directo
    con = get_db_local()
    cur = con.cursor()

    try:
        # Si tu tabla local tiene el ID como INTEGER, cambia promo_id por None 
        # o cambia la tabla a TEXT id.
        cur.execute("""
            INSERT INTO promos(id, nombre, descripcion, precio, activa)
            VALUES (?, ?, ?, ?, 1)
        """, (promo_id, nombre, descripcion, precio))

        con.commit()

        # 🔥 SYNC: Guardar en la cola para Supabase
        save_offline("promos", "insert", {
            "id": promo_id,
            "nombre": nombre,
            "descripcion": descripcion,
            "precio": precio,
            "activa": 1
        })

    except Exception as e:
        con.rollback()
        return f"❌ Error: {e}. Verificá si el ID de la tabla promos es TEXT o INTEGER."
    finally:
        con.close()

    return redirect("/promos")


import uuid
from datetime import datetime

@app.route("/productos/agregar", methods=["GET", "POST"])
def agregar_producto():
    # 🔐 VALIDACIÓN DE PERMISOS CORREGIDA
    permisos = session.get("permisos", {})
    es_admin = session.get("admin")
    tiene_permiso = permisos.get("agregar") == 1

    if not es_admin and not tiene_permiso:
        return "❌ No tenés permiso para agregar productos", 403

    if request.method == "POST":
        try:
            # Captura de datos del formulario
            codigo = (request.form.get("codigo") or "").strip().upper()
            descripcion = request.form.get("descripcion")
            litros = int(request.form.get("litros") or 0)
            precio = float(request.form.get("precio") or 0)
            stock = int(request.form.get("stock") or 0)
            departamento = request.form.get("departamento")
            
            # --- 🔥 NUEVO: MANEJO DE FOTO ---
            foto = request.files.get('foto')
            nombre_foto = "" # Valor por defecto si no suben nada
            
            if foto and foto.filename != '':
                # Aseguramos un nombre de archivo seguro y único
                extension = os.path.splitext(foto.filename)[1]
                nombre_foto = f"{codigo}_{str(uuid.uuid4())[:8]}{extension}"
                foto.save(os.path.join(UPLOAD_FOLDER, nombre_foto))

            if not codigo:
                return "❌ Código vacío"

            # Generamos un ID único y la fecha actual
            producto_id = str(uuid.uuid4()) 
            fecha = datetime.now().strftime("%Y-%m-%d")

            # ================= LOCAL (SQLite) =================
            con = get_db_local()
            cur = con.cursor()
            
            # Agregamos la columna 'foto' al INSERT
            cur.execute("""
                INSERT INTO productos (
                    id, codigo, descripcion, litros, precio, stock, fecha, departamento, foto
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (producto_id, codigo, descripcion, litros, precio, stock, fecha, departamento, nombre_foto))

            con.commit()
            con.close()

            # ================= SYNC (Cola de sincronización) =================
            data_producto = {
                "id": producto_id,
                "codigo": codigo,
                "descripcion": descripcion,
                "litros": litros,
                "precio": precio,
                "stock": stock,
                "fecha": fecha,
                "departamento": departamento,
                "foto": nombre_foto # 🔥 Enviamos el nombre de la foto a la nube
            }
            # Guardamos para que el worker lo suba a la nube después
            save_offline("productos", "insert", data_producto)

            flash("✅ Producto guardado localmente con éxito")
            return redirect("/productos/agregar")

        except sqlite3.IntegrityError:
            return "❌ El código de producto ya existe localmente"
        except Exception as e:
            return f"❌ Error: {e}"

    # Si es GET, mostramos el formulario
    return render_template("agregar_producto.html")


# ================== MIS PEDIDOS CLIENTE ==================
@app.route("/mis_pedidos")
def mis_pedidos():
    if not session.get("cliente_id"):
        return redirect("/login_cliente")

    # Intentamos conectar a la base de datos (Cloud si hay internet, Local si no)
    con = get_db() 
    cur = con.cursor()

    try:
        # 1. Traer promociones activas
        ejecutar(cur, con, "SELECT id, nombre, descripcion, precio FROM promos WHERE activa=1")
        promos = cur.fetchall()

        # 2. Traer pedidos del cliente actual
        # IMPORTANTE: Asegúrate de que cliente_id sea el mismo en ambas DBs
        ejecutar(cur, con, """
            SELECT p.id, pr.nombre, p.fecha, p.estado
            FROM pedidos p
            JOIN promos pr ON p.promo_id = pr.id
            WHERE p.cliente_id=%s
            ORDER BY p.id DESC
        """, (session["cliente_id"],))

        pedidos = cur.fetchall()

    except Exception as e:
        # Si algo falla (ej. error de conexión a la nube a mitad de camino)
        if con: con.close()
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

    con = get_db_local()
    cur = con.cursor()

    try:
        # Usamos un ID único para evitar choques en la nube
        pedido_id = str(uuid.uuid4()) 
        cur.execute("""
            INSERT INTO pedidos (id, cliente_id, promo_id, fecha, estado)
            VALUES (?, ?, ?, ?, 'pendiente')
        """, (pedido_id, session["cliente_id"], promo_id, fecha))
        con.commit()

        # 🔥 SYNC: A la cola
        save_offline("pedidos", "insert", {
            "id": pedido_id,
            "cliente_id": session["cliente_id"],
            "promo_id": promo_id,
            "fecha": fecha,
            "estado": "pendiente"
        })

    except Exception as e:
        con.rollback()
        return f"❌ Error al crear pedido: {e}"
    finally:
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
        vender = 1 if request.form.get("vender") else 0
        pedidos = 1 if request.form.get("pedidos") else 0
        reportes = 1 if request.form.get("reportes") else 0
        stock = 1 if request.form.get("stock") else 0
        agregar = 1 if request.form.get("agregar_productos") else 0

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

    if not cajero:
        return "❌ Cajero no encontrado"

    return render_template("permisos_cajero.html", cajero=cajero)

# ================== CAMBIAR ESTADO ==================
@app.route("/pedido/estado/<id>/<estado>")
def cambiar_estado_pedido(id, estado):

    # 🔐 Permisos
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ No tenés permiso"

    # ✅ Estados válidos (los mismos que usás en el HTML)
    ESTADOS_VALIDOS = ["pendiente", "enproceso", "entregado", "cancelado"]

    if estado not in ESTADOS_VALIDOS:
        return "❌ Estado inválido"

    # 🔄 Si querés guardar con espacio en DB
    mapa_estados = {
        "pendiente": "pendiente",
        "enproceso": "en proceso",
        "entregado": "entregado",
        "cancelado": "cancelado"
    }

    estado_db = mapa_estados[estado]

    con = get_db_local()
    cur = con.cursor()

    try:
        cur.execute("""
            UPDATE pedidos 
            SET estado = ? 
            WHERE id = ?
        """, (estado_db, id))

        con.commit()

        # 🔥 SYNC (si lo usás)
        save_offline("pedidos", "update", {
            "id": id,
            "estado": estado_db
        })

    except Exception as e:
        con.rollback()
        return f"❌ Error: {e}"

    finally:
        con.close()

    return redirect("/pedidos")

# ================== VENTAS ==================
@app.route("/ventas", methods=["GET", "POST"])
def ventas():
    if not (session.get("admin") or session.get("cajero_id")):
        return redirect("/")

    if request.method == "POST":
        # CONEXIÓN MANUAL PARA EVITAR INTERFERENCIAS
        con = sqlite3.connect(DB_PATH, timeout=30)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        try:
            codigo = (request.form.get("codigo") or "").strip().upper()
            cantidad = int(request.form.get("cantidad") or 0)
            print(f"DEBUG: Buscando código [{codigo}]")

            # 1. Buscar producto
            cur.execute("SELECT * FROM productos WHERE UPPER(TRIM(codigo)) = ?", (codigo,))
            prod = cur.fetchone()
            
            if not prod:
                print("DEBUG: Producto NO encontrado")
                return f"❌ El código {codigo} no existe localmente."

            p = dict(prod)
            id_real = p['id']
            stock_anterior = p['stock']
            nuevo_stock = stock_anterior - cantidad
            print(f"DEBUG: Producto [{p['descripcion']}] | Stock: {stock_anterior} -> Nuevo: {nuevo_stock}")

            # 2. INTENTO DE DESCUENTO
            cur.execute("UPDATE productos SET stock = ? WHERE id = ?", (nuevo_stock, id_real))
            
            # 3. VERIFICAR SI SQLITE REALMENTE CAMBIÓ LA FILA
            if cur.rowcount == 0:
                print("DEBUG: El UPDATE se ejecutó pero cur.rowcount es 0 (No se modificó nada)")
                return "❌ Error: La base de datos no permitió actualizar el stock."

            # 4. FORZAR GUARDADO
            con.commit()
            
            # 5. VERIFICACIÓN POST-COMMIT (Para estar 100% seguros)
            cur.execute("SELECT stock FROM productos WHERE id = ?", (id_real,))
            confirmacion = cur.fetchone()[0]
            print(f"DEBUG: Stock en disco después del commit: {confirmacion}")

            if confirmacion != nuevo_stock:
                print("DEBUG: ¡ERROR CRÍTICO! El stock en disco no coincide con el nuevo stock.")

            # 6. ENCOLAR PARA SUPABASE
            p['stock'] = nuevo_stock
            cur.execute("INSERT INTO sync_queue (tabla, data, sync) VALUES (?, ?, 0)", 
                        ("productos", json.dumps(p), 0))
            con.commit()

            return f"✅ Venta exitosa. Stock: {nuevo_stock}"

        except Exception as e:
            con.rollback()
            print(f"DEBUG: EXCEPCIÓN CACHADA: {e}")
            return f"❌ Error crítico: {e}"
        finally:
            con.close()

    # Carga normal de la página
    con = get_db_local()
    productos = con.execute("SELECT * FROM productos ORDER BY descripcion ASC").fetchall()
    con.close()
    return render_template("ventas.html", productos=productos)



@app.route("/admin/cierres_caja")
def ver_cierres_caja():
    # 1. Quitamos la validación de admin para probar que el problema no sea la sesión
    try:
        con = get_db()
        cur = con.cursor()
        
        # 2. Usamos el SQL correcto para tu tabla
        ejecutar(cur, con, """
            SELECT id, cajero, fecha_apertura, fecha_cierre, monto_inicial, cierre, diferencia, estado
            FROM caja
            ORDER BY fecha_apertura DESC
        """)
        
        cierres = cur.fetchall()
        con.close()
        
        # 3. Forzamos el renderizado
        return render_template("admin_cierres.html", cierres=cierres)
        
    except Exception as e:
        print(f"ERROR EN REPORTE: {e}")
        return f"Error al cargar el reporte: {e}"





@app.route("/litros")
def ver_litros():

    con = get_db()   # 🔥 IMPORTANTE
    cur = con.cursor()

    # 📜 HISTORIAL
    cur.execute("SELECT litros, fecha FROM litros_control ORDER BY fecha ASC")
    historial = cur.fetchall()

    # 🔵 CARGADOS
    cargados = sum(float(row[0]) for row in historial)

    # 🟢 VENDIDOS (AHORA SÍ VA A FUNCIONAR)
    cur.execute("""
        SELECT COALESCE(SUM(litros_total), 0)
        FROM venta_items
    """)
    vendidos = cur.fetchone()[0]

    # ⚖️ DIFERENCIA
    diferencia = cargados - vendidos

    con.close()

    historial_json = [
        {"litros": float(row[0]), "fecha": row[1]}
        for row in historial
    ]

    return render_template(
        "litros_dashboard.html",
        historial=historial,
        historial_json=historial_json,
        vendidos=vendidos,
        cargados=cargados,
        diferencia=diferencia
    )
@app.route("/caja/estado")
def caja_estado():

    caja_id = session.get("caja_id")

    if not caja_id:
        return {"ABIERTA": False}

    con = get_db()
    cur = con.cursor()

    # 🔥 buscar caja actual
    ejecutar(cur, con, """
        SELECT monto_inicial
        FROM caja
        WHERE id=%s AND estado='ABIERTA'
    """, (caja_id,))

    caja = cur.fetchone()

    if not caja:
        con.close()
        return {"ABIERTA": False}

    monto_inicial = float(caja[0] or 0)

    # 🔥 SOLO ventas de ESTA caja
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(total_final),0)
        FROM ventas
        WHERE caja_id = %s
    """, (caja_id,))

    ventas = float(cur.fetchone()[0] or 0)

    con.close()

    return {
        "ABIERTA": True,
        "apertura": monto_inicial,
        "ventas": ventas,
        "total_esperado": monto_inicial + ventas
    }

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

    # ================= FILTROS =================
    desde = request.args.get("desde")
    hasta = request.args.get("hasta")

    if not desde:
        desde = datetime.now().strftime("%Y-%m-%d")

    if not hasta:
        hasta = datetime.now().strftime("%Y-%m-%d")

    where = "WHERE DATE(v.fecha) BETWEEN %s AND %s"
    params = (desde, hasta)  # 🔥 SIEMPRE TUPLA

    # ================= VENTAS GENERALES =================
    ejecutar(cur, con, f"""
        SELECT COUNT(*), COALESCE(SUM(v.total_final),0)
        FROM ventas v
        {where}
    """, params)
    total_ventas, total_dinero = cur.fetchone()

    # ================= UTILIDAD =================
    ejecutar(cur, con, f"""
        SELECT COALESCE(SUM(v.total_final),0)
        FROM ventas v
        {where}
    """, params)
    utilidad = cur.fetchone()[0]

    # ================= VENTAS POR DÍA =================
    ejecutar(cur, con, f"""
        SELECT DATE(v.fecha),
               COUNT(*),
               COALESCE(SUM(v.total_final),0)
        FROM ventas v
        {where}
        GROUP BY DATE(v.fecha)
        ORDER BY DATE(v.fecha) DESC
    """, params)
    ventas_dia = cur.fetchall()

    # ================= MÉTODOS DE PAGO =================
    ejecutar(cur, con, f"""
        SELECT 
            v.metodo_pago,
            COUNT(*),
            COALESCE(SUM(v.total),0),
            COALESCE(SUM(v.recargo),0),
            COALESCE(SUM(v.descuento),0),
            COALESCE(SUM(v.total_final),0)
        FROM ventas v
        {where}
        GROUP BY v.metodo_pago
        ORDER BY COUNT(*) DESC
    """, params)
    metodos = cur.fetchall()

    # ================= LITROS VENDIDOS =================
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(vi.litros_total),0)
        FROM venta_items vi
        JOIN ventas v ON v.id = vi.venta_id
        WHERE DATE(v.fecha) BETWEEN %s AND %s
    """, params)
    litros_vendidos = cur.fetchone()[0]

    # ================= STOCK ACTUAL =================
    ejecutar(cur, con, "SELECT COALESCE(SUM(stock),0) FROM productos")
    stock_actual = cur.fetchone()[0]

    # ================= AUDITORÍA STOCK (FIX REAL) =================
    ejecutar(cur, con, """
        SELECT 
            p.descripcion,
            p.stock,
            COALESCE(SUM(vi.cantidad),0)
        FROM productos p
        LEFT JOIN venta_items vi ON vi.producto_id = p.id
        LEFT JOIN ventas v ON v.id = vi.venta_id
        WHERE (DATE(v.fecha) BETWEEN %s AND %s OR v.fecha IS NULL)
        GROUP BY p.id
        ORDER BY COALESCE(SUM(vi.cantidad),0) DESC
    """, params)
    auditoria_stock = cur.fetchall()

    # ================= PRODUCTOS MÁS VENDIDOS =================
    ejecutar(cur, con, """
        SELECT 
            p.descripcion,
            COALESCE(SUM(vi.cantidad),0),
            COALESCE(SUM(vi.subtotal),0)
        FROM venta_items vi
        JOIN productos p ON vi.producto_id = p.id
        JOIN ventas v ON v.id = vi.venta_id
        WHERE DATE(v.fecha) BETWEEN %s AND %s
        GROUP BY p.descripcion
        ORDER BY SUM(vi.cantidad) DESC
        LIMIT 10
    """, params)
    productos_vendidos = cur.fetchall()

    # ================= VENTAS POR DEPARTAMENTO =================
    ejecutar(cur, con, """
        SELECT 
            COALESCE(p.departamento, 'Sin asignar'),
            COUNT(DISTINCT v.id),
            COALESCE(SUM(v.total_final), 0)
        FROM ventas v
        JOIN venta_items vi ON v.id = vi.venta_id
        JOIN productos p ON vi.producto_id = p.id
        WHERE DATE(v.fecha) BETWEEN %s AND %s
        GROUP BY p.departamento
        ORDER BY SUM(v.total_final) DESC
    """, params)
    ventas_departamento = cur.fetchall()

    # ================= HISTORIAL DE ITEMS (FIX PRO) =================
    ejecutar(cur, con, """
        SELECT 
            v.fecha,
            COALESCE(p.descripcion, pr.nombre),
            vi.cantidad,
            vi.subtotal,
            v.metodo_pago,
            v.cajero
        FROM ventas v
        JOIN venta_items vi ON v.id = vi.venta_id
        LEFT JOIN productos p ON vi.producto_id = p.id
        LEFT JOIN promos pr ON vi.producto_id = 'promo_' || pr.id
        WHERE DATE(v.fecha) BETWEEN %s AND %s
        ORDER BY v.fecha DESC
    """, params)
    historial_items = cur.fetchall()

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
        historial_items=historial_items,
        desde=desde,
        hasta=hasta
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
@app.route("/carrito/confirmar", methods=["POST"])
def carrito_confirmar():
    carrito = session.get("carrito", [])
    if not carrito: return "❌ Carrito vacío"

    caja_id = session.get("caja_id")
    if not caja_id: return "❌ Debes abrir caja primero"

    # 1. Abrimos UNA SOLA conexión para todo el proceso
    con = get_db_local()
    cur = con.cursor()

    try:
        metodo_pago = request.form.get("metodo_pago")
        recargo_porc = float(request.form.get("recargo") or 0)
        descuento_porc = float(request.form.get("descuento") or 0)
        cajero_nombre = "admin" if session.get("admin") else session.get("nombre_cajero")
        venta_id = str(uuid.uuid4())
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        subtotal = sum(float(i["precio"]) * int(i["cantidad"]) for i in carrito)
        recargo_valor = subtotal * (recargo_porc / 100)
        descuento_valor = subtotal * (descuento_porc / 100)
        total_final = subtotal + recargo_valor - descuento_valor

        # 2. INSERT VENTA LOCAL
        cur.execute("""
            INSERT INTO ventas (id, fecha, total, recargo, descuento, total_final, metodo_pago, cajero, caja_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (venta_id, fecha, subtotal, recargo_valor, descuento_valor, total_final, metodo_pago, cajero_nombre, caja_id))

        items_para_sync = []
        for item in carrito:
            item_id = str(uuid.uuid4())
            prod_id = item["id"]
            cantidad = int(item["cantidad"])
            sub_item = float(item["precio"]) * cantidad
            
            # --- DESCONTAR STOCK ---
            if "promo_" not in str(prod_id):
                cur.execute("UPDATE productos SET stock = stock - ? WHERE id = ?", (cantidad, prod_id))
                
                # Buscamos datos para el sync (usando la misma conexión 'cur')
                cur.execute("SELECT * FROM productos WHERE id = ?", (prod_id,))
                p_row = cur.fetchone()
                if p_row:
                    p_dict = dict(p_row)
                    # INSERTAR EN COLA DE SYNC MANUALMENTE (Evita abrir otra conexión)
                    cur.execute("INSERT INTO sync_queue (tabla, accion, data, sync) VALUES (?, ?, ?, 0)",
                                ("productos", "update", json.dumps(p_dict)))

            # --- INSERT ITEM LOCAL ---
            cur.execute("SELECT litros FROM productos WHERE id = ?", (prod_id,))
            res_prod = cur.fetchone()
            litros_u = float(res_prod[0] or 0) if res_prod else 0
            litros_t = litros_u * cantidad

            cur.execute("""
                INSERT INTO venta_items (id, venta_id, producto_id, cantidad, litros_total, subtotal)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, venta_id, prod_id, cantidad, litros_t, sub_item))
            
            items_para_sync.append({
                "id": item_id, "venta_id": venta_id, "producto_id": prod_id,
                "cantidad": cantidad, "litros_total": litros_t, "subtotal": sub_item
            })

        # 3. ENCOLAR VENTA Y ITEMS (MANUALMENTE)
        data_venta_sync = {
            "id": venta_id, "fecha": fecha, "total": subtotal, "total_final": total_final, 
            "metodo_pago": metodo_pago, "cajero": cajero_nombre, "caja_id": caja_id
        }
        cur.execute("INSERT INTO sync_queue (tabla, accion, data, sync) VALUES (?, ?, ?, 0)",
                    ("ventas", "insert", json.dumps(data_venta_sync)))

        for it in items_para_sync:
            cur.execute("INSERT INTO sync_queue (tabla, accion, data, sync) VALUES (?, ?, ?, 0)",
                        ("venta_items", "insert", json.dumps(it)))

        # 4. GUARDAR TODO DE UN SOLO TIRO
        con.commit()
        session["carrito"] = []
        print(f"✅ Venta exitosa: {venta_id}")
        return redirect("/ventas_ui")

    except sqlite3.OperationalError as e:
        if con: con.rollback()
        print(f"⚠️ Base bloqueada: {e}")
        return "⚠️ El sistema está ocupado (Sync corriendo). Reintentá en 3 segundos."
    except Exception as e:
        if con: con.rollback()
        print(f"❌ Error: {e}")
        return f"❌ Error en venta: {e}"
    finally:
        if con: con.close() # Liberamos la base lo más rápido posible


@app.route("/caja/cerrar", methods=["POST"])
def cierre_caja():
    caja_id = session.get("caja_id")
    if not caja_id:
        return "❌ No hay una caja activa en la sesión"

    total_real = float(request.form.get("total_real") or 0)
    
    con = get_db_local()
    cur = con.cursor()
    
    try:
        # 1. Obtener datos básicos de la caja
        cur.execute("SELECT monto_inicial, cajero, fecha_apertura FROM caja WHERE id=?", (caja_id,))
        caja_data = cur.fetchone()
        if not caja_data:
            return "❌ Caja no encontrada en la base de datos"
        
        apertura, cajero_nombre, fecha_apertura = caja_data[0], caja_data[1], caja_data[2]

        # 2. 🔥 EL FIX: Sumar SOLO ventas en EFECTIVO (Ignora transferencias/tarjetas)
        cur.execute("""
            SELECT COALESCE(SUM(total_final), 0) 
            FROM ventas 
            WHERE caja_id = ? AND UPPER(metodo_pago) = 'EFECTIVO'
        """, (caja_id,))
        ventas_efectivo = cur.fetchone()[0]
        
        # 3. Cálculo de diferencia real
        # Esperado = 6000 (inicio) + 2000 (billetes de ventas) = 8000
        esperado = apertura + ventas_efectivo
        diferencia = total_real - esperado
        fecha_cierre = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 4. Update Local
        cur.execute("""
            UPDATE caja 
            SET estado='CERRADA', fecha_cierre=?, cierre=?, diferencia=? 
            WHERE id=?
        """, (fecha_cierre, total_real, diferencia, caja_id))
        
        con.commit()

        # 5. SYNC a Supabase
        save_offline("caja", "insert", { 
            "id": caja_id,
            "cajero": cajero_nombre,
            "fecha_apertura": fecha_apertura,
            "fecha_cierre": fecha_cierre,
            "cierre": total_real,      
            "apertura": apertura,      
            "diferencia": diferencia,
            "estado": "CERRADA"
        })

        session.pop("caja_id", None)
        return f"✅ Caja cerrada correctamente. Diferencia: ${diferencia:.2f}"

    except Exception as e:
        con.rollback()
        return f"❌ Error al cerrar caja: {e}"
    finally:
        con.close()

@app.route("/caja/apertura", methods=["GET", "POST"])
def apertura_caja():
    if request.method == "POST":
        monto_inicial = float(request.form.get("monto_inicial") or 0)
        caja_id = str(uuid.uuid4()) # ID único fundamental para Supabase
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cajero = session.get("nombre_cajero", "admin")
        
        con = get_db_local()
        cur = con.cursor()
        # 1. Guardamos localmente (SQLite usa ?)
        cur.execute("""
            INSERT INTO caja (id, cajero, fecha_apertura, estado, monto_inicial) 
            VALUES (?, ?, ?, 'ABIERTA', ?)
        """, (caja_id, cajero, fecha, monto_inicial))
        con.commit()
        con.close()

        # 2. 🔥 SYNC: Guardar en cola
        # Importante: Las llaves aquí deben ser consistentes con el sync_worker
        save_offline("caja", "insert", {
            "id": caja_id,
            "cajero": cajero,
            "fecha_apertura": fecha,
            "estado": "ABIERTA",
            "apertura": monto_inicial,  # El worker lo mapeará a 'monto_inicial' en la nube
            "fecha_cierre": None,
            "cierre": 0,                # El worker lo mapeará a 'cierre real' en la nube
            "diferencia": 0
        })

        session["caja_id"] = caja_id
        return redirect("/dashboard_cajero")
    
    return render_template("apertura_caja.html")

@app.route("/caja/ingreso", methods=["POST"])
def ingreso_caja():
    monto = float(request.form.get("monto") or 0)
    motivo = request.form.get("motivo")
    caja_id = session.get("caja_id")

    if not caja_id: return "❌ No hay caja abierta"

    con = get_db_local()
    cur = con.cursor()
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mov_id = str(uuid.uuid4())

    cur.execute("""
        INSERT INTO caja_movimientos (id, caja_id, tipo, descripcion, monto, fecha)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (mov_id, caja_id, "ingreso", motivo, monto, fecha))
    con.commit()
    con.close()

    # 🔥 SYNC
    save_offline("caja_movimientos", "insert", {
        "id": mov_id, "caja_id": caja_id, "tipo": "ingreso", 
        "descripcion": motivo, "monto": monto, "fecha": fecha
    })
    return "OK"
@app.route("/caja/egreso", methods=["POST"])
def egreso_caja():

    monto = float(request.form.get("monto"))
    motivo = request.form.get("motivo")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT id FROM caja WHERE estado='ABIERTA'")
    caja = cur.fetchone()

    if not caja:
        return "❌ No hay caja ABIERTA"

    caja_id = caja[0]

    cur.execute("""
        INSERT INTO caja_movimientos (caja_id, tipo, descripcion, monto, fecha)
        VALUES (%s, %s, %s, %s, %s)
    """, (
        caja_id,
        "egreso",
        motivo,
        monto,
        datetime.now()
    ))

    con.commit()
    con.close()

    return "OK"
@app.route("/caja/retiro", methods=["POST"])
def retiro_caja():

    caja_id = session.get("caja_id")
    monto = float(request.form.get("monto"))
    desc = request.form.get("desc")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO caja_movimientos (
            caja_id, tipo, descripcion, monto, fecha
        )
        VALUES (%s, %s, %s, %s, %s)
    """, (
        caja_id,
        "retiro",
        desc,
        monto,
        datetime.now()
    ))

    con.commit()
    con.close()

    return "OK"
@app.route("/login_cajero", methods=["GET", "POST"])
def login_cajero():
    if request.method == "POST":
        nombre = request.form.get("nombre")
        password = request.form.get("password")
        con = get_db()
        cur = con.cursor()
        
        # Traemos al cajero
        ejecutar(cur, con, "SELECT * FROM cajeros WHERE usuario = %s AND password = %s", (nombre, password))
        cajero = cur.fetchone()
        con.close()

        if cajero:
            session.clear()
            session["cajero_id"] = cajero[0]
            session["nombre_cajero"] = cajero[1]
            
            # ASIGNACIÓN EXACTA SEGÚN TU TABLA SQLITE
            session["permisos"] = {
                "vender": cajero[4],
                "pedidos": cajero[5],
                "reportes": cajero[6], # <--- Este es el de Reportes
                "stock": cajero[7],
                "agregar": cajero[8]
            }
            return redirect("/dashboard_cajero")
        else:
            return "❌ Usuario o contraseña incorrectos"
    return render_template("login_cajero.html")


@app.route("/caja/cerrar", methods=["POST"])
def cerrar_caja():

    if not session.get("caja_id"):
        return "❌ No hay caja abierta"

    caja_id = session.get("caja_id")

    try:
        total_real = float(request.form.get("total_real") or 0)
    except:
        return "❌ Monto inválido"

    con = get_db()
    cur = con.cursor()

    # ================= VALIDAR CAJA =================
    cur.execute("""
        SELECT monto_inicial, estado
        FROM caja
        WHERE id = ?
    """, (caja_id,))

    caja = cur.fetchone()

    if not caja or caja[1] != "ABIERTA":
        con.close()
        session.pop("caja_id", None)
        return "❌ Caja inválida o ya cerrada"

    apertura = float(caja[0] or 0)

    # ================= VENTAS SOLO DE ESA CAJA =================
    cur.execute("""
        SELECT COALESCE(SUM(total_final), 0)
        FROM ventas
        WHERE caja_id = ?
    """, (caja_id,))

    ventas = float(cur.fetchone()[0] or 0)

    # ================= CALCULO =================
    esperado = apertura + ventas
    diferencia = total_real - esperado

    # ================= CERRAR CAJA =================
    cur.execute("""
        UPDATE caja
        SET 
            estado = 'CERRADA',
            fecha_cierre = ?,
            cierre = ?,
            diferencia = ?
        WHERE id = ?
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_real,
        diferencia,
        caja_id
    ))

    con.commit()
    con.close()

    # 🔥 limpiar sesión
    session.pop("caja_id", None)

    return f"✅ Caja cerrada | Esperado: ${esperado:.2f} | Real: ${total_real:.2f} | Dif: ${diferencia:.2f}"
@app.route("/finalizar_venta", methods=["POST"])
def finalizar_venta():
    # 1. Obtener el ID de la caja ACTIVA desde la sesión
    id_sesion_caja = session.get("caja_id")

    if not id_sesion_caja:
        return "❌ No puedes vender si no has abierto caja"

    # ... resto de tu lógica para obtener montos ...

    con = get_db()
    cur = con.cursor()

    # 2. Insertar en la tabla 'ventas' incluyendo el caja_id de la sesión
    cur.execute("""
        INSERT INTO ventas (id, fecha, total, total_final, cajero, caja_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        generar_uuid(), # O tu lógica de ID
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total,
        total_final,
        session.get("usuario_nombre"),
        id_sesion_caja  # <--- ESTO ES LO MÁS IMPORTANTE
    ))
    
    con.commit()
    # ...

@app.route("/cajas")
def cajas():

    if not session.get("cajero_id"):
        return redirect("/login_cajero")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT 
            id,
            cajero,
            fecha_apertura,
            fecha_cierre,
            estado,
            monto_inicial,
            cierre,
            diferencia
        FROM caja
        ORDER BY fecha_apertura DESC
    """)

    cajas = cur.fetchall()

    con.close()

    return render_template("cajas.html", cajas=cajas)
@app.route("/crear_cajero", methods=["GET", "POST"])
def crear_cajero():
    if not session.get("admin"):
        return redirect("/login")
    
    if request.method == "POST":
        usuario = request.form.get("nombre")
        password = request.form.get("password")
        
        if not usuario or not password:
            return "❌ Datos incompletos"
        
        con = get_db_local() # Aseguramos primero el guardado local
        cur = con.cursor()
        
        try:
            # 1. Guardar localmente
            cur.execute("""
                INSERT INTO cajeros (usuario, password, rol)
                VALUES (?, ?, 'cajero')
            """, (usuario, password))
            con.commit()
            
            # 2. 🔥 MANDAR A LA COLA DE SYNC
            # Pasamos los datos que Supabase necesita
            data_cajero = {
                "usuario": usuario,
                "password": password,
                "rol": "cajero"
            }
            save_offline("cajeros", "insert", data_cajero)
            
            flash("✅ Cajero creado y programado para sincronizar")
            return redirect("/dashboard")
            
        except Exception as e:
            con.rollback()
            return f"❌ Error: {e}"
        finally:
            con.close()
            
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
@app.route("/stock/vaciar/<id>")
def vaciar_stock(id):
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ Sin permiso"

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, """
            UPDATE productos
            SET stock = 0
            WHERE id = %s
        """, (id,))

        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error: {e}"

    con.close()

    return redirect("/stock")
@app.route("/carrito/agregar_manual", methods=["POST"])
def carrito_agregar_manual():
    if "carrito" not in session:
        session["carrito"] = []

    desc = request.form.get("desc")
    precio = float(request.form.get("precio") or 0)
    cantidad = int(request.form.get("cantidad") or 1)

    if not desc or precio <= 0:
        return "❌ Datos inválidos"

    session["carrito"].append({
        "id": "manual_" + str(uuid.uuid4()),
        "desc": "🧾 " + desc,
        "precio": precio,
        "cantidad": cantidad,
        "manual": True
    })

    session.modified = True

    return "OK"
@app.route("/stock/eliminar/<id>")
def eliminar_producto(id):
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ Sin permiso"

    con = get_db()
    cur = con.cursor()

    try:
        # ⚠️ OJO: esto borra producto completo
        ejecutar(cur, con, """
            DELETE FROM productos
            WHERE id = %s
        """, (id,))

        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error: {e}"

    con.close()
    return redirect("/stock")
@app.route("/dashboard_cajero")
def dashboard_cajero():
    if not session.get("cajero_id"):
        return redirect("/login_cajero")

    # 1. LEER CAJA SIEMPRE LOCAL (Evita que el monto desaparezca por lag de internet)
    con_local = get_db_local()
    cur_local = con_local.cursor()

    caja_id = session.get("caja_id")
    caja_abierta = False
    apertura = 0
    total_ventas = 0
    solo_efectivo = 0
    ventas_por_metodo = []

    if caja_id:
        # Buscamos la caja en la base local
        cur_local.execute("SELECT monto_inicial, estado FROM caja WHERE id = ?", (caja_id,))
        caja = cur_local.fetchone()

        if caja and caja["estado"] == "ABIERTA":
            caja_abierta = True
            apertura = float(caja["monto_inicial"] or 0)

            # Sumar total general de ventas de esta caja
            cur_local.execute("SELECT COALESCE(SUM(total_final), 0) FROM ventas WHERE caja_id = ?", (caja_id,))
            total_ventas = float(cur_local.fetchone()[0] or 0)

            # Sumar SOLO EFECTIVO (Para el control físico)
            cur_local.execute("""
                SELECT COALESCE(SUM(total_final), 0) 
                FROM ventas 
                WHERE caja_id = ? AND UPPER(metodo_pago) = 'EFECTIVO'
            """, (caja_id,))
            solo_efectivo = float(cur_local.fetchone()[0] or 0)

            # TRAER TODOS LOS MÉTODOS DETALLADOS (Tarjeta, Transferencia, etc.)
            cur_local.execute("""
                SELECT metodo_pago, SUM(total_final) 
                FROM ventas 
                WHERE caja_id = ? 
                GROUP BY metodo_pago
            """, (caja_id,))
            ventas_por_metodo = cur_local.fetchall() 
        else:
            session.pop("caja_id", None)
    
    con_local.close()

    # 2. PRODUCTOS Y PEDIDOS (Pueden venir de la nube/local según get_db)
    con = get_db()
    cur = con.cursor()
    ejecutar(cur, con, "SELECT COUNT(*) FROM productos")
    productos = cur.fetchone()[0]
    ejecutar(cur, con, "SELECT COUNT(*) FROM pedidos")
    pedidos = cur.fetchone()[0]
    con.close()

    return render_template(
        "dashboard_cajero.html",
        productos=productos,
        pedidos=pedidos,
        nombre=session.get("nombre_cajero"),
        caja_abierta=caja_abierta,
        apertura=apertura,
        total_ventas=total_ventas,
        solo_efectivo=solo_efectivo,
        ventas_por_metodo=ventas_por_metodo
    )

@app.route("/promos/eliminar/<id>", methods=["POST"])
def eliminar_promo(id):
    con = get_db()
    cur = con.cursor()

    ejecutar(cur, con, "DELETE FROM promos WHERE id=%s", (id,))

    con.commit()
    con.close()

    return redirect("/promos")
@app.route("/promos/editar/<int:id>", methods=["POST"])
def actualizar_promo(id):
    nombre = request.form["nombre"]
    descripcion = request.form["descripcion"]
    precio = request.form["precio"]

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("""
        UPDATE promos 
        SET nombre=?, descripcion=?, precio=? 
        WHERE id=?
    """, (nombre, descripcion, precio, id))

    con.commit()
    con.close()

    return redirect("/promos")
@app.route("/promos/editar/<id>", methods=["GET", "POST"])
def editar_promo(id):
    con = get_db()
    cur = con.cursor()

    if request.method == "POST":
        nombre = request.form.get("nombre")
        descripcion = request.form.get("descripcion")
        precio = float(request.form.get("precio") or 0)

        ejecutar(cur, con, """
            UPDATE promos
            SET nombre=%s, descripcion=%s, precio=%s
            WHERE id=%s
        """, (nombre, descripcion, precio, id))

        con.commit()
        con.close()
        return redirect("/promos")

    ejecutar(cur, con, "SELECT * FROM promos WHERE id=%s", (id,))
    promo = cur.fetchone()

    con.close()
    return render_template("editar_promo.html", promo=promo)
@app.route("/stock", methods=["GET", "POST"])
def stock():
    # 🔐 VALIDACIÓN CORREGIDA
    # Obtenemos el diccionario de permisos de la sesión
    permisos = session.get("permisos", {})
    es_admin = session.get("admin")
    # Verificamos si el permiso de stock es 1
    tiene_permiso_stock = permisos.get("stock") == 1

    if not es_admin and not tiene_permiso_stock:
        return "❌ No tenés permiso para acceder al módulo de Stock", 403

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

            # 🔥 Sincronizar cambio a la nube
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
import threading
threading.Thread(target=sync_worker, daemon=True).start()

# =================================================IMIENTOS FINALES=================================================

# 1. Función para inicializar la base de datos en la nube (Render) automáticamente
def inicializar_nube():
    if os.environ.get("RENDER"):
        print("☁️ Verificando tablas en Supabase...")
        try:
            con = get_db_cloud()
            cur = con.cursor()
            
            # Crear tabla usuarios si no existe (por si Supabase está vacío)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    nombre TEXT UNIQUE NOT NULL,
                    telefono TEXT,
                    direccion TEXT,
                    password TEXT NOT NULL
                )
            """)
            
            # Verificar si existe el admin
            cur.execute("SELECT * FROM usuarios WHERE nombre = %s", ("admin",))
            if not cur.fetchone():
                from werkzeug.security import generate_password_hash
                pw_plana = os.getenv("ADMIN_PASSWORD", "1234")
                pw_hash = generate_password_hash(pw_plana)
                cur.execute("INSERT INTO usuarios (nombre, password) VALUES (%s, %s)", ("admin", pw_hash))
                print("👤 Admin creado en la nube correctamente.")
            
            con.commit()
            con.close()
        except Exception as e:
            print(f"⚠️ Error inicializando nube: {e}")

# 2. Arranque del sistema
if __name__ == "__main__":
    # Solo inicializamos la nube si estamos en Render
    inicializar_nube()

    # Iniciamos el Worker de sincronización (SOLO UNA VEZ)
    # Esto corre tanto en PC como en Render (aunque en Render no hará nada si no hay SQLite)
    threading.Thread(target=sync_worker, daemon=True).start()

    # Configuración de puerto para Render o Local
    puerto = int(os.environ.get("PORT", 5000))
    
    # Arrancamos la app
    es_produccion = os.environ.get("RENDER")
    app.run(host="0.0.0.0", port=puerto, debug=not es_produccion)



@app.route("/caja/cerrar", methods=["POST"])
def cierre_caja():
    caja_id = session.get("caja_id")
    if not caja_id:
        return "❌ No hay una caja activa en la sesión"

    total_real = float(request.form.get("total_real") or 0)
    
    con = get_db_local()
    cur = con.cursor()
    
    try:
        # 1. Obtener datos básicos de la caja
        cur.execute("SELECT monto_inicial, cajero, fecha_apertura FROM caja WHERE id=?", (caja_id,))
        caja_data = cur.fetchone()
        if not caja_data:
            return "❌ Caja no encontrada en la base de datos"
        
        apertura, cajero_nombre, fecha_apertura = caja_data[0], caja_data[1], caja_data[2]

        # 2. 🔥 EL FIX: Sumar SOLO ventas en EFECTIVO (Ignora transferencias/tarjetas)
        cur.execute("""
            SELECT COALESCE(SUM(total_final), 0) 
            FROM ventas 
            WHERE caja_id = ? AND UPPER(metodo_pago) = 'EFECTIVO'
        """, (caja_id,))
        ventas_efectivo = cur.fetchone()[0]
        
        # 3. Cálculo de diferencia real
        # Esperado = 6000 (inicio) + 2000 (billetes de ventas) = 8000
        esperado = apertura + ventas_efectivo
        diferencia = total_real - esperado
        fecha_cierre = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 4. Update Local
        cur.execute("""
            UPDATE caja 
            SET estado='CERRADA', fecha_cierre=?, cierre=?, diferencia=? 
            WHERE id=?
        """, (fecha_cierre, total_real, diferencia, caja_id))
        
        con.commit()

        # 5. SYNC a Supabase
        save_offline("caja", "insert", { 
            "id": caja_id,
            "cajero": cajero_nombre,
            "fecha_apertura": fecha_apertura,
            "fecha_cierre": fecha_cierre,
            "cierre": total_real,      
            "apertura": apertura,      
            "diferencia": diferencia,
            "estado": "CERRADA"
        })

        session.pop("caja_id", None)
        return f"✅ Caja cerrada correctamente. Diferencia: ${diferencia:.2f}"

    except Exception as e:
        con.rollback()
        return f"❌ Error al cerrar caja: {e}"
    finally:
        con.close()

@app.route("/caja/apertura", methods=["GET", "POST"])
def apertura_caja():
    if request.method == "POST":
        monto_inicial = float(request.form.get("monto_inicial") or 0)
        caja_id = str(uuid.uuid4()) # ID único fundamental para Supabase
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cajero = session.get("nombre_cajero", "admin")
        
        con = get_db_local()
        cur = con.cursor()
        # 1. Guardamos localmente (SQLite usa ?)
        cur.execute("""
            INSERT INTO caja (id, cajero, fecha_apertura, estado, monto_inicial) 
            VALUES (?, ?, ?, 'ABIERTA', ?)
        """, (caja_id, cajero, fecha, monto_inicial))
        con.commit()
        con.close()

        # 2. 🔥 SYNC: Guardar en cola
        # Importante: Las llaves aquí deben ser consistentes con el sync_worker
        save_offline("caja", "insert", {
            "id": caja_id,
            "cajero": cajero,
            "fecha_apertura": fecha,
            "estado": "ABIERTA",
            "apertura": monto_inicial,  # El worker lo mapeará a 'monto_inicial' en la nube
            "fecha_cierre": None,
            "cierre": 0,                # El worker lo mapeará a 'cierre real' en la nube
            "diferencia": 0
        })

        session["caja_id"] = caja_id
        return redirect("/dashboard_cajero")
    
    return render_template("apertura_caja.html")

@app.route("/caja/ingreso", methods=["POST"])
def ingreso_caja():
    monto = float(request.form.get("monto") or 0)
    motivo = request.form.get("motivo")
    caja_id = session.get("caja_id")

    if not caja_id: return "❌ No hay caja abierta"

    con = get_db_local()
    cur = con.cursor()
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mov_id = str(uuid.uuid4())

    cur.execute("""
        INSERT INTO caja_movimientos (id, caja_id, tipo, descripcion, monto, fecha)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (mov_id, caja_id, "ingreso", motivo, monto, fecha))
    con.commit()
    con.close()

    # 🔥 SYNC
    save_offline("caja_movimientos", "insert", {
        "id": mov_id, "caja_id": caja_id, "tipo": "ingreso", 
        "descripcion": motivo, "monto": monto, "fecha": fecha
    })
    return "OK"
@app.route("/caja/egreso", methods=["POST"])
def egreso_caja():

    monto = float(request.form.get("monto"))
    motivo = request.form.get("motivo")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT id FROM caja WHERE estado='ABIERTA'")
    caja = cur.fetchone()

    if not caja:
        return "❌ No hay caja ABIERTA"

    caja_id = caja[0]

    cur.execute("""
        INSERT INTO caja_movimientos (caja_id, tipo, descripcion, monto, fecha)
        VALUES (%s, %s, %s, %s, %s)
    """, (
        caja_id,
        "egreso",
        motivo,
        monto,
        datetime.now()
    ))

    con.commit()
    con.close()

    return "OK"
@app.route("/caja/retiro", methods=["POST"])
def retiro_caja():

    caja_id = session.get("caja_id")
    monto = float(request.form.get("monto"))
    desc = request.form.get("desc")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO caja_movimientos (
            caja_id, tipo, descripcion, monto, fecha
        )
        VALUES (%s, %s, %s, %s, %s)
    """, (
        caja_id,
        "retiro",
        desc,
        monto,
        datetime.now()
    ))

    con.commit()
    con.close()

    return "OK"
@app.route("/login_cajero", methods=["GET", "POST"])
def login_cajero():
    if request.method == "POST":
        nombre = request.form.get("nombre")
        password = request.form.get("password")
        con = get_db()
        cur = con.cursor()
        
        # Traemos al cajero
        ejecutar(cur, con, "SELECT * FROM cajeros WHERE usuario = %s AND password = %s", (nombre, password))
        cajero = cur.fetchone()
        con.close()

        if cajero:
            session.clear()
            session["cajero_id"] = cajero[0]
            session["nombre_cajero"] = cajero[1]
            
            # ASIGNACIÓN EXACTA SEGÚN TU TABLA SQLITE
            session["permisos"] = {
                "vender": cajero[4],
                "pedidos": cajero[5],
                "reportes": cajero[6], # <--- Este es el de Reportes
                "stock": cajero[7],
                "agregar": cajero[8]
            }
            return redirect("/dashboard_cajero")
        else:
            return "❌ Usuario o contraseña incorrectos"
    return render_template("login_cajero.html")


@app.route("/caja/cerrar", methods=["POST"])
def cerrar_caja():

    if not session.get("caja_id"):
        return "❌ No hay caja abierta"

    caja_id = session.get("caja_id")

    try:
        total_real = float(request.form.get("total_real") or 0)
    except:
        return "❌ Monto inválido"

    con = get_db()
    cur = con.cursor()

    # ================= VALIDAR CAJA =================
    cur.execute("""
        SELECT monto_inicial, estado
        FROM caja
        WHERE id = ?
    """, (caja_id,))

    caja = cur.fetchone()

    if not caja or caja[1] != "ABIERTA":
        con.close()
        session.pop("caja_id", None)
        return "❌ Caja inválida o ya cerrada"

    apertura = float(caja[0] or 0)

    # ================= VENTAS SOLO DE ESA CAJA =================
    cur.execute("""
        SELECT COALESCE(SUM(total_final), 0)
        FROM ventas
        WHERE caja_id = ?
    """, (caja_id,))

    ventas = float(cur.fetchone()[0] or 0)

    # ================= CALCULO =================
    esperado = apertura + ventas
    diferencia = total_real - esperado

    # ================= CERRAR CAJA =================
    cur.execute("""
        UPDATE caja
        SET 
            estado = 'CERRADA',
            fecha_cierre = ?,
            cierre = ?,
            diferencia = ?
        WHERE id = ?
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_real,
        diferencia,
        caja_id
    ))

    con.commit()
    con.close()

    # 🔥 limpiar sesión
    session.pop("caja_id", None)

    return f"✅ Caja cerrada | Esperado: ${esperado:.2f} | Real: ${total_real:.2f} | Dif: ${diferencia:.2f}"
@app.route("/finalizar_venta", methods=["POST"])
def finalizar_venta():
    # 1. Obtener el ID de la caja ACTIVA desde la sesión
    id_sesion_caja = session.get("caja_id")

    if not id_sesion_caja:
        return "❌ No puedes vender si no has abierto caja"

    # ... resto de tu lógica para obtener montos ...

    con = get_db()
    cur = con.cursor()

    # 2. Insertar en la tabla 'ventas' incluyendo el caja_id de la sesión
    cur.execute("""
        INSERT INTO ventas (id, fecha, total, total_final, cajero, caja_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        generar_uuid(), # O tu lógica de ID
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total,
        total_final,
        session.get("usuario_nombre"),
        id_sesion_caja  # <--- ESTO ES LO MÁS IMPORTANTE
    ))
    
    con.commit()
    # ...

@app.route("/cajas")
def cajas():

    if not session.get("cajero_id"):
        return redirect("/login_cajero")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT 
            id,
            cajero,
            fecha_apertura,
            fecha_cierre,
            estado,
            monto_inicial,
            cierre,
            diferencia
        FROM caja
        ORDER BY fecha_apertura DESC
    """)

    cajas = cur.fetchall()

    con.close()

    return render_template("cajas.html", cajas=cajas)
@app.route("/crear_cajero", methods=["GET", "POST"])
def crear_cajero():
    if not session.get("admin"):
        return redirect("/login")
    
    if request.method == "POST":
        usuario = request.form.get("nombre")
        password = request.form.get("password")
        
        if not usuario or not password:
            return "❌ Datos incompletos"
        
        con = get_db_local() # Aseguramos primero el guardado local
        cur = con.cursor()
        
        try:
            # 1. Guardar localmente
            cur.execute("""
                INSERT INTO cajeros (usuario, password, rol)
                VALUES (?, ?, 'cajero')
            """, (usuario, password))
            con.commit()
            
            # 2. 🔥 MANDAR A LA COLA DE SYNC
            # Pasamos los datos que Supabase necesita
            data_cajero = {
                "usuario": usuario,
                "password": password,
                "rol": "cajero"
            }
            save_offline("cajeros", "insert", data_cajero)
            
            flash("✅ Cajero creado y programado para sincronizar")
            return redirect("/dashboard")
            
        except Exception as e:
            con.rollback()
            return f"❌ Error: {e}"
        finally:
            con.close()
            
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
@app.route("/stock/vaciar/<id>")
def vaciar_stock(id):
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ Sin permiso"

    con = get_db()
    cur = con.cursor()

    try:
        ejecutar(cur, con, """
            UPDATE productos
            SET stock = 0
            WHERE id = %s
        """, (id,))

        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error: {e}"

    con.close()

    return redirect("/stock")
@app.route("/carrito/agregar_manual", methods=["POST"])
def carrito_agregar_manual():
    if "carrito" not in session:
        session["carrito"] = []

    desc = request.form.get("desc")
    precio = float(request.form.get("precio") or 0)
    cantidad = int(request.form.get("cantidad") or 1)

    if not desc or precio <= 0:
        return "❌ Datos inválidos"

    session["carrito"].append({
        "id": "manual_" + str(uuid.uuid4()),
        "desc": "🧾 " + desc,
        "precio": precio,
        "cantidad": cantidad,
        "manual": True
    })

    session.modified = True

    return "OK"
@app.route("/stock/eliminar/<id>")
def eliminar_producto(id):
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ Sin permiso"

    con = get_db()
    cur = con.cursor()

    try:
        # ⚠️ OJO: esto borra producto completo
        ejecutar(cur, con, """
            DELETE FROM productos
            WHERE id = %s
        """, (id,))

        con.commit()

    except Exception as e:
        con.close()
        return f"❌ Error: {e}"

    con.close()
    return redirect("/stock")
@app.route("/dashboard_cajero")
def dashboard_cajero():
    if not session.get("cajero_id"):
        return redirect("/login_cajero")

    # 1. LEER CAJA SIEMPRE LOCAL (Evita que el monto desaparezca por lag de internet)
    con_local = get_db_local()
    cur_local = con_local.cursor()

    caja_id = session.get("caja_id")
    caja_abierta = False
    apertura = 0
    total_ventas = 0
    solo_efectivo = 0
    ventas_por_metodo = []

    if caja_id:
        # Buscamos la caja en la base local
        cur_local.execute("SELECT monto_inicial, estado FROM caja WHERE id = ?", (caja_id,))
        caja = cur_local.fetchone()

        if caja and caja["estado"] == "ABIERTA":
            caja_abierta = True
            apertura = float(caja["monto_inicial"] or 0)

            # Sumar total general de ventas de esta caja
            cur_local.execute("SELECT COALESCE(SUM(total_final), 0) FROM ventas WHERE caja_id = ?", (caja_id,))
            total_ventas = float(cur_local.fetchone()[0] or 0)

            # Sumar SOLO EFECTIVO (Para el control físico)
            cur_local.execute("""
                SELECT COALESCE(SUM(total_final), 0) 
                FROM ventas 
                WHERE caja_id = ? AND UPPER(metodo_pago) = 'EFECTIVO'
            """, (caja_id,))
            solo_efectivo = float(cur_local.fetchone()[0] or 0)

            # TRAER TODOS LOS MÉTODOS DETALLADOS (Tarjeta, Transferencia, etc.)
            cur_local.execute("""
                SELECT metodo_pago, SUM(total_final) 
                FROM ventas 
                WHERE caja_id = ? 
                GROUP BY metodo_pago
            """, (caja_id,))
            ventas_por_metodo = cur_local.fetchall() 
        else:
            session.pop("caja_id", None)
    
    con_local.close()

    # 2. PRODUCTOS Y PEDIDOS (Pueden venir de la nube/local según get_db)
    con = get_db()
    cur = con.cursor()
    ejecutar(cur, con, "SELECT COUNT(*) FROM productos")
    productos = cur.fetchone()[0]
    ejecutar(cur, con, "SELECT COUNT(*) FROM pedidos")
    pedidos = cur.fetchone()[0]
    con.close()

    return render_template(
        "dashboard_cajero.html",
        productos=productos,
        pedidos=pedidos,
        nombre=session.get("nombre_cajero"),
        caja_abierta=caja_abierta,
        apertura=apertura,
        total_ventas=total_ventas,
        solo_efectivo=solo_efectivo,
        ventas_por_metodo=ventas_por_metodo
    )

@app.route("/promos/eliminar/<id>", methods=["POST"])
def eliminar_promo(id):
    con = get_db()
    cur = con.cursor()

    ejecutar(cur, con, "DELETE FROM promos WHERE id=%s", (id,))

    con.commit()
    con.close()

    return redirect("/promos")
@app.route("/promos/editar/<int:id>", methods=["POST"])
def actualizar_promo(id):
    nombre = request.form["nombre"]
    descripcion = request.form["descripcion"]
    precio = request.form["precio"]

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("""
        UPDATE promos 
        SET nombre=?, descripcion=?, precio=? 
        WHERE id=?
    """, (nombre, descripcion, precio, id))

    con.commit()
    con.close()

    return redirect("/promos")
@app.route("/promos/editar/<id>", methods=["GET", "POST"])
def editar_promo(id):
    con = get_db()
    cur = con.cursor()

    if request.method == "POST":
        nombre = request.form.get("nombre")
        descripcion = request.form.get("descripcion")
        precio = float(request.form.get("precio") or 0)

        ejecutar(cur, con, """
            UPDATE promos
            SET nombre=%s, descripcion=%s, precio=%s
            WHERE id=%s
        """, (nombre, descripcion, precio, id))

        con.commit()
        con.close()
        return redirect("/promos")

    ejecutar(cur, con, "SELECT * FROM promos WHERE id=%s", (id,))
    promo = cur.fetchone()

    con.close()
    return render_template("editar_promo.html", promo=promo)
@app.route("/stock", methods=["GET", "POST"])
def stock():
    # 🔐 VALIDACIÓN CORREGIDA
    # Obtenemos el diccionario de permisos de la sesión
    permisos = session.get("permisos", {})
    es_admin = session.get("admin")
    # Verificamos si el permiso de stock es 1
    tiene_permiso_stock = permisos.get("stock") == 1

    if not es_admin and not tiene_permiso_stock:
        return "❌ No tenés permiso para acceder al módulo de Stock", 403

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

            # 🔥 Sincronizar cambio a la nube
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
import threading
threading.Thread(target=sync_worker, daemon=True).start()

# =================================================IMIENTOS FINALES=================================================

# 1. Función para inicializar la base de datos en la nube (Render) automáticamente
def inicializar_nube():
    if os.environ.get("RENDER"):
        print("☁️ Verificando tablas en Supabase...")
        try:
            con = get_db_cloud()
            cur = con.cursor()
            
            # Crear tabla usuarios si no existe (por si Supabase está vacío)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    nombre TEXT UNIQUE NOT NULL,
                    telefono TEXT,
                    direccion TEXT,
                    password TEXT NOT NULL
                )
            """)
            
            # Verificar si existe el admin
            cur.execute("SELECT * FROM usuarios WHERE nombre = %s", ("admin",))
            if not cur.fetchone():
                from werkzeug.security import generate_password_hash
                pw_plana = os.getenv("ADMIN_PASSWORD", "1234")
                pw_hash = generate_password_hash(pw_plana)
                cur.execute("INSERT INTO usuarios (nombre, password) VALUES (%s, %s)", ("admin", pw_hash))
                print("👤 Admin creado en la nube correctamente.")
            
            con.commit()
            con.close()
        except Exception as e:
            print(f"⚠️ Error inicializando nube: {e}")

# 2. Arranque del sistema
if __name__ == "__main__":
    # Solo inicializamos la nube si estamos en Render
    inicializar_nube()

    # Iniciamos el Worker de sincronización (SOLO UNA VEZ)
    # Esto corre tanto en PC como en Render (aunque en Render no hará nada si no hay SQLite)
    threading.Thread(target=sync_worker, daemon=True).start()

    # Configuración de puerto para Render o Local
    puerto = int(os.environ.get("PORT", 5000))
    
    # Arrancamos la app
    es_produccion = os.environ.get("RENDER")
    app.run(host="0.0.0.0", port=puerto, debug=not es_produccion)
