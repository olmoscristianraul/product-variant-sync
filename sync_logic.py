# -*- coding: utf-8 -*-
import logging
import os
import sys
import time
import math
import sqlite3
import datetime # Importar datetime (contiene date y timedelta)
# No importar timedelta directamente, es parte de datetime
# import timedelta # Eliminar esta línea si existe

# Importar funciones del módulo RPC y del módulo DB
from odoo_xmlrpc import (
    execute_odoo_kw, find_record, # find_record también usa execute_odoo_kw
    get_or_create_attribute, get_or_create_attribute_value, # Importar funciones para atributos
    find_or_create_template # Importar find_or_create_template
)
from sync_db import ( # Importar la función para actualizar el estado en la DB
    update_item_status,
    get_db_path, DEFAULT_DB_NAME # Necesario para pasar la ruta de la DB
)

_logger = logging.getLogger(__name__)

# La variable global script_status ya NO se gestiona aquí.
# La funcion load_codes_to_process ya NO está en este archivo.


# ---> CAMBIO: Modificar la firma de la función para recibir 'config'
def get_changed_item_codes(o16_conn, config, from_date):
    """
    Consulta Odoo 16 para encontrar product.template cuyos templates o variantes
    han sido modificados (write_date) desde from_date. Devuelve una lista
    de default_code únicos de los templates afectados.
    """
    _logger.info(f"Consultando Odoo 16 por items modificados desde: {from_date.isoformat()}")

    models_o16, uid_o16 = o16_conn
    # Acceder a la DB name y password desde el diccionario config
    o16_db = config.get('O16_DB') # Acceder a la DB name desde config
    o16_pass = config.get('O16_PASS') # Acceder a la password desde config

    # ---> CAMBIO: Añadida verificación de conexión y config
    if not models_o16 or not o16_db or not uid_o16 or not o16_pass:
        _logger.error("Faltan datos de conexión Odoo 16 para consultar cambios.")
        return None # Indicar fallo si los datos de conexión son inválidos


    try:
        # Formato de fecha ISO 8601 que Odoo espera para write_date en RPC
        from_date_str = from_date.isoformat()

        # 1. Consultar Templates modificados
        domain_tmpl = [('write_date', '>=', from_date_str)]
        fields_tmpl = ['default_code', 'write_date'] # Añadimos write_date para referencia/depuración
        _logger.debug(f"Consultando product.template en O16 con domain: {domain_tmpl}")
        changed_templates_data = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.template', 'search_read', [domain_tmpl], {'fields': fields_tmpl})

        if changed_templates_data is None:
            _logger.error("Fallo RPC al consultar product.template por write_date en Odoo 16.")
            return None # Indicar fallo

        changed_codes = set()
        for tmpl in changed_templates_data:
            code = tmpl.get('default_code')
            if isinstance(code, str) and code.strip():
                changed_codes.add(code.strip())
            elif code is not False and code is not None:
                _logger.warning(f"product.template O16 ID {tmpl.get('id')} tiene un default_code no válido o vacío ('{code}'). Ignorado.")


        # 2. Consultar Variantes modificadas
        domain_variants = [('write_date', '>=', from_date_str)]
        # Necesitamos el ID del template asociado para obtener su default_code
        fields_variants = ['product_tmpl_id', 'write_date']
        _logger.debug(f"Consultando product.product en O16 con domain: {domain_variants}")
        changed_variants_data = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.product', 'search_read', [domain_variants], {'fields': fields_variants})

        if changed_variants_data is None:
            _logger.error("Fallo RPC al consultar product.product por write_date en Odoo 16.")
            # Decidimos si esto es fatal o si continuamos con los códigos de templates directos.
            # Para ser robustos, sigamos con lo que tenemos.
            pass # No es un return None global, solo falló esta parte de la consulta

        if changed_variants_data is not None: # Solo procesar si la consulta de variantes no falló RPC
            # Recopilar IDs únicos de templates a los que pertenecen las variantes modificadas
            changed_variant_tmpl_ids = set()
            for variant in changed_variants_data:
                tmpl_info = variant.get('product_tmpl_id') # Es una tuple [ID, Name] o False
                if tmpl_info and isinstance(tmpl_info, (list, tuple)) and len(tmpl_info) > 0 and isinstance(tmpl_info[0], int):
                    changed_variant_tmpl_ids.add(tmpl_info[0])
                elif tmpl_info is not False and tmpl_info is not None:
                    _logger.warning(f"product.product O16 ID {variant.get('id')} tiene un product_tmpl_id no válido ('{tmpl_info}'). Ignorado.")


            # 3. Obtener los default_codes de los templates identificados por variantes modificadas
            if changed_variant_tmpl_ids:
                _logger.debug(f"Consultando default_code para templates O16 con IDs: {list(changed_variant_tmpl_ids)}")
                domain_variant_tmpls = [('id', 'in', list(changed_variant_tmpl_ids))]
                fields_variant_tmpls = ['default_code']
                variant_tmpls_data = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.template', 'search_read', [domain_variant_tmpls], {'fields': fields_variant_tmpls})

                if variant_tmpls_data is None:
                    _logger.error(f"Fallo RPC al consultar product.template por IDs de variantes cambiadas {list(changed_variant_tmpl_ids)} en Odoo 16.")
                    # Continuamos con los códigos de templates directos si existen
                    pass

                elif variant_tmpls_data:
                    for tmpl in variant_tmpls_data:
                        code = tmpl.get('default_code')
                        if isinstance(code, str) and code.strip():
                            changed_codes.add(code.strip())
                        elif code is not False and code is not None:
                            _logger.warning(f"product.template O16 ID {tmpl.get('id')} (de variante cambiada) tiene un default_code no válido o vacío ('{code}'). Ignorado.")


        # Los códigos en changed_codes ya son únicos por ser un set
        # Convertir a lista ordenada para consistencia
        list_of_changed_codes = sorted(list(changed_codes))

        _logger.info(f"Consulta de items cambiados en Odoo 16 finalizada. Encontrados {len(list_of_changed_codes)} códigos cambiados.")
        return list_of_changed_codes # Devuelve la lista de códigos únicos

    except Exception as e:
        _logger.error(f"Error inesperado en get_changed_item_codes: {e}", exc_info=True)
        return None # Indicar fallo


# --- CAMBIO: Modificar la firma de sync_products para recibir 'sync_fields_mode' ---
def sync_products(o16_conn, o17_conn, config, items_to_process, db_path, sync_fields_mode):
    """
    Función principal que orquesta la sincronización para una lista de items.
    Recibe la lista de items a procesar (obtenida de la DB en main_sync)
    y la ruta de la DB para actualizar el estado de cada item.
    Recibe sync_fields_mode para sincronización selectiva de campos de variante.
    """
    _logger.info(f" Iniciando Lógica de Sincronización para {len(items_to_process)} items ".center(60, '-'))
    _logger.info(f"Modo de sincronización de campos de variante: {sync_fields_mode}")


    models_o16, uid_o16 = o16_conn
    models_o17, uid_o17 = o17_conn
    # Acceder a la configuración pasada como argumento
    o16_pass = config.get('O16_PASS'); o17_pass = config.get('O17_PASS')
    o16_db = config.get('O16_DB'); o17_db = config.get('O17_DB')

    if not items_to_process:
        _logger.warning("No hay items a procesar en la lista proporcionada. Finalizando lógica de sincronización.");
        # Devuelve contadores iniciales si no hay nada que procesar
        return [], {
            'templates_analyzed': 0, 'o16_variants_found': 0, 'o17_variants_existing': 0,
            'o17_variants_created': 0, 'o17_variants_updated': 0, 'o17_variants_archived': 0,
            'templates_failed': 0,
        }


    _logger.info(f"Items a procesar (códigos): {[item['code'] for item in items_to_process]}")
    summary_data = [] # Mantener para el resumen de ESTA ejecución
    counters = {
        'templates_analyzed': 0,
        'o16_variants_found': 0, # Variantes encontradas en O16 para los templates procesados
        'o17_variants_existing': 0, # Variantes existentes en O17 para los templates procesados (antes de sync)
        'o17_variants_created': 0, # Variantes creadas en O17 en ESTA ejecución
        'o17_variants_updated': 0, # Variantes actualizadas en O17 en ESTA ejecución
        'o17_variants_archived': 0, # Variantes archivadas en O17 en ESTA ejecución
        'templates_failed': 0, # Contador de templates que fallaron completamente en ESTA ejecución
    }

    # Este cache es para mapear IDs de atributos/valores de Odoo 16 a Odoo 17
    # Se reinicia para cada EJECUCIÓN del script, no para cada template
    # o17_pav_mapping = {} # <--- MOVIDA FUERA DEL BUCLE DE TEMPLATES
    # initial_o17_default_codes = {} # <--- MOVIDA FUERA DEL BUCLE DE TEMPLATES

    # Estos caches ahora se inicializan ANTES del bucle de templates si no existen ya en el scope global de la función
    # Esto asegura que persisten entre templates dentro de la misma ejecución de sync_products
    # Usar un diccionario global o pasarlos como argumento y manejarlos en main_sync sería otra opción.
    # Como es una función independiente, inicializarlos una vez al principio de la función es la opción simple.
    # Usando un truco para inicializarlos solo una vez en la primera llamada:
    if not hasattr(sync_products, 'o17_pav_mapping'):
        sync_products.o17_pav_mapping = {} # Mapea O16 PAV ID -> O17 PAV ID
    if not hasattr(sync_products, 'initial_o17_default_codes'):
        sync_products.initial_o17_default_codes = {} # Mapea O17 Template ID -> O17 Default Code inicial

    o17_pav_mapping = sync_products.o17_pav_mapping # Usar el cache persistente
    initial_o17_default_codes = sync_products.initial_o17_default_codes # Usar el cache persistente


    for item_db in items_to_process: # Iterar sobre la lista de diccionarios de la DB
        template_default_code_to_sync = item_db['code']
        # Recuperar IDs existentes de la DB si existen (pueden ser None)
        o16_template_id_db = item_db.get('o16_template_id')
        o17_template_id_db = item_db.get('o17_template_id')

        _logger.info(f"\n--- Procesando Template con default_code: {template_default_code_to_sync} ---")
        counters['templates_analyzed'] += 1
        map_success = True # Flag para saber si hubo fallos críticos en mapeo de atributos/valores para este template

        # Asegurarse de que el ID de Odoo 17 inicial para el resumen refleje la DB si existía
        template_summary = {
            'code': template_default_code_to_sync,
            'o16_data': {'id': o16_template_id_db, 'name': None, 'attributes': [], 'variants': []}, # Usar ID de DB si existe
            'o17_sync': {'id': o17_template_id_db, 'name_final': None, 'default_code_final': None, 'status': 'ok', 'variants_synced': [], 'variants_archived': []}, # Usar ID de DB si existe
            'processed_ok': True # Estado general de este template
        }

        # --- Marcar item como 'processing' en la DB ---
        # Intentamos actualizar, si falla (ej: DB bloqueada), solo logueamos, no es fatal para la ejecución en general
        try:
            update_item_status(db_path, template_default_code_to_sync, 'processing')
            _logger.debug(f"Item '{template_default_code_to_sync}' marcado como 'processing' en DB.")
        except Exception as db_update_error:
            _logger.error(f"Error al marcar item '{template_default_code_to_sync}' como 'processing' en DB: {db_update_error}", exc_info=True)
            # No marcamos processed_ok = False aún, el fallo principal puede ocurrir más tarde


        try: # Bloque TRY para el procesamiento de UN template específico
            # 1. Buscar FUENTE O16 (product.template)
            _logger.info(f"Buscando template '{template_default_code_to_sync}' en Odoo 16...")
            domain_o16_template = [('default_code', '=', template_default_code_to_sync)]
            # Añadimos write_date para futura implementación de sync de cambios
            fields_o16_template = ['id', 'name', 'default_code', 'attribute_line_ids', 'product_variant_ids', 'active', 'image_1920', 'write_date']
            o16_template_data_list = find_record(models_o16, o16_db, uid_o16, o16_pass, 'product.template', domain_o16_template, fields_o16_template, limit=1)

            if o16_template_data_list is None:
                raise Exception(f"Fallo RPC al buscar template '{template_default_code_to_sync}' en Odoo 16.") # Error RPC es fatal para este template

            if not o16_template_data_list:
                _logger.warning(f"Template '{template_default_code_to_sync}' no encontrado en Odoo 16. Saltando procesamiento.");
                template_summary['processed_ok'] = False # Marcar como no procesado OK
                counters['templates_failed'] += 1
                # Actualizar estado en DB a 'skipped' o 'failed_not_found'
                try: update_item_status(db_path, template_default_code_to_sync, 'skipped', error_message="Template not found in Odoo 16.")
                except Exception as db_update_error: _logger.error(f"Error DB al marcar item '{template_default_code_to_sync}' como skipped: {db_update_error}")
                summary_data.append(template_summary) # Añadir resumen del template que falló/saltó
                continue # Saltar al siguiente item en la lista

            o16_template = o16_template_data_list[0]
            o16_template_id = o16_template['id']
            template_summary['o16_data']['id'] = o16_template_id # Registrar ID O16 (podría ser None si no se encontró en DB inicial)
            o16_template_code = o16_template.get('default_code')
            o16_template_name = o16_template.get('name')
            o16_is_active = o16_template.get('active', True)
            o16_image_1920 = o16_template.get('image_1920')
            # write_date = o16_template.get('write_date') # Para futura lógica de "changed"

            template_summary['o16_data']['name'] = o16_template_name # Registrar nombre O16

            o16_template_code_cleaned = (o16_template_code or '').strip()
            if not o16_template_code_cleaned and o16_template_code is not False:
                 _logger.warning(f"Code de template FUENTE en Odoo 16 ID {o16_template_id} está vacío ('{o16_template_code}').")

            _logger.info(f"Fuente Odoo 16: ID {o16_template_id}, Name: '{o16_template_name}', Code: '{o16_template_code}', Active: {o16_is_active}, Has Image: {bool(o16_image_1920)}")

            # 2. Buscar o Crear DESTINO O17 (product.template)
            # Usamos el default_code de Odoo 16 como clave de búsqueda/creación en Odoo 17
            # Si el código de Odoo 16 está vacío/False, usamos el de la DB si existía,
            # o generamos uno temporal (aunque esto debería evitarse si O16 es la fuente de la verdad)
            # Mejor, si el código O16 es inválido, marcamos error para este item.
            if not o16_template_code_cleaned:
                 raise Exception(f"Template Odoo 16 ID {o16_template_id} tiene un default_code vacío o inválido ('{o16_template_code}'). No se puede sincronizar sin un código válido.")

            # Intenta usar el ID de O17 de la DB si existe, de lo contrario, busca por código
            o17_template_search_domain = []
            if o17_template_id_db:
                 _logger.info(f"Buscando template destino en Odoo 17 usando ID de DB: {o17_template_id_db}")
                 o17_template_search_domain = [('id', '=', o17_template_id_db)]
            else:
                 _logger.info(f"Buscando o creando template destino en Odoo 17 usando código fuente: '{o16_template_code_cleaned}'")
                 o17_template_search_domain = [('default_code', '=', o16_template_code_cleaned)]
                 # También buscamos por legacy_default_code si default_code no es válido en O17? Decisión de diseño.
                 # Por ahora, solo default_code como identificador primario.


            # Campos necesarios para leer/crear/actualizar el template en Odoo 17
            fields_o17_template = ['id', 'name', 'default_code', 'active', 'image_1920', 'attribute_line_ids']

            # Usamos la función genérica find_or_create_template
            o17_template_result = find_or_create_template(
                 models_o17, o17_db, uid_o17, o17_pass,
                 search_domain=o17_template_search_domain,
                 create_vals={'default_code': o16_template_code_cleaned, 'name': o16_template_name or 'Nombre temporal'}, # Valores mínimos para crear si no existe
                 read_fields=fields_o17_template
            )

            if o17_template_result is None:
                 raise Exception(f"Fallo RPC al buscar o crear template destino en Odoo 17 para código '{o16_template_code_cleaned}'.")

            if not o17_template_result:
                 # Esto solo debería ocurrir si search_domain no encontró nada Y create_vals estaba vacío (no es el caso aquí)
                 # o si create_vals no era válido y la creación falló silenciosamente (execute_odoo_kw devuelve False)
                 raise Exception(f"No se encontró ni se pudo crear el template destino en Odoo 17 para código '{o16_template_code_cleaned}'.")

            o17_template = o17_template_result
            o17_template_id = o17_template['id']

            # Guardar el default_code inicial de Odoo 17 ANTES de cualquier modificación
            # para poder restaurarlo si Odoo lo borra al actualizar los atributos.
            # Usamos get con False como default para manejar si el campo no está en fields_o17_template
            initial_o17_default_code_current = o17_template.get('default_code', False)
            if o17_template_id not in initial_o17_default_codes:
                 initial_o17_default_codes[o17_template_id] = initial_o17_default_code_current
                 _logger.info(f"[PRESERVE] Guardando default_code inicial de Odoo 17 ID {o17_template_id}: '{initial_o17_default_code_current}'")
            else:
                # Esto puede pasar si un item falló antes y se está reintentando
                 _logger.info(f"[PRESERVE] default_code inicial ya guardado para Odoo 17 ID {o17_template_id}: '{initial_o17_default_codes[o17_template_id]}'")
                 # Asegurarse de que el current code en O17 coincide con el que teníamos guardado, o loguear aviso?
                 # Por ahora, solo logueamos. La restauración se hará al final.


            # Registrar el ID de Odoo 17 en el resumen
            template_summary['o17_sync']['id'] = o17_template_id
            template_summary['o17_sync']['name_final'] = o17_template.get('name') # Guardar nombre actual O17
            template_summary['o17_sync']['default_code_final'] = o17_template.get('default_code') # Guardar code actual O17


            _logger.info(f"Destino Odoo 17: ID {o17_template_id}, Name: '{o17_template.get('name')}', Code: '{o17_template.get('default_code')}', Active: {o17_template.get('active', True)}")

            # --- 3. Sincronización de Atributos (product.template.attribute.line) ---
            # Esto implica:
            # - Leer las líneas de atributo y valores del template FUENTE (Odoo 16).
            # - Asegurar que los atributos y sus valores existen en Odoo 17, creándolos si es necesario.
            # - Mapear IDs de Odoo 16 a Odoo 17 para atributos y valores.
            # - Actualizar las líneas de atributo en el template DESTINO (Odoo 17)
            #   para que tengan los atributos y valores correspondientes de Odoo 16.
            # - Eliminar (desvincular) líneas de atributo en Odoo 17 que no existan en Odoo 16.


            # INICIALIZAR VARIABLES NECESARIAS PARA EL PROCESAMIENTO DE ATRIBUTOS/VARIANTES
            # ESTAS VARIABLES DEBEN ESTAR DENTRO DEL BUCLE DEL ITEM PARA REINICIARSE POR CADA TEMPLATE
            o17_attribute_line_commands = [] # Lista para comandos (0,0,{}), (1,id,{}), (2,id) para 'attribute_line_ids'
            processed_o17_line_ids_from_o16 = set() # IDs de las líneas O17 que se corresponden con líneas O16 (para identificar obsoletas)

            # --- CORRECCIÓN: Obtener las líneas de atributo existentes en Odoo 17 y mapearlas ---
            existing_o17_lines_map = {} # Mapea O17 Attribute ID -> O17 Attribute Line ID (para actualizar)
            existing_o17_attr_line_ids = set() # Set de todos los IDs de líneas de atributo existentes en Odoo 17 (para identificar obsoletas)

            _logger.info(f"Obteniendo líneas de atributo existentes en Odoo 17 para template ID {o17_template_id}...")
            # Los campos 'attribute_id' y 'value_ids' son necesarios
            fields_o17_existing_lines = ['id', 'attribute_id', 'value_ids'] # Añadimos value_ids para posible comparación en futuro

            # Leemos las líneas de atributo ya asociadas al template en Odoo 17
            o17_existing_attribute_lines_data = execute_odoo_kw(
                 models_o17, o17_db, uid_o17, o17_pass,
                 'product.template.attribute.line',
                 'search_read',
                 [[('product_tmpl_id', '=', o17_template_id)]],
                 {'fields': fields_o17_existing_lines}
            )

            if o17_existing_attribute_lines_data is None:
                 _logger.error(f"Fallo RPC al leer líneas de atributo existentes en Odoo 17 para template ID {o17_template_id}. No se realizará la sincronización de atributos ni variantes.")
                 map_success = False # Marcar fallo crítico para este template
                 template_summary['processed_ok'] = False # Marcar fallo general para el resumen
                 # No continuar con el procesamiento de atributos/variantes para este template.
            elif o17_existing_attribute_lines_data:
                 _logger.info(f"Encontradas {len(o17_existing_attribute_lines_data)} líneas de atributo existentes en Odoo 17. Mapeando por atributo...")
                 for o17_line in o17_existing_attribute_lines_data:
                     o17_line_id_current = o17_line['id']
                     o17_attribute_info_current = o17_line.get('attribute_id') # [ID, Name]

                     if o17_attribute_info_current and isinstance(o17_attribute_info_current,(list, tuple)) and len(o17_attribute_info_current) > 0 and isinstance(o17_attribute_info_current[0], int):
                         o17_attr_id_current = o17_attribute_info_current[0]
                         existing_o17_lines_map[o17_attr_id_current] = o17_line_id_current
                         existing_o17_attr_line_ids.add(o17_line_id_current) # Añadir a la lista de IDs existentes

                         # Opcional: loguear detalles de las líneas existentes en O17
                         # _logger.debug(f"  -> Línea O17 existente ID {o17_line_id_current} para Attr ID {o17_attr_id_current}")
                     else:
                         _logger.warning(f"  -> Línea de atributo Odoo 17 ID {o17_line_id_current} tiene información de atributo inválida. Ignorada para mapeo.")

            else:
                 _logger.info("No se encontraron líneas de atributo existentes en Odoo 17 para este template.")
                 # existing_o17_lines_map y existing_o17_attr_line_ids quedan vacíos, lo cual es correcto.


            # 3a. Leer Atributos/Valores O16 del template fuente
            o16_attribute_line_ids = o16_template.get('attribute_line_ids', [])
            o16_attribute_lines = [] # Lista para almacenar los diccionarios de detalles de las líneas O16 leídas
            o16_value_details_map = {} # Cache para nombres de valores de atributo O16: O16 Value ID -> O16 Value Name

            if o16_attribute_line_ids and map_success: # Solo leer de O16 si hay IDs y no hubo fallo RPC en O17 antes
                 _logger.info(f"Leyendo detalles de {len(o16_attribute_line_ids)} líneas de atributo desde Odoo 16...")
                 # Leer detalles de las líneas (attribute_id, value_ids)
                 o16_lines_details = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.template.attribute.line', 'read', o16_attribute_line_ids, {'fields': ['attribute_id', 'value_ids']})

                 if o16_lines_details is None:
                     _logger.error(f"Fallo RPC al leer detalles de attribute_line_ids {o16_attribute_line_ids} desde Odoo 16. No se sincronizarán atributos ni variantes.")
                     map_success = False # Marcar fallo crítico
                     template_summary['processed_ok'] = False # Marcar fallo general
                 elif o16_lines_details:
                     o16_attribute_lines = o16_lines_details # Usar los datos leídos

                     # Recopilar todos los IDs de valores para leer sus nombres de una vez
                     all_o16_value_ids = list(set(val_id for line in o16_attribute_lines for val_id in line.get('value_ids',[])))
                     if all_o16_value_ids:
                         _logger.debug(f"Leyendo nombres para {len(all_o16_value_ids)} valores de atributo desde Odoo 16...")
                         o16_values_names = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.attribute.value', 'read', all_o16_value_ids, {'fields': ['id', 'name']})

                         if o16_values_names is None:
                             _logger.warning(f"Fallo RPC al leer nombres de valores de atributo {all_o16_value_ids} desde Odoo 16. Algunos detalles del resumen podrían faltar o el mapeo de valores ser incompleto.")
                             # No es fatal, intentaremos seguir con los valores que sí leímos o mapeamos
                         elif o16_values_names:
                              # Validate IDs and names before adding to map
                              o16_value_details_map = {val['id']: val['name'] for val in o16_values_names if val.get('id') is not None and val.get('name') is not None}
                              if len(o16_value_details_map) != len(o16_values_names):
                                  _logger.warning(f"  -> {len(o16_values_names) - len(o16_value_details_map)} valores de atributo de Odoo 16 tenían ID/nombre inválido y fueron ignorados.")
                         else:
                             _logger.debug("No se encontraron datos de nombre para los IDs de valor de atributo leídos en Odoo 16.")

                 else:
                     _logger.info(f"No se obtuvieron detalles para las líneas de atributo {o16_attribute_line_ids} de Odoo 16.")

            elif o16_attribute_line_ids and not map_success:
                 _logger.warning("Saltando lectura de líneas de atributo Odoo 16 debido a fallos RPC anteriores en Odoo 17.")

            elif not o16_attribute_line_ids:
                 _logger.info(f"Template Odoo 16 ID {o16_template_id} no tiene líneas de atributo.")
                 # map_success sigue True si no hubo fallos RPC en O17 antes.

            # 3b. Procesar cada línea de atributo de Odoo 16 y preparar comandos para Odoo 17
            if map_success: # Continuar solo si la lectura O16 y O17 de atributos/valores fue OK
                 if o16_attribute_lines:
                     _logger.info(f"Procesando {len(o16_attribute_lines)} líneas de atributo de Odoo 16 para sincronizar en Odoo 17...")
                     for o16_line in o16_attribute_lines:
                          o16_line_id = o16_line['id']
                          o16_attribute_info = o16_line.get('attribute_id') # [ID, Name]

                          if not o16_attribute_info or not isinstance(o16_attribute_info,(list, tuple)) or len(o16_attribute_info) < 2:
                              _logger.warning(f"Línea de atributo Odoo 16 ID:{o16_line_id} sin información de atributo válida. Saltando.")
                              template_summary['o16_data']['attributes'].append({'id': o16_line_id, 'name': 'ERROR: No Attribute Info'})
                              continue # Continuar con la siguiente línea O16

                          o16_attr_id, o16_attr_name = o16_attribute_info
                          o16_value_ids_for_this_line = o16_line.get('value_ids', [])

                          # Registrar info básica de la línea O16 en el resumen (antes del mapeo O17)
                          summary_o16_line_data = {'id': o16_line_id, 'name': o16_attr_name, 'values': []}
                          template_summary['o16_data']['attributes'].append(summary_o16_line_data)


                          _logger.debug(f"  Procesando línea de atributo Odoo 16 ID {o16_line_id}: Attr '{o16_attr_name}' ({o16_attr_id}), Valores IDs: {o16_value_ids_for_this_line}")

                          # Obtener o crear el atributo correspondiente en Odoo 17
                          # Usamos o16_attr_name como clave para buscar/crear en Odoo 17
                          o17_attr_id = get_or_create_attribute(models_o17, o17_db, uid_o17, o17_pass, o16_attr_name)

                          if not o17_attr_id:
                              map_success = False # Marcar que el mapeo falló para este template
                              _logger.error(f"  -> Fallo CRÍTICO al obtener/crear atributo '{o16_attr_name}' en Odoo 17. Esta línea de atributo y sus variantes NO se sincronizarán correctamente.")
                              summary_o16_line_data['status'] = 'Mapping Failed' # Actualizar estado en resumen
                              # No break, intentar procesar otras líneas O16 si es posible, pero map_success=False detendrá la sync de variantes
                              continue # Continuar con la siguiente línea O16

                          o17_value_ids_for_line = [] # Lista de IDs de valores de atributo en Odoo 17 para esta línea

                          # Procesar cada valor de atributo de Odoo 16 para esta línea
                          if o16_value_ids_for_this_line:
                              _logger.debug(f"  -> Procesando {len(o16_value_ids_for_this_line)} valores para atributo '{o16_attr_name}'...")
                              for o16_value_id in o16_value_ids_for_this_line:
                                   o16_value_name = o16_value_details_map.get(o16_value_id) # Obtener nombre de la cache

                                   if not o16_value_name:
                                        _logger.warning(f"    -> No se encontró nombre para el valor de atributo Odoo 16 ID {o16_value_id}. Saltando este valor.")
                                        summary_o16_line_data['values'].append({'id_o16': o16_value_id, 'name_o16': 'N/A', 'id_o17': None, 'status': 'Skipped (No Name)'})
                                        continue # Saltar solo este valor, no la línea entera

                                   # Obtener o crear el valor de atributo correspondiente en Odoo 17 (usando cache a nivel de ejecución)
                                   if o16_value_id in o17_pav_mapping:
                                        o17_value_id = o17_pav_mapping[o16_value_id]
                                        _logger.debug(f"    -> Valor '{o16_value_name}' (O16 ID {o16_value_id}) encontrado en cache O17 ID: {o17_value_id}")
                                   else:
                                        # Necesitamos el ID del atributo Odoo 17 para crear el valor
                                        o17_value_id = get_or_create_attribute_value(models_o17, o17_db, uid_o17, o17_pass, o16_value_name, o17_attr_id)
                                        o17_pav_mapping[o16_value_id] = o17_value_id # Guardar en cache
                                        if o17_value_id:
                                            _logger.debug(f"    -> Valor '{o16_value_name}' (O16 ID {o16_value_id}) obtenido/creado en O17 ID: {o17_value_id}")


                                   if o17_value_id:
                                        o17_value_ids_for_line.append(o17_value_id)
                                        summary_o16_line_data['values'].append({'id_o16': o16_value_id, 'name_o16': o16_value_name, 'id_o17': o17_value_id, 'status': 'Mapped OK'})
                                   else:
                                        map_success = False # Marcar que el mapeo falló para este template
                                        _logger.error(f"    -> Fallo al obtener/crear valor '{o16_value_name}' (AttrID O17 {o17_attr_id}) en Odoo 17. Marcando map_success=False.")
                                        summary_o16_line_data['values'].append({'id_o16': o16_value_id, 'name_o16': o16_value_name, 'id_o17': None, 'status': 'Mapping Failed'})
                                        # No break, procesar otros valores en la misma línea si es posible

                          else: # La línea de Odoo 16 no tiene valores de atributo
                              _logger.warning(f"  -> Línea de atributo Odoo 16 ID {o16_line_id} ('{o16_attr_name}') no tiene valores asociados. Se creará la línea en Odoo 17 sin valores (posiblemente incorrecto).")
                              summary_o16_line_data['status'] = 'No Values in O16'


                          # Determinar si la línea de atributo ya existe en Odoo 17 (buscando por ID de atributo Odoo 17)
                          # CORRECCIÓN: Ahora existing_o17_lines_map está definido y populado.
                          o17_line_id_existing = existing_o17_lines_map.get(o17_attr_id)

                          # Preparar comando de escritura M2M para la lista attribute_line_ids del template en Odoo 17
                          # Usamos [(6, 0, [ids])] para reemplazar completamente los valores existentes
                          # Ordenar los IDs de valores para consistencia
                          sorted_o17_value_ids_for_line = sorted(list(set(o17_value_ids_for_line))) # Asegurar únicos y ordenados

                          line_values_m2m = {
                              'attribute_id': o17_attr_id,
                              'value_ids': [(6, 0, sorted_o17_value_ids_for_line)] # Usar IDs de Odoo 17
                          }

                          if o17_line_id_existing:
                              # Si la línea existe, preparar comando de actualización
                              _logger.debug(f"  Preparando comando (1, {o17_line_id_existing}, ...) para actualizar línea de atributo Odoo 17.")
                              o17_attribute_line_commands.append((1, o17_line_id_existing, line_values_m2m))
                              processed_o17_line_ids_from_o16.add(o17_line_id_existing) # Marcar como procesada
                              summary_o16_line_data['status'] = summary_o16_line_data.get('status', 'Update Prepared') # Mantener estado de mapeo de valores si existía

                          else:
                              # Si la línea no existe, preparar comando de creación
                              _logger.debug(f"  Preparando comando (0, 0, ...) para crear línea de atributo Odoo 17.")
                              o17_attribute_line_commands.append((0, 0, line_values_m2m))
                              # No se añade a processed_o17_line_ids_from_o16 en este paso, ya que aún no tiene un ID real en Odoo 17.
                              # Se podría recuperar el ID después del 'write' si fuera necesario mapear O16 Line ID -> O17 Line ID,
                              # pero para el propósito actual (identificar obsoletas), no es estrictamente necesario.
                              summary_o16_line_data['status'] = summary_o16_line_data.get('status', 'Create Prepared') # Mantener estado de mapeo de valores si existía


                     # Después de procesar todas las líneas de Odoo 16, identificar las obsoletas en Odoo 17
                     # Comparar el set de líneas O17 existentes con el set de líneas O17 que *corresponden* a O16
                     lines_to_delete_in_o17 = [line_id for line_id in existing_o17_attr_line_ids if line_id not in processed_o17_line_ids_from_o16]
                     if lines_to_delete_in_o17:
                          _logger.info(f"  {len(lines_to_delete_in_o17)} líneas de atributo obsoletas encontradas en Odoo 17. Preparando para eliminar (desvincular del template).")
                          for line_id_to_delete in lines_to_delete_in_o17:
                              _logger.debug(f"  Preparando comando (2, {line_id_to_delete}) para eliminar línea de atributo Odoo 17.")
                              o17_attribute_line_commands.append((2, line_id_to_delete))

                 else: # Odoo 16 no tiene líneas de atributo para este template
                      _logger.info(f"Template Odoo 16 ID {o16_template_id} no tiene líneas de atributo.")
                      # Si Odoo 16 no tiene líneas pero Odoo 17 sí, eliminar las de Odoo 17
                      if existing_o17_attr_line_ids:
                           _logger.info(f"  Template Odoo 16 no tiene líneas, pero Odoo 17 sí. Preparando para eliminar líneas obsoletas en Odoo 17.")
                           for line_id_to_delete in existing_o17_attr_line_ids:
                                _logger.debug(f"  Preparando comando (2, {line_id_to_delete}) para eliminar línea de atributo Odoo 17.")
                                o17_attribute_line_commands.append((2, line_id_to_delete))


                 # Aplicar los comandos a la lista attribute_line_ids del template en Odoo 17
                 if o17_attribute_line_commands:
                      if map_success: # Aplicar cambios solo si el mapeo de atributos/valores fue exitoso
                          _logger.info(f"  Aplicando {len(o17_attribute_line_commands)} comandos a 'attribute_line_ids' del template Odoo 17 ID {o17_template_id}...")

                          # Leer el default_code antes de actualizar las líneas (Odoo puede borrarlo)
                          # Esta lógica ya está presente y parece correcta.
                          o17_code_before_attr_update = False
                          template_read_result = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'read', [o17_template_id], {'fields': ['default_code']})
                          if template_read_result and isinstance(template_read_result, list) and template_read_result:
                               o17_code_before_attr_update = template_read_result[0].get('default_code', False)
                               _logger.info(f"  [PRESERVE CHECK] default_code de Odoo 17 ID {o17_template_id} antes de actualizar attrs: '{o17_code_before_attr_update}'")
                          else:
                               _logger.error(f"  [PRESERVE CHECK] No se pudo leer el default_code antes de actualizar attrs. Resultado: {template_read_result}") # No fatal para la sync, pero impide el fix


                          attr_line_update_ok = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'write', [[o17_template_id], {'attribute_line_ids': o17_attribute_line_commands}])

                          if attr_line_update_ok is True:
                              _logger.info(f"  -> Update de líneas de atributo Odoo 17 OK.")
                              # Verificar si Odoo borró el default_code y restaurarlo si es necesario
                              # Esta lógica ya está presente y parece correcta.
                              o17_code_after_attr_update = False # Initialize variable
                              template_read_result_after = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'read', [o17_template_id], {'fields': ['default_code']})

                              if template_read_result_after and isinstance(template_read_result_after, list) and template_read_result_after:
                                   o17_code_after_attr_update = template_read_result_after[0].get('default_code', False) # Assign variable if read succeeds
                                   _logger.info(f"  [POST CHECK] default_code de Odoo 17 ID {o17_template_id} después de actualizar attrs: '{o17_code_after_attr_update}'")
                                   initial_code_for_this_template = initial_o17_default_codes.get(o17_template_id, None)

                                   # Logic to check if restoration is needed
                                   has_value_after = bool(o17_code_after_attr_update)
                                   had_value_initially = bool(initial_code_for_this_template)

                                   if not has_value_after and had_value_initially:
                                        _logger.warning(f"  -> [FIX DETECTADO] Code perdido después de actualizar attrs (era '{initial_code_for_this_template}', ahora '{o17_code_after_attr_update}'). Intentando restaurar.")
                                        rewrite_code_ok = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'write', [[o17_template_id], {'default_code': initial_code_for_this_template}])
                                        if rewrite_code_ok is True:
                                             _logger.info(f"  -> [FIX] Restauración de default_code a '{initial_code_for_this_template}' OK.")
                                             template_summary['o17_sync']['default_code_final'] = initial_code_for_this_template # Actualizar resumen con el valor restaurado
                                             # Re-read to confirm restoration? Optional but good practice
                                             final_code_check_data = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'read', [o17_template_id], {'fields': ['default_code']})
                                             final_code_after_fix = final_code_check_data[0].get('default_code') if final_code_check_data and isinstance(final_code_check_data, list) and final_code_check_data else 'ERROR_READ'
                                             _logger.info(f"  [FINAL CHECK Fix] default_code post-restauración: '{final_code_after_fix}'")

                                        else:
                                             _logger.error(f"  -> [FIX] Fallo al restaurar default_code a '{initial_code_for_this_template}'. Resultado: {rewrite_code_ok}") # No fatal?
                                             template_summary['o17_sync']['default_code_final'] = o17_code_after_attr_update # Si falló, mantener el que quedó

                                   elif has_value_after and o17_code_after_attr_update != initial_code_for_this_template:
                                        # If it has a value, but it's different from the original
                                        _logger.warning(f"  -> [CHECK] default_code cambió de '{initial_code_for_this_template}' a '{o17_code_after_attr_update}' después de actualizar attrs, pero no fue borrado completamente.")
                                        template_summary['o17_sync']['default_code_final'] = o17_code_after_attr_update # Actualizar resumen con el valor que quedó

                                   elif has_value_after and o17_code_after_attr_update == initial_code_for_this_template:
                                        # Usar la variable correcta aquí
                                        _logger.info(f"  [CHECK] default_code ('{o17_code_after_attr_update}') ya coincide con el inicial. No se necesitó restauración.")
                                        template_summary['o17_sync']['default_code_final'] = o17_code_after_attr_update # Actualizar resumen con el valor que quedó


                              else:
                                   _logger.error(f"  [POST CHECK] No se pudo leer el default_code después de actualizar attrs. No se pudo verificar si se perdió.") # No fatal
                                   # No podemos asegurar el code final, mantener el que teníamos del primer read
                                   template_summary['o17_sync']['default_code_final'] = template_summary['o17_sync'].get('default_code_final', 'N/A (Error post-update)')


                          elif attr_line_update_ok is False:
                               _logger.error(f"  -> Fallo lógico al actualizar líneas de atributo del template Odoo 17 ID {o17_template_id}. Resultado: False.")
                               map_success = False # Marcar fallo, no procesar variantes

                          elif attr_line_update_ok is None:
                               _logger.error(f"  -> Fallo RPC/inesperado al actualizar líneas de atributo del template Odoo 17 ID {o17_template_id}.")
                               map_success = False # Marcar fallo, no procesar variantes
                               raise Exception("Fallo RPC Odoo 17 al actualizar líneas de atributo") # Fatal para este template
                          else:
                               _logger.warning(f"  -> Resultado inesperado ({attr_line_update_ok}) al actualizar líneas de atributo del template Odoo 17 ID {o17_template_id}.")
                               map_success = False # Considerar fallo, no procesar variantes

                      elif not map_success:
                           _logger.warning("  Mapeo de atributos/valores falló antes. No se aplicarán comandos a las líneas de atributo.")

                 elif not o17_attribute_line_commands:
                      _logger.info("  No hay comandos para aplicar a las líneas de atributo en Odoo 17.")


            # 4. Sincronización de Variantes (product.product)
            # Este paso crea/actualiza/elimina (archiva) las variantes en Odoo 17
            # para que coincidan con las de Odoo 16, basándose en el mapeo de PTAVs.

            if map_success: # Proceder solo si el mapeo de atributos/valores fue exitoso
                 _logger.info(f"Iniciando sincronización de variantes para template Odoo 17 ID {o17_template_id}...")

                 # --- OBTENER VARIANTE(S) DE ODOO 16 ---
                 # Para productos con atributos, product_variant_ids contiene los IDs de las variantes.
                 # Para productos sin atributos (plantilla = variante), product_variant_ids es [template_id].
                 fields_o16_variant_read = ['id', 'default_code', 'legacy_default_code', 'barcode', 'product_template_attribute_value_ids', 'active', 'image_1920', 'lst_price']


                 o16_variant_ids = o16_template.get('product_variant_ids', [])

                 # Manejar el caso especial de productos sin atributos donde la plantilla es la variante
                 is_simple_product_o16 = not o16_attribute_line_ids and len(o16_variant_ids) == 1 and o16_variant_ids[0] == o16_template_id

                 if not o16_attribute_line_ids and not o16_variant_ids:
                      # Caso de producto simple recién creado en Odoo 16 que aún no tiene variantes en product_variant_ids
                      _logger.info(f"  Template Odoo 16 ID {o16_template_id} parece ser un producto simple (sin líneas attr). Asumiendo ID {o16_template_id} es la variante.")
                      is_simple_product_o16 = True
                      o16_variant_ids = [o16_template_id]


                 if not o16_variant_ids:
                      _logger.info("  Template Odoo 16 sin variantes encontradas. Saltando sincronización de variantes.")
                      # No hay variantes en O16, debemos archivar TODAS las variantes existentes en O17 para este template
                      # Esto se maneja en la limpieza de obsoletas más abajo.
                      o16_variants_data = [] # Asegurar que la lista está vacía si no se encontraron variantes O16

                 else:
                      o16_variants_data = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.product', 'read', o16_variant_ids, {'fields': fields_o16_variant_read})

                      if o16_variants_data is None:
                           _logger.error(f"Fallo RPC al leer datos de variantes {o16_variant_ids} desde Odoo 16. Saltando sincronización de variantes para este template.")
                           template_summary['processed_ok'] = False # Este template falló parcialmente/totalmente
                           o16_variants_data = [] # Asegurar que la lista está vacía para no procesar

                      elif not o16_variants_data and o16_variant_ids:
                           _logger.warning(f"No se obtuvieron datos para las variantes {o16_variant_ids} de Odoo 16. Saltando sincronización de variantes.")
                           template_summary['processed_ok'] = False # Marcar fallo para este template
                           o16_variants_data = [] # Asegurar que la lista está vacía para no procesar

                      else: # Tenemos datos de variantes de Odoo 16
                           counters['o16_variants_found'] += len(o16_variants_data)
                           # Guardar detalles O16 para el resumen
                           template_summary['o16_data']['variants'] = [{'id': v['id'], 'code': v.get('default_code'), 'legacy': v.get('legacy_default_code'), 'barcode': v.get('barcode'), 'price': v.get('lst_price', 0.0)} for v in o16_variants_data]


                 # --- OBTENER VARIANTE(S) EXISTENTES EN ODOO 17 ---
                 _logger.info(f"  Obteniendo variantes existentes en Odoo 17 para template ID {o17_template_id}...")
                 fields_o17_variant = ['id', 'default_code', 'legacy_default_code', 'barcode', 'product_template_attribute_value_ids', 'active', 'image_1920', 'lst_price']

                 existing_o17_variants_data = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'search_read', [[('product_tmpl_id', '=', o17_template_id), ('active', 'in', [True, False])]], {'fields': fields_o17_variant})

                 o17_variants_map_by_ptavs = {} # Mapea tuple(sorted(PTAV IDs O17)) -> variant_data O17
                 o17_variant_ids_to_clean_potential = set() # Todos los IDs existentes en Odoo 17 para este template

                 if existing_o17_variants_data is None:
                      _logger.error(f"Fallo RPC al leer variantes existentes en Odoo 17 para template ID {o17_template_id}. No se realizará la sincronización de variantes ni el archivado de obsoletas.")
                      template_summary['processed_ok'] = False # Marcar fallo para este template

                 elif existing_o17_variants_data:
                       counters['o17_variants_existing'] += len(existing_o17_variants_data)
                       _logger.info(f"  {len(existing_o17_variants_data)} variantes existentes encontradas en Odoo 17. Mapeando por combinación de atributos...")
                       for variant_data in existing_o17_variants_data:
                           o17_variant_id_inner = variant_data['id']
                           o17_variant_ids_to_clean_potential.add(o17_variant_id_inner) # Añadir a la lista potencial de limpieza

                           o17_variant_ptav_ids = variant_data.get('product_template_attribute_value_ids', [])
                           key_ptav_ids = tuple(sorted(o17_variant_ptav_ids)) # Crear clave única (tuple de IDs ordenados)

                           # Manejar posibles variantes duplicadas con la misma combinación de atributos (raro pero posible)
                           if key_ptav_ids in o17_variants_map_by_ptavs:
                               existing_variant_id = o17_variants_map_by_ptavs[key_ptav_ids]['id']
                               _logger.warning(f"  Variante duplicada encontrada en Odoo 17 con la misma combinación de PTAVs {key_ptav_ids} para template {o17_template_id}. Variante existente ID: {existing_variant_id}, Nueva variante encontrada ID: {o17_variant_id_inner}. Se usará la última encontrada para mapeo.")

                           o17_variants_map_by_ptavs[key_ptav_ids] = variant_data # Mapear la combinación de PTAVs a la variante O17 encontrada


                 else:
                      _logger.info("  No se encontraron variantes existentes en Odoo 17 para este template.")


                 # --- PROCESAR CADA VARIANTE DE ODOO 16 ---
                 processed_o17_variant_ids = set() # IDs de variantes en Odoo 17 que se corresponden con una variante de Odoo 16 y se procesaron (crearon/actualizaron)

                 if o16_variants_data: # Solo si hay variantes en Odoo 16 (y la lectura O16 no falló)
                     _logger.info(f"  Procesando {len(o16_variants_data)} variantes desde Odoo 16...")

                     for o16_variant in o16_variants_data:
                          o16_variant_id = o16_variant['id']
                          o16_variant_code = o16_variant.get('default_code')
                          o16_variant_legacy = o16_variant.get('legacy_default_code')
                          o16_variant_barcode = o16_variant.get('barcode')
                          o16_variant_is_active = o16_variant.get('active', True)
                          o16_ptav_ids_o16 = o16_variant.get('product_template_attribute_value_ids', [])
                          o16_variant_image = o16_variant.get('image_1920')
                          o16_variant_price = o16_variant.get('lst_price', 0.0) # Usar 0.0 si es False o None


                          _logger.debug(f"  Procesando variante Odoo 16 ID {o16_variant_id}: Code='{o16_variant_code}', Active={o16_variant_is_active}, PTAVs O16: {o16_ptav_ids_o16}, Has Image: {bool(o16_variant_image)}, Price: {o16_variant_price}")

                          o17_target_ptav_ids = [] # Lista de IDs de PTAVs en Odoo 17 para esta variante
                          variant_mapping_failed = False # Flag para esta variante específica

                          # Mapear los PTAVs de Odoo 16 a los PTAVs correspondientes en Odoo 17
                          if o16_ptav_ids_o16:
                              # Leer detalles de PTAVs de Odoo 16
                              o16_ptav_details = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.template.attribute.value', 'read', o16_ptav_ids_o16, {'fields': ['id', 'product_attribute_value_id']})

                              if o16_ptav_details is None:
                                   _logger.error(f"    Fallo RPC al leer detalles de PTAVs {o16_ptav_ids_o16} para variante Odoo 16 ID {o16_variant_id}. Saltando esta variante.")
                                   continue # Saltar esta variante específica, no todo el template

                              if not o16_ptav_details and o16_ptav_ids_o16:
                                   _logger.warning(f"    No se obtuvieron detalles para los PTAVs {o16_ptav_ids_o16} de Odoo 16 para variante {o16_variant_id}. Saltando esta variante.")
                                   continue # Saltar esta variante específica


                              o17_mapped_pav_ids = [] # Lista de IDs de PAVs en Odoo 17 correspondientes a los PAVs de Odoo 16
                              # Mapear PAVs de Odoo 16 a Odoo 17 (usando la cache o17_pav_mapping de antes)
                              for ptav_detail_o16 in o16_ptav_details:
                                   o16_pav_info = ptav_detail_o16.get('product_attribute_value_id') # [ID, Name]

                                   if o16_pav_info and isinstance(o16_pav_info,(list, tuple)) and len(o16_pav_info) > 0 and isinstance(o16_pav_info[0], int):
                                        o16_pav_id = o16_pav_info[0]

                                        if o16_pav_id in o17_pav_mapping:
                                             o17_mapped_pav_id = o17_pav_mapping[o16_pav_id]
                                             if o17_mapped_pav_id:
                                                  o17_mapped_pav_ids.append(o17_mapped_pav_id)
                                             else:
                                                  # Esto indica que el mapeo de ese PAV falló antes (en paso 3)
                                                  _logger.error(f"    Valor de atributo (PAV) Odoo 16 ID {o16_pav_id} mapeado a None en cache Odoo 17 para variante {o16_variant_id}. Esto significa que su creación/búsqueda falló. Saltando esta variante.")
                                                  variant_mapping_failed = True
                                                  break # Salir del bucle de valores, esta variante no se puede mapear

                                        else:
                                             # Esto indica que el PAV O16 no fue encontrado en el mapeo (raro si el mapeo fue exitoso en paso 3)
                                             _logger.error(f"    Valor de atributo (PAV) Odoo 16 ID {o16_pav_id} no encontrado en cache de mapeo Odoo 17 para variante {o16_variant_id}. Saltando esta variante.")
                                             variant_mapping_failed = True
                                             break # Salir del bucle de valores

                                   else:
                                        _logger.warning(f"    PTAV Odoo 16 ID {ptav_detail_o16.get('id')} para variante {o16_variant_id} no tiene información de PAV válida. Saltando este PTAV.")
                                        # No marcar variant_mapping_failed aquí, solo loguear y saltar este PTAV específico


                              if variant_mapping_failed:
                                   continue # Saltar a la siguiente variante Odoo 16

                              if o17_mapped_pav_ids:
                                   # Buscar los PTAVs correspondientes en Odoo 17 que pertenecen a este template y tienen estos PAVs
                                   # La combinación de product_tmpl_id y product_attribute_value_id (PAV) identifica un PTAV único
                                   domain_ptav_combo_o17 = [('product_tmpl_id', '=', o17_template_id), ('product_attribute_value_id', 'in', o17_mapped_pav_ids)]
                                   o17_ptavs_for_combo_data = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template.attribute.value', 'search_read', [domain_ptav_combo_o17], {'fields': ['id', 'product_attribute_value_id']}) # Necesitamos el product_attribute_value_id para mapear PAV a PTAV

                                   if o17_ptavs_for_combo_data is None:
                                        _logger.error(f"    Fallo RPC al buscar PTAVs en Odoo 17 para PAVs {o17_mapped_pav_ids} y template {o17_template_id}. Saltando esta variante.")
                                        continue # Saltar esta variante específica

                                   # Crear un mapeo temporal de O17 PAV ID a O17 PTAV ID para esta variante
                                   temp_pav_ptav_map_o17 = {
                                        ptav['product_attribute_value_id'][0]: ptav['id']
                                        for ptav in o17_ptavs_for_combo_data
                                        if ptav.get('product_attribute_value_id') and isinstance(ptav['product_attribute_value_id'], (list, tuple)) and len(ptav['product_attribute_value_id']) > 0 and isinstance(ptav['product_attribute_value_id'][0], int) and ptav.get('id')
                                    } if o17_ptavs_for_combo_data else {}

                                   # Obtener los IDs de los PTAVs de Odoo 17 usando el mapeo
                                   o17_target_ptav_ids = [temp_pav_ptav_map_o17.get(pav_id_o17) for pav_id_o17 in o17_mapped_pav_ids if pav_id_o17 in temp_pav_ptav_map_o17]

                                   if len(o17_target_ptav_ids) != len(o17_mapped_pav_ids):
                                        _logger.error(f"    No se encontraron PTAVs en Odoo 17 para TODOS los PAVs mapeados ({len(o17_target_ptav_ids)}/{len(o17_mapped_pav_ids)}) para variante Odoo 16 ID {o16_variant_id}. Saltando esta variante.")
                                        continue # Saltar esta variante específica

                                   # La clave para identificar la variante en Odoo 17 es la tupla ordenada de sus PTAV IDs
                                   variant_key_ptavs = tuple(sorted(o17_target_ptav_ids))
                                   _logger.debug(f"    PTAVs destino Odoo 17 para variante Odoo 16 ID {o16_variant_id}: {o17_target_ptav_ids}. Clave de mapeo: {variant_key_ptavs}")

                              else:
                                    # Si la variante de Odoo 16 tiene PTAVs pero no se mapearon PAVs válidos
                                    _logger.error(f"    Variante Odoo 16 ID {o16_variant_id} tenía PTAVs ({o16_ptav_ids_o16}) pero no se mapearon PAVs válidos a Odoo 17. Saltando esta variante.")
                                    continue # Saltar esta variante específica


                          else:
                               # Si la variante de Odoo 16 NO tiene PTAVs, es la variante por defecto del producto simple
                               # Su clave de PTAVs en Odoo 17 es una tupla vacía ().
                               # Esto aplica si el template Odoo 16 tampoco tiene líneas de atributo.
                               if not o16_template.get('attribute_line_ids'):
                                    variant_key_ptavs = ()
                                    o17_target_ptav_ids = [] # Lista vacía de PTAVs para productos simples
                                    _logger.debug(f"    Variante Odoo 16 ID {o16_variant_id} es un producto simple. Clave PTAV: {variant_key_ptavs}")
                               else:
                                    # Si la variante O16 no tiene PTAVs pero el template sí tiene atributos, es una variante inesperada o un error en O16
                                    _logger.warning(f"    Variante Odoo 16 ID {o16_variant_id} no tiene PTAVs pero el template Odoo 16 ID {o16_template_id} sí tiene atributos. Saltando esta variante.")
                                    continue # Saltar esta variante específica


                          # --- Inicio - Procesamiento Variante Individual (Crear/Actualizar) ---
                          # Buscar la variante en Odoo 17 usando la clave de PTAVs mapeada
                          o17_variant_match = o17_variants_map_by_ptavs.get(variant_key_ptavs)

                          # Preparar resumen para esta variante específica
                          o16_variant_summary = {
                               'id': o16_variant_id,
                               'code': o16_variant_code or '',
                               'barcode': o16_variant_barcode or '',
                               'legacy': o16_variant_legacy or '',
                               'price': o16_variant_price
                               }
                          variant_sync_result = {
                               'o16': o16_variant_summary,
                               'o17_id': None, # Se llenará si se encuentra o crea
                               'status': 'pending', # created, updated, found_unchanged, create_failed, update_failed
                               'details_o17': None # Se llenará al final del procesamiento de la variante
                               }

                          # --- Lógica para la sincronización selectiva de campos (--sync-fields) ---
                          fields_to_check_and_sync = set() # Campos a verificar y potencialmente actualizar/crear

                          # Campos siempre necesarios para la estructura básica de la variante
                          essential_vals = {
                               'product_tmpl_id': o17_template_id,
                               'product_template_attribute_value_ids': [(6, 0, sorted(o17_target_ptav_ids))],
                               'type': 'product', # Odoo 17 espera esto para productos
                          }
                          # Campos que solo se sincronizan si el modo es 'all'
                          all_fields_vals = {
                               'default_code': o16_variant_code or False,
                               'legacy_default_code': o16_variant_legacy or False,
                               'barcode': o16_variant_barcode or False,
                               'active': o16_variant_is_active,
                               'image_1920': o16_variant_image or False,
                          }
                            # Campos que se sincronizan si el modo es 'all' o 'price_only'
                          price_field_vals = {
                               'lst_price': float(o16_variant_price or 0.0) # Asegurarse de que sea float
                          }

                          # Diccionario de valores a usar en el 'create' o para comparación/update
                          vals_to_sync = {}
                          if sync_fields_mode == 'all':
                              vals_to_sync.update(all_fields_vals)
                              vals_to_sync.update(price_field_vals)
                              fields_to_check_and_sync.update(all_fields_vals.keys())
                              fields_to_check_and_sync.update(price_field_vals.keys())
                          elif sync_fields_mode == 'price_only':
                              vals_to_sync.update(price_field_vals)
                              fields_to_check_and_sync.update(price_field_vals.keys())
                          else:
                              # Esto no debería pasar si choices está bien definido en argparse en main_sync
                              _logger.error(f"Modo sync_fields desconocido: {sync_fields_mode}. No se sincronizarán campos de variante.")
                              template_summary['processed_ok'] = False # Marcar fallo para este template
                              variant_sync_result['status'] = 'sync_fields_error'
                              template_summary['o17_sync']['variants_synced'].append(variant_sync_result)
                              continue # Saltar a la siguiente variante O16

                          # Asegurar que 'active' esté en vals_to_sync si la variante O16 está inactiva,
                          # incluso si el modo no es 'all', para poder archivarla si existe en O17.
                          if not o16_variant_is_active and 'active' not in vals_to_sync:
                              vals_to_sync['active'] = False
                              fields_to_check_and_sync.add('active') # Asegurarse de que se compare si existe en O17

                          # También asegurarse de que 'image_1920' esté si hay imagen en O16, para crearla o actualizarla.
                          if o16_variant_image and 'image_1920' not in vals_to_sync:
                               vals_to_sync['image_1920'] = o16_variant_image
                               fields_to_check_and_sync.add('image_1920')


                          # --- Fin Lógica sincronización selectiva ---


                          if o17_variant_match:
                              # --- Variante Encontrada en Odoo 17 ---
                              o17_variant_id_match = o17_variant_match['id']
                              processed_o17_variant_ids.add(o17_variant_id_match) # Marcar como procesada (no obsoleta)
                              variant_sync_result['o17_id'] = o17_variant_id_match

                              _logger.info(f"    Variante encontrada en Odoo 17 con ID: {o17_variant_id_match} (Clave PTAVs: {variant_key_ptavs}).")

                              # Obtener valores actuales de Odoo 17 para comparar
                              # Solo necesitamos leer los campos que estamos considerando sincronizar + los de identificación (id, ptav_ids)
                              current_o17_data_subset = {f: o17_variant_match.get(f) for f in fields_o17_variant if f in fields_to_check_and_sync or f in ['id', 'product_template_attribute_value_ids']}


                              needs_update = False
                              update_vals = {} # Usaremos este diccionario para el write

                              # Comparar los campos que deben sincronizarse en este modo
                              # Recorrer los campos que se decidií sincronizar y comparar con los valores actuales en O17
                              for field in fields_to_check_and_sync:
                                  o16_value = vals_to_sync.get(field)
                                  # Usar .get con un valor por defecto apropiado si el campo no está en current_o17_data_subset (ej. False para códigos, 0.0 para precio, True para active)
                                  if field == 'lst_price':
                                        current_o17_value = current_o17_data_subset.get(field, 0.0) # Default 0.0
                                        # Comparación flotante
                                        if not math.isclose(float(o16_value or 0.0), float(current_o17_value or 0.0), rel_tol=1e-9, abs_tol=0.000001):
                                             needs_update = True; update_vals[field] = float(o16_value or 0.0); _logger.info(f"      -> Campo '{field}' difiere (Precio).")
                                  elif field in ['default_code', 'legacy_default_code', 'barcode']:
                                        current_o17_value = (current_o17_data_subset.get(field) or '').strip() # Default ''
                                        o16_value_cleaned = (o16_value or '').strip() # O16 value might be False
                                        if o16_value_cleaned != current_o17_value:
                                             needs_update = True; update_vals[field] = o16_value or False; _logger.info(f"      -> Campo '{field}' difiere (Código/Barcode).")
                                  elif field == 'active':
                                        current_o17_value = current_o17_data_subset.get(field, True) # Default True
                                        # Comparar bools directamente
                                        if bool(o16_value) != bool(current_o17_value):
                                             needs_update = True; update_vals[field] = bool(o16_value); _logger.info(f"      -> Campo '{field}' difiere (Activo).")
                                  elif field == 'image_1920':
                                        current_o17_value = current_o17_data_subset.get(field) # Default None/False
                                        # Si hay imagen en O16 pero no en O17, o si son diferentes.
                                        if o16_value and (not current_o17_value or o16_value != current_o17_value):
                                             needs_update = True; update_vals[field] = o16_value; _logger.info(f"      -> Campo '{field}' difiere o presente en O16 (Imagen). Se actualizará.")
                                        elif o16_value:
                                             _logger.debug(f"      -> Campo '{field}' idéntico (Imagen).")
                                        elif current_o17_value:
                                             # Si no hay imagen en O16 pero sí en O17, la eliminamos
                                             needs_update = True; update_vals[field] = False; _logger.info(f"      -> Campo '{field}' presente en O17 pero no en O16 (Imagen). Se eliminará.")
                                  else:
                                        # Para otros campos que no sean códigos, barcode, active, image, price
                                        current_o17_value = current_o17_data_subset.get(field) # Default None
                                        if o16_value != current_o17_value:
                                             needs_update = True; update_vals[field] = o16_value; _logger.info(f"      -> Campo '{field}' difiere.")


                              # Realizar la actualización si hay cambios
                              if needs_update:
                                   counters['o17_variants_updated'] += 1
                                   variant_sync_result['status'] = 'updated'
                                   _logger.info(f"    Actualizando var Odoo 17 ID: {o17_variant_id_match} con: {list(update_vals.keys())}")

                                   # Intentar escribir
                                   write_ok = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'write', [[o17_variant_id_match], update_vals])

                                   if write_ok is True:
                                        _logger.info(f"      -> Update de variante Odoo 17 ID {o17_variant_id_match} OK.")
                                   elif write_ok is None and 'barcode' in update_vals and update_vals['barcode']:
                                        # Manejo específico de fallo al actualizar barcode (posible duplicado)
                                        variant_sync_result['status'] = 'update_failed (barcode?)'
                                        _logger.warning(f"      -> Fallo al actualizar variante Odoo 17 ID {o17_variant_id_match}, posible conflicto de barcode '{update_vals['barcode']}'. Reintentando sin el campo 'barcode'...")
                                        update_vals_no_barcode = update_vals.copy()
                                        del update_vals_no_barcode['barcode']
                                        if update_vals_no_barcode:
                                             write_ok_no_barcode = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'write', [[o17_variant_id_match], update_vals_no_barcode])
                                             if write_ok_no_barcode is True:
                                                  _logger.info(f"        -> Update de variante Odoo 17 ID {o17_variant_id_match} (sin barcode) OK.")
                                                  variant_sync_result['status'] = 'updated (no barcode)' # Estado más específico
                                             else:
                                                  _logger.error(f"        -> Fallo al actualizar variante Odoo 17 ID {o17_variant_id_match} incluso sin barcode. Resultado: {write_ok_no_barcode}")
                                        else:
                                             _logger.info("        -> No hay otros campos para actualizar aparte del barcode.")

                                   elif write_ok is False:
                                        variant_sync_result['status'] = 'update_failed (logic)'
                                        _logger.error(f"      -> Fallo lógico al actualizar variante Odoo 17 ID {o17_variant_id_match}. Resultado: False.")

                                   elif write_ok is None:
                                        variant_sync_result['status'] = 'update_failed (rpc)'
                                        _logger.error(f"      -> Fallo RPC/inesperado general al actualizar variante Odoo 17 ID {o17_variant_id_match}.")
                                        # Podríamos considerar levantar una excepción aquí si queremos que falle todo el template
                                        # raise Exception(f"Fallo RPC al actualizar variante {o17_variant_id_match}")

                                   else:
                                        variant_sync_result['status'] = 'update_failed (unknown)'
                                        _logger.warning(f"      -> Resultado inesperado ({write_ok}) al actualizar variante Odoo 17 ID {o17_variant_id_match}.")
                              else:
                                   variant_sync_result['status'] = 'found_unchanged'
                                   _logger.info(f"    Variante Odoo 17 ID: {o17_variant_id_match} ya sincronizada (no hay campos que requieran actualización en modo '{sync_fields_mode}').")

                              # Guardar detalles FINALES de Odoo 17 para el resumen
                              # Simplificado: usamos los valores que intentamos escribir + los que ya estaban leídos.
                              # Una lectura post-write sería más precisa pero añade otra llamada RPC por variante.
                              # Recopilar todos los campos relevantes leídos de O17 primero
                              final_o17_details = {f: current_o17_data_subset.get(f) for f in fields_o17_variant if f in current_o17_data_subset}
                              # Sobrescribir con los valores que *intentamos* actualizar
                              final_o17_details.update(update_vals)

                              # Añadir campos clave al resumen (incluso si no se actualizaron)
                              final_o17_details['code'] = final_o17_details.get('default_code', False)
                              final_o17_details['barcode'] = final_o17_details.get('barcode', False)
                              final_o17_details['legacy'] = final_o17_details.get('legacy_default_code', False)
                              final_o17_details['price'] = final_o17_details.get('lst_price', 0.0)
                              final_o17_details['active'] = final_o17_details.get('active', True)
                              final_o17_details['image_1920'] = final_o17_details.get('image_1920', False)


                              variant_sync_result['details_o17'] = final_o17_details


                              # Añadir resultado de esta variante al resumen del template
                              template_summary['o17_sync']['variants_synced'].append(variant_sync_result)


                          else:
                              # --- Variante No Encontrada en Odoo 17 ---
                              counters['o17_variants_created'] += 1
                              variant_sync_result['status'] = 'created'
                              _logger.info(f"    Variante Odoo 17 con clave PTAVs {variant_key_ptavs} no encontrada. Creando...")

                              # Valores para crear: essential + campos según sync_fields_mode
                              create_vals = {**essential_vals, **vals_to_sync}
                              # Asegurarse de que 'active' está en create_vals si la variante O16 está inactiva
                              if 'active' not in create_vals and 'active' in all_fields_vals: # Usa all_fields_vals como fuente del valor original de O16
                                   create_vals['active'] = all_fields_vals['active']
                              # Asegurarse de que 'image_1920' está si hay imagen en O16
                              if 'image_1920' not in create_vals and 'image_1920' in all_fields_vals: # Usa all_fields_vals como fuente del valor original de O16
                                   create_vals['image_1920'] = all_fields_vals['image_1920']


                              _logger.debug(f"    Valores para crear variante Odoo 17: {list(create_vals.keys())} (excluyendo M2M)") # No loguear valores grandes

                              # Intentar crear
                              new_variant_id = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'create', [create_vals])

                              if new_variant_id and isinstance(new_variant_id, int):
                                   _logger.info(f"    ✔ Variante creada en Odoo 17 con ID: {new_variant_id} (Clave PTAVs: {variant_key_ptavs})")
                                   processed_o17_variant_ids.add(new_variant_id) # Marcar como procesada
                                   variant_sync_result['o17_id'] = new_variant_id
                                   # Guardar detalles de creación para el resumen
                                   # Usar los valores que intentamos crear
                                   final_o17_details = {f: create_vals.get(f) for f in fields_to_check_and_sync if f in create_vals}
                                   # Añadir campos clave al resumen
                                   final_o17_details['code'] = create_vals.get('default_code')
                                   final_o17_details['barcode'] = create_vals.get('barcode')
                                   final_o17_details['legacy'] = create_vals.get('legacy_default_code')
                                   final_o17_details['price'] = create_vals.get('lst_price')
                                   final_o17_details['active'] = create_vals.get('active', True)
                                   final_o17_details['image_1920'] = create_vals.get('image_1920') # Puede ser False

                                   variant_sync_result['details_o17'] = final_o17_details

                              elif new_variant_id is None and 'barcode' in create_vals and create_vals['barcode']:
                                   # Manejo específico de fallo al crear con barcode (posible duplicado)
                                   variant_sync_result['status'] = 'create_failed (barcode?)'
                                   _logger.warning(f"    -> Fallo al crear variante Odoo 17 con clave {variant_key_ptavs}, posible conflicto de barcode '{create_vals['barcode']}'. Reintentando creación sin el campo 'barcode'...")
                                   create_vals_no_barcode = create_vals.copy()
                                   create_vals_no_barcode['barcode'] = False # Intentar crear sin barcode

                                   new_variant_id_no_barcode = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'create', [create_vals_no_barcode])

                                   if new_variant_id_no_barcode and isinstance(new_variant_id_no_barcode, int):
                                        _logger.info(f"      ✔ Variante creada en Odoo 17 con ID: {new_variant_id_no_barcode} (Clave: {variant_key_ptavs}, SIN BARCODE)")
                                        processed_o17_variant_ids.add(new_variant_id_no_barcode) # Marcar como procesada
                                        variant_sync_result['status'] = 'created (no barcode)' # Estado más específico
                                        variant_sync_result['o17_id'] = new_variant_id_no_barcode
                                        # Guardar detalles de creación para el resumen (sin barcode)
                                        final_o17_details = {f: create_vals_no_barcode.get(f) for f in fields_to_check_and_sync if f in create_vals_no_barcode}
                                        # Añadir campos clave al resumen (sin barcode)
                                        final_o17_details['code'] = create_vals_no_barcode.get('default_code')
                                        final_o17_details['barcode'] = False # El barcode falló en la creación
                                        final_o17_details['legacy'] = create_vals_no_barcode.get('legacy_default_code')
                                        final_o17_details['price'] = create_vals_no_barcode.get('lst_price')
                                        final_o17_details['active'] = create_vals_no_barcode.get('active', True)
                                        final_o17_details['image_1920'] = create_vals_no_barcode.get('image_1920') # Puede ser False

                                        variant_sync_result['details_o17'] = final_o17_details


                                   else:
                                        _logger.error(f"      -> Fallo al crear variante Odoo 17 con clave {variant_key_ptavs} incluso sin barcode. Resultado: {new_variant_id_no_barcode}")

                              elif new_variant_id is False:
                                   variant_sync_result['status'] = 'create_failed (logic)'
                                   _logger.error(f"    -> Fallo lógico al crear variante Odoo 17 con clave {variant_key_ptavs}. Resultado: False.")

                              elif new_variant_id is None:
                                   variant_sync_result['status'] = 'create_failed (rpc)'
                                   _logger.error(f"    -> Fallo RPC/inesperado general al crear variante Odoo 17 con clave {variant_key_ptavs}.")

                              else:
                                   variant_sync_result['status'] = 'create_failed (unknown)'
                                   _logger.warning(f"    -> Resultado inesperado ({new_variant_id}) al crear variante Odoo 17 con clave {variant_key_ptavs}.")

                              # Añadir resultado de esta variante al resumen del template
                              template_summary['o17_sync']['variants_synced'].append(variant_sync_result)


                    # --- Limpieza de Variantes Obsoletas en Odoo 17 ---
                    # Las variantes existentes en Odoo 17 para este template que no fueron encontradas
                    # y procesadas desde Odoo 16 (no están en processed_o17_variant_ids) se consideran obsoletas.
                    # NOTA: Si o16_variants_data estaba vacío, o17_variant_ids_to_clean_potential contendrá todas
                    # las variantes existentes en O17, y processed_o17_variant_ids estará vacío,
                    # por lo que TODAS las variantes de O17 para este template se marcarán como obsoletas.
                              o17_variant_ids_to_clean = o17_variant_ids_to_clean_potential - processed_o17_variant_ids

                              if o17_variant_ids_to_clean:
                                   _logger.info(f"  Encontradas {len(o17_variant_ids_to_clean)} variantes obsoletas en Odoo 17 para template ID {o17_template_id}. Obteniendo detalles para archivado...")
                                   # Leer detalles de las variantes obsoletas (solo id, active, code, barcode para el log/resumen)
                                   o17_variants_to_clean_details = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'read', list(o17_variant_ids_to_clean), {'fields': ['id', 'active', 'default_code', 'barcode']})

                                   if o17_variants_to_clean_details is None:
                                        _logger.error(f"  Fallo RPC leer detalles vars obsoletas {list(o17_variant_ids_to_clean)}. No se realizará el archivado.")

                                   elif o17_variants_to_clean_details:
                                        o17_ids_to_archive = []
                                        template_summary['o17_sync']['variants_archived'] = [] # Inicializar lista de variantes archivadas para este template

                                        for v_clean in o17_variants_to_clean_details:
                                             # Si la variante obsoleta está activa en Odoo 17, marcar para archivar
                                             if v_clean.get('active', True):
                                                  o17_ids_to_archive.append(v_clean['id'])

                                             # Añadir a la lista de archivadas en el resumen (independientemente de si estaban activas o no)
                                             template_summary['o17_sync']['variants_archived'].append({
                                                  'id': v_clean['id'],
                                                  'code': v_clean.get('default_code'),
                                                  'barcode': v_clean.get('barcode')
                                                  })

                                        if o17_ids_to_archive:
                                             counters['o17_variants_archived'] += len(o17_ids_to_archive)
                                             _logger.info(f"  Archivando {len(o17_ids_to_archive)} vars obsoletas activas en Odoo 17 ({o17_ids_to_archive})...")
                                             archive_success = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'write', [o17_ids_to_archive, {'active': False}])

                                             if archive_success is True:
                                                  _logger.info("    -> Archivado de variantes obsoletas OK.")
                                             elif archive_success is False:
                                                  _logger.error(f"    -> Fallo lógico al archivar variantes obsoletas. Resultado: False.")
                                             elif archive_success is None:
                                                  _logger.error(f"    -> Fallo RPC/inesperado al archivar variantes obsoletas.")
                                             else:
                                                  _logger.warning(f"    -> Resultado inesperado ({archive_success}) al archivar variantes obsoletas.")

                                        else:
                                             _logger.info("  No se encontraron variantes obsoletas activas que archivar para este template.")

                                   else:
                                        _logger.warning(f"  No se obtuvieron detalles para las variantes obsoletas {list(o17_variant_ids_to_clean)} en Odoo 17. No se realizará el archivado.")

                              else:
                                   _logger.info("  No se encontraron variantes obsoletas en Odoo 17 para este tmpl.")

            else: # Si map_success fue False para attrs, no se procesan variantes
                 _logger.error(f"Sincronización de atributos falló o fue incompleta para template '{template_default_code_to_sync}'. Saltando sincronización de variantes.")
                 template_summary['processed_ok'] = False # Marcar fallo para este template si no se marcó antes


            # --- Final del bloque TRY para el procesamiento de UN template específico ---
        except Exception as template_error:
            # Captura cualquier error NO manejado dentro del procesamiento de UN template específico
            _logger.error(f"Error CRÍTICO no controlado durante el procesamiento del template '{template_default_code_to_sync}': {template_error}", exc_info=True)
            template_summary['processed_ok'] = False # Marcar como fallido

            # Incrementar contador de templates fallidos
            # Solo contar si el template fue encontrado/identificado (o16_template_id existe o el code fuente era válido)
            if o16_template_id or ('o16_template_code_cleaned' in locals() and o16_template_code_cleaned):
                 counters['templates_failed'] += 1


            # --- Actualizar estado del item en la DB a 'failed' ---
            # Capturar el mensaje de error para guardarlo
            error_msg = str(template_error) # Convertir la excepción a string
            # Usar el código original de la DB como clave para la actualización
            item_code_in_db = item_db['code']
            try:
                update_item_status(db_path, item_code_in_db, 'failed', error_message=error_msg[:500], o16_id=o16_template_id_db, o17_id=o17_template_id if 'o17_template_id' in locals() else None) # Limitar a 500 chars para la DB
                _logger.debug(f"Item '{item_code_in_db}' marcado como 'failed' en DB.")
            except Exception as db_update_error:
                _logger.error(f"Error CRÍTICO al marcar item '{item_code_in_db}' como 'failed' en DB: {db_update_error}", exc_info=True)
                # Este fallo al actualizar la DB es CRITICO para la reanudación, marcar error general
                # script_status = "ERROR" # No podemos cambiar script_status desde sync_logic


        # --- ESCRITURA FINAL GARANTIZADA DEL DEFAULT_CODE DEL TEMPLATE ---
        # Este bloque se hace siempre que tengamos un Odoo 17 template ID válido
        # para asegurar que el default_code original de Odoo 17 no se pierda
        # si Odoo lo resetea al actualizar atributos.
        # NOTA: Este bloque se ejecuta incluso si el try falló, siempre que o17_template_id tenga valor.
        # o17_template_id solo tendrá valor si la búsqueda o creación en O17 tuvo éxito (paso 2)
        if 'o17_template_id' in locals() and o17_template_id and o17_template_id in initial_o17_default_codes:
            code_to_force = initial_o17_default_codes[o17_template_id]
            _logger.info(f"  [FINAL WRITE CHECK] Verificando default_code final en Odoo 17 ID {o17_template_id}. El código original era: '{code_to_force}'")

            # Leer el código y nombre actuales por si cambiaron justo al final
            final_read_data = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'read', [o17_template_id], {'fields': ['default_code', 'name']})
            if final_read_data and isinstance(final_read_data, list) and final_read_data:
                 final_data_dict = final_read_data[0]
                 final_current_code_after_template_loop = final_data_dict.get('default_code', False) # Usar un nombre de variable diferente
                 # Actualizar el nombre final en el resumen por si se cambió al final
                 template_summary['o17_sync']['name_final'] = final_data_dict.get('name')
                 # Guardar el código final actual de Odoo 17 en el resumen
                 template_summary['o17_sync']['default_code_final'] = final_current_code_after_template_loop

                 _logger.info(f"  [FINAL WRITE CHECK] default_code actual ANTES de la escritura final: '{final_current_code_after_template_loop}'")

                 if final_current_code_after_template_loop != code_to_force:
                      _logger.warning(f"  [FINAL WRITE] default_code actual ('{final_current_code_after_template_loop}') difiere del código inicial guardado ('{code_to_force}'). Forzando escritura para restaurar.")
                      final_write_ok = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'write', [[o17_template_id], {'default_code': code_to_force}])
                      if final_write_ok is True:
                           _logger.info(f"    -> Escritura final del default_code '{code_to_force}' en Odoo 17 OK.")
                           template_summary['o17_sync']['default_code_final'] = code_to_force # Actualizar el resumen con el valor forzado
                           # Re-read to confirm restoration?
                           final_code_check_data = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'read', [o17_template_id], {'fields': ['default_code']})
                           final_code_after_fix = final_code_check_data[0].get('default_code') if final_code_check_data and isinstance(final_code_check_data, list) and final_code_check_data else 'ERROR_READ'
                           _logger.info(f"  [FINAL CHECK Fix] default_code post-restauración: '{final_code_after_fix}'")
                      else:
                           _logger.error(f"    -> FALLO al forzar la escritura final del default_code '{code_to_force}' en Odoo 17. Resultado: {final_write_ok}")
                           template_summary['o17_sync']['default_code_final'] = final_current_code_after_template_loop # Si falló, mantener el que quedó

                 else:
                      _logger.info(f"  [FINAL WRITE CHECK] default_code actual ('{final_current_code_after_template_loop}') ya coincide con el código inicial guardado. No se necesita escritura final.")

            else:
                 _logger.error(f"  [FINAL WRITE CHECK] No se pudo leer el default_code actual en Odoo 17 ID {o17_template_id} para la verificación final. No se forzará la escritura.")
                 template_summary['o17_sync']['default_code_final'] = 'N/A (Error lectura final?)'

        elif 'o17_template_id' in locals() and o17_template_id:
             # Si tenemos un ID de Odoo 17 pero no teníamos un código inicial guardado (ej. por fallo muy temprano)
             template_summary['o17_sync']['default_code_final'] = 'N/A (Sin código inicial guardado)'
             _logger.warning(f"  [FINAL WRITE CHECK] No se guardó el default_code inicial para Odoo 17 ID {o17_template_id}. Saltando verificación y escritura final del código.")


        # --- Si no hubo error crítico en el try, marcar item como 'succeeded' ---
        # Esto solo se ejecuta si el try no lanzó una excepción para ESTE template.
        # El estado ya se marcó como 'failed' en el except si hubo error.
        # Si llegó aquí y processed_ok sigue True, fue exitoso para este template.
        # Usar el código original de la DB como clave para la actualización
        item_code_in_db = item_db['code']
        if template_summary['processed_ok']:
            # Asegurarse de que el estado en la DB no sea ya 'failed' por si hubo un error de DB *antes* del try/except
            # (menos probable, pero posible)
            try:
                 update_item_status(db_path, item_code_in_db, 'succeeded', o16_id=o16_template_id, o17_id=o17_template_id)
                 _logger.debug(f"Item '{item_code_in_db}' marcado como 'succeeded' en DB.")
            except Exception as db_update_error:
                 _logger.error(f"Error CRÍTICO al marcar item '{item_code_in_db}' como 'succeeded' en DB: {db_update_error}", exc_info=True)
                 # Este fallo al actualizar la DB es CRITICO para la reanudación, marcar error general
                 # script_status = "ERROR" # No podemos cambiar script_status desde sync_logic

        elif template_summary['processed_ok'] is False and 'o16_template_code_cleaned' in locals() and o16_template_code_cleaned:
             # Si falló el procesamiento PERO sí se encontró el template en O16
             _logger.warning(f"Template '{o16_template_code_cleaned}' (ID O16: {o16_template_id}, ID O17: {o17_template_id if 'o17_template_id' in locals() else 'N/A'}) tuvo fallos durante el procesamiento principal.")
             # El estado 'failed' ya debería haberse marcado en el except. No hacemos nada más aquí.
        elif template_summary['processed_ok'] is False and not ('o16_template_code_cleaned' in locals() and o16_template_code_cleaned):
            # Si falló el procesamiento Y NO se encontró el template en O16 (o su código era inválido)
             _logger.warning(f"Template '{template_default_code_to_sync}' no fue encontrado en Odoo 16 o tuvo código inválido. Fue saltado o falló muy temprano.")
             # El estado 'skipped' o 'failed' ya debería haberse marcado. No hacemos nada más aquí.


        # --- Guardar resumen de este template para la salida de consola de ESTA ejecución ---
        # Este resumen se usa para la impresión FINAL en main_sync.
        summary_data.append(template_summary)


        _logger.info(f"\n--- Finalizado procesamiento de Template con default_code: {template_default_code_to_sync} ---")


    # --- FIN Bucle Principal de Templates ---

    _logger.info(f" Fin Lógica de Sincronización ".center(60, '-'))


    # Devolver datos para el resumen final a main_sync.py
    return summary_data, counters