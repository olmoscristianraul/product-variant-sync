#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ===========================================================
# Script Principal: main_sync.py
# ===========================================================
# Ejecuta este archivo para iniciar la sincronización.
# Asegúrate de que config.ini, products_to_sync.csv,
# config_loader.py, odoo_xmlrpc.py, y sync_logic.py
# estén en el mismo directorio.
# ===========================================================

import logging
import time
import os
import sys
# configparser ya no es necesario aquí si config_loader lo maneja
# import configparser
import datetime
import traceback

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


# --- Configuración Inicial del Logging ---
provisional_log_filename = None # Inicializar
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") # Timestamp para nombre de archivo
logs_dir = "" # Inicializar
try:
    # Crear directorio de logs relativo a este script principal
    script_dir = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(script_dir, 'logs')
    os.makedirs(logs_dir, exist_ok=True)

    provisional_log_filename = os.path.join(logs_dir, f"{timestamp}_TEMP.log")

    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(funcName)s] %(message)s')
    logger = logging.getLogger()
    # Establecer el nivel de logging raíz ANTES de añadir handlers
    logger.setLevel(logging.DEBUG) # DEBUG para capturar todos los mensajes

    # Limpiar handlers existentes para evitar duplicados si se ejecuta varias veces en un entorno interactivo
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
    # Nivel por defecto para consola puede ser INFO o DEBUG
    console_handler.setLevel(logging.INFO) # INFO para salida menos ruidosa en consola, cambiar a DEBUG para ver logs detallados en consola
    logger.addHandler(console_handler)

    logging.info(f"Logging configurado. Logs completos en: {provisional_log_filename} (provisional)")


except Exception as log_setup_error:
    print(f"CRITICAL: Fallo al configurar el logging: {log_setup_error}", file=sys.stderr)
    # Si falla el logging, no podemos continuar de forma fiable
    sys.exit(2)


# --- Importar Módulos Principales (después de configurar logging básico) ---
try:
    from odoo_xmlrpc import connect_odoo
    from sync_logic import sync_products
except ImportError as e:
     logging.error(f"ERROR: No se pudo importar un módulo necesario ('odoo_xmlrpc' o 'sync_logic'). ¿Están los archivos en el mismo directorio? Detalle: {e}", exc_info=True)
     sys.exit(2)
except Exception as e:
     logging.error(f"ERROR: Fallo inesperado durante la importación de módulos: {e}", exc_info=True)
     sys.exit(2)


# --- Ejecución del Script y Manejo Final ---
if __name__ == "__main__":
    start_time_global = datetime.datetime.now()
    logging.info(f"Script execution started at: {start_time_global.strftime('%Y-%m-%d %H:%M:%S')}")

    script_status = "ok" # Estado inicial: asumir éxito hasta que falle algo
    final_summary_data = []
    final_counters = {}
    exit_code = 0

    o16_conn_info = (None, None) # Inicializar para evitar UnboundLocalError en finally
    o17_conn_info = (None, None) # Inicializar para evitar UnboundLocalError en finally


    try:
        # Conectar a Odoo (usando valores importados de config_loader)
        logging.info("Estableciendo conexiones Odoo...")
        o16_conn_info = connect_odoo(config_values['O16_URL'], config_values['O16_DB'], config_values['O16_USER'], config_values['O16_PASS'])
        o17_conn_info = connect_odoo(config_values['O17_URL'], config_values['O17_DB'], config_values['O17_USER'], config_values['O17_PASS'])

        # ---> CAMBIO: Corregir la verificación de conexión fallida
        # Verificar si el objeto models (primer elemento de la tupla) es None
        if o16_conn_info[0] is None or o17_conn_info[0] is None:
        # Fin CAMBIO

            logging.error("Conexión(es) fallida(s). Abortando script.");
            script_status = "ERROR"
            # Asignar diccionarios vacíos para que el finally no falle
            final_summary_data = []
            final_counters = {}
        else:
            # Pasar conexiones y el diccionario de configuración completo a sync_products
            final_summary_data, final_counters = sync_products(o16_conn_info, o17_conn_info, config_values)

            # Verificar si hubo fallos en templates individuales registrados por sync_products
            # (sync_products ya devuelve None/{} en caso de fallos de conexión pasados,
            # o de fallos internos si no pudo inicializar la data, pero si falló
            # procesando templates individuales, el contador templates_failed será > 0)
            if final_summary_data is None: # Esto manejaría un fallo crítico dentro de sync_products *antes* de procesar templates
                 script_status = "ERROR"
                 if not final_counters: final_counters = {} # Asegurar que counters exista

            if final_counters and final_counters.get('templates_failed', 0) > 0:
                script_status = "ERROR" # Marcar como error si algún template falló


    except Exception as main_error:
        # Capturar cualquier excepción no controlada durante la ejecución en main_sync
        script_status = "ERROR"
        logging.error(f"Error CRÍTICO no controlado en main_sync: {main_error}", exc_info=True)
        # Asegurar que counters existe para el finally si falló muy temprano
        if 'final_counters' not in locals() or not final_counters: final_counters = {}

    finally:
        # --- Calcular y loguear/imprimir tiempos ---
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
        print(" Resumen Final de Sincronización Detallado ".center(80, '='))
        print("="*80)

        if not final_summary_data and script_status != "ERROR":
             # Caso sin códigos para procesar o sync_products devolvió [] pero sin error fatal
             print("No se encontraron códigos válidos para procesar según el archivo CSV, o no hubo templates para sincronizar.")
        elif not final_summary_data and script_status == "ERROR":
             # Caso fallo muy temprano (conexión, carga config, etc.)
             print("Fallo crítico durante la inicialización o conexión. No se procesaron templates. Verifique los logs para más detalles.")
        else: # Caso normal o con fallos parciales registrados en summary_data
            for item in final_summary_data:
                status_icon = "✔" if item.get('processed_ok', False) else "✖"
                o16_info = item.get('o16_data', {})
                o17_info = item.get('o17_sync', {})

                print(f"\n{status_icon} Template Code: {item.get('code', 'N/A')}")
                print(f"  O16 -> ID: {o16_info.get('id', 'N/A')}, Nombre: '{o16_info.get('name', 'N/A')}'")
                if o16_info.get('attributes'):
                     print("    Atributos O16:")
                     [print(f"      - {attr_line}") for attr_line in o16_info['attributes']]
                else:
                     print("    Atributos O16: Ninguno")
                print(f"  O17 -> ID: {o17_info.get('id', 'N/A')}, Nombre Final: '{o17_info.get('name_final', 'N/A')}', Code Final: '{o17_info.get('default_code_final', 'N/A')}'")
                if not item.get('processed_ok', False):
                    print("    ESTADO TEMPLATE: Fallido (ver logs para detalles)")

                print("  --- Variantes O16 y Estado Sincronización O17 ---")
                variants_synced = o17_info.get('variants_synced', [])
                variants_archived = o17_info.get('variants_archived', [])

                if not variants_synced and not o16_info.get('variants'):
                    print("    No había variantes en Odoo 16 para este template.")
                elif not variants_synced and o16_info.get('variants'):
                    print("    Se encontraron variantes en Odoo 16 pero falló su sincronización a Odoo 17 (ver logs).")
                elif variants_synced: # Solo imprimir si hay variantes sincronizadas
                    print(f"  Variantes Sincronizadas ({len(variants_synced)}):")
                    for v_sync in variants_synced:
                        o16_v = v_sync.get('o16', {})
                        o17_v = v_sync.get('details_o17', {}) or {} # Usar {} si details_o17 es None
                        o17_status = v_sync.get('status', 'N/A').upper()

                        # ---> CAMBIO: Sincronización lst_price - Mostrar precio en resumen
                        # Usar .get() con default 'N/A' y formateo para float si el valor existe
                        o16_price = o16_v.get('price')
                        o16_price_display = f" P:{o16_price:.2f}" if isinstance(o16_price, (int, float)) else " P:N/A"

                        o17_price = o17_v.get('price')
                        o17_price_display = f" P:{o17_price:.2f}" if isinstance(o17_price, (int, float)) else " P:N/A"

                        print(f"    - O16 (ID:{o16_v.get('id', 'N/A')} B:'{o16_v.get('barcode', '')}' C:'{o16_v.get('code', '')}' L:'{o16_v.get('legacy', '')}'{o16_price_display})")
                        print(f"      O17 (ID:{v_sync.get('o17_id', 'N/A')}): Estado: {o17_status}")
                        if 'FAILED' not in o17_status and o17_v:
                             print(f"        -> B:'{o17_v.get('barcode', '')}' C:'{o17_v.get('code', '')}' L:'{o17_v.get('legacy', '')}'{o17_price_display}")
                        elif 'FAILED' in o17_status:
                             print(f"        -> Falló la operación en Odoo 17 (ver logs)")
                        # Fin CAMBIO lst_price mostrar en resumen

                if variants_archived:
                    print(f"  Variantes Archivadas en Odoo 17 ({len(variants_archived)}):")
                    for v_arch in variants_archived:
                         print(f"    - ID:{v_arch.get('id', 'N/A')}, Code:'{v_arch.get('code', '')}', Barcode:'{v_arch.get('barcode', '')}'")

                print("-" * 40)

        # Imprimir estadísticas si existen
        if final_counters:
            print("\n" + "="*80)
            print(" Estadísticas Generales ".center(80, '='))
            print("="*80)
            print(f"- Templates Analizados: {final_counters.get('templates_analyzed', 0)}")
            print(f"- Templates con Fallos: {final_counters.get('templates_failed', 0)}")
            print(f"- Total Variantes Encontradas en Odoo 16: {final_counters.get('o16_variants_found', 0)}")
            print(f"- Total Variantes Existentes en Odoo 17 (antes): {final_counters.get('o17_variants_existing', 0)}")
            print(f"- Total Variantes Creadas en Odoo 17: {final_counters.get('o17_variants_created', 0)}")
            print(f"- Total Variantes Actualizadas en Odoo 17: {final_counters.get('o17_variants_updated', 0)}")
            print(f"- Total Variantes Archivadas en Odoo 17: {final_counters.get('o17_variants_archived', 0)}")
            print("-" * 80)
            print(f"Hora de Inicio:      {start_time_global.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Hora de Fin:         {end_time_global.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Tiempo de Ejecución: {duration_str}")
            print("="*80)
        else:
            print("\nNo se generaron estadísticas de procesamiento (probablemente fallo temprano en conexión/inicialización).")


        # --- Renombrar archivo de log ---
        logging.info("Cerrando y renombrando archivo de log...");
        # Importante: cerrar el logging para asegurar que todo se escribe al archivo antes de renombrar
        logging.shutdown()

        final_log_filename = ""
        #exit_code ya se inicializó a 0

        try:
            # Construir nombre final basado en el estado del script
            if script_status == "ERROR":
                 final_log_filename = os.path.join(logs_dir, f"{timestamp}_ERROR.log")
                 print(f"Script finalizado con ERRORES. Ver log: {final_log_filename}")
                 exit_code = 1
            else:
                 final_log_filename = os.path.join(logs_dir, f"{timestamp}.log")
                 print(f"Script finalizado OK. Ver log: {final_log_filename}")
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