# -*- coding: utf-8 -*-
import xmlrpc.client
import logging
import time

# Las funciones aquí dependen de que el logging ya esté configurado
# por el script principal (main_sync.py) que las importa y usa.

def connect_odoo(url, db, user, password):
    """Intenta conectar y autenticarse a una instancia Odoo."""
    try:
        logging.info(f"Intentando conectar a {url}, DB: {db}")
        common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
        uid = common.authenticate(db, user, password, {})
        if not uid:
            logging.error(f"Auth fallo para {user} en {url} DB {db}")
            return None, None
        models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
        logging.info(f"Conexión OK a {url}, DB: {db} (UID: {uid})")
        return models, uid
    except xmlrpc.client.Fault as e:
        logging.error(f"RPC Error conexión/auth a {url} DB {db}: Code {e.faultCode}, Str: {e.faultString}")
        return None, None
    except Exception as e:
        logging.error(f"Error inesperado conexión a {url} DB {db}: {type(e).__name__} - {e}", exc_info=True)
        return None, None

# --- execute_odoo_kw (Versión con manejo de logs y errores) ---
def execute_odoo_kw(models, db_name, uid, password, model, method, args=[], kwargs={}):
    """
    Ejecuta un método Odoo via XML-RPC execute_kw con logging y manejo de errores.
    """
    # Definir longitud máxima para logs una sola vez
    max_log_len = 500
    # Copiar args y kwargs para evitar modificar originales
    rpc_args = list(args)
    rpc_kwargs = dict(kwargs)

    try:
        # --- Ajuste específico para 'read' con 'fields' ---
        # Odoo exige fields como segundo argumento posicional para 'read'
        if method == 'read' and 'fields' in rpc_kwargs:
            fields_list = rpc_kwargs.pop('fields')
            # Verificar si args es una lista de IDs
            if isinstance(rpc_args, list) and rpc_args and all(isinstance(x, int) for x in rpc_args):
                 ids_list_to_read = rpc_args
                 rpc_args = [ids_list_to_read, fields_list] # Modificar args a [IDs, fields]
            # Verificar si args es [lista de IDs]
            elif isinstance(rpc_args, list) and len(rpc_args) == 1 and isinstance(rpc_args[0], list) and all(isinstance(x, int) for x in rpc_args[0]):
                 ids_list_to_read = rpc_args[0]
                 rpc_args = [ids_list_to_read, fields_list] # Modificar args a [IDs, fields]
            # Caso de lectura de IDs vacíos []
            elif not rpc_args or (isinstance(rpc_args, list) and len(rpc_args) == 1 and isinstance(rpc_args[0], list) and not rpc_args[0]):
                 ids_list_to_read = []
                 rpc_args = [ [], fields_list] # Modificar args a [[], fields]
            else:
                # Si no coincide con los formatos esperados de read(IDs, fields), restaurar 'fields' a kwargs
                logging.warning(f"Formato args inesperado para 'read' con fields. Args: {args}. Restaurando 'fields' a kwargs.");
                rpc_kwargs['fields'] = fields_list


        # --- Logging de argumentos ---
        args_log_str = repr(rpc_args)
        if len(args_log_str) > max_log_len:
            args_log_str = args_log_str[:max_log_len] + "...(truncated)"

        kwargs_log_str = repr(rpc_kwargs)
        if len(kwargs_log_str) > max_log_len:
            kwargs_log_str = kwargs_log_str[:max_log_len] + "...(truncated)"

        log_level = logging.DEBUG if method in ['read', 'search_read'] else logging.INFO
        logging.log(log_level, f"Executing RPC: DB='{db_name}', Mdl='{model}', Meth='{method}'")
        logging.debug(f"RPC Args: {args_log_str}, Kwargs: {kwargs_log_str}")
        # --- Fin Logging Args ---

        start_time = time.time()
        result = models.execute_kw(db_name, uid, password, model, method, rpc_args, rpc_kwargs)
        duration = time.time() - start_time

        # --- Logging del resultado ---
        result_log_str = repr(result)
        # Omitir datos grandes (como imágenes base64) del log
        if isinstance(result, list) and result and isinstance(result[0], dict) and 'image_1920' in result[0]:
             result_log_str = f"List of {len(result)} dicts (image data omitted)"
        elif isinstance(result, dict) and 'image_1920' in result:
             result_log_str = f"Dict (image data omitted)"
        elif isinstance(result, str) and len(result) > max_log_len:
             # Heurística simple para detectar base64 (puede no ser perfecta)
             if result.startswith('iVBOR') or result.startswith('/9j/') or (len(result) > 1000 and '=' in result[-4:]):
                 result_log_str = f"Base64 string length {len(result)} (data omitted)"

        # Truncar si el resultado sigue siendo largo
        if len(result_log_str) > max_log_len:
            result_log_str = result_log_str[:max_log_len] + "...(truncated)"

        logging.debug(f"RPC Result ({duration:.4f}s) {model}.{method}: {result_log_str}")
        # --- Fin Logging Resultado ---

        return result

    except xmlrpc.client.Fault as e:
        # Loguear error RPC
        args_log_str = repr(rpc_args) # Recalcular por si se modificó
        if len(args_log_str) > max_log_len:
             args_log_str = args_log_str[:max_log_len] + "...(truncated)"
        logging.error(f"RPC Fault: DB='{db_name}', Mdl='{model}', Meth='{method}'. Code: {e.faultCode}. Str: {e.faultString}. Args: {args_log_str}", exc_info=False)
        return None

    except Exception as e:
        # Loguear error inesperado
        args_log_str = repr(rpc_args) # Recalcular
        if len(args_log_str) > max_log_len:
             args_log_str = args_log_str[:max_log_len] + "...(truncated)"
        logging.error(f"RPC Error: {method} on {model} in {db_name}: {type(e).__name__} - {e}. Args: {args_log_str}", exc_info=True)
        return None


def find_record(models, db_name, uid, password, model, domain, fields=['id'], limit=None):
    """Busca registros Odoo (incluye inactivos). Utiliza search_read."""
    kwargs = {'fields': fields, 'context': {'active_test': False}};
    if limit is not None:
        kwargs['limit'] = limit
    logging.debug(f"Searching {model} domain {domain} (limit={limit}, active_test=False)...")
    result = execute_odoo_kw(models, db_name, uid, password, model, 'search_read', [domain], kwargs)
    # execute_odoo_kw ya loguea errores, solo retornar el resultado
    return result

def get_or_create_attribute(models_o17, db_name_o17, uid_o17, o17_pass, attr_name_o16):
    """ Busca/Crea atributo o17 (case-insensitive). """
    normalized_name = " ".join((attr_name_o16 or '').split()).strip()
    if not normalized_name:
        logging.error(f"Attr name o16 vacío o inválido: '{attr_name_o16}'")
        return None

    logging.debug(f"Get/Create Attr o17: '{normalized_name}'")

    # Buscar existente
    domain = [('name', '=ilike', normalized_name)] # Búsqueda insensible a mayúsculas/minúsculas y espacios
    attribute_data = find_record(models_o17, db_name_o17, uid_o17, o17_pass, 'product.attribute', domain, fields=['id', 'name'], limit=1)

    if attribute_data is None:
        logging.error(f"Fallo RPC buscar attr '{normalized_name}' o17.")
        return None

    if attribute_data:
        attr_id = attribute_data[0]['id']
        logging.debug(f"Attr '{normalized_name}' encontrado o17 ID: {attr_id}")
        return attr_id
    else:
        # Crear si no existe
        logging.info(f"Attr '{normalized_name}' no encontrado o17. Creando...")
        create_vals = {
            'name': normalized_name,
            'create_variant': 'no_variant' # 'no_variant' suele ser el valor seguro por defecto si no se sabe cómo afecta la variante
        }
        new_attr_id = execute_odoo_kw(models_o17, db_name_o17, uid_o17, o17_pass, 'product.attribute', 'create', [create_vals])

        if new_attr_id and isinstance(new_attr_id, int):
            logging.info(f"Attr '{normalized_name}' creado o17 ID: {new_attr_id}")
            return new_attr_id
        else:
            logging.error(f"No se pudo crear attr '{normalized_name}' o17. Resultado RPC: {new_attr_id}.")
            return None


def get_or_create_attribute_value(models_o17, db_name_o17, uid_o17, o17_pass, value_name_o16, attribute_id_o17):
    """ Busca/Crea valor attr o17 (case-insensitive) asociado a un atributo padre. """
    if not isinstance(attribute_id_o17, int) or attribute_id_o17 <= 0:
        logging.error(f"ID de atributo padre inválido ({attribute_id_o17}) para valor '{value_name_o16}' en Odoo 17.")
        return None

    normalized_name = " ".join((value_name_o16 or '').split()).strip()
    if not normalized_name:
        logging.error(f"Nombre de valor Odoo 16 vacío o inválido para AttrID {attribute_id_o17}: '{value_name_o16}'")
        return None

    logging.debug(f"Get/Create Val o17 AttrID {attribute_id_o17}: '{normalized_name}'")

    # Buscar existente
    domain = [('attribute_id', '=', attribute_id_o17), ('name', '=ilike', normalized_name)] # Búsqueda insensible a mayúsculas/minúsculas y espacios
    value_data = find_record(models_o17, db_name_o17, uid_o17, o17_pass, 'product.attribute.value', domain, fields=['id', 'name'], limit=1)

    if value_data is None:
        logging.error(f"Fallo RPC buscar valor '{normalized_name}' (AttrID {attribute_id_o17}) o17.")
        return None

    if value_data:
        value_id = value_data[0]['id']
        logging.debug(f"Valor '{normalized_name}' encontrado o17 (AttrID {attribute_id_o17}) ID: {value_id}")
        return value_id
    else:
        # Crear si no existe
        logging.info(f"Valor '{normalized_name}' (AttrID {attribute_id_o17}) no encontrado o17. Creando...")
        create_vals = {
            'name': normalized_name,
            'attribute_id': attribute_id_o17
        }
        new_value_id = execute_odoo_kw(models_o17, db_name_o17, uid_o17, o17_pass, 'product.attribute.value', 'create', [create_vals])

        if new_value_id and isinstance(new_value_id, int):
            logging.info(f"Valor '{normalized_name}' creado o17 (AttrID {attribute_id_o17}) ID: {new_value_id}")
            return new_value_id
        else:
            logging.error(f"No se pudo crear valor '{normalized_name}' (AttrID {attribute_id_o17}) o17. Resultado RPC: {new_value_id}.");
            return None

def find_or_create_template(models, db, uid, password, search_domain, create_vals, read_fields):
    """
    Busca un registro en Odoo usando el dominio proporcionado.
    Si no se encuentra, crea un nuevo registro con los valores proporcionados.
    Finalmente, lee y devuelve los datos del registro (existente o creado).

    :param models: Objeto 'models' de la conexión Odoo.
    :param db: Nombre de la base de datos Odoo.
    :param uid: ID de usuario de Odoo.
    :param password: Contraseña del usuario de Odoo.
    :param search_domain: Lista de tuplas que define el dominio de búsqueda.
    :param create_vals: Diccionario de valores para crear el registro si no se encuentra.
    :param read_fields: Lista de campos a leer del registro.
    :return: Diccionario con los datos del registro encontrado o creado, o None si hay un error RPC.
    """
    try:
        record_ids = models.execute_kw(db, uid, password, 'product.template', 'search', [search_domain], {'limit': 1})
        if record_ids:
            _logger = logging.getLogger(__name__) # Asegúrate de que el logger esté accesible aquí
            _logger.debug(f"Template encontrado con IDs: {record_ids}")
            read_result = models.execute_kw(db, uid, password, 'product.template', 'read', [record_ids[0]], {'fields': read_fields})
            return read_result[0] if read_result else None
        else:
            _logger = logging.getLogger(__name__) # Asegúrate de que el logger esté accesible aquí
            _logger.debug(f"Template no encontrado. Creando con valores: {create_vals}")
            new_record_id = models.execute_kw(db, uid, password, 'product.template', 'create', [create_vals])
            if new_record_id:
                _logger.info(f"Template creado con ID: {new_record_id}")
                read_result = models.execute_kw(db, uid, password, 'product.template', 'read', [new_record_id], {'fields': read_fields})
                return read_result[0] if read_result else None
            else:
                _logger.error("Fallo al crear el template.")
                return None
    except Exception as e:
        _logger = logging.getLogger(__name__) # Asegúrate de que el logger esté accesible aquí
        _logger.error(f"Error RPC en find_or_create_template: {e}", exc_info=True)
        return None