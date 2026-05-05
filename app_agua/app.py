def sync_worker():
    # Si ya hay un proceso de sincronización activo, este rebota
    if not worker_running_lock.acquire(blocking=False):
        return

    print("🚀 WORKER INTEGRAL: SUBIDA DINÁMICA + BAJADA CON ESCUDO (LOCK ACTIVADO)")
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
                    # Bloqueamos solo para leer y marcar como "en proceso" (sync=2)
                    with db_lock:
                        con_l = get_db_local()
                        cur_l = con_l.cursor()
                        cur_l.execute("SELECT id, tabla, data FROM sync_queue WHERE sync=0 ORDER BY id ASC LIMIT 50")
                        originales = cur_l.fetchall()
                        
                        if originales:
                            pendientes = [dict(row) for row in originales]
                            ids = [p["id"] for p in pendientes]
                            # Marcamos inmediatamente como sync=2 (EN PROCESO)
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
                                        UPDATE productos 
                                        SET stock = stock - %s 
                                        WHERE id = %s
                                    """, (data["stock_restar"], data["id"]))
                                
                                # --- CASO B: SINCRONIZACIÓN DINÁMICA ---
                                else:
                                    columnas = [k for k in data.keys() if k != 'stock_restar']
                                    valores = [data[k] for k in columnas]
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

                                # Marcar como sincronizado FINAL (sync=1)
                                with db_lock:
                                    con_upd = get_db_local()
                                    con_upd.execute("UPDATE sync_queue SET sync=1 WHERE id=?", (id_q,))
                                    con_upd.commit()
                                    con_upd.close()
                                
                                print(f"  ✅ {tabla} sincronizado correctamente.")

                            except Exception as e_row:
                                con_cloud.rollback()
                                print(f"  ❌ Error subiendo {tabla} (ID Cola: {id_q}): {e_row}")
                                # Si falló, lo volvemos a poner en sync=0 para reintentar después
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
                        tablas_bajar = ["productos", "cajeros", "ventas", "venta_items", "promos", "usuarios", "caja"]
                        
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

                                        # 🛡️ ESCUDO LOCAL: No pisar si hay cambios pendientes (sync 0 o 2)
                                        cur_check = con_loc.execute(
                                            "SELECT 1 FROM sync_queue WHERE tabla=? AND sync IN (0,2) AND data LIKE ?", 
                                            (t, f'%"{rid}"%')
                                        )
                                        if cur_check.fetchone():
                                            continue

                                        vals_limpios = [float(v) if isinstance(v, Decimal) else v for v in r_nube]
                                        placeholders = ",".join(["?"] * len(cols))
                                        con_loc.execute(f"INSERT OR REPLACE INTO {t} ({','.join(cols)}) VALUES ({placeholders})", vals_limpios)
                                    
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
        # Siempre liberamos el candado al salir (aunque haya error)
        worker_running_lock.release()
