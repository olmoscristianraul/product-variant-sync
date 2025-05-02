#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#========================================================================
#          Script de Sincronización de Productos Odoo 16 -> Odoo 17
#========================================================================
#
# Autor:   Cristián R. Olmos
# Empresa: HC Sinergia S.A.
# Fecha:   2025-04-20
# Versión: 1.2 (Incluye sync de imagen y resumen detallado)

# Resumen:
# --------
# Este script Python utiliza XML-RPC para conectar una instancia de Odoo 16
# (origen) y una de Odoo 17 (destino) con el fin de sincronizar datos de
# productos. Lee la configuración de conexión desde `config.ini` y la lista
# de `default_code` de productos a procesar desde un archivo CSV
# (configurable, por defecto `products_to_sync.csv`).

# Permite sincronizar todos los productos activos con `default_code` (usando '*'
# en el CSV) o una lista específica indicando uno o más productos separados por coma. 
# Durante el proceso, sincroniza el `product.template` (nombre, activo, imagen principal), 
# sus atributos y valores (creándolos si no existen en destino), las líneas de atributo, y
# las variantes (`product.product`) correspondientes (código, código legacy,
# código de barras, activo, imagen principal), archivando variantes obsoletas
# en destino.
#
# Incluye mecanismos para preservar la Referencia Interna (default_code) del
# producto plantilla en Odoo 17 y genera logs detallados de la ejecución
# en un subdirectorio `logs`, indicando el estado final (OK/ERROR) en el
# nombre del archivo. Al finalizar, imprime un resumen detallado en consola
# con información comparativa de O16 y O17.


import xmlrpc.client
import logging
import time
import os
import sys
import configparser
import datetime
import traceback

# --- Nombre del archivo de configuración INI ---
CONFIG_FILENAME = 'config.ini'

# --- Configuración Inicial del Logging ---
# (crea 'logs' y archivo _TEMP.log)
try:
    script_dir = os.path.dirname(os.path.abspath(__file__)); logs_dir = os.path.join(script_dir, 'logs'); os.makedirs(logs_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S"); provisional_log_filename = os.path.join(logs_dir, f"{timestamp}_TEMP.log")
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(funcName)s] %(message)s'); logger = logging.getLogger(); logger.setLevel(logging.DEBUG)
    for handler in logger.handlers[:]: logger.removeHandler(handler) # Limpiar handlers
    file_handler = logging.FileHandler(provisional_log_filename, encoding='utf-8', mode='w'); file_handler.setFormatter(log_formatter); file_handler.setLevel(logging.DEBUG); logger.addHandler(file_handler)
    console_handler = logging.StreamHandler(sys.stdout); console_handler.setFormatter(log_formatter); console_handler.setLevel(logging.INFO); logger.addHandler(console_handler)
    # logging.info(f"Logging configurado. Logs completos en: {provisional_log_filename} (provisional)") # Loguear después de registrar hora inicio
except Exception as log_setup_error: print(f"CRITICAL: Fallo config logging: {log_setup_error}", file=sys.stderr); sys.exit(2)

# --- Cargar configuración ---
# (lee config.ini)
config = configparser.ConfigParser(); files_read = config.read(CONFIG_FILENAME, encoding='utf-8')
if not files_read: logging.error(f"Error: No leer '{CONFIG_FILENAME}'."); sys.exit(1)
logging.info(f"Configuración cargada desde '{CONFIG_FILENAME}'")
try:
    O16_URL = config.get('ODOO16', 'URL', fallback=None); O16_DB = config.get('ODOO16', 'DB', fallback=None); O16_USER = config.get('ODOO16', 'USER', fallback=None); O16_PASS = config.get('ODOO16', 'PASS', fallback=None)
    O17_URL = config.get('ODOO17', 'URL', fallback=None); O17_DB = config.get('ODOO17', 'DB', fallback=None); O17_USER = config.get('ODOO17', 'USER', fallback=None); O17_PASS = config.get('ODOO17', 'PASS', fallback=None)
    CSV_FILENAME = config.get('SYNC_SETTINGS', 'CSV_FILENAME', fallback='products_to_sync.csv')
except configparser.NoSectionError as e: logging.error(f"Error: Falta sección '{e.section}' en '{CONFIG_FILENAME}'."); sys.exit(1)
except Exception as e: logging.error(f"Error inesperado leer config: {e}", exc_info=True); sys.exit(1)
required_vars = { 'O16_URL': O16_URL, 'O16_DB': O16_DB, 'O16_USER': O16_USER, 'O16_PASS': O16_PASS, 'O17_URL': O17_URL, 'O17_DB': O17_DB, 'O17_USER': O17_USER, 'O17_PASS': O17_PASS }
missing_vars = [key for key, value in required_vars.items() if value is None]
if missing_vars: logging.error(f"Error: Faltan claves en '{CONFIG_FILENAME}': {', '.join(missing_vars)}"); sys.exit(1)


# --- Funciones Auxiliares ---
# (connect_odoo, execute_odoo_kw, find_record, get_or_create_attribute, get_or_create_attribute_value, load_codes_to_process)
def connect_odoo(url, db, user, password):
    """Intenta conectar y autenticarse a una instancia Odoo."""
    try:
        logging.info(f"Intentando conectar a {url}, DB: {db}")
        common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common'); uid = common.authenticate(db, user, password, {})
        if not uid: logging.error(f"Auth fallo para {user} en {url} DB {db}"); return None, None
        models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object'); logging.info(f"Conexión OK a {url}, DB: {db} (UID: {uid})"); return models, uid
    except xmlrpc.client.Fault as e: logging.error(f"RPC Error conexión/auth a {url} DB {db}: Code {e.faultCode}, Str: {e.faultString}"); return None, None
    except Exception as e: logging.error(f"Error inesperado conexión a {url} DB {db}: {type(e).__name__} - {e}", exc_info=True); return None, None

def execute_odoo_kw(models, db_name, uid, password, model, method, args=[], kwargs={}):
    """
    Ejecuta un método Odoo via XML-RPC execute_kw con logging y manejo de errores.
    (Versión con sintaxis CORREGIDA v4 en logging y except blocks)
    """
    # Definir longitud máxima para logs una sola vez
    max_log_len = 500
    # Copiar args y kwargs para evitar modificar originales
    rpc_args = list(args)
    rpc_kwargs = dict(kwargs)

    try:
        # --- Ajuste específico para 'read' con 'fields' ---
        if method == 'read' and 'fields' in rpc_kwargs:
            fields_list = rpc_kwargs.pop('fields'); ids_list_to_read = None
            if isinstance(rpc_args, list) and rpc_args and all(isinstance(x, int) for x in rpc_args): ids_list_to_read = rpc_args; rpc_args = [ids_list_to_read, fields_list]
            elif isinstance(rpc_args, list) and len(rpc_args) == 1 and isinstance(rpc_args[0], list) and all(isinstance(x, int) for x in rpc_args[0]): ids_list_to_read = rpc_args[0]; rpc_args = [ids_list_to_read, fields_list]
            elif not rpc_args or (isinstance(rpc_args, list) and len(rpc_args) == 1 and isinstance(rpc_args[0], list) and not rpc_args[0]): ids_list_to_read = []; rpc_args = [ [], fields_list]
            else: logging.warning(f"Formato args no ID para 'read' con fields. Args: {args}. No ajustado."); rpc_kwargs['fields'] = fields_list

        # --- Logging de argumentos  ---
        args_log_str = repr(rpc_args)
        if len(args_log_str) > max_log_len:               # Separado
            args_log_str = args_log_str[:max_log_len] + "...(truncated)"

        kwargs_log_str = repr(rpc_kwargs)
        if len(kwargs_log_str) > max_log_len:             # Separado
            kwargs_log_str = kwargs_log_str[:max_log_len] + "...(truncated)"

        log_level = logging.DEBUG if method in ['read', 'search_read'] else logging.INFO
        logging.log(log_level, f"Executing: DB='{db_name}', Mdl='{model}', Meth='{method}'")
        logging.debug(f"Args: {args_log_str}, Kwargs: {kwargs_log_str}")
        # --- Fin Logging Args ---

        start_time = time.time()
        result = models.execute_kw(db_name, uid, password, model, method, rpc_args, rpc_kwargs)
        duration = time.time() - start_time

        # --- Logging del resultado  ---
        result_log_str = repr(result)
        # Omitir imagen base64
        if isinstance(result, list) and result and isinstance(result[0], dict) and 'image_1920' in result[0]: result_log_str = f"List of {len(result)} dicts (image data omitted)"
        elif isinstance(result, dict) and 'image_1920' in result: result_log_str = f"Dict (image data omitted)"
        elif isinstance(result, str) and len(result) > max_log_len:
             if result.startswith('iVBOR') or result.startswith('/9j/') or (len(result) > 1000 and '=' in result[-4:]): result_log_str = f"Base64 string length {len(result)} (data omitted)"
             # No es necesario re-truncar aquí si ya se hizo abajo

        # Truncar si sigue siendo largo
        if len(result_log_str) > max_log_len:               # Separado
            result_log_str = result_log_str[:max_log_len] + "...(truncated)"
        logging.debug(f"Result ({duration:.4f}s) {model}.{method}: {result_log_str}")
        # --- Fin Logging Resultado ---

        return result

    except xmlrpc.client.Fault as e:
        # Loguear error RPC 
        args_log_str = repr(rpc_args) # Recalcular
        if len(args_log_str) > max_log_len:                 # Separado
            args_log_str = args_log_str[:max_log_len] + "...(truncated)"
        logging.error(f"RPC Error: DB='{db_name}', Mdl='{model}', Meth='{method}'. Code: {e.faultCode}. Str: {e.faultString}. Args: {args_log_str}", exc_info=False)
        return None

    except Exception as e:
        # Loguear error inesperado 
        args_log_str = repr(rpc_args) # Recalcular
        if len(args_log_str) > max_log_len:                 # Separado
            args_log_str = args_log_str[:max_log_len] + "...(truncated)"
        logging.error(f"Error: {method} on {model} in {db_name}: {type(e).__name__} - {e}. Args: {args_log_str}", exc_info=True)
        return None

def find_record(models, db_name, uid, password, model, domain, fields=['id'], limit=None):
    """Busca registros Odoo (incluye inactivos)."""
    kwargs = {'fields': fields, 'context': {'active_test': False}};
    if limit is not None: kwargs['limit'] = limit
    logging.debug(f"Searching {model} domain {domain} (limit={limit}, active_test=False)...")
    result = execute_odoo_kw(models, db_name, uid, password, model, 'search_read', [domain], kwargs)
    if result is None: logging.error(f"RPC search_read failed {model} domain {domain}"); return None
    return result

def get_or_create_attribute(models_o17, db_name_o17, uid_o17, o17_pass, attr_name_o16):
    """ Busca/Crea atributo o17 (case-insensitive). """
    normalized_name = " ".join((attr_name_o16 or '').split()).strip()
    if not normalized_name: logging.error(f"Attr name o16 vacío: '{attr_name_o16}'"); return None
    logging.debug(f"Get/Create Attr o17: '{normalized_name}'")
    domain = [('name', '=ilike', normalized_name)]; attribute_data = find_record(models_o17, db_name_o17, uid_o17, o17_pass, 'product.attribute', domain, fields=['id', 'name'], limit=1)
    if attribute_data is None: logging.error(f"Fallo RPC buscar attr '{normalized_name}' o17."); return None
    if attribute_data: attr_id = attribute_data[0]['id']; logging.debug(f"Attr '{normalized_name}' encontrado o17 ID: {attr_id}"); return attr_id
    else:
        logging.info(f"Attr '{normalized_name}' no encontrado o17. Creando..."); create_vals = {'name': normalized_name, 'create_variant': 'no_variant'}
        new_attr_id = execute_odoo_kw(models_o17, db_name_o17, uid_o17, o17_pass, 'product.attribute', 'create', [create_vals])
        if new_attr_id and isinstance(new_attr_id, int): logging.info(f"Attr '{normalized_name}' creado o17 ID: {new_attr_id}"); return new_attr_id
        else: logging.error(f"No crear attr '{normalized_name}' o17. Result: {new_attr_id}."); return None

def get_or_create_attribute_value(models_o17, db_name_o17, uid_o17, o17_pass, value_name_o16, attribute_id_o17):
    """ Busca/Crea valor attr o17 (case-insensitive). """
    if not isinstance(attribute_id_o17, int) or attribute_id_o17 <= 0: logging.error(f"ID attr padre inválido ({attribute_id_o17}) valor '{value_name_o16}' o17."); return None
    normalized_name = " ".join((value_name_o16 or '').split()).strip()
    if not normalized_name: logging.error(f"Nombre valor o16 vacío AttrID {attribute_id_o17}: '{value_name_o16}'"); return None
    logging.debug(f"Get/Create Val o17 AttrID {attribute_id_o17}: '{normalized_name}'")
    domain = [('attribute_id', '=', attribute_id_o17), ('name', '=ilike', normalized_name)]
    value_data = find_record(models_o17, db_name_o17, uid_o17, o17_pass, 'product.attribute.value', domain, fields=['id', 'name'], limit=1)
    if value_data is None: logging.error(f"Fallo RPC buscar valor '{normalized_name}' (AttrID {attribute_id_o17}) o17."); return None
    if value_data: value_id = value_data[0]['id']; logging.debug(f"Valor '{normalized_name}' encontrado o17 (AttrID {attribute_id_o17}) ID: {value_id}"); return value_id
    else:
        logging.info(f"Valor '{normalized_name}' (AttrID {attribute_id_o17}) no encontrado o17. Creando...")
        create_vals = {'name': normalized_name,'attribute_id': attribute_id_o17}
        new_value_id = execute_odoo_kw(models_o17, db_name_o17, uid_o17, o17_pass, 'product.attribute.value', 'create', [create_vals])
        if new_value_id and isinstance(new_value_id, int): logging.info(f"Valor '{normalized_name}' creado o17 (AttrID {attribute_id_o17}) ID: {new_value_id}"); return new_value_id
        else: logging.error(f"No crear valor '{normalized_name}' (AttrID {attribute_id_o17}) o17. Result: {new_value_id}."); return None

def load_codes_to_process(csv_filename, models_o16, uid_o16, o16_pass):
    """ Lee códigos desde CSV. Si es '*', busca todos en Odoo 16. """
    codes = []; logging.info(f"Intentando leer códigos desde: {csv_filename}")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__)); file_path = os.path.join(script_dir, csv_filename)
        with open(file_path, 'r', encoding='utf-8') as f: line = f.readline().strip()
        if not line: logging.warning(f"Archivo CSV '{csv_filename}' vacío."); return []
        if line == '*':
            logging.info("'*' encontrado. Obteniendo todos default_code activos o16..."); domain_all = [('default_code', '!=', False), ('default_code', '!=', ''), ('active', '=', True)]; fields_all = ['default_code']
            all_templates_data = execute_odoo_kw(models_o16, O16_DB, uid_o16, o16_pass,'product.template', 'search_read', [domain_all], {'fields': fields_all})
            if all_templates_data is None: logging.error("Fallo RPC obtener todos default_codes O16."); return []
            codes_from_o16 = set()
            for template in all_templates_data:
                code = template.get('default_code')
                if isinstance(code, str) and code.strip(): codes_from_o16.add(code.strip())
                elif code and not isinstance(code, str): logging.warning(f"default_code no-string no-False o16: {code}. Ignorado.")
            codes = sorted(list(codes_from_o16)); logging.info(f"Se procesarán {len(codes)} códigos desde O16.")
            if not codes: logging.warning("Consulta O16 no devolvió códigos válidos.")
        else:
            logging.info("Procesando códigos específicos desde CSV..."); raw_codes = line.split(',')
            codes = [code.strip() for code in raw_codes if code.strip()]; logging.info(f"Se procesarán {len(codes)} códigos desde CSV.")
            if not codes: logging.warning("No códigos válidos en CSV.")
    except FileNotFoundError: logging.error(f"Error: No se encontró archivo CSV '{csv_filename}' en {script_dir}."); return []
    except Exception as e: logging.error(f"Error inesperado leer/procesar CSV '{csv_filename}': {e}", exc_info=True); return []
    return codes


# --- Lógica Principal de Sincronización ---
def sync_products():
    """Función principal que orquesta la sincronización."""
    logging.info(" Iniciando Sincronización ".center(80, '*'))
    global script_status # Necesario para modificar el estado global

    models_o16, uid_o16 = connect_odoo(O16_URL, O16_DB, O16_USER, O16_PASS)
    models_o17, uid_o17 = connect_odoo(O17_URL, O17_DB, O17_USER, O17_PASS)

    if not models_o16 or not models_o17:
        logging.error("Conexión(es) fallida(s). Abortando."); script_status = "ERROR"; return None, {}

    codes_to_process = load_codes_to_process(CSV_FILENAME, models_o16, uid_o16, O16_PASS)
    if not codes_to_process:
        logging.warning("No hay códigos para procesar. Finalizando."); return [], {}

    logging.info(f"Códigos a procesar: {codes_to_process}")
    initial_o17_default_codes = {}
    summary_data = []
    counters = { 'templates_analyzed': 0, 'o16_variants_found': 0, 'o17_variants_existing': 0, 'o17_variants_created': 0, 'o17_variants_updated': 0, 'o17_variants_archived': 0, 'templates_failed': 0, }

    for template_default_code_to_sync in codes_to_process:
        logging.info(f"\n--- Procesando Template con default_code: {template_default_code_to_sync} ---")
        counters['templates_analyzed'] += 1
        o17_template_id = None; map_success = True
        template_summary = { 'code': template_default_code_to_sync, 'o16_data': {'id': None, 'name': None, 'attributes': [], 'variants': []}, 'o17_sync': {'id': None, 'name_final': None, 'default_code_final': None, 'status': 'ok', 'variants_synced': [], 'variants_archived': []}, 'processed_ok': True }

        try:
            # 1. Buscar FUENTE O16
            domain_o16_template = [('default_code', '=', template_default_code_to_sync)]; fields_o16_template = ['id', 'name', 'default_code', 'attribute_line_ids', 'product_variant_ids', 'active', 'image_1920']
            o16_template_data_list = find_record(models_o16, O16_DB, uid_o16, O16_PASS, 'product.template', domain_o16_template, fields_o16_template, limit=1)
            if o16_template_data_list is None: raise Exception(f"Fallo RPC buscar '{template_default_code_to_sync}' o16.")
            if not o16_template_data_list: logging.warning(f"Template '{template_default_code_to_sync}' no encontrado o16. Saltando."); template_summary['processed_ok'] = False; counters['templates_failed'] += 1; summary_data.append(template_summary); continue # No fatal, solo saltar
            o16_template = o16_template_data_list[0]; o16_template_id = o16_template['id']; template_summary['o16_data']['id'] = o16_template_id; o16_template_code = o16_template.get('default_code'); o16_template_name = o16_template.get('name'); o16_is_active = o16_template.get('active', True); o16_image_1920 = o16_template.get('image_1920')
            template_summary['o16_data']['name'] = o16_template_name
            o16_template_code_cleaned = (o16_template_code or '').strip()
            if not o16_template_code_cleaned and o16_template_code is not False: logging.warning(f"Code template FUENTE o16 ID {o16_template_id} vacío ('{o16_template_code}').")
            logging.info(f"Fuente o16: ID {o16_template_id}, Name: '{o16_template_name}', Code: '{o16_template_code}', Active: {o16_is_active}, Has Image: {bool(o16_image_1920)}")

            # 1b. Leer Atributos/Valores O16 para resumen
            o16_attribute_line_ids = o16_template.get('attribute_line_ids', [])
            if o16_attribute_line_ids:
                o16_lines_details = execute_odoo_kw(models_o16, O16_DB, uid_o16, O16_PASS, 'product.template.attribute.line', 'read', o16_attribute_line_ids, {'fields': ['attribute_id', 'value_ids']})
                if o16_lines_details:
                    all_value_ids = list(set(vid for line in o16_lines_details for vid in line.get('value_ids',[]))); value_names = {}
                    if all_value_ids:
                         o16_values_names = execute_odoo_kw(models_o16, O16_DB, uid_o16, O16_PASS, 'product.attribute.value', 'read', all_value_ids, {'fields': ['id', 'name']})
                         if o16_values_names: value_names = {v['id']: v['name'] for v in o16_values_names}
                    for line in o16_lines_details:
                        attr_name = line.get('attribute_id', [None, 'N/A'])[1]; value_ids_in_line = line.get('value_ids', [])
                        value_names_in_line = [value_names.get(vid, f'ID:{vid}') for vid in value_ids_in_line]
                        template_summary['o16_data']['attributes'].append(f"{attr_name}: {', '.join(value_names_in_line)}")
                else: logging.warning(f"No se pudieron leer detalles de attribute_line_ids {o16_attribute_line_ids} desde O16.")

            # 2. Buscar/Crear DESTINO O17
            domain_o17_template = [('default_code', '=', template_default_code_to_sync), ('active', 'in', [True, False])]; fields_o17_template = ['id', 'name', 'default_code', 'attribute_line_ids', 'active', 'image_1920']
            o17_template_data_list = find_record(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template', domain_o17_template, fields_o17_template, limit=1)
            existing_o17_attr_line_ids = []; o17_template_data = None
            if o17_template_data_list is None: raise Exception(f"Fallo RPC buscar DESTINO '{template_default_code_to_sync}' o17.")
            if o17_template_data_list: # --- ENCONTRADO EN O17 ---
                o17_template_data = o17_template_data_list[0]; o17_template_id = o17_template_data['id']; template_summary['o17_sync']['id'] = o17_template_id
                current_o17_name = o17_template_data.get('name'); current_o17_code = o17_template_data.get('default_code'); current_o17_active = o17_template_data.get('active', True); existing_o17_attr_line_ids = o17_template_data.get('attribute_line_ids', []); current_o17_image = o17_template_data.get('image_1920')
                template_summary['o17_sync']['name_final'] = current_o17_name
                initial_o17_default_codes[o17_template_id] = current_o17_code; logging.info(f"  [PRESERVE INITIAL] Guardando code inicial o17 ID {o17_template_id}: '{current_o17_code}'")
                logging.info(f"Destino existe o17: ID {o17_template_id}."); logging.info(f"  Actual o17 -> Name: '{current_o17_name}', Code: '{current_o17_code}', Active: {current_o17_active}, Has Image: {bool(current_o17_image)}"); logging.info(f"  o16 fuente -> Name: '{o16_template_name}', Code: '{o16_template_code_cleaned}', Active: {o16_is_active}, Has Image: {bool(o16_image_1920)}")
                template_update_vals = {}
                if (current_o17_name or '').strip() != (o16_template_name or '').strip(): template_update_vals['name'] = o16_template_name or ''; logging.info(f"  -> Nombre difiere.")
                if current_o17_active != o16_is_active: template_update_vals['active'] = o16_is_active; logging.info(f"  -> Active difiere.")
                if o16_image_1920 and o16_image_1920 != current_o17_image: template_update_vals['image_1920'] = o16_image_1920; logging.info(f"  -> Imagen difiere o presente en O16. Se actualizará.")
                elif o16_image_1920: logging.info(f"  -> Imagen idéntica. No se actualizará.")
                if template_update_vals:
                    logging.info(f"  Actualizando template o17 ID {o17_template_id}: {list(template_update_vals.keys())}")
                    write_ok = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template', 'write', [[o17_template_id], template_update_vals])
                    if write_ok is True: logging.info(f"  -> Update inicial OK."); template_summary['o17_sync']['name_final'] = template_update_vals.get('name', current_o17_name)
                    elif write_ok is False: logging.error(f"  -> Fallo lógico update inicial. Result False.") # No fatal
                    elif write_ok is None: raise Exception("Fallo RPC O17 write template update") # Fatal
                    else: logging.warning(f"  -> Result inesperado ({write_ok}) update inicial.")
                else: logging.info(f"  -> Template o17 ya sincronizado (nombre/activo/imagen).")
            else: # --- NO ENCONTRADO EN O17 ---
                logging.info(f"Template '{template_default_code_to_sync}' no existe o17. Creando..."); code_for_create = o16_template_code_cleaned if o16_template_code_cleaned else False
                template_vals_to_create = {'name': o16_template_name or '', 'default_code': code_for_create, 'type': 'product', 'active': o16_is_active, 'attribute_line_ids': [], 'image_1920': o16_image_1920 or False }; logging.debug(f"  Valores crear: name, code, type, active, attrs, image_1920={bool(o16_image_1920)}")
                new_o17_template_id = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template', 'create', [template_vals_to_create])
                if new_o17_template_id and isinstance(new_o17_template_id, int):
                    o17_template_id = new_o17_template_id; template_summary['o17_sync']['id'] = o17_template_id; created_code = template_vals_to_create.get('default_code'); logging.info(f"  Template creado o17 ID: {o17_template_id} code '{created_code}'.")
                    initial_o17_default_codes[o17_template_id] = created_code; logging.info(f"  [PRESERVE INITIAL] Guardando code inicial o17 ID {o17_template_id}: '{created_code}'")
                    o17_template_data_list_after_create = find_record(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template', [('id', '=', o17_template_id)], fields_o17_template, limit=1)
                    if o17_template_data_list_after_create: o17_template_data = o17_template_data_list_after_create[0]; existing_o17_attr_line_ids = o17_template_data.get('attribute_line_ids', []); template_summary['o17_sync']['name_final'] = o17_template_data.get('name')
                    else: raise Exception("Fallo RPC O17 read after create") # Fatal
                else: raise Exception(f"Fallo create O17 template. Result: {new_o17_template_id}") # Fatal

            if not o17_template_id: raise Exception("ID O17 no válido post find/create") # Fatal

            # 3. Sincronización Atributos/Valores
            # ... (código igual) ...
            logging.info(f"Sincronizando attrs/líneas template o17 ID {o17_template_id}...")
            o16_attribute_line_ids = o16_template.get('attribute_line_ids', [])
            existing_o17_lines_map = {}
            if existing_o17_attr_line_ids:
                o17_existing_lines_details = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template.attribute.line', 'read', existing_o17_attr_line_ids, {'fields': ['id', 'attribute_id']})
                if o17_existing_lines_details is None: logging.warning(f"Fallo RPC leer líneas attr existentes {existing_o17_attr_line_ids} o17.") # No fatal?
                elif o17_existing_lines_details: existing_o17_lines_map = {line['attribute_id'][0]: line['id'] for line in o17_existing_lines_details if line.get('attribute_id') and isinstance(line['attribute_id'],(list, tuple)) and len(line['attribute_id']) > 0 and isinstance(line['attribute_id'][0], int)}
            o17_attribute_line_commands = []; o17_pav_mapping = {}; # map_success = True (definida al inicio del loop)
            if o16_attribute_line_ids:
                o16_attribute_lines = execute_odoo_kw(models_o16, O16_DB, uid_o16, O16_PASS, 'product.template.attribute.line', 'read', o16_attribute_line_ids, {'fields': ['id', 'attribute_id', 'value_ids']})
                if o16_attribute_lines is None: map_success = False; logging.error(f"Fallo RPC leer líneas attr {o16_attribute_line_ids} o16."); raise Exception("Fallo RPC O16 read lines")
                elif not o16_attribute_lines and o16_attribute_line_ids: logging.warning(f"No detalles líneas attr {o16_attribute_line_ids} o16.")
                if map_success and o16_attribute_lines:
                    processed_o17_line_ids_from_o16 = set(); all_o16_value_ids_needed = list(set(val_id for line in o16_attribute_lines for val_id in line.get('value_ids', []))); o16_value_details_map = {}
                    if all_o16_value_ids_needed:
                        o16_all_value_details = execute_odoo_kw(models_o16, O16_DB, uid_o16, O16_PASS, 'product.attribute.value', 'read', all_o16_value_ids_needed, {'fields': ['id', 'name']})
                        if o16_all_value_details is None: map_success = False; logging.error(f"  -> Fallo RPC leer valores {all_o16_value_ids_needed} o16."); raise Exception("Fallo RPC O16 read values")
                        elif o16_all_value_details: o16_value_details_map = {val['id']: val['name'] for val in o16_all_value_details if val.get('id') and val.get('name')}
                    if map_success:
                        for o16_line in o16_attribute_lines:
                            o16_line_id = o16_line['id']; o16_attribute_info = o16_line.get('attribute_id'); o16_value_ids_for_this_line = o16_line.get('value_ids', [])
                            if not o16_attribute_info or not isinstance(o16_attribute_info,(list, tuple)) or len(o16_attribute_info) < 2: logging.warning(f"Línea attr o16 ID:{o16_line_id} sin attr válido. Saltando."); continue
                            o16_attr_id, o16_attr_name = o16_attribute_info; logging.debug(f"  Proc línea attr o16 ID {o16_line_id}: Attr '{o16_attr_name}' ({o16_attr_id}), Vals: {o16_value_ids_for_this_line}")
                            o17_attr_id = get_or_create_attribute(models_o17, O17_DB, uid_o17, O17_PASS, o16_attr_name)
                            if not o17_attr_id: map_success = False; logging.error(f"  -> Fallo obtener/crear attr '{o16_attr_name}' o17. Saltando."); continue # No fatal?
                            o17_value_ids_for_line = []
                            if o16_value_ids_for_this_line:
                                for o16_value_id in o16_value_ids_for_this_line:
                                    o16_value_name = o16_value_details_map.get(o16_value_id)
                                    if not o16_value_name: logging.warning(f"  -> No nombre valor o16 ID {o16_value_id}. Saltando."); continue
                                    if o16_value_id in o17_pav_mapping: o17_value_id = o17_pav_mapping[o16_value_id];
                                    else: o17_value_id = get_or_create_attribute_value(models_o17, O17_DB, uid_o17, O17_PASS, o16_value_name, o17_attr_id); o17_pav_mapping[o16_value_id] = o17_value_id
                                    if o17_value_id: o17_value_ids_for_line.append(o17_value_id)
                                    else: map_success = False; logging.error(f"  -> Fallo obtener/crear valor '{o16_value_name}'. Marcando map_success=False.") # No fatal?
                            o17_line_id_existing = existing_o17_lines_map.get(o17_attr_id)
                            line_values_m2m = {'attribute_id': o17_attr_id, 'value_ids': [(6, 0, sorted(list(set(o17_value_ids_for_line))))]}
                            if o17_line_id_existing: logging.debug(f"  Prep cmd (1, {o17_line_id_existing}, ..) update."); o17_attribute_line_commands.append((1, o17_line_id_existing, line_values_m2m)); processed_o17_line_ids_from_o16.add(o17_line_id_existing)
                            else: logging.debug(f"  Prep cmd (0, 0, ..) create."); o17_attribute_line_commands.append((0, 0, line_values_m2m))
                    lines_to_delete_in_o17 = [line_id for line_id in existing_o17_attr_line_ids if line_id not in processed_o17_line_ids_from_o16]
                    if lines_to_delete_in_o17:
                        logging.info(f"  {len(lines_to_delete_in_o17)} líneas obsoletas. Prep eliminar.");
                        for line_id_to_delete in lines_to_delete_in_o17: logging.debug(f"  Prep cmd (2, {line_id_to_delete}) delete."); o17_attribute_line_commands.append((2, line_id_to_delete))
            else: # o16 no tiene lineas
                logging.info(f"Template o16 ID {o16_template_id} no tiene líneas attr.")
                if existing_o17_attr_line_ids:
                    logging.info(f"  Template o16 no líneas, o17 sí. Prep eliminar.");
                    for line_id_to_delete in existing_o17_attr_line_ids: logging.debug(f"  Prep cmd (2, {line_id_to_delete}) delete."); o17_attribute_line_commands.append((2, line_id_to_delete))
            # Aplicar cambios a lineas
            if o17_attribute_line_commands:
                if map_success: # Aplicar solo si mapeo fue OK (o no hubo errores fatales)
                    logging.info(f"  Aplicando {len(o17_attribute_line_commands)} cmds a attribute_line_ids o17 ID {o17_template_id}...")
                    o17_code_before_attr_update = False; template_read_result = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template', 'read', [o17_template_id], {'fields': ['default_code']})
                    if template_read_result and isinstance(template_read_result, list) and template_read_result: o17_code_before_attr_update = template_read_result[0].get('default_code', False); logging.info(f"  [PRESERVE CHECK] Code antes: '{o17_code_before_attr_update}'")
                    else: logging.error(f"  [PRESERVE CHECK] No leer code antes. Result: {template_read_result}") # No fatal?
                    attr_line_update_ok = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template', 'write', [[o17_template_id], {'attribute_line_ids': o17_attribute_line_commands}])
                    if attr_line_update_ok is True:
                        logging.info(f"  -> Update líneas attr OK.")
                        o17_code_after_attr_update = False; template_read_result_after = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template', 'read', [o17_template_id], {'fields': ['default_code']})
                        if template_read_result_after and isinstance(template_read_result_after, list) and template_read_result_after:
                             o17_code_after_attr_update = template_read_result_after[0].get('default_code', False); logging.info(f"  [POST CHECK] Code después: '{o17_code_after_attr_update}'")
                             initial_code_for_this_template = initial_o17_default_codes.get(o17_template_id, None); has_value_now = bool(o17_code_after_attr_update); had_value_initially = bool(initial_code_for_this_template)
                             if not has_value_now and had_value_initially:
                                 logging.warning(f"  -> [FIX DETECTADO] Code perdido (era '{initial_code_for_this_template}', ahora '{o17_code_after_attr_update}'). Restaurando.");
                                 rewrite_code_ok = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template', 'write', [[o17_template_id], {'default_code': initial_code_for_this_template}])
                                 if rewrite_code_ok is True:
                                     logging.info(f"  -> [FIX] Restauración a '{initial_code_for_this_template}' OK.");
                                     final_code_check_data = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template', 'read', [o17_template_id], {'fields': ['default_code']}); final_code = final_code_check_data[0].get('default_code') if final_code_check_data and isinstance(final_code_check_data, list) and final_code_check_data else 'ERROR_READ'; logging.info(f"  [FINAL CHECK Fix] Code post-restaurar: '{final_code}'")
                                 else: logging.error(f"  -> [FIX] Fallo restaurar code '{initial_code_for_this_template}'. Result: {rewrite_code_ok}") # No fatal?
                             elif o17_code_after_attr_update != initial_code_for_this_template: logging.warning(f"  -> [CHECK] Code cambió de '{initial_code_for_this_template}' a '{o17_code_after_attr_update}' post-attrs, pero no borrado.")
                        else: logging.error(f"  [POST CHECK] No leer code después. No fix.")
                    elif attr_line_update_ok is False: logging.error(f"  -> Fallo lógico update líneas attr. Result False."); map_success = False # Marcar fallo
                    elif attr_line_update_ok is None: logging.error(f"  -> Fallo RPC/inesperado update líneas attr."); map_success = False; raise Exception("Fallo RPC O17 write lines") # Fatal
                    else: logging.warning(f"  -> Result inesperado ({attr_line_update_ok}) update líneas attr."); map_success = False # Marcar fallo
                elif not map_success: logging.warning("  Mapeo falló antes. No aplicar cmds líneas attr.")
            elif not o17_attribute_line_commands: logging.info("  No cmds para líneas attr.")

            # 4. Sincronización de Variantes
            if map_success:
                logging.info(f"Iniciando sync variantes template o17 ID {o17_template_id}...")
              
                # --- INICIO CÓDIGO VARIANTES ---
                fields_o16_variant_read = ['id', 'default_code', 'legacy_default_code', 'barcode', 'product_template_attribute_value_ids', 'active', 'image_1920']
                o16_variant_ids = o16_template.get('product_variant_ids', []); is_simple_product_o16 = not o16_attribute_line_ids and len(o16_variant_ids) == 1 and o16_variant_ids[0] == o16_template_id
                if not o16_attribute_line_ids and not o16_variant_ids: logging.info(f"  Tmpl o16 ID {o16_template_id} simple."); is_simple_product_o16 = True; o16_variant_ids = [o16_template_id]
                if not o16_variant_ids: logging.info("  Tmpl o16 sin variantes. Saltando sync vars.")
                else:
                    o16_variants_data = execute_odoo_kw(models_o16, O16_DB, uid_o16, O16_PASS, 'product.product', 'read', o16_variant_ids, {'fields': fields_o16_variant_read})
                    if o16_variants_data is None: logging.error(f"Fallo RPC leer vars {o16_variant_ids} o16. Saltando sync vars.")
                    elif not o16_variants_data and o16_variant_ids: logging.warning(f"No datos vars {o16_variant_ids} o16. Saltando sync vars.")
                    else:
                        counters['o16_variants_found'] += len(o16_variants_data)
                        # Guardar detalles O16 para resumen
                        template_summary['o16_data']['variants'] = [{'id': v['id'], 'code': v.get('default_code'), 'barcode': v.get('barcode'), 'legacy': v.get('legacy_default_code')} for v in o16_variants_data]

                        logging.info(f"  Obteniendo vars existentes o17 tmpl ID {o17_template_id}..."); fields_o17_variant = ['id', 'default_code', 'legacy_default_code', 'barcode', 'product_template_attribute_value_ids', 'active', 'image_1920']
                        existing_o17_variants_data = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.product', 'search_read', [[('product_tmpl_id', '=', o17_template_id), ('active', 'in', [True, False])]], {'fields': fields_o17_variant})
                        o17_variants_map_by_ptavs = {}; o17_variant_ids_to_clean_potential = set()
                        if existing_o17_variants_data is None: logging.error(f"Fallo RPC leer vars existentes o17 tmpl ID {o17_template_id}. Saltando sync vars.")
                        elif existing_o17_variants_data:
                             counters['o17_variants_existing'] += len(existing_o17_variants_data); logging.info(f"  {len(existing_o17_variants_data)} vars existentes o17. Mapeando...");
                             for variant_data in existing_o17_variants_data:
                                 o17_variant_id_inner = variant_data['id']; o17_variant_ids_to_clean_potential.add(o17_variant_id_inner)
                                 o17_variant_ptav_ids = variant_data.get('product_template_attribute_value_ids', []); key_ptav_ids = tuple(sorted(o17_variant_ptav_ids))
                                 if key_ptav_ids in o17_variants_map_by_ptavs: existing_variant_id = o17_variants_map_by_ptavs[key_ptav_ids]['id']; logging.warning(f"  Var duplicada o17 PTAV {key_ptav_ids}. Exist ID: {existing_variant_id}, Nueva ID: {o17_variant_id_inner}. Usando última.")
                                 o17_variants_map_by_ptavs[key_ptav_ids] = variant_data
                        else: logging.info("  No vars existentes o17 para este tmpl.")
                        processed_o17_variant_ids = set(); logging.info(f"  Procesando {len(o16_variants_data)} vars desde o16...")
                        for o16_variant in o16_variants_data:
                             o16_variant_id = o16_variant['id']; o16_variant_code = o16_variant.get('default_code'); o16_variant_legacy = o16_variant.get('legacy_default_code'); o16_variant_barcode = o16_variant.get('barcode'); o16_variant_is_active = o16_variant.get('active', True); o16_ptav_ids_o16 = o16_variant.get('product_template_attribute_value_ids', []); o16_variant_image = o16_variant.get('image_1920')
                             logging.debug(f"  Proc var o16 ID {o16_variant_id}: Code='{o16_variant_code}', Active={o16_variant_is_active}, PTAVs o16: {o16_ptav_ids_o16}, Has Image: {bool(o16_variant_image)}")
                             o17_target_ptav_ids = []; variant_mapping_failed = False; variant_key_ptavs = ()
                             # ... (Mapeo PTAVs O16->O17) ...
                             if o16_ptav_ids_o16: # Mapeo PTAVs...
                                o16_ptav_details = execute_odoo_kw(models_o16, O16_DB, uid_o16, O16_PASS, 'product.template.attribute.value', 'read', o16_ptav_ids_o16, {'fields': ['id', 'product_attribute_value_id']})
                                if o16_ptav_details is None: logging.error(f"    Fallo RPC PTAVs o16 {o16_ptav_ids_o16} var {o16_variant_id}. Saltando."); continue
                                if not o16_ptav_details and o16_ptav_ids_o16: logging.warning(f"    No detalles PTAVs {o16_ptav_ids_o16} o16. Saltando."); continue
                                o17_mapped_pav_ids = []
                                for ptav_detail_o16 in o16_ptav_details:
                                    o16_pav_info = ptav_detail_o16.get('product_attribute_value_id')
                                    if o16_pav_info and isinstance(o16_pav_info,(list, tuple)) and len(o16_pav_info) > 0 and isinstance(o16_pav_info[0], int):
                                        o16_pav_id = o16_pav_info[0]
                                        if o16_pav_id in o17_pav_mapping:
                                             o17_mapped_pav_id = o17_pav_mapping[o16_pav_id]
                                             if o17_mapped_pav_id: o17_mapped_pav_ids.append(o17_mapped_pav_id)
                                             else: logging.error(f"    PAV o16 ID {o16_pav_id} mapeado a None var {o16_variant_id}. Saltando."); variant_mapping_failed = True; break
                                        else: logging.error(f"    PAV o16 ID {o16_pav_id} no en mapeo var {o16_variant_id}. Saltando."); variant_mapping_failed = True; break
                                    else: logging.warning(f"    PTAV o16 ID {ptav_detail_o16.get('id')} var {o16_variant_id} no PAV válido. Saltando."); variant_mapping_failed = True; break
                                if variant_mapping_failed: continue
                                if o17_mapped_pav_ids:
                                     domain_ptav_combo_o17 = [('product_tmpl_id', '=', o17_template_id), ('product_attribute_value_id', 'in', o17_mapped_pav_ids)]
                                     o17_ptavs_for_combo_data = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template.attribute.value', 'search_read', [domain_ptav_combo_o17], {'fields': ['id', 'product_attribute_value_id']})
                                     if o17_ptavs_for_combo_data is None: logging.error(f"    Fallo RPC PTAVs o17 PAVs {o17_mapped_pav_ids} tmpl {o17_template_id}. Saltando."); continue
                                     temp_pav_ptav_map_o17 = { ptav['product_attribute_value_id'][0]: ptav['id'] for ptav in o17_ptavs_for_combo_data if ptav.get('product_attribute_value_id') and isinstance(ptav['product_attribute_value_id'],(list, tuple)) and len(ptav['product_attribute_value_id']) > 0 and isinstance(ptav['product_attribute_value_id'][0], int) and ptav.get('id') } if o17_ptavs_for_combo_data else {}
                                     o17_target_ptav_ids = [temp_pav_ptav_map_o17.get(pav_id_o17) for pav_id_o17 in o17_mapped_pav_ids if pav_id_o17 in temp_pav_ptav_map_o17]
                                     if len(o17_target_ptav_ids) != len(o17_mapped_pav_ids): logging.error(f"    No PTAVs o17 para TODOS PAVs ({len(o17_target_ptav_ids)}/{len(o17_mapped_pav_ids)}) var {o16_variant_id}. Saltando."); continue
                                     variant_key_ptavs = tuple(sorted(o17_target_ptav_ids))
                                     logging.debug(f"    PTAVs destino o17: {o17_target_ptav_ids}. Clave: {variant_key_ptavs}")
                                else: logging.error(f"    Var o16 {o16_variant_id} PTAVs {o16_ptav_ids_o16} pero no PAVs mapeados o17. Saltando."); continue
                             else: # Simple product
                                 if not o16_template.get('attribute_line_ids'): variant_key_ptavs = (); o17_target_ptav_ids = []; logging.debug(f"    Var o16 {o16_variant_id} simple. Clave PTAV: {variant_key_ptavs}")
                                 else: logging.warning(f"    Var o16 {o16_variant_id} no PTAVs pero tmpl sí attrs. Saltando."); continue

                             # --- Inicio - Procesamiento Variante Individual ---
                             o17_variant_match = o17_variants_map_by_ptavs.get(variant_key_ptavs)
                             o16_variant_summary = {'id': o16_variant_id, 'code': o16_variant_code or '', 'barcode': o16_variant_barcode or '', 'legacy': o16_variant_legacy or ''}
                             variant_sync_result = {'o16': o16_variant_summary, 'o17_id': None, 'status': 'pending', 'details_o17': None}

                             if o17_variant_match: # --- Variante Encontrada ---
                                 o17_variant_id_match = o17_variant_match['id']; processed_o17_variant_ids.add(o17_variant_id_match); variant_sync_result['o17_id'] = o17_variant_id_match; logging.info(f"    Var o17 encontrada (ID: {o17_variant_id_match}) clave {variant_key_ptavs}.")
                                 current_o17_code = o17_variant_match.get('default_code', False); current_o17_legacy = o17_variant_match.get('legacy_default_code', False); current_o17_barcode = o17_variant_match.get('barcode', False); current_o17_active = o17_variant_match.get('active', True); current_o17_image = o17_variant_match.get('image_1920')
                                 needs_update = False; update_vals = {}
                                 # --- Comparaciones ---
                                 code1 = (current_o17_code or '').strip()
                                 code2 = (o16_variant_code or '').strip()
                                 if code1 != code2:
                                     needs_update = True; update_vals['default_code'] = o16_variant_code or False; logging.info(f"      -> Var Default Code difiere.")
                                 legacy1 = (current_o17_legacy or '').strip()
                                 legacy2 = (o16_variant_legacy or '').strip()
                                 if legacy1 != legacy2:
                                     needs_update = True; update_vals['legacy_default_code'] = o16_variant_legacy or False; logging.info(f"      -> Var Legacy Code difiere.")
                                 barcode1 = (current_o17_barcode or '').strip()
                                 barcode2 = (o16_variant_barcode or '').strip()
                                 if barcode1 != barcode2:
                                     needs_update = True; update_vals['barcode'] = o16_variant_barcode or False; logging.info(f"      -> Var Barcode difiere.")
                                 if current_o17_active != o16_variant_is_active:
                                     needs_update = True; update_vals['active'] = o16_variant_is_active; logging.info(f"      -> Var Active difiere.")
                                 if o16_variant_image and o16_variant_image != current_o17_image:
                                     needs_update = True; update_vals['image_1920'] = o16_variant_image; logging.info(f"      -> Var Imagen difiere o presente en O16.")
                                 elif o16_variant_image:
                                     logging.debug(f"      -> Var Imagen idéntica.")
                                 # --- Fin Comparaciones ---
                                 # Actualizar
                                 if needs_update:
                                     counters['o17_variants_updated'] += 1; variant_sync_result['status'] = 'updated'
                                     logging.info(f"    Actualizando var o17 ID: {o17_variant_id_match} con: {list(update_vals.keys())}")
                                     write_ok = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.product', 'write', [[o17_variant_id_match], update_vals])
                                     if write_ok is True: logging.info(f"      -> Update var ID {o17_variant_id_match} OK.")
                                     elif write_ok is None and 'barcode' in update_vals: # Manejo barcode dup
                                         variant_sync_result['status'] = 'update_failed (barcode?)'
                                         logging.warning(f"      -> Fallo update var ID {o17_variant_id_match}, posible barcode dup. Reintentando sin barcode..."); update_vals_no_barcode = update_vals.copy(); del update_vals_no_barcode['barcode']
                                         if update_vals_no_barcode:
                                             write_ok_no_barcode = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.product', 'write', [[o17_variant_id_match], update_vals_no_barcode])
                                             if write_ok_no_barcode is True: logging.info(f"        -> Update var ID {o17_variant_id_match} (sin barcode) OK."); variant_sync_result['status'] = 'updated (no barcode)'
                                             else: logging.error(f"        -> Fallo update var ID {o17_variant_id_match} sin barcode. Result: {write_ok_no_barcode}")
                                         else: logging.info("        -> No otros campos quitar barcode.")
                                     elif write_ok is False: variant_sync_result['status'] = 'update_failed (logic)'; logging.error(f"      -> Fallo lógico update var ID {o17_variant_id_match}. Result False.")
                                     elif write_ok is None: variant_sync_result['status'] = 'update_failed (rpc)'; logging.error(f"      -> Fallo RPC/inesperado general update var ID {o17_variant_id_match}.")
                                     else: variant_sync_result['status'] = 'update_failed (unknown)'; logging.warning(f"      -> Result inesperado ({write_ok}) update var ID {o17_variant_id_match}.")
                                 else: variant_sync_result['status'] = 'found_unchanged'; logging.info(f"    Var o17 ID: {o17_variant_id_match} ya sincronizada.")
                                 # Guardar detalles FINALES O17
                                 variant_sync_result['details_o17'] = {'code': update_vals.get('default_code', current_o17_code), 'barcode': update_vals.get('barcode', current_o17_barcode), 'legacy': update_vals.get('legacy_default_code', current_o17_legacy)}
                                 template_summary['o17_sync']['variants_synced'].append(variant_sync_result)

                             else: # --- Variante No Encontrada ---
                                 counters['o17_variants_created'] += 1; variant_sync_result['status'] = 'created'
                                 logging.info(f"    Var o17 clave {variant_key_ptavs} no encontrada. Creando...")
                                 create_vals = { 'product_tmpl_id': o17_template_id, 'default_code': o16_variant_code or False, 'legacy_default_code': o16_variant_legacy or False, 'barcode': o16_variant_barcode or False, 'product_template_attribute_value_ids': [(6, 0, sorted(o17_target_ptav_ids))], 'type': 'product', 'active': o16_variant_is_active, 'image_1920': o16_variant_image or False }
                                 logging.debug(f"    Valores crear var: name, code, legacy, barcode, ptavs, type, active, image={bool(o16_variant_image)}")
                                 new_variant_id = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.product', 'create', [create_vals])
                                 if new_variant_id and isinstance(new_variant_id, int):
                                     logging.info(f"    ✔ Var creada o17 ID: {new_variant_id} (Clave: {variant_key_ptavs})"); processed_o17_variant_ids.add(new_variant_id)
                                     variant_sync_result['o17_id'] = new_variant_id
                                     variant_sync_result['details_o17'] = {'code': create_vals['default_code'], 'barcode': create_vals['barcode'], 'legacy': create_vals['legacy_default_code']}
                                 elif new_variant_id is None and 'barcode' in create_vals and create_vals['barcode']: # Manejo barcode dup
                                      variant_sync_result['status'] = 'create_failed (barcode?)'
                                      logging.warning(f"    -> Fallo create var clave {variant_key_ptavs}, posible barcode dup. Reintentando sin barcode..."); create_vals_no_barcode = create_vals.copy(); create_vals_no_barcode['barcode'] = False
                                      new_variant_id_no_barcode = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.product', 'create', [create_vals_no_barcode])
                                      if new_variant_id_no_barcode and isinstance(new_variant_id_no_barcode, int):
                                           logging.info(f"      ✔ Var creada o17 ID: {new_variant_id_no_barcode} (Clave: {variant_key_ptavs}, SIN BARCODE)"); processed_o17_variant_ids.add(new_variant_id_no_barcode); variant_sync_result['status'] = 'created (no barcode)'; variant_sync_result['o17_id'] = new_variant_id_no_barcode
                                           variant_sync_result['details_o17'] = {'code': create_vals_no_barcode['default_code'], 'barcode': False, 'legacy': create_vals_no_barcode['legacy_default_code']}
                                      else: logging.error(f"      -> Fallo create var clave {variant_key_ptavs} sin barcode. Result: {new_variant_id_no_barcode}")
                                 elif new_variant_id is False: variant_sync_result['status'] = 'create_failed (logic)'; logging.error(f"    -> Fallo lógico create var clave {variant_key_ptavs}. Result False.")
                                 elif new_variant_id is None: variant_sync_result['status'] = 'create_failed (rpc)'; logging.error(f"    -> Fallo RPC/inesperado general create var clave {variant_key_ptavs}.")
                                 else: variant_sync_result['status'] = 'create_failed (unknown)'; logging.warning(f"    -> Result inesperado ({new_variant_id}) create var clave {variant_key_ptavs}.")
                                 template_summary['o17_sync']['variants_synced'].append(variant_sync_result) # <-- Añadir al nuevo resumen de sync

                        # Limpieza Variantes Obsoletas
                        o17_variant_ids_to_clean = o17_variant_ids_to_clean_potential - processed_o17_variant_ids
                        if o17_variant_ids_to_clean:
                             o17_variants_to_clean_details = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.product', 'read', list(o17_variant_ids_to_clean), {'fields': ['id', 'active', 'default_code', 'barcode']})
                             if o17_variants_to_clean_details is None: logging.error(f"  Fallo RPC leer detalles vars obsoletas {o17_variant_ids_to_clean}.")
                             elif o17_variants_to_clean_details:
                                  o17_ids_to_archive = []
                                  template_summary['o17_sync']['variants_archived'] = [] # Inicializar lista de archivadas
                                  for v_clean in o17_variants_to_clean_details:
                                       if v_clean.get('active', True): o17_ids_to_archive.append(v_clean['id'])
                                       template_summary['o17_sync']['variants_archived'].append({'id': v_clean['id'], 'code': v_clean.get('default_code'), 'barcode': v_clean.get('barcode')})
                                  if o17_ids_to_archive:
                                       counters['o17_variants_archived'] += len(o17_ids_to_archive)
                                       logging.info(f"  Archivando {len(o17_ids_to_archive)} vars obsoletas activas o17 ({o17_ids_to_archive})...")
                                       archive_success = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.product', 'write', [o17_ids_to_archive, {'active': False}])
                                       if archive_success is True: logging.info("    -> Archivado vars obsoletas OK.")
                                       elif archive_success is False: logging.error("    -> Fallo lógico archivar vars obsoletas. Result False.")
                                       elif archive_success is None: logging.error("    -> Fallo RPC/inesperado archivar vars obsoletas.")
                                       else: logging.warning(f"    -> Result inesperado ({archive_success}) archivar vars obsoletas.")
                                  else: logging.info("  No vars obsoletas activas que archivar.")
                             else: logging.warning(f"  No detalles para vars limpieza {o17_variant_ids_to_clean}.")
                        else: logging.info("  No vars obsoletas en o17 para este tmpl.")
            # --- Fin sección Variantes ---
            else: # Si map_success fue False para attrs
                 template_summary['processed_ok'] = False
                 # if template_summary['processed_ok']: counters['templates_failed'] += 1 # Evitar doble conteo
                 logging.error(f"Sync attrs falló o incompleto. Saltando sync variantes.")
            # --- Fin sección Variantes (Condicional) ---


        # --- Final del bloque TRY para este Template ---
        except Exception as template_error:
            logging.error(f"Error CRÍTICO procesando template '{template_default_code_to_sync}': {template_error}", exc_info=True)
            # script_status se marcará en el finally global
            template_summary['processed_ok'] = False
            # Solo contar si no se contó antes por 'not found'
            if template_summary['o16_data']['id']: counters['templates_failed'] += 1


        # --- ESCRITURA FINAL GARANTIZADA ---
        if o17_template_id and o17_template_id in initial_o17_default_codes:
            code_to_force = initial_o17_default_codes[o17_template_id]
            logging.info(f"  [FINAL WRITE CHECK] Verificando code final o17 ID {o17_template_id}. Inicial: '{code_to_force}'")
            final_read_data = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template', 'read', [o17_template_id], {'fields': ['default_code', 'name']})
            if final_read_data and isinstance(final_read_data, list) and final_read_data:
                final_data_dict = final_read_data[0]; final_current_code = final_data_dict.get('default_code', False)
                template_summary['o17_sync']['name_final'] = final_data_dict.get('name') # Actualizar nombre final
                template_summary['o17_sync']['default_code_final'] = final_current_code # Guardar código final O17
                logging.info(f"  [FINAL WRITE CHECK] Code actual ANTES escritura final: '{final_current_code}'")
                if final_current_code != code_to_force:
                    logging.warning(f"  [FINAL WRITE] Code actual ('{final_current_code}') != inicial ('{code_to_force}'). Forzando escritura.")
                    final_write_ok = execute_odoo_kw(models_o17, O17_DB, uid_o17, O17_PASS, 'product.template', 'write', [[o17_template_id], {'default_code': code_to_force}])
                    if final_write_ok is True:
                        logging.info(f"    -> Escritura final code '{code_to_force}' OK.")
                        template_summary['o17_sync']['default_code_final'] = code_to_force # Actualizar con el forzado
                    else: logging.error(f"    -> FALLO escritura final code '{code_to_force}'. Result: {final_write_ok}")
                else: logging.info(f"  [FINAL WRITE CHECK] Code actual ('{final_current_code}') ya coincide. No escritura final.")
            else: logging.error(f"  [FINAL WRITE CHECK] No leer code actual final o17 ID {o17_template_id}. No forzar escritura.")
        elif o17_template_id: # Si tenemos ID pero no código inicial (error muy temprano?)
             template_summary['o17_sync']['default_code_final'] = 'N/A (Error lectura inicial?)'
             logging.warning(f"  [FINAL WRITE CHECK] No code inicial guardado o17 ID {o17_template_id}. Saltando escritura final.")


        # --- Guardar resumen de este template ---
        summary_data.append(template_summary)

        logging.info(f"\n--- Finalizado procesamiento de Template con default_code: {template_default_code_to_sync} ---")

    # --- FIN Bucle Principal de Templates ---

    # Devolver datos para el resumen final
    return summary_data, counters


# --- Ejecución del Script y Manejo Final ---
if __name__ == "__main__":
    start_time_global = datetime.datetime.now(); logging.info(f"Script execution started at: {start_time_global.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Logging configured. Log file (provisional): {provisional_log_filename}")

    script_status = "ok"; final_summary_data = []; final_counters = {}; exit_code = 0

    try:
        final_summary_data, final_counters = sync_products()
        if final_summary_data is None: script_status = "ERROR";
        if not final_counters: final_counters = {}
        if final_counters and final_counters.get('templates_failed', 0) > 0: script_status = "ERROR"
    except Exception as main_error:
        script_status = "ERROR"; logging.error(f"Error CRÍTICO no controlado: {main_error}", exc_info=True)
        if 'final_counters' not in locals() or not final_counters: final_counters = {}

    finally:
        end_time_global = datetime.datetime.now(); logging.info(f"Script execution finished at: {end_time_global.strftime('%Y-%m-%d %H:%M:%S')}")
        execution_duration = end_time_global - start_time_global; total_seconds = execution_duration.total_seconds(); hours, remainder = divmod(total_seconds, 3600); minutes, seconds = divmod(remainder, 60); duration_str = f"{int(hours):02}:{int(minutes):02}:{seconds:06.3f}"
        logging.info(f"Total execution time: {duration_str} (Total seconds: {total_seconds:.3f})")

        # --- Imprimir Resumen Final Detallado ---
        # (imprime summary_data y final_counters)
        print("\n" + "="*80); print(" Resumen Final de Sincronización Detallado ".center(80, '=')); print("="*80)
        if not final_summary_data: print("No se procesó ningún template o hubo un error muy temprano.")
        else:
            for item in final_summary_data:
                status_icon = "✔" if item.get('processed_ok', False) else "✖"; o16_info = item.get('o16_data', {}); o17_info = item.get('o17_sync', {})
                print(f"\n{status_icon} Template Code: {item.get('code', 'N/A')}")
                print(f"  O16 -> ID: {o16_info.get('id', 'N/A')}, Nombre: '{o16_info.get('name', 'N/A')}'")
                if o16_info.get('attributes'): print("    Atributos O16:"); [print(f"      - {attr_line}") for attr_line in o16_info['attributes']]
                else: print("    Atributos O16: Ninguno")
                print(f"  O17 -> ID: {o17_info.get('id', 'N/A')}, Nombre Final: '{o17_info.get('name_final', 'N/A')}', Code Final: '{o17_info.get('default_code_final', 'N/A')}'")
                if not item.get('processed_ok', False): print("    ESTADO TEMPLATE: Fallido (ver logs)")
                print("  --- Variantes O16 y Estado Sincronización O17 ---")
                variants_synced = o17_info.get('variants_synced', []); variants_archived = o17_info.get('variants_archived', [])
                if not variants_synced and not o16_info.get('variants'): print("    No había variantes en O16.")
                elif not variants_synced and o16_info.get('variants'): print("    Se encontraron variantes en O16 pero falló su sincronización (ver logs).")
                else:
                    print(f"  Variantes Sincronizadas ({len(variants_synced)}):")
                    for v_sync in variants_synced:
                        o16_v = v_sync.get('o16', {}); o17_v = v_sync.get('details_o17', {}) or {}; o17_status = v_sync.get('status', 'N/A').upper()
                        print(f"    - O16 (ID:{o16_v.get('id', 'N/A')} B:'{o16_v.get('barcode', '')}' C:'{o16_v.get('code', '')}' L:'{o16_v.get('legacy', '')}')")
                        print(f"      O17 (ID:{v_sync.get('o17_id', 'N/A')}): Estado: {o17_status}")
                        if 'FAILED' not in o17_status and o17_v: print(f"        -> B:'{o17_v.get('barcode', '')}' C:'{o17_v.get('code', '')}' L:'{o17_v.get('legacy', '')}'")
                        elif 'FAILED' in o17_status: print(f"        -> Falló la operación O17 (ver logs)")
                if variants_archived:
                    print(f"  Variantes Archivadas en O17 ({len(variants_archived)}):")
                    for v_arch in variants_archived: print(f"    - ID:{v_arch.get('id', 'N/A')}, Code:'{v_arch.get('code', '')}', Barcode:'{v_arch.get('barcode', '')}'")
                print("-" * 40)
        if final_counters:
            print("\n" + "="*80); print(" Estadísticas Generales ".center(80, '=')); print("="*80)
            print(f"- Templates Analizados: {final_counters.get('templates_analyzed', 0)}")
            print(f"- Templates con Fallos: {final_counters.get('templates_failed', 0)}")
            print(f"- Total Variantes Encontradas en O16: {final_counters.get('o16_variants_found', 0)}")
            print(f"- Total Variantes Existentes en O17 (antes): {final_counters.get('o17_variants_existing', 0)}")
            print(f"- Total Variantes Creadas en O17: {final_counters.get('o17_variants_created', 0)}")
            print(f"- Total Variantes Actualizadas en O17: {final_counters.get('o17_variants_updated', 0)}")
            print(f"- Total Variantes Archivadas en O17: {final_counters.get('o17_variants_archived', 0)}")
            print("-" * 80); print(f"Hora de Inicio:      {start_time_global.strftime('%Y-%m-%d %H:%M:%S')}"); print(f"Hora de Fin:         {end_time_global.strftime('%Y-%m-%d %H:%M:%S')}"); print(f"Tiempo de Ejecución: {duration_str}"); print("="*80)
        else: print("\nNo se generaron estadísticas (probablemente fallo temprano).")

        # Renombrar Log
        logging.info("Cerrando y renombrando archivo de log..."); logging.shutdown()
        final_log_filename = ""; exit_code = 0
        try:
            if script_status == "ERROR": final_log_filename = os.path.join(logs_dir, f"{timestamp}_ERROR.log"); print(f"Script finalizado con ERRORES. Ver log: {final_log_filename}"); exit_code = 1
            else: final_log_filename = os.path.join(logs_dir, f"{timestamp}.log"); print(f"Script finalizado OK. Ver log: {final_log_filename}"); exit_code = 0
            if os.path.exists(provisional_log_filename) and final_log_filename: os.rename(provisional_log_filename, final_log_filename)
            elif not os.path.exists(provisional_log_filename): print(f"WARN: No se encontró log temporal {provisional_log_filename} para renombrar.", file=sys.stderr)
        except OSError as rename_error: print(f"ERROR: No renombrar log de '{provisional_log_filename}' a '{final_log_filename}': {rename_error}", file=sys.stderr); exit_code = 1
        except Exception as final_error: print(f"ERROR: Error inesperado finalización/renombrado log: {final_error}", file=sys.stderr); exit_code = 1
        sys.exit(exit_code)