#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ===========================================================
# Script Principal: main_sync.py
# ===========================================================
# Ejecuta este archivo para iniciar la sincronización.
# Asegúrate de que config.ini, products_to_sync.csv,
# config_loader.py, odoo_xmlrpc.py, sync_logic.py, y sync_db.py
# estén en el mismo directorio.
# ===========================================================

import logging
import time
import os
import sys
import argparse
# configparser ya no es necesario aquí si config_loader lo maneja
# import configparser
import datetime
import traceback
import sqlite3 # Importar sqlite3 para manejar errores específicos de DB

# --- Cargar Configuración ---
# Importa el diccionario 'config_values' cargado desde config_loader.py
try:
    from config_loader import config_values
except ImportError as e:
     # Error crítico si no se pueden importar módulos básicos
     print(f"ERROR: No se pudo importar 'config_loader'. ¿Está el archivo 'config_loader.py' en el mismo directorio? Detalle: {e}", file=sys.stderr)
     sys.exit(2)
except Exception as e:
     print(f"ERROR: Fallo inesperado durante la importación de configuración: {e}", file=sys.stderr)
     sys.exit(2)

# --- Importar Módulos de Sincronización y DB ---
try:
    from odoo_xmlrpc import connect_odoo
    # Importamos funciones específicas necesarias de sync_logic
    from sync_logic import ( # Importamos sync_products y la nueva función de cambios
        sync_products,
        get_changed_item_codes # Nueva función para obtener códigos cambiados en Odoo 16
    )
    from sync_db import ( # Importar funciones del nuevo módulo de base de datos
        get_db_path, init_db, add_items_from_csv, add_item, # add_item puede ser útil para añadir códigos de otros modos
        load_items_to_process, load_items_by_codes,
        clear_sync_items, count_items_by_status,
        DEFAULT_DB_NAME # Importar el nombre por defecto de la DB
    )
except ImportError as e:
     logging.error(f"ERROR: No se pudo importar un módulo necesario (odoo_xmlrpc, sync_logic, o sync_db). ¿Están los archivos en el mismo directorio? Detalle: {e}", exc_info=True)
     sys.exit(2)
except Exception as e:
     logging.error(f"ERROR: Fallo inesperado durante la importación de módulos: {e}", exc_info=True)
     sys.exit(2)


# --- Configuración Inicial del Logging ---
provisional_log_filename = None # Inicializar
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") # Timestamp para nombre de archivo
logs_dir = "" # Inicializar
try:
    # Crear directorio de logs relativo a este script principal
    script_dir = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(script_dir, 'logs')
    os.makedirs(logs_dir, exist_ok=True)

    # El nombre final del log dependerá del estado, pero empezamos con un temporal
    provisional_log_filename = os.path.join(logs_dir, f"{timestamp}_TEMP.log")

    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(funcName)s] %(message)s')
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG) # Capturar todos los mensajes en el logger raíz

    # Limpiar handlers existentes para evitar duplicados si se ejecuta varias veces
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # File Handler (siempre DEBUG, al archivo provisional)
    file_handler = logging.FileHandler(provisional_log_filename, encoding='utf-8', mode='w') # 'w' para sobrescribir
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.DEBUG) # Escribir todo (DEBUG y superior) al archivo
    logger.addHandler(file_handler)

    # Console Handler (INFO por defecto)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.INFO) # INFO para salida menos ruidosa en consola
    logger.addHandler(console_handler)

    logging.info(f"Logging configurado. Logs completos en: {provisional_log_filename} (provisional)")

except Exception as log_setup_error:
    print(f"CRITICAL: Fallo al configurar el logging: {log_setup_error}", file=sys.stderr)
    sys.exit(2)


# --- Configuración de Argumentos de Línea de Comandos ---
def setup_arg_parser():
    parser = argparse.ArgumentParser(
        description="Script de Sincronización de Productos Odoo 16 (EE) a Odoo 17 (CE).",
        formatter_class=argparse.RawTextHelpFormatter # Mantiene formato en ayuda
    )

    # --- Modos de Operación ---
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        '--mode',
        choices=['load_csv', 'load_all', 'run_pending', 'run_failed', 'run_codes', 'run_changed', 'clear_db', 'status'], # Añadido load_all y run_changed
        help="""Modo de operación:
        load_csv: Carga códigos desde el CSV a la base de datos (status=pending).
        load_all: Carga TODOS los códigos activos de Odoo 16 a la base de datos (status=pending).
        run_pending: Sincroniza items con status 'pending' en la base de datos.
        run_failed: Sincroniza items con status 'failed' en la base de datos.
        run_codes: Sincroniza items con códigos específicos pasados via --codes.
        run_changed: Sincroniza items cambiados en Odoo 16 en los últimos --days días.
        clear_db: Limpia la tabla 'sync_items' en la base de datos.
        status: Muestra el conteo de items por estado en la base de datos.
        """
    )

    # --- Opciones para modos ---
    parser.add_argument(
        '--db-file',
        default=DEFAULT_DB_NAME, # Usa el nombre por defecto definido en sync_db
        help=f"Nombre o ruta del archivo de base de datos SQLite (por defecto: {DEFAULT_DB_NAME})."
    )
    # csv-file solo es relevante para load_csv
    parser.add_argument(
        '--csv-file',
        default=config_values.get('CSV_FILENAME', 'products_to_sync.csv'),
        help="Nombre del archivo CSV para el modo 'load_csv' (por defecto: desde config.ini o 'products_to_sync.csv')."
    )
    # codes solo es relevante para run_codes
    parser.add_argument(
        '--codes',
        nargs='+', # Permite 1 o más códigos
        help="Lista de códigos separados por espacio para el modo 'run_codes'."
    )
    # days solo es relevante para run_changed
    parser.add_argument(
        '--days',
        type=int,
        help="Número de días hacia atrás para el modo 'run_changed'. Requerido para este modo."
    )
    # sync-fields es relevante para modos run_*
    parser.add_argument(
        '--sync-fields',
        choices=['all', 'price_only'],
        default='all', # Por defecto sincroniza todos los campos relevantes en los modos run_*
        help="Qué campos de la variante sincronizar en los modos 'run_*' (por defecto: all). 'price_only' sincroniza solo el precio de lista."
    )


    return parser

# --- Ejecución del Script y Manejo Final ---
if __name__ == "__main__":
    parser = setup_arg_parser()
    args = parser.parse_args() # Parsear argumentos aquí al inicio

    start_time_global = datetime.datetime.now()
    logging.info(f"Script execution started at: {start_time_global.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Modo de operación seleccionado: {args.mode}")
    logging.info(f"Archivo de base de datos: {args.db_file}")
    if args.mode.startswith('run_') or args.mode.startswith('load_all'):
         logging.info(f"Campos a sincronizar (--sync-fields): {args.sync_fields}")


    script_status = "ok" # Estado inicial: asumir éxito hasta que falle algo
    final_summary_data = [] # Mantener para el resumen de la ejecución actual
    final_counters = {} # Mantener para los contadores de la ejecución actual
    items_loaded_count = 0 # Cuántos items se cargaron/identificaron para procesar en esta ejecución
    items_processed_count = 0 # Cuántos items se intentaron sincronizar en esta ejecución
    items_failed_count = 0 # Cuántos items fallaron en ESTA ejecución
    exit_code = 0

    # Determinar la ruta absoluta de la DB
    # La ruta base es el directorio del script principal
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_full_path = get_db_path(script_dir, args.db_file)

    # --- Conexiones Odoo (solo necesarias para modos 'run' o 'load_all'/'load_changed') ---
    models_o16 = None; uid_o16 = None; o16_conn_info = (None, None)
    models_o17 = None; uid_o17 = None; o17_conn_info = (None, None)
    connections_successful = False

    # Conectar si el modo requiere acceso a Odoo (run_*, load_all, load_changed)
    if args.mode.startswith('run_') or args.mode.startswith('load_all'): # Añadido load_all aquí
         try:
             logging.info("Estableciendo conexiones Odoo...")
             o16_conn_info = connect_odoo(config_values['O16_URL'], config_values['O16_DB'], config_values['O16_USER'], config_values['O16_PASS'])
             o17_conn_info = connect_odoo(config_values['O17_URL'], config_values['O17_DB'], config_values['O17_USER'], config_values['O17_PASS'])

             # Verificar si el objeto models (primer elemento de la tupla) es None
             if o16_conn_info[0] is None or o17_conn_info[0] is None:
                 logging.error("Conexión(es) Odoo fallida(s). No se puede ejecutar este modo. Abortando script.");
                 script_status = "ERROR"
             else:
                 models_o16, uid_o16 = o16_conn_info
                 models_o17, uid_o17 = o17_conn_info
                 connections_successful = True # Solo True si AMBAS conexiones son OK

         except Exception as e:
             logging.error(f"Error al establecer conexiones Odoo: {e}", exc_info=True)
             script_status = "ERROR"


    # --- Ejecución basada en el modo ---
    # Procedemos solo si no hubo un error crítico antes (ej: error de importación, error de log, error de configuración)
    if script_status == "ok":
        try: # Bloque TRY principal para la lógica de procesamiento
            # Inicializar la base de datos (siempre, para asegurar que exista antes de cualquier operación)
            if not init_db(db_full_path):
                script_status = "ERROR"
                logging.critical("Fallo al inicializar/verificar la base de datos. Abortando.")
            else: # DB inicializada/verificada correctamente
                # --- Lógica principal del modo seleccionado ---
                if args.mode == 'load_csv':
                    items_loaded_count = add_items_from_csv(db_full_path, args.csv_file, script_dir)
                    logging.info(f"Modo '{args.mode}' completado. Se añadieron o ya existían {items_loaded_count} items 'pending' desde {args.csv_file}.")

                elif args.mode == 'load_all' and connections_successful:
                    # Future implementation: Query Odoo 16 for all active product.template codes
                    # For now, let's just log that it's not implemented
                    logging.warning("Modo 'load_all' no implementado en esta fase.")
                    script_status = "ERROR" # Marcar como error porque no hace lo que se espera

                elif args.mode == 'run_pending' and connections_successful:
                    # Cargar items con estado 'pending' de la DB
                    items_to_process = load_items_to_process(db_full_path, target_status='pending')
                    items_loaded_count = len(items_to_process)
                    if items_loaded_count > 0:
                         logging.info(f"Modo '{args.mode}': {items_loaded_count} items 'pending' cargados de la DB.")
                         # Ejecutar la sincronización para la lista de items cargada
                         final_summary_data, final_counters = sync_products(
                             o16_conn_info,
                             o17_conn_info,
                             config_values, # Pasamos la configuración completa
                             items_to_process, # Lista de items de la DB
                             db_full_path,
                             args.sync_fields # Pasar el parámetro sync-fields
                         )
                         # sync_products maneja el conteo de fallos por item.
                         # El estado general del script dependerá del resultado de sync_products
                         if final_summary_data is None or (final_counters and final_counters.get('templates_failed', 0) > 0):
                             script_status = "ERROR"
                             if not final_counters: final_counters = {}
                         items_processed_count = final_counters.get('templates_analyzed', 0)
                         items_failed_count = final_counters.get('templates_failed', 0)

                    else:
                        logging.info("No hay items con estado 'pending' en la base de datos para sincronizar.")


                elif args.mode == 'run_failed' and connections_successful:
                    # Cargar items con estado 'failed' de la DB
                    items_to_process = load_items_to_process(db_full_path, target_status='failed')
                    items_loaded_count = len(items_to_process)
                    if items_loaded_count > 0:
                         logging.info(f"Modo '{args.mode}': {items_loaded_count} items 'failed' cargados de la DB. Preparando para re-procesar.")
                         # Opcional: podrías querer resetear su estado a 'pending' antes de procesar
                         # for item in items_to_process:
                         #     update_item_status(db_full_path, item['code'], 'pending')
                         # logging.info(f"Reseteado estado a 'pending' para {len(items_to_process)} items fallidos.")

                         # Ejecutar la sincronización para la lista de items fallidos
                         final_summary_data, final_counters = sync_products(
                             o16_conn_info,
                             o17_conn_info,
                             config_values,
                             items_to_process, # Pasamos la lista cargada de la DB
                             db_full_path,
                             args.sync_fields # Pasar el parámetro sync-fields
                         )
                         if final_summary_data is None or (final_counters and final_counters.get('templates_failed', 0) > 0):
                             script_status = "ERROR"
                             if not final_counters: final_counters = {}
                         items_processed_count = final_counters.get('templates_analyzed', 0)
                         items_failed_count = final_counters.get('templates_failed', 0)
                    else:
                        logging.info("No hay items con estado 'failed' en la base de datos para re-sincronizar.")


                elif args.mode == 'run_codes' and connections_successful:
                    if not args.codes:
                        logging.error("Modo 'run_codes' requiere especificar códigos con --codes.")
                        script_status = "ERROR"
                    else:
                        # Cargar items por lista de códigos específica de la DB
                        items_to_process = load_items_by_codes(db_full_path, args.codes)
                        items_loaded_count = len(items_to_process)
                        if items_loaded_count > 0:
                             logging.info(f"Modo '{args.mode}': {items_loaded_count} de {len(args.codes)} códigos especificados encontrados en la base de datos para procesar.")
                             # Opcional: Si se pasan códigos que no están en la DB, ¿qué hacer?
                             # Por ahora, solo procesamos los que están. Podríamos añadirlos con estado 'pending' aquí.
                             # logging.info(f"Intentando procesar {len(args.codes)} códigos especificados.")
                             # for code in args.codes:
                             #      # Añadir a DB si no existe, o asegurar estado pending si existe
                             #      add_item(db_full_path, code, 'codes_list', status='pending')
                             # logging.info(f"Códigos especificados preparados en la base de datos.")
                             # # Recargar la lista asegurando que tenemos todos los campos de la DB
                             # items_to_process = load_items_by_codes(db_full_path, args.codes)


                             # Ejecutar la sincronización para la lista de códigos cargada
                             final_summary_data, final_counters = sync_products(
                                 o16_conn_info,
                                 o17_conn_info,
                                 config_values,
                                 items_to_process, # Pasamos la lista cargada de la DB
                                 db_full_path,
                                 args.sync_fields # Pasar el parámetro sync-fields
                             )
                             if final_summary_data is None or (final_counters and final_counters.get('templates_failed', 0) > 0):
                                 script_status = "ERROR"
                                 if not final_counters: final_counters = {}
                             items_processed_count = final_counters.get('templates_analyzed', 0)
                             items_failed_count = final_counters.get('templates_failed', 0)

                        else:
                             logging.warning(f"Ninguno de los códigos especificados {args.codes} fue encontrado en la base de datos para procesar.")
                             # Considerar esto un error si se esperaba que existieran? Por ahora, no fatal.


                elif args.mode == 'run_changed' and connections_successful: # Nuevo modo run_changed
                    if args.days is None or args.days < 0:
                        logging.error("Modo 'run_changed' requiere un número de días válido especificado con --days.")
                        script_status = "ERROR"
                    else:
                        logging.info(f"Modo '{args.mode}': Buscando items cambiados en Odoo 16 en los últimos {args.days} días.")
                        # Calcular la fecha de inicio para el filtro
                        from_date = datetime.datetime.now() - datetime.timedelta(days=args.days)
                        logging.info(f"Consultando cambios desde Odoo 16 a partir de: {from_date.isoformat()}")

                        # --- Obtener códigos de items cambiados de Odoo 16 ---
                        #changed_codes = get_changed_item_codes(o16_conn_info, from_date) # Nueva función en sync_logic
                        changed_codes = get_changed_item_codes(o16_conn_info, config_values, from_date) # <-- Añadido config_values

                        if changed_codes is None: # Fallo RPC en get_changed_item_codes
                             logging.error("Fallo al obtener la lista de códigos cambiados de Odoo 16.")
                             script_status = "ERROR"
                        elif not changed_codes:
                             logging.info("No se encontraron items cambiados en Odoo 16 en el rango de fechas especificado.")
                        else:
                             logging.info(f"Encontrados {len(changed_codes)} códigos cambiados en Odoo 16.")
                             # --- Añadir los códigos cambiados a la base de datos (si no existen) o marcar como pending ---
                             items_loaded_count = 0
                             logging.info("Añadiendo items cambiados a la base de datos (status=pending)...")
                             for code in changed_codes:
                                 # Intentar añadir. Si ya existe, no lo añade de nuevo por INSERT OR IGNORE
                                 # Podríamos querer actualizar el source_type o status si ya existe, pero por ahora, solo añadir nuevos.
                                 # Si ya existe como 'failed', 'pending' o 'succeeded', no lo modificamos aquí.
                                 added = add_item(db_full_path, code, 'changed_o16', status='pending') # 'changed_o16' como source_type
                                 if added:
                                     items_loaded_count += 1
                             logging.info(f"Items cambiados añadidos a la base de datos: {items_loaded_count} (nuevos en DB).")

                             # --- Ahora cargar los items PENDING de la base de datos (que incluye los recién añadidos) ---
                             items_to_process = load_items_to_process(db_full_path, target_status='pending')
                             items_loaded_count = len(items_to_process) # Actualizar items_loaded_count a los que *realmente* se van a procesar
                             if items_loaded_count > 0:
                                 logging.info(f"Modo '{args.mode}': {items_loaded_count} items 'pending' (incluyendo cambiados) cargados de la DB para sincronizar.")
                                 # Ejecutar la sincronización para la lista cargada
                                 final_summary_data, final_counters = sync_products(
                                     o16_conn_info,
                                     o17_conn_info,
                                     config_values,
                                     items_to_process,
                                     db_full_path,
                                     args.sync_fields # Pasar el parámetro sync-fields
                                 )
                                 if final_summary_data is None or (final_counters and final_counters.get('templates_failed', 0) > 0):
                                     script_status = "ERROR"
                                     if not final_counters: final_counters = {}
                                 items_processed_count = final_counters.get('templates_analyzed', 0)
                                 items_failed_count = final_counters.get('templates_failed', 0)

                             else:
                                # Esto debería ocurrir si get_changed_item_codes encontró códigos, pero todos ya estaban en la DB y NO en estado 'pending'.
                                # Podríamos añadir lógica para resetear estado 'succeeded'/'failed' a 'pending' en modo 'run_changed' si queremos re-procesar *todos* los cambiados.
                                logging.warning("No se encontraron items 'pending' en la base de datos después de añadir los cambiados de Odoo 16.")


                elif args.mode == 'clear_db':
                    confirm = input(f"¿Está seguro de que desea limpiar COMPLETAMENTE la tabla 'sync_items' en la base de datos '{args.db_file}'? Esto eliminará el historial y el estado de reanudación. (yes/no): ")
                    if confirm.lower() == 'yes':
                         if clear_sync_items(db_full_path):
                             logging.info("Modo 'clear_db' completado.")
                         else:
                             script_status = "ERROR"
                             logging.error("Modo 'clear_db' fallido.")
                    else:
                         logging.info("Limpieza de base de datos cancelada.")


                elif args.mode == 'status':
                     logging.info(f"Estado actual de la base de datos '{args.db_file}':")
                     counts = count_items_by_status(db_full_path)
                     if counts:
                         for status, count in counts.items():
                             print(f"- {status.capitalize()}: {count}")
                     else:
                         print("La base de datos está vacía o hubo un error al leerla.")
                     logging.info("Modo 'status' completado.")

                # --- Manejar casos donde el modo requería conexión pero falló ---
                elif args.mode.startswith('run_') or args.mode.startswith('load_all') and not connections_successful:
                     # Este caso ya se maneja al inicio antes del primer try principal
                     pass


        except Exception as main_processing_error:
            # Capturar errores durante la carga/procesamiento general que no fueron manejados internamente
            # (Ej: error inesperado al leer CSV, error de DB no manejado en sync_db, error antes del bucle de templates)
            script_status = "ERROR"
            logging.error(f"Error CRÍTICO no controlado durante el procesamiento principal: {main_processing_error}", exc_info=True)
            if 'final_counters' not in locals() or not final_counters: final_counters = {} # Asegurar existencia


        # --- Bloque FINALLY para resumen y limpieza de log ---
        # Este bloque se ejecuta SIEMPRE al final del try/except/else del main
        finally:
            end_time_global = datetime.datetime.now()
            logging.info(f"Script execution finished at: {end_time_global.strftime('%Y-%m-%d %H:%M:%S')}")
            execution_duration = end_time_global - start_time_global
            total_seconds = execution_duration.total_seconds()
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            duration_str = f"{int(hours):02}:{int(minutes):02}:{seconds:06.3f}"
            logging.info(f"Total execution time: {duration_str} (Total seconds: {total_seconds:.3f})")


            # --- Imprimir Resumen Final Detallado a Consola---
            print("\n" + "="*80)
            print(" Resumen Final de Ejecución ".center(80, '=')) # Título ajustado
            print("="*80)
            print(f"Modo Ejecutado: {args.mode}")
            print(f"Base de Datos: {args.db_file}")
            print(f"Estado General: {script_status.upper()}")
            print("-" * 80)

            # Mostrar información sobre cuántos items se identificaron/cargaron en este modo
            if args.mode.startswith('load_'):
                print(f"Items identificados/añadidos a la DB en este modo: {items_loaded_count}") # Usar el contador específico de load
                print("-" * 80)
            elif args.mode.startswith('run_'):
                print(f"Items cargados de la DB para procesar: {items_loaded_count}") # Usar el contador específico de run
                print(f"Items procesados en esta ejecución: {items_processed_count}")
                print(f"Items con fallos en esta ejecución: {items_failed_count}")
                print("-" * 80)


            # Solo imprimir resumen detallado de templates/variantes si se ejecutó un modo 'run'
            # y si se generó algún summary_data (es decir, si se procesó al menos un template)
            if args.mode.startswith('run_') and final_summary_data:
                print("Resultados por Template Procesado en esta Ejecución:")
                # No necesitamos el check de items_processed_count == 0 aquí, si final_summary_data está vacío, este bucle no se ejecuta.
                for item in final_summary_data:
                    status_icon = "✔" if item.get('processed_ok', False) else "✖"
                    o16_info = item.get('o16_data', {})
                    o17_info = item.get('o17_sync', {})

                    print(f"\n{status_icon} Template Code: {item.get('code', 'N/A')}")
                    print(f"  O16 -> ID: {o16_info.get('id', 'N/A')}, Nombre: '{o16_info.get('name', 'N/A')}'")
                    if o16_info.get('attributes'):
                        print("    Atributos O16:");
                        [print(f"      - {attr_line}") for attr_line in o16_info['attributes']]
                    else:
                        print("    Atributos O16: Ninguno")
                    print(f"  O17 -> ID: {o17_info.get('id', 'N/A')}, Nombre Final: '{o17_info.get('name_final', 'N/A')}', Code Final: '{o17_info.get('default_code_final', 'N/A')}'")
                    if not item.get('processed_ok', False):
                        print("    ESTADO TEMPLATE: Fallido (ver logs para detalles)")

                    print("  --- Variantes O16 y Estado Sincronización O17 ---")
                    variants_synced = o17_info.get('variants_synced', [])
                    variants_archived = o17_info.get('variants_archived', [])

                    if not variants_synced and not o16_info.get('variants'):
                        print("    No había variantes en Odoo 16 para este template.")
                    elif not variants_synced and o16_info.get('variants'):
                        print("    Se encontraron variantes en Odoo 16 pero falló su sincronización a Odoo 17 (ver logs).")
                    elif variants_synced: # Solo imprimir si hay variantes sincronizadas
                        print(f"  Variantes Sincronizadas ({len(variants_synced)}):")
                        for v_sync in variants_synced:
                            o16_v = v_sync.get('o16', {})
                            o17_v = v_sync.get('details_o17', {}) or {} # Usar {} si details_o17 es None
                            o17_status = v_sync.get('status', 'N/A').upper()

                            # Mostrar precio en resumen
                            o16_price = o16_v.get('price')
                            o16_price_display = f" P:{o16_price:.2f}" if isinstance(o16_price, (int, float)) else " P:N/A"

                            o17_price = o17_v.get('price')
                            o17_price_display = f" P:{o17_price:.2f}" if isinstance(o17_price, (int, float)) else " P:N/A"

                            print(f"    - O16 (ID:{o16_v.get('id', 'N/A')} B:'{o16_v.get('barcode', '')}' C:'{o16_v.get('code', '')}' L:'{o16_v.get('legacy', '')}'{o16_price_display})")
                            print(f"      O17 (ID:{v_sync.get('o17_id', 'N/A')}): Estado: {o17_status}")
                            if 'FAILED' not in o17_status and o17_v:
                                print(f"        -> B:'{o17_v.get('barcode', '')}' C:'{o17_v.get('code', '')}' L:'{o17_v.get('legacy', '')}'{o17_price_display}")
                            elif 'FAILED' in o17_status:
                                print(f"        -> Falló la operación en Odoo 17 (ver logs)")

                        if variants_archived:
                            print(f"  Variantes Archivadas en Odoo 17 ({len(variants_archived)}):")
                            for v_arch in variants_archived:
                                print(f"    - ID:{v_arch.get('id', 'N/A')}, Code:'{v_arch.get('code', '')}', Barcode:'{v_arch.get('barcode', '')}'")

                        print("-" * 40)


        # Mostrar estadísticas de esta ejecución si existen
        if final_counters:
            print("\nEstadísticas Detalladas de la Ejecución Actual:") # Título ajustado
            print(f"- Templates cuyo procesamiento se intentó: {final_counters.get('templates_analyzed', 0)}")
            print(f"- Templates con fallos internos en el procesamiento: {final_counters.get('templates_failed', 0)}") # Fallos que impidieron completarlo
            # Note: These counters might be less useful now, but kept for structure
            # print(f"- Total Variantes Procesadas desde Odoo 16: {final_counters.get('o16_variants_found', 0)}")
            # print(f"- Total Variantes Existentes en Odoo 17 (antes de sync de este template): {final_counters.get('o17_variants_existing', 0)}")

            print(f"- Variantes Creadas en Odoo 17 en esta ejecución: {final_counters.get('o17_variants_created', 0)}")
            print(f"- Variantes Actualizadas en Odoo 17 en esta ejecución: {final_counters.get('o17_variants_updated', 0)}")
            print(f"- Variantes Archivadas en Odoo 17 en esta ejecución: {final_counters.get('o17_variants_archived', 0)}")
            print("-" * 80)


        print(f"Hora de Inicio:      {start_time_global.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Hora de Fin:         {end_time_global.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Tiempo de Ejecución: {duration_str}")
        print("="*80)

        # --- Información del estado total de la DB ---
        # Mostrar un resumen del estado total de la DB al final de cualquier modo
        print("\nEstado Total de la Base de Datos de Sincronización:")
        total_counts = count_items_by_status(db_full_path)
        if total_counts:
            for status, count in total_counts.items():
                print(f"- {status.capitalize()}: {count}")
        else:
            print("La base de datos está vacía o hubo un error al leerla.")
        print("="*80)


        # --- Renombrar archivo de log ---
        logging.info("Cerrando y renombrando archivo de log...");
        logging.shutdown() # Asegurar que todo se escribe

        final_log_filename = ""
        #exit_code ya se inicializó a 0

        try:
            # Construir nombre final basado en el estado del script
            # El estado 'ERROR' general es si hubo error crítico global o templates fallidos en modo 'run'
            if script_status == "ERROR":
                 final_log_filename = os.path.join(logs_dir, f"{timestamp}_ERROR.log")
                 print(f"\nScript finalizado con ERRORES. Ver log completo: {final_log_filename}")
                 exit_code = 1
            elif args.mode == 'status': # Modo status es siempre OK si llega aquí
                 final_log_filename = os.path.join(logs_dir, f"{timestamp}_STATUS.log") # Nombrar diferente para status
                 print(f"\nModo '{args.mode}' completado. Ver log: {final_log_filename}")
                 exit_code = 0
            else: # Modos load_* y run_* exitosos
                 final_log_filename = os.path.join(logs_dir, f"{timestamp}.log")
                 print(f"\nScript finalizado OK. Ver log completo: {final_log_filename}")
                 exit_code = 0


            # Intentar renombrar solo si el archivo temporal existe y tenemos un nombre final
            if provisional_log_filename and os.path.exists(provisional_log_filename) and final_log_filename:
                 os.rename(provisional_log_filename, final_log_filename)
            elif not provisional_log_filename or not os.path.exists(provisional_log_filename):
                 print(f"WARN: No se encontró log temporal {provisional_log_filename} para renombrar.", file=sys.stderr)


        except OSError as rename_error:
            print(f"ERROR: No se pudo renombrar el archivo de log de '{provisional_log_filename}' a '{final_log_filename}': {rename_error}", file=sys.stderr)
            exit_code = 1 # Un fallo al renombrar el log también indica un problema

        except Exception as final_error:
            # Capturar cualquier otro error durante la finalización
            print(f"ERROR: Error inesperado durante la finalización/renombrado del log: {final_error}", file=sys.stderr)
            exit_code = 1 # Marcar fallo

        # Salir con el código de estado apropiado
        sys.exit(exit_code)