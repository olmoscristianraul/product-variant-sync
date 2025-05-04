# -*- coding: utf-8 -*-
import sqlite3
import os
import logging
import datetime
import sys # Importar sys aquí también si es necesario para get_db_path

_logger = logging.getLogger(__name__)

# --- Configuración de la Base de Datos ---
DEFAULT_DB_NAME = 'sync_state.db'

# Esquema de la tabla sync_items:
# code: default_code del producto (clave única)
# source_type: Cómo se añadió este item ('csv', 'all_o16', 'changed_o16', 'failed_prev', 'codes_list')
# status: Estado actual ('pending', 'processing', 'succeeded', 'failed', 'skipped')
# error_message: Mensaje del último error si status es 'failed'
# last_sync_attempt: Timestamp del último intento de sincronización (ISO 8601 TEXT)
# last_sync_success: Timestamp de la última sincronización exitosa (ISO 8601 TEXT)
# o16_template_id: ID del product.template en Odoo 16 (INTEGER)
# o17_template_id: ID del product.template en Odoo 17 (INTEGER)
# sync_modes: JSON string o texto simple indicando qué se debe sincronizar para este item (ej: 'all', 'price_only') - Implementación futura
SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    source_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    last_sync_attempt TEXT,
    last_sync_success TEXT,
    o16_template_id INTEGER,
    o17_template_id INTEGER,
    sync_modes TEXT DEFAULT 'all' -- Campo para futura granularidad
);
"""

def get_db_path(base_dir=None, db_name=DEFAULT_DB_NAME):
    """Determina la ruta completa al archivo de base de datos."""
    if base_dir is None:
        # Asume el directorio del script principal si no se especifica base_dir
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.join(base_dir, db_name)


def get_db_connection(db_path):
    """Establece y devuelve una conexión a la base de datos SQLite."""
    _logger.debug(f"Intentando conectar a la base de datos SQLite en: {db_path}")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row # Permite acceder a columnas por nombre
        _logger.info("Conexión a la base de datos SQLite establecida.")
        return conn
    except sqlite3.Error as e:
        _logger.critical(f"Error al conectar a la base de datos SQLite en {db_path}: {e}", exc_info=True)
        return None

def init_db(db_path):
    """Inicializa el esquema de la base de datos si no existe."""
    conn = get_db_connection(db_path)
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute(SCHEMA)
            conn.commit()
            _logger.info("Esquema de la base de datos SQLite verificado/inicializado.")
            return True
        except sqlite3.Error as e:
            _logger.critical(f"Error al inicializar el esquema de la base de datos: {e}", exc_info=True)
            return False
        finally:
            conn.close()
    return False


def add_item(db_path, code, source_type, status='pending', sync_modes='all'):
    """Añade un único item a la tabla sync_items si no existe."""
    conn = get_db_connection(db_path)
    if conn:
        try:
            cursor = conn.cursor()
            # Usamos INSERT OR IGNORE para no fallar si el código ya está en la DB
            cursor.execute("INSERT OR IGNORE INTO sync_items (code, source_type, status, sync_modes) VALUES (?, ?, ?, ?)",
                           (code, source_type, status, sync_modes))
            if cursor.rowcount > 0:
                 _logger.debug(f"Añadido item '{code}' (source: {source_type}, status: {status}) a la base de datos.")
                 conn.commit() # Commit inmediato para add_item
                 return True
            else:
                 _logger.debug(f"Item '{code}' ya existe en la base de datos. No se añadió de nuevo.")
                 return False # Indica que no se añadió una fila nueva

        except sqlite3.Error as e:
            _logger.error(f"Error al añadir/verificar item '{code}' en la base de datos: {e}", exc_info=True)
            return False
        finally:
            conn.close()
    return False # Conexión fallida


def add_items_from_csv(db_path, csv_filename, base_dir=None):
    """Lee códigos desde CSV y los añade a la base de datos con status 'pending'."""
    _logger.info(f"Cargando códigos desde CSV: {csv_filename}")
    codes = []
    try:
        # Asume que el CSV está relativo al directorio base del script si no se especifica base_dir
        script_base_dir = base_dir or os.path.dirname(os.path.abspath(sys.argv[0]))
        file_path = os.path.join(script_base_dir, csv_filename)

        with open(file_path, 'r', encoding='utf-8') as f:
             line = f.readline().strip()

        if not line:
            _logger.warning(f"Archivo CSV '{csv_filename}' vacío.")
            return 0 # Devuelve cuántos se añadieron

        # En la Fase 1, el modo '*' se manejará como una fuente diferente para añadir a la DB.
        # Por ahora, este método asume que el CSV solo contiene una lista de códigos.
        raw_codes = line.split(',')
        codes = [code.strip() for code in raw_codes if code.strip()]

    except FileNotFoundError:
        _logger.error(f"Error: No se encontró archivo CSV '{csv_filename}' en {script_base_dir}.")
        return 0
    except Exception as e:
        _logger.error(f"Error inesperado al leer/procesar CSV '{csv_filename}': {e}", exc_info=True)
        return 0

    if not codes:
         _logger.warning("No se encontraron códigos válidos en el archivo CSV para añadir.")
         return 0

    conn = get_db_connection(db_path)
    if conn:
        try:
            cursor = conn.cursor()
            added_count = 0
            for code in codes:
                 # Usamos INSERT OR IGNORE para no fallar si el código ya está en la DB
                 cursor.execute("INSERT OR IGNORE INTO sync_items (code, source_type, status) VALUES (?, ?, ?)",
                                (code, 'csv', 'pending'))
                 if cursor.rowcount > 0:
                      added_count += 1
            conn.commit()
            _logger.info(f"Intentados añadir {len(codes)} códigos desde CSV. Items nuevos añadidos: {added_count}.")
            # No podemos saber fácilmente cuántos ya existían con INSERT OR IGNORE sin SELECT adicional,
            # el rowcount es solo para las filas que *realmente* se insertaron.
            return added_count # Retorna el número de filas *añadidas*
        except sqlite3.Error as e:
            _logger.error(f"Error al añadir códigos desde CSV a la base de datos: {e}", exc_info=True)
            return 0
        finally:
            conn.close()
    return 0 # Conexión fallida


# --- Funciones para cargar items a procesar ---
def load_items_to_process(db_path, target_status='pending'):
    """Carga items de la base de datos con un estado específico."""
    conn = get_db_connection(db_path)
    items = []
    if conn:
        try:
            cursor = conn.cursor()
            # Ordenar por code para procesamiento por lotes si se desea en fases futuras, o simplemente para consistencia
            cursor.execute("SELECT * FROM sync_items WHERE status = ? ORDER BY code", (target_status,))
            # Fetch all rows. Each row is a sqlite3.Row object (like a dict)
            items = [dict(row) for row in cursor.fetchall()]
            _logger.info(f"Cargados {len(items)} items con estado '{target_status}' de la base de datos.")
        except sqlite3.Error as e:
            _logger.error(f"Error al cargar items con estado '{target_status}' de la base de datos: {e}", exc_info=True)
        finally:
            conn.close()
    return items # Devuelve la lista de diccionarios (puede estar vacía)


def load_items_by_codes(db_path, codes):
    """Carga items de la base de datos por una lista específica de códigos."""
    if not codes:
        return []
    conn = get_db_connection(db_path)
    items = []
    if conn:
        try:
            cursor = conn.cursor()
            # Crear placeholders (?, ?, ...) para la lista de códigos
            placeholders = ','.join('?' for _ in codes)
            query = f"SELECT * FROM sync_items WHERE code IN ({placeholders}) ORDER BY code"
            cursor.execute(query, codes)
            items = [dict(row) for row in cursor.fetchall()]
            _logger.info(f"Cargados {len(items)} items por lista de códigos ({len(codes)} solicitados) de la base de datos.")
        except sqlite3.Error as e:
            _logger.error(f"Error al cargar items por lista de códigos de la base de datos: {e}", exc_info=True)
        finally:
            conn.close()
    return items # Devuelve la lista de diccionarios (puede estar vacía)


# --- Funciones para actualizar estado ---
def update_item_status(db_path, code, status, error_message=None, o16_id=None, o17_id=None):
    """Actualiza el estado y otros campos de un item en la base de datos."""
    conn = get_db_connection(db_path)
    if conn:
        try:
            cursor = conn.cursor()
            update_time = datetime.datetime.now().isoformat()
            update_fields = {'status': status, 'last_sync_attempt': update_time}
            
            # Limpiar el error_message si el estado cambia a no fallido
            if status != 'failed':
                 update_fields['error_message'] = None
            elif error_message is not None:
                 update_fields['error_message'] = error_message # Solo guardar error si es 'failed' y se proporciona


            if status == 'succeeded':
                update_fields['last_sync_success'] = update_time

            # Actualizar IDs si se proporcionan y son válidos (no None)
            if o16_id is not None:
                 update_fields['o16_template_id'] = o16_id
            if o17_id is not None:
                 update_fields['o17_template_id'] = o17_id

            # Construir la parte SET de la consulta dinámicamente
            set_clause = ', '.join([f"{key} = ?" for key in update_fields])
            query = f"UPDATE sync_items SET {set_clause} WHERE code = ?"
            values = list(update_fields.values()) + [code]

            cursor.execute(query, values)
            conn.commit()
            # _logger.debug(f"Estado del item '{code}' actualizado a '{status}'.") # Demasiado verbose en DEBUG
            return True
        except sqlite3.Error as e:
            _logger.error(f"Error al actualizar estado del item '{code}' a '{status}' en la base de datos: {e}", exc_info=True)
            return False
        finally:
            conn.close()
    return False # Conexión fallida

# --- Función para borrar todos los items ---
def clear_sync_items(db_path):
    """Borra todos los items de la tabla sync_items."""
    conn = get_db_connection(db_path)
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sync_items")
            conn.commit()
            _logger.info("Tabla sync_items limpiada.")
            return True
        except sqlite3.Error as e:
            _logger.error(f"Error al limpiar la tabla sync_items: {e}", exc_info=True)
            return False
        finally:
            conn.close()
    return False # Conexión fallida

# --- Función para contar items por estado (útil para el resumen o status command) ---
def count_items_by_status(db_path):
    """Cuenta el número de items por cada estado."""
    conn = get_db_connection(db_path)
    counts = {}
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT status, COUNT(*) FROM sync_items GROUP BY status")
            counts = dict(cursor.fetchall())
            _logger.debug("Contados items por estado.")
        except sqlite3.Error as e:
            _logger.error(f"Error al contar items por estado: {e}", exc_info=True)
        finally:
            conn.close()
    return counts # Devuelve diccionario {status: count}