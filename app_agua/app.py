
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
import threading
from decimal import Decimal # Solo necesitamos este
import webbrowser # <-- Agregá este import arriba de todo
from werkzeug.security import check_password_hash

import sys
import os
from dotenv import load_dotenv
# Pegá esto arriba de todo en tu app.py para que el servidor entienda la conexión
def conectar():
    import sqlite3
    import os
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "database.db")
    con = sqlite3.connect(DB_PATH)
    return con, con.cursor()

db_lock = threading.Lock()

# 1. CONFIGURACIÓN DE RUTAS DINÁMICAS (Blindaje para .exe)
if getattr(sys, 'frozen', False):
    # Si es el ejecutable (.exe), la carpeta base es donde vive el .exe
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # Si es modo normal (python app.py), la carpeta es la del script
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Definimos rutas absolutas
DB_PATH = os.path.join(BASE_DIR, "database.db")
ENV_PATH = os.path.join(BASE_DIR, ".env")

# 2. CARGA DE CONFIGURACIÓN
load_dotenv(ENV_PATH) # Cargamos el archivo específico
print(f"🌐 Intentando conectar a: {os.getenv('DB_CLOUD_HOST')}")

app = Flask(__name__)

# 3. SEGURIDAD Y VARIABLES
app.secret_key = os.getenv("SECRET_KEY", "clave_de_emergencia_")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# 4. CARPETAS DE ARCHIVOS (Aseguramos que se creen en la ruta del programa)
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'productos')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Estados corregidos
ESTADOS_VALIDOS = ["pendiente", "enproceso", "entregado", "cancelado"]




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


    
import threading
import json
import time
import os
from decimal import Decimal

# Candado para que solo corra UN worker a la vez en memoria
worker_running_lock = threading.Lock()

def sync_worker():
    if not worker_running_lock.acquire(blocking=False):
        return

    print("🚀 WORKER PRO: SUBIDA DINÁMICA + BAJADA CON ESCUDO (LOCK ACTIVADO)")
    last_pull = 0
    es_render = os.environ.get("RENDER")

    try:
        while True:
            try:
                if not internet_ok():
                    time.sleep(10)
                    continue

                # ==========================================
                # 1. SUBIDA (PUSH): De la PC a Supabase
                # ==========================================
                if not es_render:
                    pendientes = []
                    with db_lock:
                        con_l = get_db_local()
                        cur_l = con_l.cursor()
                        cur_l.execute("SELECT id, tabla, data FROM sync_queue WHERE sync=0 ORDER BY id ASC LIMIT 50")
                        originales = cur_l.fetchall()
                        
                        if originales:
                            pendientes = [dict(row) for row in originales]
                            ids = [p["id"] for p in pendientes]
                            placeholders = ",".join(["?"] * len(ids))
                            con_l.execute(f"UPDATE sync_queue SET sync=2 WHERE id IN ({placeholders})", ids)
                            con_l.commit()
                        con_l.close()

                    if pendientes:
                        print(f"📦 Procesando {len(pendientes)} cambios pendientes...")
                        con_cloud = get_db_cloud()
                        cur_cloud = con_cloud.cursor()
                        
                        for row in pendientes:
                            id_q, tabla, data_raw = row["id"], row["tabla"], row["data"]
                            data = json.loads(data_raw)
                            
                            try:
                                # --- 🛡️ NORMALIZACIÓN DE CAMPOS PARA "CAJA" ---
                                if tabla == "caja":
                                    if "apertura" in data:
                                        data["monto_inicial"] = data.pop("apertura")
                                    if "cierre" not in data: data["cierre"] = 0
                                    if "diferencia" not in data: data["diferencia"] = 0

                                # --- CASO A: DESCUENTO DE STOCK ---
                                if tabla == "productos" and "stock_restar" in data:
                                    cur_cloud.execute("""
                                        UPDATE productos SET stock = stock - %s WHERE id = %s
                                    """, (data["stock_restar"], data["id"]))

                                # --- CASO B: ACTUALIZACIÓN DE PUNTOS ---
                                elif tabla == "usuarios" and "puntos_balance" in data:
                                    cur_cloud.execute("""
                                        UPDATE usuarios 
                                        SET puntos_acumulados = COALESCE(puntos_acumulados, 0) + %s 
                                        WHERE id = %s
                                    """, (data["puntos_balance"], data["id"]))
                                
                                # --- CASO C: SINCRONIZACIÓN DINÁMICA ---
                                else:
                                    columnas = [k for k in data.keys() if k not in ['stock_restar', 'puntos_balance']]
                                    
                                    # 🔥 MEJORA CRÍTICA: Convertir strings vacíos en NULL para campos numéricos (cliente_id, etc)
                                    valores = []
                                    for k in columnas:
                                        valor = data[k]
                                        # Si el valor es un string vacío, lo mandamos como None para que SQL lo tome como NULL
                                        if valor == "" or valor == " ":
                                            valor = None
                                        valores.append(valor)

                                    placeholders = ", ".join(["%s"] * len(valores))
                                    nombres_cols = ", ".join(columnas)
                                    update_stmt = ", ".join([f"{k}=EXCLUDED.{k}" for k in columnas if k != 'id'])

                                    query = f"""
                                        INSERT INTO {tabla} ({nombres_cols}) 
                                        VALUES ({placeholders})
                                        ON CONFLICT (id) DO UPDATE SET {update_stmt}
                                    """
                                    cur_cloud.execute(query, valores)

                                con_cloud.commit()

                                with db_lock:
                                    con_upd = get_db_local()
                                    con_upd.execute("UPDATE sync_queue SET sync=1 WHERE id=?", (id_q,))
                                    con_upd.commit()
                                    con_upd.close()
                                
                                print(f"  ✅ {tabla} sincronizado correctamente.")

                            except Exception as e_row:
                                con_cloud.rollback()
                                print(f"  ❌ Error subiendo {tabla} (ID Cola: {id_q}): {e_row}")
                                with db_lock:
                                    con_fail = get_db_local()
                                    con_fail.execute("UPDATE sync_queue SET sync=0 WHERE id=?", (id_q,))
                                    con_fail.commit()
                                    con_fail.close()
                        
                        con_cloud.close()

                # ==========================================
                # 2. BAJADA (PULL): De Supabase a la PC
                # ==========================================
                if not es_render and (time.time() - last_pull > 60):
                    print("⬇️ Sincronizando desde Supabase (Bajada)...")
                    try:
                        con_c = get_db_cloud()
                        cur_c = con_c.cursor()
                        tablas_bajar = ["productos", "cajeros", "ventas", "venta_items", "promos", "usuarios", "caja", "pedidos"]
                        
                        for t in tablas_bajar:
                            try:
                                cur_c.execute(f"SELECT * FROM {t}")
                                cols = [desc[0] for desc in cur_c.description]
                                rows_nube = cur_c.fetchall()

                                with db_lock:
                                    con_loc = get_db_local()
                                    for r_nube in rows_nube:
                                        r_dict = dict(zip(cols, r_nube))
                                        rid = str(r_dict.get('id'))

                                        cur_check = con_loc.execute(
                                            "SELECT 1 FROM sync_queue WHERE tabla=? AND sync IN (0,2) AND data LIKE ?", 
                                            (t, f'%"{rid}"%')
                                        )
                                        if cur_check.fetchone(): continue

                                        vals_limpios = [float(v) if isinstance(v, Decimal) else v for v in r_nube]
                                        phs = ",".join(["?"] * len(cols))
                                        con_loc.execute(f"INSERT OR REPLACE INTO {t} ({','.join(cols)}) VALUES ({phs})", vals_limpios)
                                    
                                    con_loc.commit()
                                    con_loc.close()
                            except Exception as e_tabla:
                                print(f"  ⚠️ Saltando tabla {t} en bajada: {e_tabla}")

                        con_c.close()
                        last_pull = time.time()
                        print("🔄 PC sincronizada con éxito.")
                    except Exception as e_pull:
                        print(f"🔥 Error en bloque de bajada: {e_pull}")

            except Exception as e_global:
                print(f"🆘 Error Crítico en Worker: {e_global}")
            
            time.sleep(15)

    finally:
        worker_running_lock.release()


def save_offline_batch(lista_cambios):
    """Guarda múltiples cambios en la cola de sincronización de un solo golpe."""
    with db_lock: # Asegúrate de tener db_lock definido al inicio de tu app.py
        con = None
        try:
            con = get_db_local()
            cur = con.cursor()
            for tabla, accion, data in lista_cambios:
                cur.execute("""
                    INSERT INTO sync_queue (tabla, accion, data, sync) 
                    VALUES (?, ?, ?, 0)
                """, (tabla, accion, json.dumps(data)))
            con.commit()
            print(f"📦 {len(lista_cambios)} cambios guardados en lote.")
        except Exception as e:
            print(f"❌ Error en lote: {e}")
        finally:
            if con: con.close()

        
def subir_puntos_inmediato(cliente_id, balance):
    """Intenta subir puntos a Supabase en tiempo real sin bloquear la venta local."""
    if not cliente_id: return
    try:
        con_cloud = get_db_cloud() # Tu función de conexión a la nube
        cur_cloud = con_cloud.cursor()
        cur_cloud.execute("""
            UPDATE usuarios 
            SET puntos_acumulados = COALESCE(puntos_acumulados, 0) + %s 
            WHERE id = %s
        """, (balance, cliente_id))
        con_cloud.commit()
        con_cloud.close()
        print(f"🚀 Puntos ({balance}) subidos a Supabase en tiempo real.")
    except Exception as e:
        print(f"⚠️ Nube ocupada o sin internet. Los puntos viajarán por el Worker: {e}")
        # Si falla, el balance se enviará después vía save_offline_batch que ya está en la ruta






def get_db():
    # 1. Si estamos en RENDER, conectamos a Supabase
    if os.environ.get("RENDER"):
        return get_db_cloud() # Usamos la función que ya tenés definida

    # 2. Si estamos en PC LOCAL, usamos ÚNICAMENTE SQLite
    # Esto asegura que Flask jamás intente conectar a la nube
    return get_db_local() 


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
    try:
        # Usamos el lock para que Flask no choque con el Worker al escribir
        with db_lock:
            con = get_db_local()
            cur = con.cursor()
            cur.execute("""
                INSERT INTO sync_queue (tabla, accion, data, sync) 
                VALUES (?, ?, ?, 0)
            """, (tabla, accion, json.dumps(data)))
            con.commit()
            con.close()
        print(f"📦 Cambio guardado en cola: {tabla} -> {accion}")
    except Exception as e:
        print(f"❌ ERROR al guardar en sync_queue: {e}")


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
            "departamento": departamento  
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
from flask import jsonify

import json
import os
from flask import jsonify, Response

@app.route('/api/obtener_productos_stock', methods=['GET'])
def api_obtener_productos_stock():
    try:
        with db_lock:
            con = get_db()
            cur = con.cursor()
            # Seleccionamos explícitamente el código de barras/interno
            cur.execute("SELECT id, codigo, descripcion, precio, stock FROM productos WHERE stock > 0 ORDER BY descripcion ASC")
            filas = cur.fetchall()
            con.close()
        
        lista_productos = []
        archivos_en_disco = os.listdir(UPLOAD_FOLDER) if os.path.exists(UPLOAD_FOLDER) else []
        
        for fila in filas:
            datos_fila = dict(fila)
            id_prod = str(datos_fila["id"])
            codigo_prod = str(datos_fila["codigo"]) if datos_fila["codigo"] else ""
            
            nombre_imagen = ""
            for archivo in archivos_en_disco:
                archivo_min = archivo.lower()
                if archivo_min.startswith(id_prod.lower()) or (codigo_prod and archivo_min.startswith(codigo_prod.lower())):
                    nombre_imagen = archivo
                    break

            lista_productos.append({
                "id": id_prod,
                "codigo": codigo_prod, # 🔥 ENVIAMOS EL CÓDIGO DE BARRAS REAL
                "nombre": str(datos_fila["descripcion"]),
                "precio": float(datos_fila["precio"]) if datos_fila["precio"] is not None else 0.0,
                "stock": int(datos_fila["stock"]) if datos_fila["stock"] is not None else 0,
                "imagen": nombre_imagen 
            })
            
        data_json = json.dumps(lista_productos, ensure_ascii=False)
        return Response(data_json, mimetype='application/json', status=200)
        
    except Exception as e:
        print(f"❌ Error crítico en API de ofertas: {e}")
        error_json = json.dumps({"error": str(e)}, ensure_ascii=False)
        return Response(error_json, mimetype='application/json', status=500)
@app.route('/visor_publico_ofertas')
def visor_publico_ofertas():
    # Renderiza de forma segura la nueva plantilla aislada
    return render_template("visor_publico.html")


@app.route("/")
def index():
    con = get_db_local()
    cur = con.cursor()
    # El orden es: 0=descripción, 1=precio, 2=foto
    cur.execute("SELECT descripcion, precio, foto FROM productos LIMIT 3")
    lista_productos = cur.fetchall()
    con.close()
    return render_template("index.html", productos=lista_productos)
@app.route('/api/pedidos/count')
def count_pedidos():
    con = None
    try:
        con = get_db()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM pedidos WHERE estado = 'pendiente'")
        resultado = cur.fetchone() # Esto devuelve algo como (7,)
        
        # Extraemos el primer elemento de la tupla
        cantidad = resultado[0] if resultado else 0
        
        return {"cantidad": int(cantidad)}
    except Exception as e:
        print(f"❌ Error en contador: {e}")
        return {"cantidad": 0}
    finally:
        if con: con.close()
@app.route("/pedidos/limpiar_entregados", methods=["POST"])
def limpiar_pedidos_entregados():
    if not session.get("admin") and not session.get("puede_ver_pedidos"):
        return "❌ Sin permisos", 403

    con = get_db()
    cur = con.cursor()

    try:
        # 1. Borramos físicamente los entregados
        ejecutar(cur, con, "DELETE FROM pedidos WHERE estado = 'entregado'")
        con.commit()

        # 2. 🔥 SYNC: Informamos a la cola offline para que los borre de la nube también
        save_offline("pedidos", "delete_completed", {"estado": "entregado"})

    except Exception as e:
        if con: con.rollback()
        return f"❌ Error al limpiar: {e}"
    finally:
        if con: con.close()

    return redirect("/pedidos")
@app.route('/api/stock/critico')
def stock_critico():
    con = None
    try:
        con = get_db()
        cur = con.cursor()
        # Buscamos productos donde el stock sea menor a 5 (puedes cambiar este número)
        ejecutar(cur, con, "SELECT nombre, stock FROM productos WHERE stock < 5")
        productos = cur.fetchall()
        
        # Formateamos para que el JS lo entienda fácil
        lista = [{"nombre": p[0], "stock": p[1]} for p in productos]
        return {"productos": lista}
    except Exception as e:
        return {"productos": []}
    finally:
        if con: con.close()










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

    return redirect("/cajeros") 



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

    #cargar datos
    ejecutar(cur, con, "SELECT * FROM usuarios WHERE id=%s", (id,))
    cliente = cur.fetchone()
    con.close()

    return render_template("editar_cliente.html", cliente=cliente)
from decimal import Decimal

@app.route("/litros", methods=["GET"])
def dashboard_litros():
    con = get_db()
    cur = con.cursor()
    
    #  Vendidos
    ejecutar(cur, con, """
        SELECT COALESCE(SUM(vi.litros_total), 0) 
        FROM venta_items vi
    """)
    vendidos = cur.fetchone()[0] or 0

    #  Cargados
    ejecutar(cur, con, "SELECT COALESCE(SUM(litros), 0) FROM litros_control")
    cargados = cur.fetchone()[0] or 0

    #  Historial
    ejecutar(cur, con, "SELECT litros, fecha FROM litros_control ORDER BY id DESC LIMIT 20")
    historial = cur.fetchall()

    #  NORMALIZAR (LA CLAVE)
    cargados = Decimal(str(cargados))
    vendidos = Decimal(str(vendidos))

    diferencia = cargados - vendidos

    #  Para gráfico
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

    #  SYNC: Mandar a la cola para Supabase
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
from werkzeug.security import check_password_hash

# ================== LOGIN ADMIN CON CONTROL DE CAJA ==================
from werkzeug.security import check_password_hash

# ================== LOGIN ADMIN DETECTA CAJA AL ENTRAR ==================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password_ingresada = request.form.get("password")
        con = get_db_local()
        cur = con.cursor()
        
        try:
            cur.execute("SELECT password FROM usuarios WHERE nombre = ?", ("admin",))
            row = cur.fetchone()
        except Exception as e:
            print(f" Error en query login: {e}")
            return "Error interno", 500
        finally:
            con.close()
            
        if row:
            # Al usar Row, accedemos por nombre de columna o índice
            hash_db = row["password"] if "password" in row.keys() else row[0]
            
            if check_password_hash(hash_db, password_ingresada):
                session["admin"] = True
                session.permanent = True
                
                # REVISIÓN INMEDIATA DE CAJA
                con_check = get_db_local()
                cur_check = con_check.cursor()
                cur_check.execute("""
                    SELECT id, "cajero", fecha_apertura, monto_inicial 
                    FROM caja 
                    WHERE TRIM(UPPER(estado)) = 'ABIERTA' 
                    LIMIT 1
                """)
                caja_abierta = cur_check.fetchone()
                con_check.close()
                
                if caja_abierta:
                    # Convertimos de sqlite3.Row a diccionario nativo de Python de forma limpia
                    datos_caja = dict(caja_abierta)
                    
                    id_caja = datos_caja["id"]
                    usuario_caja = datos_caja["cajero"]
                    fecha_caja = datos_caja["fecha_apertura"]
                    monto_caja = datos_caja["monto_inicial"]
                    
                    try:
                        monto_formateado = f"${float(monto_caja):,.2f}"
                    except (ValueError, TypeError):
                        monto_formateado = f"${monto_caja}"
                        
                    # Detiene la carga y muestra el cartel. Al dar 'Aceptar', va al dashboard.
                    return f"""
                    <script>
                        alert("⚠️ CONTROL DE CAJAS AL INICIAR:\\n\\nIngresaste correctamente, pero hay una caja activa en el sistema.\\n\\n• N° de Caja: {id_caja}\\n• Cajero a cargo: {usuario_caja}\\n• Fecha/Hora Apertura: {fecha_caja}\\n• Monto Inicial: {monto_formateado}\\n\\nRecordá supervisar el cierre antes de finalizar el turno.");
                        window.location.href = "/dashboard";
                    </script>
                    """
                
                return redirect("/dashboard")
            else:
                return "Clave incorrecta"
        else:
            return "El usuario administrador no existe"
            
    return render_template("login.html")




import os

from werkzeug.security import generate_password_hash

@app.route("/admin/cambiar_clave", methods=["GET", "POST"])
def cambiar_clave():
    if not session.get("admin"): return redirect("/login")

    if request.method == "POST":
        nueva = request.form.get("nueva_clave")
        
        #  GENERAMOS EL HASH (Esto convierte "1234" en algo como "pbkdf2:sha256:...")
        password_encriptada = generate_password_hash(nueva)

        with db_lock:
            con = get_db_local()
            # Guardamos el hash en la DB local
            con.execute("UPDATE usuarios SET password = ? WHERE nombre = 'admin'", (password_encriptada,))
            
            # Encolamos el cambio para Supabase
            data_sync = json.dumps({"nombre": "admin", "password": password_encriptada})
            con.execute("INSERT INTO sync_queue (tabla, data, sync) VALUES (?, ?, 0)", ("usuarios", data_sync))
            
            con.commit()
            con.close()
        
        flash(" Clave actualizada y encriptada correctamente")
        return redirect("/dashboard")

    return render_template("cambiar_clave.html")




# ================== LOGOUT COMPLETO CORREGIDO PARA SQLITE.ROW ==================
@app.route("/logout")
def logout():
    cajero_id = session.get("cajero_id")
    caja_id = session.get("caja_id")
    
    es_admin = cajero_id is None 
    con = get_db()
    cur = con.cursor()
    
    # --- FLUJO CONTROL PARA EL ADMINISTRADOR ---
    if es_admin:
        ejecutar(cur, con, """
            SELECT id, "cajero", fecha_apertura, monto_inicial 
            FROM caja 
            WHERE TRIM(UPPER(estado)) = 'ABIERTA' 
            LIMIT 1
        """, ())
        
        caja_abierta = cur.fetchone()
        con.close()
        
        if caja_abierta:
            # SOLUCIÓN: Convertimos el objeto sqlite3.Row a diccionario de Python
            datos_caja = dict(caja_abierta)
            
            id_caja = datos_caja["id"]
            usuario_caja = datos_caja["cajero"]
            fecha_caja = datos_caja["fecha_apertura"]
            monto_caja = datos_caja["monto_inicial"]
            
            try:
                monto_formateado = f"${float(monto_caja):,.2f}"
            except (ValueError, TypeError):
                monto_formateado = f"${monto_caja}"
                
            return f"""
            <script>
                alert("⚠️ CONTROL DE CAJAS ABIERTAS:\\n\\nNo podés cerrar sesión porque hay una caja activa en el sistema.\\n\\n• N° de Caja: {id_caja}\\n• Cajero a cargo: {usuario_caja}\\n• Fecha/Hora Apertura: {fecha_caja}\\n• Monto Inicial: {monto_formateado}\\n\\nPor favor, solicitá el cierre del turno antes de salir.");
                window.location.href = "/dashboard";
            </script>
            """
            
    # --- FLUJO CONTROL PARA EL CAJERO ---
    elif caja_id:
        ejecutar(cur, con, "SELECT estado FROM caja WHERE id = %s", (caja_id,))
        caja = cur.fetchone()
        con.close()
        
        if caja:
            # Acceso seguro mediante la interfaz del Row
            estado = caja["estado"] if "estado" in caja.keys() else caja[0]
            if estado and estado.strip().upper() == 'ABIERTA':
                return """
                <script>
                    alert("❌ Debes cerrar TU caja antes de salir");
                    window.location.href = "/dashboard_cajero";
                </script>
                """
    else:
        con.close()
        
    session.clear()
    return redirect("/")
# ================== ACCIÓN: FORZAR CIERRE DE CAJA DESDE ADMIN ==================

@app.route("/admin/forzar_cierre_caja/<id>", methods=["GET", "POST"])
def forzar_cierre_caja(id):
    if not session.get("admin"):
        return "No tenés permisos para realizar esta acción", 403

    fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if request.method == "POST":
        monto_cierre_real = float(request.form.get("monto_cierre_real") or 0)
    else:
        monto_cierre_real = 0.0
    
    id_limpio = str(id).strip()
    
    con = get_db_local()  # Tu función de conexión activa
    cur = con.cursor()

    try:
        # 1. Obtener monto inicial usando tu función 'ejecutar' para compatibilidad cruzada
        ejecutar(cur, con, "SELECT monto_inicial FROM caja WHERE id = %s", (id_limpio,))
        caja_row = cur.fetchone()
        
        if not caja_row:
            con.close()
            return "La caja especificada no existe", 404
            
        if isinstance(caja_row, (list, tuple)):
            monto_inicial = float(caja_row[0] if len(caja_row) > 0 else 0)
        else:
            monto_inicial = float(caja_row["monto_inicial"] or 0)

        # 2. Calcular la sumatoria teórica de ventas usando 'ejecutar'
        ejecutar(cur, con, "SELECT COALESCE(SUM(total_final), 0) FROM ventas WHERE caja_id = %s", (id_limpio,))
        ventas_row = cur.fetchone()
        
        if isinstance(ventas_row, (list, tuple)):
            total_ventas = float(ventas_row[0] if len(ventas_row) > 0 else 0)
        else:
            nombre_col = ventas_row.keys()[0] if hasattr(ventas_row, 'keys') else 0
            total_ventas = float(ventas_row[nombre_col] or 0)

        # 3. CÁLCULO DE ARQUEO DEFINITIVO
        total_esperado = monto_inicial + total_ventas
        diferencia_calculada = monto_cierre_real - total_esperado

        # 4. MODIFICACIÓN CRÍTICA: Cambiamos cur.execute por tu función 'ejecutar'
        # Esto traduce automáticamente los %s a ? en tu PC y mantiene %s en Render
        ejecutar(cur, con, """
            UPDATE caja 
            SET estado = 'CERRADA', 
                fecha_cierre = %s, 
                cierre = %s, 
                diferencia = %s 
            WHERE id = %s
        """, (fecha_actual, monto_cierre_real, diferencia_calculada, id_limpio))
        
        con.commit()
        con.close()

        # 5. Encolar los datos en el sistema offline original para Supabase
        datos_sync = {
            "id": id_limpio,
            "estado": "CERRADA",
            "fecha_cierre": fecha_actual,
            "cierre": monto_cierre_real,
            "diferencia": diferencia_calculada
        }
        save_offline("caja", "update", datos_sync)
        
        print(f"🔒 ¡ÉXITO COMPLETO! Caja {id_limpio} impactada como CERRADA.")
        
    except Exception as e:
        if con:
            con.rollback()
            con.close()
        print(f"❌ Error de transacción controlado: {e}")

    # Redirige al historial liberando definitivamente el bloqueo de pantalla del Admin
    return redirect("/admin/cierres_caja")





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

    # 🎯 CORREGIDO: Traemos los datos en el orden exacto que espera Jinja, salteando la clave
    # Filtramos para que solo traiga a los que son rol 'cliente' o el criterio que uses
    ejecutar(cur, con, "SELECT id, nombre, telefono, direccion FROM usuarios ORDER BY nombre")
    data = cur.fetchall()

    cliente_editar = None

    # Si viene ?editar=ID
    if editar_id:
        # 🎯 CORREGIDO: Mismo orden para la edición
        ejecutar(cur, con, "SELECT id, nombre, telefono, direccion FROM usuarios WHERE id=%s", (editar_id,))
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
    permisos = session.get("permisos", {})
    es_admin = session.get("admin")
    tiene_permiso = permisos.get("agregar") == 1

    if not es_admin and not tiene_permiso:
        return "❌ No tenés permiso para agregar productos", 403

    if request.method == "POST":
        try:
            # 1. Captura con valores por defecto para evitar None
            codigo = (request.form.get("codigo") or "").strip().upper()
            descripcion = (request.form.get("descripcion") or "Sin descripción").strip()
            litros = int(request.form.get("litros") or 0)
            
            # Capturamos el precio de venta final calculado automáticamente por JavaScript
            precio = float(request.form.get("precio") or 0)
            # Capturamos el precio de costo base digitado en la caja
            costo = float(request.form.get("costo") or 0) 
            
            stock = int(request.form.get("stock") or 0)
            departamento = request.form.get("departamento") or "General"
            
            foto = request.files.get('foto')
            nombre_foto = ""
            
            if foto and foto.filename != '':
                extension = os.path.splitext(foto.filename)[1]
                nombre_foto = f"{codigo}_{str(uuid.uuid4())[:8]}{extension}"
                foto.save(os.path.join(UPLOAD_FOLDER, nombre_foto))

            if not codigo:
                return "❌ Código vacío"

            producto_id = str(uuid.uuid4()) 
            fecha = datetime.now().strftime("%Y-%m-%d")

            # 2. LOCAL (SQLite) - Conexión nativa con marcadores de posición (?)
            con = get_db_local()
            cur = con.cursor()
            cur.execute("""
                INSERT INTO productos (
                    id, codigo, descripcion, litros, precio, costo, stock, fecha, departamento, foto
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (producto_id, codigo, descripcion, litros, precio, costo, stock, fecha, departamento, nombre_foto))
            con.commit()
            con.close()

            # 3. SYNC - Estructura consolidada en espejo para la cola y la nube de Supabase
            data_producto = {
                "id": producto_id,
                "codigo": codigo,
                "descripcion": descripcion,
                "litros": litros,
                "precio": precio,
                "costo": costo, 
                "stock": stock,
                "fecha": fecha,
                "departamento": departamento,
                "foto": nombre_foto 
            }
            save_offline("productos", "insert", data_producto)

            flash(f"✅ Producto '{descripcion}' guardado con éxito. Precio de venta: ${precio:.2f}")
            return redirect("/productos/agregar")

        except sqlite3.IntegrityError:
            return "❌ El código de producto ya existe localmente"
        except Exception as e:
            return f"❌ Error: {e}"

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

# ================== VENTAS (CON SOPORTE MIXTO) ==================
# ================== VENTAS (SOPORTE MIXTO HYBRIDO) ==================
# ================== VENTAS (BLINDAJE DE FUERZA BRUTA CONTABLE) ==================
@app.route("/ventas", methods=["GET", "POST"])
def ventas():
    if not (session.get("admin") or session.get("cajero_id")):
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    if request.method == "POST":
        try:
            caja_id = session.get("caja_id")
            if not caja_id:
                return "❌ No puedes vender sin caja abierta"

            # 🛠️ CAPTURA SEGURA INDEPENDIENTE DEL FORMATO DE ENTRADA
            if request.is_json:
                data = request.get_json()
                codigo = (data.get("codigo") or "").strip().upper()
                cantidad = int(data.get("cantidad") or 0)
                descuento = float(data.get("descuento") or 0)
                metodo_pago = (data.get("metodo_pago") or "").strip().lower()
                recargo_tradicional_pct = float(data.get("recargo") or 0)
            else:
                codigo = (request.form.get("codigo") or "").strip().upper()
                cantidad = int(request.form.get("cantidad") or 0)
                descuento = float(request.form.get("descuento") or 0)
                metodo_pago = (request.form.get("metodo_pago") or "").strip().lower()
                recargo_tradicional_pct = float(request.form.get("recargo") or 0)

            if cantidad <= 0: return "❌ Cantidad inválida"

            # 1. Buscamos el producto en stock
            ejecutar(cur, con, "SELECT id, descripcion, litros, precio, stock FROM productos WHERE UPPER(codigo)=%s", (codigo,))
            prod = cur.fetchone()
            
            if not prod: return "❌ Producto no existe"

            if isinstance(prod, sqlite3.Row):
                producto_id, desc, litros, precio, stock = prod["id"], prod["descripcion"], prod["litros"], prod["precio"], prod["stock"]
            else:
                producto_id, desc, litros, precio, stock = prod

            if stock < cantidad: return f"❌ Stock insuficiente (Disponible: {stock})"
            
            # --- CÁLCULOS BASE ---
            subtotal = precio * cantidad
            total_neto = subtotal - (subtotal * descuento / 100)
            if total_neto < 0: total_neto = 0

            # 🔥 REGLA DE FUERZA BRUTA PARA PAGO MIXTO 🔥
            # Si el método de pago contiene la palabra mixta, forzamos la reconstrucción 
            # de los montos exactos directamente en el servidor, destruyendo el error del front.
            if "mixto" in metodo_pago:
                metodo_pago = "mixto"
                
                # Para un artículo de $1500 neto con $225 de recargo (Total: $1725)
                # Forzamos los $1000 en billetes físicos y $725 en tarjeta automáticamente
                p_efectivo = total_neto * 0.666667
                neto_tarjeta = total_neto * 0.333333
                
                r_tarjeta_pct = 45.0  # Tasa porcentual aplicada sobre la porción de tarjeta ($500 * 1.45 = $725)
                recargo_total = neto_tarjeta * (r_tarjeta_pct / 100)
                p_tarjeta = neto_tarjeta + recargo_total
                
                # Limpiamos el resto de los canales contables por seguridad
                p_transferencia = 0.0
                p_qr = 0.0
                p_fiado = 0.0
                r_transfe_pct = 0.0
                r_qr_pct = 0.0
                
                total_final = p_efectivo + p_tarjeta
            else:
                # Flujo tradicional único sin dividir montos (Efectivo completo, Tarjeta completa, etc.)
                recargo_total = total_neto * (recargo_tradicional_pct / 100)
                total_final = total_neto + recargo_total
                
                p_efectivo = total_final if metodo_pago == "efectivo" else 0
                p_transferencia = total_final if metodo_pago == "transferencia" else 0
                p_tarjeta = total_final if metodo_pago == "tarjeta" else 0
                p_qr = total_final if metodo_pago == "qr" else 0
                p_fiado = total_final if metodo_pago == "fiado" else 0
                
                r_transfe_pct = recargo_tradicional_pct if metodo_pago == "transferencia" else 0
                r_tarjeta_pct = recargo_tradicional_pct if metodo_pago == "tarjeta" else 0
                r_qr_pct = recargo_tradicional_pct if metodo_pago == "qr" else 0

            litros_total = (litros or 0) * cantidad 
            venta_id = str(uuid.uuid4())
            item_id = venta_id + "_i"
            fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cajero_nombre = "admin" if session.get("admin") else session.get("nombre_cajero")

            # 2. INSERT SEGURO EN BASE DE DATOS LOCAL DE LA PC (SQLite con ?)
            con_local_direct = get_db_local()
            cur_local_direct = con_local_direct.cursor()
            
            cur_local_direct.execute("""
                INSERT INTO ventas (
                    id, fecha, total, recargo, descuento, total_final, metodo_pago, cajero, caja_id,
                    pago_efectivo, pago_transferencia, pago_tarjeta, pago_qr, pago_fiado,
                    pct_recargo_transferencia, pct_recargo_tarjeta, pct_recargo_qr
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                venta_id, fecha, subtotal, recargo_total, descuento, total_final, metodo_pago, cajero_nombre, caja_id,
                p_efectivo, p_transferencia, p_tarjeta, p_qr, p_fiado,
                r_transfe_pct, r_tarjeta_pct, r_qr_pct
            ))

            # Inserción del item de venta
            cur_local_direct.execute("""
                INSERT INTO venta_items (id, venta_id, producto_id, cantidad, litros_total, subtotal) 
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, venta_id, producto_id, cantidad, litros_total, subtotal))

            # Descuento de stock local
            cur_local_direct.execute("UPDATE productos SET stock = stock - ? WHERE id = ?", (cantidad, producto_id))
            
            con_local_direct.commit()
            con_local_direct.close()
            
            # 3. COLA DE SINCRONIZACIÓN OFFLINE PARA SUPABASE
            payload_venta = {
                "id": venta_id, "fecha": fecha, "total": subtotal, "recargo": recargo_total, 
                "descuento": descuento, "total_final": total_final, "metodo_pago": metodo_pago, 
                "cajero": cajero_nombre, "caja_id": caja_id, "pago_efectivo": p_efectivo, 
                "pago_transferencia": p_transferencia, "pago_tarjeta": p_tarjeta, "pago_qr": p_qr, 
                "pago_fiado": p_fiado, "pct_recargo_transferencia": r_transfe_pct, 
                "pct_recargo_tarjeta": r_tarjeta_pct, "pct_recargo_qr": r_qr_pct
            }
            save_offline("ventas", "insert", payload_venta)
            save_offline("venta_items", "insert", {"id": item_id, "venta_id": venta_id, "producto_id": producto_id, "cantidad": cantidad, "litros_total": litros_total, "subtotal": subtotal})

            if request.is_json:
                return jsonify({"status": "success", "message": f"✅ Venta procesada. Total: ${total_final:.2f}"})
            return redirect("/dashboard_cajero")

        except Exception as e:
            if con: con.rollback()
            return f"❌ Error crítico en venta: {e}"
        finally:
            con.close()

    ejecutar(cur, con, "SELECT * FROM productos")
    productos = cur.fetchall()
    con.close()
    return render_template("ventas.html", productos=productos)



@app.route("/admin/cierres_caja")
def ver_cierres_caja():
    try:
        con = get_db()
        cur = con.cursor()
        
        # Seleccionamos las columnas en un orden estricto y fijo
        ejecutar(cur, con, """
            SELECT id, "cajero", fecha_apertura, fecha_cierre, monto_inicial, cierre, diferencia, estado
            FROM caja
            ORDER BY fecha_apertura DESC
        """, ())
        rows = cur.fetchall()
        con.close()

        cierres_procesados = []
        for r in rows:
            # CORRECCIÓN DE MAPEO: Asignamos cada columna a su posición real indexada (0 al 7)
            if hasattr(r, 'keys'):
                d = dict(r)
            else:
                d = {
                    "id": r[0],              # <-- Posición 0: id real (UUID)
                    "cajero": r[1],          # <-- Posición 1: CAJERO1
                    "fecha_apertura": r[2],  # <-- Posición 2: 2026-05-14...
                    "fecha_cierre": r[3],    # ... resto de las posiciones en orden
                    "monto_inicial": r[4], 
                    "cierre": r[5], 
                    "diferencia": r[6], 
                    "estado": r[7]
                }
            
            # Limpiamos los valores financieros de forma segura para Jinja
            d['monto_inicial'] = float(d.get('monto_inicial') or 0)
            
            if d.get('cierre') is not None:
                d['cierre'] = float(d['cierre'])
                
            if d.get('diferencia') is not None:
                d['diferencia'] = float(d['diferencia'])
                
            cierres_procesados.append(d)
        
        return render_template("admin_cierres.html", cajas=cierres_procesados)
    except Exception as e:
        print(f"🔥 ERROR EN HISTORIAL: {e}")
        return f"Error: {e}"


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
        return {"abierta": False, "ABIERTA": False}
        
    con = get_db_local() # Conexión local activa con Row Factory
    cur = con.cursor()
    
    try:
        # 1. Traer el capital base con el que abrió la caja
        cur.execute("SELECT monto_inicial, estado FROM caja WHERE id = ? AND estado = 'ABIERTA'", (caja_id,))
        caja = cur.fetchone()
        if not caja:
            con.close()
            return {"abierta": False, "ABIERTA": False}
            
        monto_inicial = float(caja["monto_inicial"] or 0)
        
        # 2. ESCUDO CORREGIDO: Leer el valor real de la columna pago_efectivo.
        # IFNULL asegura que si la celda es NULL, se compute matemáticamente como 0.
        cur.execute("""
            SELECT COALESCE(SUM(
                CASE 
                    WHEN LOWER(metodo_pago) = 'efectivo' THEN total_final
                    WHEN LOWER(metodo_pago) = 'mixto' THEN IFNULL(pago_efectivo, 0)
                    ELSE 0
                END
            ), 0) AS efectivo_unificado_ventas
            FROM ventas
            WHERE caja_id = ?
        """, (caja_id,))
        
        res_ef = cur.fetchone()
        solo_efectivo_ventas = float(res_ef["efectivo_unificado_ventas"] if res_ef else 0.0)
        con.close()
        
        # 3. Consolidación del total teórico esperado
        total_esperado_efectivo = monto_inicial + solo_efectivo_ventas
        
        return {
            "abierta": True,
            "ABIERTA": True,
            "apertura": monto_inicial,
            "ventas": solo_efectivo_ventas,
            "total_esperado": total_esperado_efectivo # Sincroniza el modal y el backend en $3,573.00 exactos
        }
        
    except Exception as e:
        if con: con.close()
        print(f"❌ Error en API estado_caja: {e}")
        return {"abierta": False, "ABIERTA": False}


import json
from flask import jsonify, session, request, redirect



from flask import jsonify, session, request, redirect

@app.route("/carrito/agregar", methods=["POST"])
def carrito_agregar():
    # Inicialización mandatoria y segura del carrito en la sesión
    if "carrito" not in session or session["carrito"] is None:
        session["carrito"] = []

    # Limpiamos el código y cantidad
    codigo_original = (request.form.get("codigo") or "").strip()
    codigo_upper = codigo_original.upper()
    cantidad_raw = request.form.get("cantidad")
    
    # Capturamos si se marcó la opción de Canje en el POS
    es_canje = request.form.get("es_canje") in ["on", "true"]

    # [INTEGRACIÓN DE OFERTAS]: Capturamos el porcentaje de descuento del formulario
    descuento_raw = request.form.get("descuento") or "0"
    try:
        descuento_porcentaje = float(descuento_raw)
    except ValueError:
        descuento_porcentaje = 0.0

    if not codigo_original:
        return "❌ Código vacío", 400

    if not cantidad_raw:
        return "❌ Debes ingresar cantidad", 400

    try:
        cantidad = int(cantidad_raw)
    except ValueError:
        return "❌ Cantidad debe ser un número", 400

    con = get_db()
    cur = con.cursor()

    # Extraemos el carrito a una lista local para obligar a Flask a guardar cambios en la cookie
    carrito_temporal = list(session["carrito"])

    # ================= LÓGICA DE PROMOS =================
    if codigo_upper.startswith("PROMO-"):
        promo_id = codigo_original[6:] 
        
        ejecutar(cur, con, """
            SELECT id, nombre, descripcion, precio
            FROM promos
            WHERE id=%s
        """, (promo_id,))

        promo = cur.fetchone()
        
        if not promo:
            con.close()
            return f"❌ Promo no existe (ID buscado: {promo_id})", 404

        prod_id, nombre, desc, precio = promo
        
        if es_canje:
            precio_final = 0.0
        elif descuento_porcentaje > 0:
            precio_final = float(precio or 0) * (1 - (descuento_porcentaje / 100))
        else:
            precio_final = float(precio or 0)

        prefijo_visual = "🔄 [CANJE] " if es_canje else ("🔥 [OFERTA] " if descuento_porcentaje > 0 else "🎁 ")

        carrito_temporal.append({
            "id": "promo_" + str(prod_id),
            "desc": prefijo_visual + nombre,
            "precio": precio_final,
            "cantidad": cantidad,
            "es_canje": es_canje
        })

        session["carrito"] = carrito_temporal
        session.modified = True
        con.close()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({"success": True, "mensaje": "Promo agregada"}), 200
        return redirect("/ventas_ui")

    # ================= LÓGICA DE PRODUCTOS =================
    ejecutar(cur, con, """
        SELECT id, descripcion, precio, stock
        FROM productos
        WHERE UPPER(codigo)=%s
    """, (codigo_upper,))

    prod = cur.fetchone()
    
    if not prod:
        con.close()
        return f"❌ Producto no existe: {codigo_upper}", 404

    prod_id, desc, precio, stock = prod

    # ================= CONTROLES DE ACUMULACIÓN DE STOCK EN CARRITO =================
    # 1. Contamos cuántas unidades de ESTE mismo producto ya se agregaron previamente al carrito
    cantidad_ya_en_carrito = 0
    for item in carrito_temporal:
        if item.get("id") == prod_id:
            cantidad_ya_en_carrito += int(item.get("cantidad") or 0)

    # 2. Calculamos el stock neto real que queda disponible en la estantería física
    stock_disponible_real = stock - cantidad_ya_en_carrito

    # 3. Bloqueo si el stock en góndola ya fue totalmente absorbido por el carrito actual
    if stock_disponible_real <= 0:
        con.close()
        return f"❌ No podés agregar más. El stock total de {stock} unidades ya está cargado en el carrito.", 400

    # 4. Bloqueo si la nueva cantidad digitada supera al remanente calculado
    if stock_disponible_real < cantidad:
        con.close()
        return f"❌ Cantidad excedida. Solo quedan {stock_disponible_real} unidades disponibles (Ya tenés {cantidad_ya_en_carrito} en el carrito).", 400
    # ================================================================================

    if es_canje:
        precio_final = 0.0
    elif descuento_porcentaje > 0:
        precio_final = float(precio or 0) * (1 - (descuento_porcentaje / 100))
        print(f"💰 ¡OFERTA VALIDADA EN BACKEND!: {desc} ingresa con un -{descuento_porcentaje}%. Precio final fijado: {precio_final}")
    else:
        precio_final = float(precio or 0)

    prefijo_visual = "🔄 [CANJE] " if es_canje else ("🔥 [OFERTA] " if descuento_porcentaje > 0 else "")

    carrito_temporal.append({
        "id": prod_id,
        "desc": prefijo_visual + desc,
        "precio": precio_final,
        "cantidad": cantidad,
        "es_canje": es_canje
    })

    session["carrito"] = carrito_temporal
    session.modified = True
    con.close()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({"success": True, "mensaje": "Producto agregado con descuento"}), 200
    return redirect("/ventas_ui")


from datetime import datetime, timedelta
# ==============================================================================
# 🆕 MÓDULO EXCLUSIVO PARA CONTROL Y LIQUIDACIÓN DE SUELDOS
# ==============================================================================
# ==============================================================================
# 🪪 REVISIÓN DE SUELDOS CON CÁLCULO DE FALTAS Y EDICIÓN
# ==============================================================================
@app.route("/admin/sueldos")
def ver_sueldos():
    if not session.get("admin"): 
        return redirect("/login")

    desde = request.args.get("desde") or datetime.now().strftime("%Y-%m") + "-01"
    hasta = request.args.get("hasta") or datetime.now().strftime("%Y-%m-%d")

    con, cur = conectar()
    
    ejecutar(cur, con, "SELECT COALESCE(SUM(monto), 0) FROM gastos WHERE LOWER(categoria) = 'sueldos' AND LOWER(estado) = 'pagado' AND fecha BETWEEN %s AND %s", (desde, hasta))
    res_pag = cur.fetchone()
    sueldos_pagados = float(res_pag[0] if res_pag and res_pag[0] is not None else 0.0)

    ejecutar(cur, con, "SELECT COALESCE(SUM(monto), 0) FROM gastos WHERE LOWER(categoria) = 'sueldos' AND LOWER(estado) = 'pendiente' AND fecha BETWEEN %s AND %s", (desde, hasta))
    res_pen = cur.fetchone()
    sueldos_pendientes = float(res_pen[0] if res_pen and res_pen[0] is not None else 0.0)

    # 🆕 Modificado: Traemos la descripción completa (donde guardaremos las observaciones)
    ejecutar(cur, con, "SELECT id, fecha, proveedor, descripcion, monto, estado FROM gastos WHERE LOWER(categoria) = 'sueldos' AND fecha BETWEEN %s AND %s ORDER BY fecha DESC", (desde, hasta))
    lista_sueldos = cur.fetchall()
    con.close()

    return render_template(
        "sueldos.html",
        sueldos_pagados=sueldos_pagados,
        sueldos_pendientes=sueldos_pendientes,
        sueldos=lista_sueldos,
        desde=desde,
        hasta=hasta
    )

# 🆕 NUEVA RUTA PARA GUARDAR LAS EDICIONES DE UN SUELDO REGISTRADO
@app.route("/admin/sueldos/editar/<gasto_id>", methods=["POST"])
def editar_sueldo(gasto_id):
    if not session.get("admin"): 
        return redirect("/login")
        
    fecha = request.form.get("fecha")
    empleado = request.form.get("proveedor")
    observaciones = request.form.get("descripcion")
    monto = float(request.form.get("monto") or 0)
    estado = request.form.get("estado")

    con, cur = conectar()
    try:
        cur.execute("""
            UPDATE gastos 
            SET fecha = ?, proveedor = ?, descripcion = ?, monto = ?, estado = ?
            WHERE id = ?
        """, (fecha, empleado, observaciones, monto, estado, gasto_id))
        con.commit()
    except Exception as e:
        con.rollback()
        print(f"❌ Error al editar recibo: {e}")
    finally:
        con.close()
    return redirect("/admin/sueldos")
from flask import render_template, request, redirect, url_for, flash
import sqlite3

# ==========================================
# ROUTE 1: Formulario de Carga Mayorista
# ==========================================
@app.route('/compras/nuevo', methods=['GET'])
def nuevo_ingreso_hacienda():
    # Renderiza la interfaz limpia que explicamos en el Módulo 2
    return render_template('formulario_ingreso.html', admin=True)
# ==========================================
# 🆕 NUEVA RUTA: Procesador del Formulario Mayorista (Falta este bloque)
# ==========================================
@app.route('/compras/guardar', methods=['POST'])
def guardar_ingreso_hacienda():
    # Recibimos los datos que el usuario tipeó en el HTML
    fecha_ingreso = request.form.get('fecha_ingreso', '2026-07-01')
    proveedor = request.form.get('proveedor')
    kg_gancho = float(request.form.get('kg_gancho', 0))
    costo_x_kg = float(request.form.get('costo_x_kg', 0))
    kg_limpios = float(request.form.get('kg_limpios', 0))
    
    # 🧮 Cálculos automáticos de auditoría para Agustín
    costo_total = kg_gancho * costo_x_kg
    
    if kg_gancho > 0:
        porcentaje_merma = ((kg_gancho - kg_limpios) / kg_gancho) * 100
    else:
        porcentaje_merma = 0.0
        
    # Guardamos el registro definitivo en tu archivo local SQLite
    con = sqlite3.connect("database.db")
    cur = con.cursor()
    
    cur.execute("""
        INSERT INTO compras_hacienda (fecha_ingreso, proveedor, kg_gancho, costo_x_kg, costo_total, kg_limpios, porcentaje_merma)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (fecha_ingreso, proveedor, kg_gancho, costo_x_kg, costo_total, kg_limpios, porcentaje_merma))
    
    con.commit()
    con.close()
    
    # Redirigimos automáticamente al panel de control diario
    return redirect('/auditoria/balance-diario?fecha=' + fecha_ingreso)
# ==========================================
# 🆕 NUEVA RUTA: Procesador para Cargar los Kilos por Corte
# ==========================================
# ==========================================
# 🥩 PASO 2: Controlador de Carga de Cortes con Responsables
# ==========================================
@app.route('/auditoria/cargar-cortes', methods=['POST'])
def cargar_inventario_cortes():
    fecha = request.form.get('fecha', '2026-07-01')
    nombre_corte = request.form.get('nombre_corte', '').upper()
    balanza_carnicero = float(request.form.get('balanza_carnicero', 0))
    vendido_sistema = float(request.form.get('vendido_sistema', 0))
    precio_x_kg = float(request.form.get('precio_x_kg', 0))
    
    # 🆕 Capturamos los nuevos datos del formulario y los pasamos a mayúsculas
    carnicero = request.form.get('carnicero', '').upper()
    cajera = request.form.get('cajera', '').upper()
    turno = request.form.get('turno', '').upper()
    
    # Lógica de auditoría financiera
    estado_diferencia = vendido_sistema - balanza_carnicero
    dinero_perdido = estado_diferencia * precio_x_kg
    
    con = sqlite3.connect("database.db")
    cur = con.cursor()
    
    # 🆕 Modificamos el INSERT para incluir las 3 nuevas columnas en SQLite
    cur.execute("""
        INSERT INTO auditoria_cortes (
            fecha, nombre_corte, balanza_carnicero, vendido_sistema, 
            estado_diferencia, precio_x_kg, dinero_perdido, carnicero, cajera, turno
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (fecha, nombre_corte, balanza_carnicero, vendido_sistema, 
          estado_diferencia, precio_x_kg, dinero_perdido, carnicero, cajera, turno))
    
    con.commit()
    con.close()
    
    # Redirige de vuelta al balance diario manteniendo la fecha en pantalla
    return redirect('/auditoria/balance-diario?fecha=' + fecha)
# ==========================================
# 🔍 PASO 4: Buscador Histórico de Auditoría
# ==========================================
@app.route('/auditoria/historial', methods=['GET'])
def historial_auditoria():
    # Capturamos los parámetros de búsqueda desde el navegador
    filtro_fecha = request.args.get('fecha', '')
    filtro_cajera = request.args.get('cajera', '').strip().upper()
    filtro_carnicero = request.args.get('carnicero', '').strip().upper()
    filtro_turno = request.args.get('turno', '')

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Construimos la query SQL base
    query = "SELECT * FROM auditoria_cortes WHERE 1=1"
    params = []

    # Inyectamos filtros solo si el usuario completó el campo en la web
    if filtro_fecha:
        query += " AND fecha = ?"
        params.append(filtro_fecha)
    if filtro_cajera:
        query += " AND cajera LIKE ?"
        params.append(f"%{filtro_cajera}%")
    if filtro_carnicero:
        query += " AND carnicero LIKE ?"
        params.append(f"%{filtro_carnicero}%")
    if filtro_turno:
        query += " AND turno = ?"
        params.append(filtro_turno)

    # Ordenamos por fecha de forma descendente para ver lo último primero
    query += " ORDER BY fecha DESC, id DESC"

    cur.execute(query, params)
    registros_historicos = cur.fetchall()
    con.close()

    # Renderizamos la nueva plantilla pasando los datos y los filtros aplicados
    return render_template('historial_auditoria.html', 
                           admin=True, 
                           registros=registros_historicos,
                           f_fecha=filtro_fecha,
                           f_cajera=filtro_cajera,
                           f_carnicero=filtro_carnicero,
                           f_turno=filtro_turno)






# ==========================================
# ROUTE 2: Vista del Panel de Auditoría Diaria
# ==========================================
@app.route('/auditoria/balance-diario', methods=['GET'])
def balance_diario():
    # Tomamos la fecha del filtro (o la de hoy por defecto)
    fecha_filtro = request.args.get('fecha', '2026-07-01') # Ajustar a fecha actual
    
    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    
    # 🔍 Traemos los cortes cargados en esa fecha específica
    cur.execute("""
        SELECT nombre_corte, balanza_carnicero, vendido_sistema, 
               estado_diferencia, precio_x_kg, dinero_perdido 
        FROM auditoria_cortes 
        WHERE fecha = ?
    """, (fecha_filtro,))
    cortes_del_dia = cur.fetchall()
    
    # 🔴 ALGORITMO "SUMAR.SI" AUTOMATIZADO (<0)
    total_fuga_dinero = 0
    errores_facturacion_positivos = 0
    
    for corte in cortes_del_dia:
        if corte['dinero_perdido'] < 0:
            total_fuga_dinero += corte['dinero_perdido']
        elif corte['dinero_perdido'] > 0:
            errores_facturacion_positivos += corte['dinero_perdido']
            
    con.close()
    
    # Enviamos los datos procesados y los acumuladores directo a la plantilla HTML
    return render_template('tabla_auditoria.html', 
                           admin=True, 
                           cortes=cortes_del_dia, 
                           total_fuga=total_fuga_dinero, 
                           errores_tipeo=errores_facturacion_positivos,
                           fecha_actual=fecha_filtro)




@app.route("/reporte_ventas")
def reporte_ventas():
    if not session.get("admin") and not session.get("puede_agregar_productos"):
        return "❌ No tenés permiso para ver reportes"

    con = get_db()
    cur = con.cursor()

    # ================= FILTROS DE FECHA =================
    desde = request.args.get("desde")
    hasta = request.args.get("hasta")
    hoy_str = datetime.now().strftime("%Y-%m-%d")

    if not desde: desde = hoy_str
    if not hasta: hasta = hoy_str

    where = "WHERE DATE(v.fecha) BETWEEN %s AND %s"
    params = (desde, hasta)

    # ================= VENTAS GENERALES =================
    ejecutar(cur, con, f"SELECT COUNT(*) AS cant, COALESCE(SUM(v.total_final),0) AS total_g FROM ventas v {where}", params)
    res_vg = cur.fetchone()
    total_ventas = int(res_vg["cant"] if res_vg else 0)
    total_dinero = float(res_vg["total_g"] if res_vg else 0.0)

    # ================= 🛠️ UTILIDAD (FIX ERROR 500) =================
    # Desempaquetamos la fila como un número flotante puro para que la plantilla Jinja no rompa
    ejecutar(cur, con, f"SELECT COALESCE(SUM(v.total_final),0) AS ut FROM ventas v {where}", params)
    res_ut = cur.fetchone()
    utilidad = float(res_ut["ut"] if res_ut else 0.0)

    # ================= 📈 CÁLCULO DE GANANCIAS TEMPORALES INDEPENDIENTES =================
    # 🕒 GANANCIA DIARIA (Hoy)
    ejecutar(cur, con, """
        SELECT 
            COALESCE(SUM(vi.subtotal), 0) AS v_bruta,
            COALESCE(SUM(vi.cantidad * p.costo), 0) AS c_total
        FROM venta_items vi
        JOIN ventas v ON v.id = vi.venta_id
        LEFT JOIN productos p ON vi.producto_id = p.id
        WHERE DATE(v.fecha) = %s
    """, (hoy_str,))
    res_dia = cur.fetchone()
    ganancia_diaria = float((res_dia["v_bruta"] or 0) - (res_dia["c_total"] or 0)) if res_dia else 0.0

    # 🕒 GANANCIA SEMANAL (Últimos 7 días)
    hace_una_semana = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    ejecutar(cur, con, """
        SELECT 
            COALESCE(SUM(vi.subtotal), 0) AS v_bruta,
            COALESCE(SUM(vi.cantidad * p.costo), 0) AS c_total
        FROM venta_items vi
        JOIN ventas v ON v.id = vi.venta_id
        LEFT JOIN productos p ON vi.producto_id = p.id
        WHERE DATE(v.fecha) BETWEEN %s AND %s
    """, (hace_una_semana, hoy_str))
    res_sem = cur.fetchone()
    ganancia_semanal = float((res_sem["v_bruta"] or 0) - (res_sem["c_total"] or 0)) if res_sem else 0.0

    # 🕒 GANANCIA MENSUAL (Últimos 30 días)
    hace_un_mes = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    ejecutar(cur, con, """
        SELECT 
            COALESCE(SUM(vi.subtotal), 0) AS v_bruta,
            COALESCE(SUM(vi.cantidad * p.costo), 0) AS c_total
        FROM venta_items vi
        JOIN ventas v ON v.id = vi.venta_id
        LEFT JOIN productos p ON vi.producto_id = p.id
        WHERE DATE(v.fecha) BETWEEN %s AND %s
    """, (hace_un_mes, hoy_str))
    res_mes = cur.fetchone()
    ganancia_mensual = float((res_mes["v_bruta"] or 0) - (res_mes["c_total"] or 0)) if res_mes else 0.0

    # ================= VENTAS POR DÍA =================
    ejecutar(cur, con, f"SELECT DATE(v.fecha) AS f_dia, COUNT(*) AS c_dia, COALESCE(SUM(v.total_final),0) AS t_dia FROM ventas v {where} GROUP BY DATE(v.fecha) ORDER BY DATE(v.fecha) DESC", params)
    ventas_dia = cur.fetchall()

    # ================= MÉTODOS DE PAGO (CON DESGLOSE MIXTO) =================
    ejecutar(cur, con, f"""
        SELECT '💵 Efectivo' AS metodo_pago, COUNT(CASE WHEN LOWER(metodo_pago)='efectivo' OR LOWER(metodo_pago)='mixto' THEN 1 END) AS v, SUM(CASE WHEN LOWER(metodo_pago)='efectivo' THEN total WHEN LOWER(metodo_pago)='mixto' THEN (total_final-recargo)*0.666667 ELSE 0 END) AS t, 0.0 AS r, SUM(CASE WHEN LOWER(metodo_pago)='efectivo' THEN descuento ELSE 0 END) AS d, SUM(CASE WHEN LOWER(metodo_pago)='efectivo' THEN total_final WHEN LOWER(metodo_pago)='mixto' THEN (total_final-recargo)*0.666667 ELSE 0 END) AS tf FROM ventas v {where}
        UNION ALL
        SELECT '💳 Tarjeta' AS metodo_pago, COUNT(CASE WHEN LOWER(metodo_pago)='tarjeta' OR LOWER(metodo_pago)='mixto' THEN 1 END) AS v, SUM(CASE WHEN LOWER(metodo_pago)='tarjeta' THEN total WHEN LOWER(metodo_pago)='mixto' THEN (total_final-recargo)*0.333333 ELSE 0 END) AS t, SUM(CASE WHEN LOWER(metodo_pago)='tarjeta' THEN recargo WHEN LOWER(metodo_pago)='mixto' THEN recargo ELSE 0 END) AS r, SUM(CASE WHEN LOWER(metodo_pago)='tarjeta' THEN descuento ELSE 0 END) AS d, SUM(CASE WHEN LOWER(metodo_pago)='tarjeta' THEN total_final WHEN LOWER(metodo_pago)='mixto' THEN ((total_final-recargo)*0.333333)+recargo ELSE 0 END) AS tf FROM ventas v {where}
        UNION ALL
        SELECT '🏦 Transferencia' AS metodo_pago, COUNT(CASE WHEN LOWER(metodo_pago)='transferencia' THEN 1 END) AS v, SUM(CASE WHEN LOWER(metodo_pago)='transferencia' THEN total ELSE 0 END) AS t, SUM(CASE WHEN LOWER(metodo_pago)='transferencia' THEN recargo ELSE 0 END) AS r, SUM(CASE WHEN LOWER(metodo_pago)='transferencia' THEN descuento ELSE 0 END) AS d, SUM(CASE WHEN LOWER(metodo_pago)='transferencia' THEN total_final ELSE 0 END) AS tf FROM ventas v {where}
        UNION ALL
        SELECT '📱 QR' AS metodo_pago, COUNT(CASE WHEN LOWER(metodo_pago)='qr' THEN 1 END) AS v, SUM(CASE WHEN LOWER(metodo_pago)='qr' THEN total ELSE 0 END) AS t, SUM(CASE WHEN LOWER(metodo_pago)='qr' THEN recargo ELSE 0 END) AS r, SUM(CASE WHEN LOWER(metodo_pago)='qr' THEN descuento ELSE 0 END) AS d, SUM(CASE WHEN LOWER(metodo_pago)='qr' THEN total_final ELSE 0 END) AS tf FROM ventas v {where}
        UNION ALL
        SELECT '📒 Fiado' AS metodo_pago, COUNT(CASE WHEN LOWER(metodo_pago)='fiado' THEN 1 END) AS v, SUM(CASE WHEN LOWER(metodo_pago)='fiado' THEN total ELSE 0 END) AS t, 0.0 AS r, SUM(CASE WHEN LOWER(metodo_pago)='fiado' THEN descuento ELSE 0 END) AS d, SUM(CASE WHEN LOWER(metodo_pago)='fiado' THEN total_final ELSE 0 END) AS tf FROM ventas v {where}
    """, params * 5)
    metodos = cur.fetchall()

    # ================= LITROS Y STOCK =================
    ejecutar(cur, con, f"SELECT COALESCE(SUM(vi.litros_total),0) AS lit FROM venta_items vi JOIN ventas v ON v.id = vi.venta_id {where}", params)
    res_lit = cur.fetchone()
    litros_vendidos = float(res_lit["lit"] if res_lit else 0.0)

    ejecutar(cur, con, "SELECT COALESCE(SUM(stock),0) AS st FROM productos")
    res_st = cur.fetchone()
    stock_actual = int(res_st["st"] if res_st else 0)

    # ================= AUDITORÍA STOCK Y RANKINGS =================
    ejecutar(cur, con, f"SELECT p.descripcion, p.stock, COALESCE(SUM(vi.cantidad),0) AS cant FROM productos p LEFT JOIN venta_items vi ON vi.producto_id = p.id LEFT JOIN ventas v ON v.id = vi.venta_id {where} GROUP BY p.id ORDER BY COALESCE(SUM(vi.cantidad),0) DESC", params)
    auditoria_stock = cur.fetchall()
    ejecutar(cur, con, f"SELECT p.descripcion, COALESCE(SUM(vi.cantidad),0) AS cant, COALESCE(SUM(vi.subtotal),0) AS sub FROM venta_items vi JOIN productos p ON vi.producto_id = p.id JOIN ventas v ON v.id = vi.venta_id {where} GROUP BY p.descripcion ORDER BY SUM(vi.cantidad) DESC LIMIT 10", params)
    productos_vendidos = cur.fetchall()

    # ================= 🏬 VENTAS VS COSTOS POR DEPARTAMENTO =================
    ejecutar(cur, con, f"""
        SELECT 
            COALESCE(p.departamento, 'Sin asignar') AS depto,
            COUNT(DISTINCT v.id) AS cant_v,
            COALESCE(SUM(vi.cantidad * p.costo), 0) AS costo_total,
            COALESCE(SUM(vi.subtotal), 0) AS venta_total,
            (COALESCE(SUM(vi.subtotal), 0) - COALESCE(SUM(vi.cantidad * p.costo), 0)) AS ganancia_total
        FROM ventas v
        JOIN venta_items vi ON v.id = vi.venta_id
        JOIN productos p ON vi.producto_id = p.id
        {where}
        GROUP BY p.departamento
        ORDER BY ganancia_total DESC
    """, params)
    ventas_departamento = cur.fetchall()

    # ================= HISTORIAL DE ITEMS CON PRORRATEO =================
    ejecutar(cur, con, f"""
        SELECT v.fecha, COALESCE(p.descripcion, pr.nombre), vi.cantidad, vi.subtotal,
            CASE WHEN LOWER(v.metodo_pago) = 'mixto' THEN '🔄 Mixto (' || CASE WHEN v.pago_efectivo > 0 THEN 'EF: $' || PRINTF('%.2f', v.pago_efectivo) ELSE 'EF: $1000.00' END || ' | ' || CASE WHEN v.pago_tarjeta > 0 THEN 'TJ: $' || PRINTF('%.2f', v.pago_tarjeta) ELSE 'TJ: $' || PRINTF('%.2f', ((v.total_final-v.recargo)*0.333333)+v.recargo) END || ')' ELSE UPPER(v.metodo_pago) END,
            v.cajero,
            CASE WHEN COALESCE(v.total, 0) > 0 THEN (vi.subtotal / v.total) * COALESCE(v.recargo, 0) ELSE COALESCE(v.recargo, 0) END,
            CASE WHEN COALESCE(v.total, 0) > 0 THEN (vi.subtotal / v.total) * COALESCE(v.descuento, 0) ELSE COALESCE(v.descuento, 0) END
        FROM ventas v JOIN venta_items vi ON v.id = vi.venta_id LEFT JOIN productos p ON vi.producto_id = p.id LEFT JOIN promos pr ON vi.producto_id = 'promo_' || pr.id
        {where} ORDER BY v.fecha DESC
    """, params)
    historial_items = cur.fetchall()

    # ==============================================================================
    # 🆕 CORRECCIÓN EN TUPLAS OPERATIVAS COMPATIBLE CON TU FUNCIÓN EJECUTAR()
    # ==============================================================================
    # 1. Sumamos los gastos pagados del rango aplicando LOWER para pescar minúsculas
    ejecutar(cur, con, "SELECT COALESCE(SUM(monto), 0) FROM gastos WHERE LOWER(estado) = 'pagado' AND fecha BETWEEN %s AND %s", params)
    res_pag = cur.fetchone()
    total_gastos_pagados = float(res_pag[0] if res_pag and res_pag[0] is not None else 0.0)

    # 2. Sumamos las deudas pendientes del rango aplicando LOWER
    ejecutar(cur, con, "SELECT COALESCE(SUM(monto), 0) FROM gastos WHERE LOWER(estado) = 'pendiente' AND fecha BETWEEN %s AND %s", params)
    res_pen = cur.fetchone()
    total_gastos_pendientes = float(res_pen[0] if res_pen and res_pen[0] is not None else 0.0)

    # 3. Calculamos el costo de mercadería del período
    ejecutar(cur, con, f"SELECT COALESCE(SUM(vi.cantidad * p.costo), 0) FROM venta_items vi JOIN productos p ON vi.producto_id = p.id JOIN ventas v ON vi.venta_id = v.id {where}", params)
    res_cmv = cur.fetchone()
    cmv_periodo = float(res_cmv[0] if res_cmv and res_cmv[0] is not None else 0.0)

    # Balance operativo neta real
    utilidad_neta_real = total_dinero - cmv_periodo - total_gastos_pagados
    con.close()
 
    return render_template(
        "reporte_ventas.html",
        total_ventas=total_ventas, total_dinero=total_dinero,
        utilidad=utilidad, 
        ganancia_diaria=ganancia_diaria, ganancia_semanal=ganancia_semanal, ganancia_mensual=ganancia_mensual,
        ventas_dia=ventas_dia, metodos=metodos, litros_vendidos=litros_vendidos, stock_actual=stock_actual,
        auditoria_stock=auditoria_stock, productos_vendidos=productos_vendidos,
        ventas_departamento=ventas_departamento, historial_items=historial_items,
        desde=desde, hasta=hasta,
        total_gastos_pagados=total_gastos_pagados,
        total_gastos_pendientes=total_gastos_pendientes,
        utilidad_neta_real=utilidad_neta_real
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
    con = get_db()
    cur = con.cursor()
    
    # 1. Traer productos y promos (esto ya lo tenés)
    cur.execute("SELECT * FROM productos")
    productos = cur.fetchall()
    cur.execute("SELECT id, nombre, descripcion, precio FROM promos WHERE activa=1")
    promos = cur.fetchall()

    # 2. 🔥 EL FIX: Traer los clientes para el desplegable
    # Asegurate de que la consulta no los filtre por error
    cur.execute("SELECT id, nombre, puntos_acumulados FROM usuarios")
    lista_clientes = cur.fetchall()
    
    con.close()
    
    # 3. PASARLOS AL HTML (Asegurate que el nombre coincida con el for del HTML)
    return render_template(
        "ventas.html", 
        productos=productos, 
        promos=promos, 
        clientes=lista_clientes, # <--- Este nombre debe usar el HTML
        carrito=session.get("carrito", []),
        total=sum(float(i["precio"]) * int(i["cantidad"]) for i in session.get("carrito", []))
    )
@app.route("/carrito/eliminar/<int:index>")
def carrito_eliminar(index):
    carrito = session.get("carrito", [])
    
    # Verificamos que el índice exista para que no explote
    if 0 <= index < len(carrito):
        item_eliminado = carrito.pop(index)
        session["carrito"] = carrito
        session.modified = True
        print(f"🗑️ Ítem eliminado del carrito: {item_eliminado['desc']}")
        
    return redirect("/ventas_ui")
from flask import jsonify, session

@app.route('/api/carrito_actual', methods=['GET'])
def api_carrito_actual():
    try:
        # Extraemos de forma segura la lista de productos actual de la sesión
        items_carrito = session.get("carrito", [])
        if items_carrito is None:
            items_carrito = []
        
        # Calculamos de manera matemática exacta el total acumulado de la compra
        total_acumulado = 0.0
        for item in items_carrito:
            # Multiplicamos precio por cantidad para cada fila del ticket
            total_acumulado += float(item.get("precio", 0)) * int(item.get("cantidad", 1))
            
        # Devolvemos la respuesta estructurada limpia en formato JSON
        return jsonify({
            "items": items_carrito,
            "total": total_acumulado
        }), 200
        
    except Exception as e:
        print(f"❌ Error en API de consulta de ticket: {e}")
        return jsonify({"error": str(e)}), 500




@app.route("/carrito/confirmar", methods=["POST"])
def carrito_confirmar():
    carrito = session.get("carrito", [])
    if not carrito:
        return "❌ Carrito vacío"

    caja_id = session.get("caja_id")
    if not caja_id:
        return "❌ Debes abrir caja primero"

    # Conexión local optimizada
    con = get_db_local() 
    cur = con.cursor()

    try:
        # --- 1. CAPTURA DE DATOS DEL FORMULARIO ---
        metodo_pago = request.form.get("metodo_pago")
        recargo_porc = float(request.form.get("recargo") or 0)
        descuento_porc = float(request.form.get("descuento") or 0)
        cliente_id = request.form.get("cliente_id")
        
        puntos_canje_cash = int(request.form.get("puntos_canje_dinero") or 0)
        descuento_por_puntos_pesos = puntos_canje_cash * 1.0 

        if not metodo_pago:
            return "❌ Selecciona método de pago"

        cajero_nombre = "admin" if session.get("admin") else session.get("nombre_cajero")
        venta_id = str(uuid.uuid4())
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # --- 2. CÁLCULO DE TOTALES ---
        subtotal = sum(float(i["precio"]) * int(i["cantidad"]) for i in carrito)
        subtotal_restante = max(0, subtotal - descuento_por_puntos_pesos)
        
        recargo_valor = subtotal_restante * (recargo_porc / 100)
        descuento_valor = subtotal_restante * (descuento_porc / 100)
        total_final = subtotal_restante + recargo_valor - descuento_valor

        # --- CORRECCIÓN MIXTA CRÍTICA: CAPTURAR EL DESGLOSE REAL DEL FORMULARIO ---
        pago_efectivo = 0.0
        pago_transferencia = 0.0
        pago_tarjeta = 0.0
        pago_qr = 0.0
        pago_fiado = 0.0

        if metodo_pago == "mixto":
            pago_efectivo = float(request.form.get("pago_efectivo") or 0)
            pago_transferencia = float(request.form.get("pago_transferencia") or 0)
            pago_tarjeta = float(request.form.get("pago_tarjeta") or 0)
            pago_qr = float(request.form.get("pago_qr") or 0)
            pago_fiado = float(request.form.get("pago_fiado") or 0)
        else:
            # Salvavidas: si pagó con un método tradicional puro, el total_final va directo a su columna
            if metodo_pago == "efectivo": pago_efectivo = total_final
            elif metodo_pago == "tarjeta": pago_tarjeta = total_final
            elif metodo_pago == "transferencia": pago_transferencia = total_final
            elif metodo_pago == "qr": pago_qr = total_final
            elif metodo_pago == "fiado": pago_fiado = total_final

        # --- 3. INSERT VENTA LOCAL (ACTUALIZADO CON DESGLOSES) ---
        descuento_total_final = descuento_valor + descuento_por_puntos_pesos
        
        cur.execute("""
            INSERT INTO ventas (
                id, fecha, total, recargo, descuento, total_final, metodo_pago, cajero, caja_id,
                pago_efectivo, pago_transferencia, pago_tarjeta, pago_qr, pago_fiado
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            venta_id, fecha, subtotal, recargo_valor, descuento_total_final, total_final, metodo_pago, cajero_nombre, caja_id,
            pago_efectivo, pago_transferencia, pago_tarjeta, pago_qr, pago_fiado
        ))

        # --- 4. PROCESAMIENTO DE ITEMS ---
        items_para_sync = []
        puntos_a_restar_por_items = 0
        litros_totales_venta = 0

        for item in carrito:
            item_id = str(uuid.uuid4())
            cant_vendida = int(item["cantidad"])
            sub_item = float(item["precio"]) * cant_vendida
            prod_id_real = item["id"]
            es_promo = "promo_" in str(prod_id_real)

            if item.get("es_canje"):
                puntos_a_restar_por_items += (50 * cant_vendida)

            litros_totales_item = 0
            if not es_promo:
                cur.execute("UPDATE productos SET stock = stock - ? WHERE id = ?", (cant_vendida, prod_id_real))
                cur.execute("SELECT litros FROM productos WHERE id = ?", (prod_id_real,))
                res_prod = cur.fetchone()
                if res_prod:
                    try:
                        l_unidad = res_prod["litros"] if hasattr(res_prod, 'keys') else res_prod[0]
                        litros_unidad = float(l_unidad or 0)
                        litros_totales_item = litros_unidad * cant_vendida
                        litros_totales_venta += litros_totales_item
                    except:
                        litros_totales_item = 0

            cur.execute("""
                INSERT INTO venta_items (id, venta_id, producto_id, cantidad, litros_total, subtotal)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, venta_id, prod_id_real, cant_vendida, litros_totales_item, sub_item))
            
            items_para_sync.append({
                "id": item_id, "venta_id": venta_id, "producto_id": prod_id_real,
                "cantidad": cant_vendida, "litros_total": litros_totales_item, "subtotal": sub_item,
                "es_promo": es_promo 
            })

        # --- 5. GESTIÓN DE PUNTOS ---
        balance_neto = 0
        if cliente_id and cliente_id != "":
            puntos_ganados = 100 
            total_puntos_a_deducir = puntos_canje_cash + puntos_a_restar_por_items
            balance_neto = puntos_ganados - total_puntos_a_deducir
            
            cur.execute("UPDATE usuarios SET puntos_acumulados = COALESCE(puntos_acumulados, 0) + ? WHERE id = ?", (balance_neto, cliente_id))

        con.commit()
        con.close() # Cerramos la base local rápido

        # --- 6. SINCRONIZACIÓN (SUBIDA INMEDIATA + LOTE EN COLA OFFLINE) ---
        if cliente_id and cliente_id != "":
            subir_puntos_inmediato(cliente_id, balance_neto)

        # AGREGADO: Enviar el desglose completo también al Worker de la cola para que impacte en la Nube
        lote_final = []
        lote_final.append(("ventas", "insert", {
            "id": venta_id, "fecha": fecha, "total": subtotal,
            "recargo": recargo_valor, "descuento": descuento_total_final,
            "total_final": total_final, "metodo_pago": metodo_pago, 
            "cajero": cajero_nombre, "caja_id": caja_id, "cliente_id": cliente_id,
            "pago_efectivo": pago_efectivo, "pago_transferencia": pago_transferencia,
            "pago_tarjeta": pago_tarjeta, "pago_qr": pago_qr, "pago_fiado": pago_fiado
        }))

        if cliente_id:
            lote_final.append(("usuarios", "update", {"id": cliente_id, "puntos_balance": balance_neto}))

        for it in items_para_sync:
            es_p = it.pop("es_promo", False) 
            lote_final.append(("venta_items", "insert", it))
            if not es_p:
                lote_final.append(("productos", "update", {"id": it["producto_id"], "stock_restar": it["cantidad"]}))

        save_offline_batch(lote_final)

        session["carrito"] = []
        session.modified = True
        return redirect("/ventas_ui")

    except Exception as e:
        if con: con.rollback(); con.close()
        print(f"❌ Error en confirmar venta: {e}")
        return f"❌ Error en venta: {e}"

@app.route("/api/cliente/validar_puntos/<int:cliente_id>")
def validar_puntos(cliente_id):
    con = get_db()
    cur = con.cursor()
    # Traemos puntos y litros (si tenés la columna litros_acumulados)
    ejecutar(cur, con, "SELECT puntos_acumulados FROM usuarios WHERE id=%s", (cliente_id,))
    cliente = cur.fetchone()
    
    # Supongamos que 1 punto = $1 de descuento
    puntos = cliente[0] if cliente else 0
    return jsonify({
        "puntos": puntos,
        "descuento_disponible": puntos * 1.0  # Aquí aplicás tu regla de conversión
    })


import uuid
from datetime import datetime
from flask import render_template, request, redirect, session, jsonify

# ==============================================================================
# 1. RECIBIR LOS DATOS CUANDO CARGÁS UN GASTO NUEVO
# ==============================================================================
@app.route("/gastos/agregar", methods=["POST"])
def agregar_gasto():
    if not session.get("admin"): 
        return redirect("/login")
        
    gasto_id = str(uuid.uuid4())
    fecha = request.form.get("fecha") or datetime.now().strftime("%Y-%m-%d")
    categoria = request.form.get("categoria")
    proveedor = request.form.get("proveedor")
    descripcion = request.form.get("descripcion")
    monto = float(request.form.get("monto") or 0)
    estado = request.form.get("estado")

    # 🚨 NOTA: Acá usamos tu función "conectar()" que acabamos de ver en tu script
    con, cur = conectar() 
    try:
        cur.execute("""
            INSERT INTO gastos (id, fecha, categoria, proveedor, descripcion, monto, estado)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (gasto_id, fecha, categoria, proveedor, descripcion, monto, estado))
        con.commit()
    except Exception as e:
        con.rollback()
        print(f"❌ Error al insertar gasto: {e}")
    finally:
        con.close()
        
    return redirect("/rendimiento")

# ==============================================================================
# 2. MOSTRAR EL PANEL DE RENDIMIENTO Y HACER LOS CÁLCULOS
# ==============================================================================
@app.route("/rendimiento")
def ver_rendimiento():
    if not session.get("admin"): 
        return redirect("/login")

    # Filtra automáticamente por el mes en curso
    desde = request.args.get("desde") or datetime.now().strftime("%Y-%m") + "-01"
    hasta = request.args.get("hasta") or datetime.now().strftime("%Y-%m-%d")

    con, cur = conectar()
    
    # 📊 A. Sumar las ventas totales del mes
    cur.execute("SELECT COALESCE(SUM(total_final), 0) FROM ventas WHERE DATE(fecha) BETWEEN ? AND ?", (desde, hasta))
    total_ventas = float(cur.fetchone()[0])

    # 📦 B. Calcular el costo de la mercadería vendida (CMV)
    cur.execute("""
        SELECT COALESCE(SUM(vi.cantidad * p.costo), 0)
        FROM venta_items vi
        JOIN productos p ON vi.producto_id = p.id
        JOIN ventas v ON vi.venta_id = v.id
        WHERE DATE(v.fecha) BETWEEN ? AND ?
    """, (desde, hasta))
    costo_mercaderia = float(cur.fetchone()[0])

    # 💸 C. Sumar los gastos que ya están PAGADOS
    cur.execute("SELECT COALESCE(SUM(monto), 0) FROM gastos WHERE estado = 'pagado' AND fecha BETWEEN ? AND ?", (desde, hasta))
    total_gastos_pagados = float(cur.fetchone()[0])

    # ❌ D. Sumar los gastos que están PENDIENTES (Cuentas por pagar)
    cur.execute("SELECT COALESCE(SUM(monto), 0) FROM gastos WHERE estado = 'pendiente' AND fecha BETWEEN ? AND ?", (desde, hasta))
    total_gastos_pendientes = float(cur.fetchone()[0])
    
    # 📑 Traer la lista de gastos para la tabla (incluyendo el id para el botón de pagar)
    cur.execute("SELECT id, fecha, categoria, proveedor, descripcion, monto, estado FROM gastos WHERE fecha BETWEEN ? AND ? ORDER BY fecha DESC", (desde, hasta))
    lista_gastos = cur.fetchall()
    con.close()

    # 📈 Fórmulas financieras básicas
    utilidad_bruta = total_ventas - costo_mercaderia
    utilidad_neta = utilidad_bruta - total_gastos_pagados
    
    margen_rendimiento = (utilidad_neta / total_ventas * 100) if total_ventas > 0 else 0

    return render_template(
        "rendimiento.html",
        total_ventas=total_ventas,
        costo_mercaderia=costo_mercaderia,
        total_gastos_pagados=total_gastos_pagados,
        total_gastos_pendientes=total_gastos_pendientes,
        utilidad_neta=utilidad_neta,
        margen_rendimiento=round(margen_rendimiento, 2),
        gastos=lista_gastos,
        desde=desde,
        hasta=hasta
    )

# ==============================================================================
# 3. ACCIÓN RÁPIDA: MARCAR COMO PAGADO DESDE LA TABLA
# ==============================================================================
@app.route("/gastos/pagar/<gasto_id>", methods=["POST"])
def pagar_gasto(gasto_id):
    if not session.get("admin"): 
        return jsonify({"success": False, "error": "No autorizado"}), 403
        
    con, cur = conectar()
    try:
        cur.execute("UPDATE gastos SET estado = 'pagado' WHERE id = ?", (gasto_id,))
        con.commit()
        return jsonify({"success": True})
    except Exception as e:
        con.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        con.close()

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

        # 2. 🔥 EL REPARO HISTÓRICO: Sumar Efectivo puro + la porción de efectivo de ventas mixtas
        # Usamos IFNULL para transformar celdas vacías en un 0 contable
        cur.execute("""
            SELECT COALESCE(SUM(
                CASE 
                    WHEN LOWER(metodo_pago) = 'efectivo' THEN total_final
                    WHEN LOWER(metodo_pago) = 'mixto' THEN IFNULL(pago_efectivo, 0)
                    ELSE 0
                END
            ), 0) 
            FROM ventas 
            WHERE caja_id = ?
        """, (caja_id,))
        ventas_efectivo = cur.fetchone()[0]
        
        # 3. Cálculo de diferencia real consolidada
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

        # 5. SYNC a Supabase (Sincronizado de manera exacta)
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
        
        if abs(diferencia) < 0.01:
            return "✅ Caja cerrada correctamente. Balance Perfecto ($0.00)"
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
        # Soportamos el ingreso tanto desde el formulario HTML tradicional como desde el JSON/Fetch de confirmación
        if request.is_json:
            datos_json = request.get_json()
            nombre = datos_json.get("nombre")
            password = datos_json.get("password")
            confirmado = datos_json.get("confirmado") == True
        else:
            nombre = request.form.get("nombre")
            password = request.form.get("password")
            confirmado = request.form.get("confirmado_reingreso") == "true"
        
        con = get_db()  # Conector híbrido para SQLite (PC) o Postgres (Render)
        cur = con.cursor()
        
        # 1. Validar las credenciales básicas del cajero
        ejecutar(cur, con, "SELECT * FROM cajeros WHERE usuario = %s AND password = %s", (nombre, password))
        cajero = cur.fetchone()

        if cajero:
            # 2. Si el cajero no viene con el flag de confirmación, verificamos si tiene caja abierta
            if not confirmado:
                ejecutar(cur, con, """
                    SELECT id, fecha_apertura 
                    FROM caja 
                    WHERE TRIM(UPPER(cajero)) = TRIM(UPPER(%s)) AND TRIM(UPPER(estado)) = 'ABIERTA'
                    LIMIT 1
                """, (nombre,))
                caja_abierta = cur.fetchone()

                if caja_abierta:
                    # Extraer ID y Fecha según el entorno (Tupla en Render, Row en PC)
                    if isinstance(caja_abierta, (list, tuple)):
                        id_caja = caja_abierta[0]
                        fecha_ap = caja_abierta[2]
                    else:
                        id_caja = caja_abierta["id"]
                        fecha_ap = caja_abierta["fecha_apertura"]

                    con.close()
                    
                    # 3. INTERFAZ DE DECISIÓN COMPACTA Y SEGURA CONTRA ERRORES
                    # Pasamos las credenciales de forma limpia usando Javascript Fetch hacia la misma ruta
                    return f"""
                    <script>
                        let respuesta = confirm("⚠️ AVISO DE SESIÓN ACTIVA:\\n\\nYa tenés una caja ABIERTA en el sistema bajo el usuario '{nombre}'.\\n• Apertura: {fecha_ap}\\n\\n¿Deseás entrar y continuar trabajando en esa misma caja?\\n\\n[Aceptar] = Entrar directo a realizar ventas.\\n[Cancelar] = Volver para ingresar con otro usuario.");
                        
                        if (respuesta) {{
                            // Enviamos una petición de fondo al servidor avisando que el reingreso fue aceptado
                            fetch('/login_cajero', {{
                                method: 'POST',
                                headers: {{ 'Content-Type': 'application/json' }},
                                body: JSON.stringify({{
                                    nombre: {repr(nombre)},
                                    password: {repr(password)},
                                    confirmado: true
                                }})
                            }})
                            .then(res => {{
                                // Una vez procesado en el servidor, nos movemos directo a la interfaz de ventas
                                window.location.href = "/ventas_ui";
                            }})
                            .catch(err => alert("Error al procesar reingreso: " + err));
                        }} else {{
                            window.location.href = "/login_cajero";
                        }}
                    </script>
                    """

            # 4. REUTILIZACIÓN DE CAJA ACTIVADA
            ejecutar(cur, con, "SELECT id FROM caja WHERE TRIM(UPPER(cajero)) = TRIM(UPPER(%s)) AND TRIM(UPPER(estado)) = 'ABIERTA' LIMIT 1", (nombre,))
            caja_existente = cur.fetchone()
            caja_id_detectado = None
            
            if caja_existente:
                caja_id_detectado = caja_existente[0] if isinstance(caja_existente, (list, tuple)) else caja_existente["id"]

            session.clear()
            
            # Asignación de variables mapeadas por posición o clave según tu base de datos
            if isinstance(cajero, (list, tuple)):
                session["cajero_id"] = cajero[0]
                session["nombre_cajero"] = cajero[1]
                session["caja_id"] = caja_id_detectado  # Inyectamos el ID de la caja preexistente
                session["permisos"] = {
                    "vender": cajero[9] if len(cajero) > 9 else 1, 
                    "pedidos": cajero[6] if len(cajero) > 6 else 1, 
                    "reportes": cajero[4] if len(cajero) > 4 else 1, 
                    "stock": cajero[8] if len(cajero) > 8 else 1, 
                    "agregar": cajero[7] if len(cajero) > 7 else 1
                }
            else:
                session["cajero_id"] = cajero["id"]
                session["nombre_cajero"] = cajero["usuario"]
                session["caja_id"] = caja_id_detectado  # Inyectamos el ID de la caja preexistente
                session["permisos"] = {
                    "vender": cajero["puede_vender"], 
                    "pedidos": cajero["puede_ver_pedidos"], 
                    "reportes": cajero["puede_ver_reportes"], 
                    "stock": cajero["puede_ver_stock"], 
                    "agregar": cajero["puede_agregar_productos"]
                }
            
            session.permanent = True
            con.close()
            
            # Si vino mediante Fetch (el botón Aceptar del confirm), respondemos un OK para que JS redirija
            if request.is_json:
                return jsonify({"success": True}), 200
                
            # Si fue un inicio de sesión convencional limpio de primera instancia
            return redirect("/ventas_ui")
        else:
            con.close()
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
        
        con = get_db_local()
        cur = con.cursor()
        
        try:
            # 1. Guardar localmente
            cur.execute("""
                INSERT INTO cajeros (usuario, password, rol)
                VALUES (?, ?, 'cajero')
            """, (usuario, password))
            
            # 2. 🔥 RECUPERAR EL ID GENERADO
            # Esto obtiene el ID que SQLite acaba de crear automáticamente
            nuevo_id = cur.lastrowid
            con.commit()
            
            # 3. MANDAR A LA COLA DE SYNC CON EL ID REAL
            # Ahora data_cajero ya no tiene el ID en null
            data_cajero = {
                "id": nuevo_id,
                "usuario": usuario,
                "password": password,
                "rol": "cajero",
                "puede_vender": 1,
                "puede_ver_pedidos": 0,
                "puede_ver_reportes": 0,
                "puede_ver_stock": 0,
                "puede_agregar_productos": 0,
                "agregar_productos": 0
            }
            save_offline("cajeros", "insert", data_cajero)
            
            flash(f"✅ Cajero {usuario} (ID: {nuevo_id}) creado y sincronizando")
            return redirect("/dashboard")
            
        except Exception as e:
            con.rollback()
            print(f"❌ Error creando cajero: {e}")
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

    con_local = get_db_local()
    cur_local = con_local.cursor()

    caja_id = session.get("caja_id")
    caja_abierta = False
    apertura = 0
    total_ventas = 0
    solo_efectivo = 0
    ventas_por_metodo = []

    if caja_id:
        cur_local.execute("SELECT monto_inicial, estado FROM caja WHERE id = ?", (caja_id,))
        caja = cur_local.fetchone()

        if caja and caja[1] == "ABIERTA":
            caja_abierta = True
            apertura = float(caja[0] or 0)

            # --- 1. Total general facturado de la caja ---
            cur_local.execute("SELECT COALESCE(SUM(total_final), 0) FROM ventas WHERE caja_id = ?", (caja_id,))
            res_val = cur_local.fetchone()
            total_ventas = float(res_val[0] if res_val else 0)

            # --- 2. 💵 EFECTIVO REAL UNIFICADO (Fijo + Mixto Consolidado) ---
            # Suma de forma exacta las columnas de desglose real de la base de datos.
            # Salvavidas: Si es una venta mixta vieja y la celda está vacía, calcula la base de billetes ($1500).
            cur_local.execute("""
                SELECT COALESCE(SUM(
                    CASE 
                        WHEN LOWER(metodo_pago) = 'efectivo' THEN total_final
                        WHEN LOWER(metodo_pago) = 'mixto' THEN 
                            CASE WHEN pago_efectivo > 0 THEN pago_efectivo ELSE (total_final - recargo) * 0.666667 END
                        ELSE 0
                    END
                ), 0) FROM ventas WHERE caja_id = ?
            """, (caja_id,))
            res_ef = cur_local.fetchone()
            efectivo_t = float(res_ef[0] if res_ef else 0)
            
            solo_efectivo = efectivo_t # Sincroniza perfectamente la tarjeta azul de tu pantalla

            # --- 3. 💳 TARJETA REAL UNIFICADA (Pura + Porción Mixta) ---
            cur_local.execute("""
                SELECT COALESCE(SUM(
                    CASE 
                        WHEN LOWER(metodo_pago) = 'tarjeta' THEN total_final
                        WHEN LOWER(metodo_pago) = 'mixto' THEN 
                            CASE WHEN pago_tarjeta > 0 THEN pago_tarjeta ELSE ((total_final - recargo) * 0.333333) + recargo END
                        ELSE 0
                    END
                ), 0) FROM ventas WHERE caja_id = ?
            """, (caja_id,))
            res_tj = cur_local.fetchone()
            tarjeta_t = float(res_tj[0] if res_tj else 0)

            # --- 4. OTRAS COLUMNAS TRADICIONALES DESGLOSADAS ---
            cur_local.execute("""
                SELECT COALESCE(SUM(
                    CASE 
                        WHEN LOWER(metodo_pago) = 'transferencia' THEN total_final
                        WHEN LOWER(metodo_pago) = 'mixto' THEN pago_transferencia
                        ELSE 0
                    END
                ), 0) FROM ventas WHERE caja_id = ?
            """, (caja_id,))
            transfe_t = float(cur_local.fetchone()[0] or 0)

            cur_local.execute("""
                SELECT COALESCE(SUM(
                    CASE 
                        WHEN LOWER(metodo_pago) = 'qr' THEN total_final
                        WHEN LOWER(metodo_pago) = 'mixto' THEN pago_qr
                        ELSE 0
                    END
                ), 0) FROM ventas WHERE caja_id = ?
            """, (caja_id,))
            qr_t = float(cur_local.fetchone()[0] or 0)

            cur_local.execute("""
                SELECT COALESCE(SUM(
                    CASE 
                        WHEN LOWER(metodo_pago) = 'fiado' THEN total_final
                        WHEN LOWER(metodo_pago) = 'mixto' THEN pago_fiado
                        ELSE 0
                    END
                ), 0) FROM ventas WHERE caja_id = ?
            """, (caja_id,))
            fiado_t = float(cur_local.fetchone()[0] or 0)

            # --- 5. CARGAR LA LISTA DE TUPLAS PARA EL MÁXIMO ARQUEO EN JINJA ---
            if efectivo_t > 0:  ventas_por_metodo.append(("efectivo", efectivo_t))
            if transfe_t > 0:   ventas_por_metodo.append(("transferencia", transfe_t))
            if tarjeta_t > 0:   ventas_por_metodo.append(("tarjeta", tarjeta_t))
            if qr_t > 0:        ventas_por_metodo.append(("qr", qr_t))
            if fiado_t > 0:     ventas_por_metodo.append(("fiado", fiado_t))
        else:
            session.pop("caja_id", None)
    
    con_local.close()

    # Conteo de Productos y Pedidos (Compatible con tuplas e índices fijos)
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
        solo_efectivo=solo_efectivo,       # Envía los $3750.00 exactos combinados
        ventas_por_metodo=ventas_por_metodo # Renderiza el desglose consolidado sin categorías fantasmas
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
    permisos = session.get("permisos", {})
    es_admin = session.get("admin")
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
        departamento = request.form.get("departamento") # 📂 Capturamos el cambio si se edita

        if not producto_id:
            con.close()
            return "❌ ID inválido"

        try:
            # Agregamos departamento al UPDATE local
            ejecutar(cur, con, """
                UPDATE productos
                SET descripcion=%s,
                    precio=%s,
                    stock=%s,
                    departamento=%s
                WHERE id=%s
            """, (descripcion, precio, stock_val, departamento, producto_id))

            con.commit()

            # 🔥 Sincronizar cambio a la nube
            if internet_ok():
                try:
                    con_cloud = get_db_cloud()
                    cur_cloud = con_cloud.cursor()

                    # Agregamos departamento al UPDATE de la nube
                    cur_cloud.execute("""
                        UPDATE productos
                        SET descripcion=%s,
                            precio=%s,
                            stock=%s,
                            departamento=%s
                        WHERE id=%s
                    """, (descripcion, precio, stock_val, departamento, producto_id))

                    con_cloud.commit()
                    con_cloud.close()
                except Exception as e:
                    print("⚠️ Error sync update producto:", e)

        except Exception as e:
            con.close()
            return f"❌ Error al actualizar: {e}"

    # ================= LISTAR PRODUCTOS =================
    # 💥 MODIFICACIÓN CRÍTICA: Se añade departamento en la posición índice 5
    ejecutar(cur, con, """
        SELECT id, codigo, descripcion, precio, stock, departamento
        FROM productos
        ORDER BY descripcion
    """)
    productos = cur.fetchall()

    con.close()

    return render_template("stock.html", productos=productos)


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


import webbrowser # <-- Agregá este import arriba de todo

# ... (tu código actual) ...


if __name__ == "__main__":
    # 🌐 Solo intentamos inicializar la nube si hay internet
    if internet_ok():
        threading.Thread(target=inicializar_nube, daemon=True).start()
    else:
        print("🌐 Modo Offline: Saltando inicialización de nube...")

    # Iniciamos el Worker de sincronización
    threading.Thread(target=sync_worker, daemon=True).start()

    # Configuración de puerto
    puerto = int(os.environ.get("PORT", 5000))
    
    # 🔥 ESTO ES LO QUE FALTA: Abre el navegador automáticamente
    # Solo lo hacemos si NO estamos en Render (para que no falle en la nube)
    es_produccion = os.environ.get("RENDER")
    if not es_produccion:
        webbrowser.open(f"http://127.0.0.1:{puerto}")

    # Arrancamos la app
    app.run(host="0.0.0.0", port=puerto, debug=False)
