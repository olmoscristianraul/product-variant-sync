# -*- coding: utf-8 -*-
import logging
import os
import sys # Necesario para sys.argv[0] en load_codes_to_process
import time
import math # Importar módulo math para comparación de floats

# Importar funciones del módulo RPC y configuración
from odoo_xmlrpc import (
    execute_odoo_kw, find_record,
    get_or_create_attribute, get_or_create_attribute_value
    # No necesitamos importar connect_odoo aquí, ya se pasa como argumento a sync_products
)
# Importar el diccionario de configuración (necesario para load_codes_to_process)
# from config_loader import config_values # config_values se pasa como argumento a sync_products

# La variable global script_status ya NO se gestiona aquí.
# La gestionará main_sync.py basándose en el retorno o excepciones.

def load_codes_to_process(csv_filename, models_o16, uid_o16, o16_db, o16_pass):
    """ Lee códigos desde CSV. Si es '*', busca todos en Odoo 16. """
    codes = []; logging.info(f"Intentando leer códigos desde: {csv_filename}")
    try:
        # Asume que el CSV está en el mismo directorio que el script principal que lo ejecuta
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0])) # Obtener dir del script principal
        file_path = os.path.join(script_dir, csv_filename)

        with open(file_path, 'r', encoding='utf-8') as f:
             line = f.readline().strip()

        if not line:
            logging.warning(f"Archivo CSV '{csv_filename}' vacío.")
            return []

        if line == '*':
            logging.info("'*' encontrado. Obteniendo todos default_code activos o16...")
            domain_all = [('default_code', '!=', False), ('default_code', '!=', ''), ('active', '=', True)]
            fields_all = ['default_code']
            # Usar los parámetros pasados para la conexión Odoo 16
            all_templates_data = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.template', 'search_read', [domain_all], {'fields': fields_all})

            if all_templates_data is None:
                logging.error("Fallo RPC obtener todos default_codes O16.")
                return []

            codes_from_o16 = set()
            for template in all_templates_data:
                code = template.get('default_code')
                if isinstance(code, str) and code.strip():
                    codes_from_o16.add(code.strip())
                elif code and not isinstance(code, str):
                     logging.warning(f"default_code no-string no-False o16: {code}. Ignorado.")

            codes = sorted(list(codes_from_o16))
            logging.info(f"Se procesarán {len(codes)} códigos (desde O16).")
            if not codes:
                 logging.warning("Consulta O16 no devolvió códigos válidos con default_code.")

        else:
            logging.info("Procesando códigos específicos desde CSV...")
            raw_codes = line.split(',')
            codes = [code.strip() for code in raw_codes if code.strip()]
            logging.info(f"Se procesarán {len(codes)} códigos (desde CSV).")
            if not codes:
                logging.warning("No se encontraron códigos válidos en el archivo CSV.")

    except FileNotFoundError:
        logging.error(f"Error: No se encontró archivo CSV '{csv_filename}' (buscado en {script_dir}).")
        return []
    except Exception as e:
        logging.error(f"Error inesperado al leer/procesar CSV '{csv_filename}': {e}", exc_info=True)
        return []
    return codes

def sync_products(o16_conn, o17_conn, config):
    """Función principal que orquesta la sincronización."""
    logging.info(" Iniciando Lógica de Sincronización ".center(60, '-'))

    models_o16, uid_o16 = o16_conn
    models_o17, uid_o17 = o17_conn
    # Acceder a la configuración pasada como argumento
    o16_pass = config.get('O16_PASS'); o17_pass = config.get('O17_PASS')
    o16_db = config.get('O16_DB'); o17_db = config.get('O17_DB')
    csv_filename = config.get('CSV_FILENAME')

    # Pasar parámetros de conexión a load_codes_to_process
    codes_to_process = load_codes_to_process(csv_filename, models_o16, uid_o16, o16_db, o16_pass)
    if not codes_to_process:
        logging.warning("No hay códigos para procesar. Finalizando lógica de sincronización.");
        # Devolver datos vacíos pero válidos para el resumen
        return [], {}

    logging.info(f"Códigos a procesar: {codes_to_process}")
    initial_o17_default_codes = {} # Para preservar el default_code en O17 template
    summary_data = []
    counters = {
        'templates_analyzed': 0,
        'o16_variants_found': 0,
        'o17_variants_existing': 0,
        'o17_variants_created': 0,
        'o17_variants_updated': 0,
        'o17_variants_archived': 0,
        'templates_failed': 0, # Contador de templates que fallaron completamente
    }

    for template_default_code_to_sync in codes_to_process:
        logging.info(f"\n--- Procesando Template con default_code: {template_default_code_to_sync} ---")
        counters['templates_analyzed'] += 1
        o17_template_id = None
        map_success = True # Flag para saber si hubo fallos críticos en mapeo de atributos/valores
        template_summary = {
            'code': template_default_code_to_sync,
            'o16_data': {'id': None, 'name': None, 'attributes': [], 'variants': []},
            'o17_sync': {'id': None, 'name_final': None, 'default_code_final': None, 'status': 'ok', 'variants_synced': [], 'variants_archived': []},
            'processed_ok': True # Estado general de este template
        }

        try:
            # 1. Buscar FUENTE O16 (product.template)
            logging.info(f"Buscando template '{template_default_code_to_sync}' en Odoo 16...")
            domain_o16_template = [('default_code', '=', template_default_code_to_sync)]
            fields_o16_template = ['id', 'name', 'default_code', 'attribute_line_ids', 'product_variant_ids', 'active', 'image_1920']
            o16_template_data_list = find_record(models_o16, o16_db, uid_o16, o16_pass, 'product.template', domain_o16_template, fields_o16_template, limit=1)

            if o16_template_data_list is None:
                 raise Exception(f"Fallo RPC al buscar template '{template_default_code_to_sync}' en Odoo 16.") # Error RPC es fatal para este template

            if not o16_template_data_list:
                logging.warning(f"Template '{template_default_code_to_sync}' no encontrado en Odoo 16. Saltando procesamiento.");
                template_summary['processed_ok'] = False # Marcar como no procesado OK
                counters['templates_failed'] += 1
                summary_data.append(template_summary)
                continue # Saltar al siguiente código en el CSV

            o16_template = o16_template_data_list[0]
            o16_template_id = o16_template['id']
            template_summary['o16_data']['id'] = o16_template_id # Registrar ID O16
            o16_template_code = o16_template.get('default_code')
            o16_template_name = o16_template.get('name')
            o16_is_active = o16_template.get('active', True)
            o16_image_1920 = o16_template.get('image_1920')

            template_summary['o16_data']['name'] = o16_template_name # Registrar nombre O16

            o16_template_code_cleaned = (o16_template_code or '').strip()
            if not o16_template_code_cleaned and o16_template_code is not False:
                 logging.warning(f"Code de template FUENTE en Odoo 16 ID {o16_template_id} está vacío ('{o16_template_code}').")


            logging.info(f"Fuente Odoo 16: ID {o16_template_id}, Name: '{o16_template_name}', Code: '{o16_template_code}', Active: {o16_is_active}, Has Image: {bool(o16_image_1920)}")

            # 1b. Leer Atributos/Valores O16 para resumen (solo lectura, no mapeo aún)
            o16_attribute_line_ids = o16_template.get('attribute_line_ids', [])
            if o16_attribute_line_ids:
                o16_lines_details = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.template.attribute.line', 'read', o16_attribute_line_ids, {'fields': ['attribute_id', 'value_ids']})
                if o16_lines_details is None:
                     # Loguear fallo RPC pero intentar continuar con lo posible
                     logging.warning(f"Fallo RPC al leer detalles de attribute_line_ids {o16_attribute_line_ids} desde Odoo 16. Algunos detalles del resumen podrían faltar.")
                elif o16_lines_details:
                    all_value_ids = list(set(vid for line in o16_lines_details for vid in line.get('value_ids',[])))
                    value_names = {}
                    if all_value_ids:
                         o16_values_names = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.attribute.value', 'read', all_value_ids, {'fields': ['id', 'name']})
                         if o16_values_names is None:
                             logging.warning(f"Fallo RPC al leer nombres de valores de atributo {all_value_ids} desde Odoo 16. Algunos detalles del resumen podrían faltar.")
                         elif o16_values_names:
                             value_names = {v['id']: v['name'] for v in o16_values_names}

                    for line in o16_lines_details:
                        attr_name = line.get('attribute_id', [None, 'N/A'])[1] # [ID, Name] tuple or list
                        value_ids_in_line = line.get('value_ids', [])
                        value_names_in_line = [value_names.get(vid, f'ID:{vid}') for vid in value_ids_in_line]
                        template_summary['o16_data']['attributes'].append(f"{attr_name}: {', '.join(value_names_in_line)}")
                else:
                    logging.warning(f"No se obtuvieron detalles para attribute_line_ids {o16_attribute_line_ids} desde Odoo 16.")


            # 2. Buscar/Crear DESTINO O17 (product.template)
            logging.info(f"Buscando template '{template_default_code_to_sync}' en Odoo 17...")
            domain_o17_template = [('default_code', '=', template_default_code_to_sync), ('active', 'in', [True, False])] # Buscar también inactivos
            fields_o17_template = ['id', 'name', 'default_code', 'attribute_line_ids', 'active', 'image_1920'] # Fields para el template destino
            o17_template_data_list = find_record(models_o17, o17_db, uid_o17, o17_pass, 'product.template', domain_o17_template, fields_o17_template, limit=1)

            existing_o17_attr_line_ids = []
            o17_template_data = None

            if o17_template_data_list is None:
                 raise Exception(f"Fallo RPC al buscar template DESTINO '{template_default_code_to_sync}' en Odoo 17.") # Error RPC es fatal para este template

            if o17_template_data_list:
                # --- ENCONTRADO EN O17 ---
                o17_template_data = o17_template_data_list[0]
                o17_template_id = o17_template_data['id']
                template_summary['o17_sync']['id'] = o17_template_id # Registrar ID O17

                current_o17_name = o17_template_data.get('name')
                current_o17_code = o17_template_data.get('default_code')
                current_o17_active = o17_template_data.get('active', True)
                existing_o17_attr_line_ids = o17_template_data.get('attribute_line_ids', [])
                current_o17_image = o17_template_data.get('image_1920')

                template_summary['o17_sync']['name_final'] = current_o17_name # Registrar nombre actual O17

                # Guardar el default_code inicial de Odoo 17 para forzarlo al final
                initial_o17_default_codes[o17_template_id] = current_o17_code
                logging.info(f"  [PRESERVE INITIAL] Guardando default_code inicial de Odoo 17 ID {o17_template_id}: '{current_o17_code}'")


                logging.info(f"Destino Odoo 17: Template existe con ID {o17_template_id}.")
                logging.info(f"  Actual Odoo 17 -> Name: '{current_o17_name}', Code: '{current_o17_code}', Active: {current_o17_active}, Has Image: {bool(current_o17_image)}")
                logging.info(f"  Odoo 16 Fuente -> Name: '{o16_template_name}', Code: '{o16_template_code_cleaned}', Active: {o16_is_active}, Has Image: {bool(o16_image_1920)}")


                # Comparar y preparar valores para actualizar el template O17
                template_update_vals = {}
                if (current_o17_name or '').strip() != (o16_template_name or '').strip():
                     template_update_vals['name'] = o16_template_name or ''
                     logging.info(f"  -> Nombre difiere. Se actualizará.")
                if current_o17_active != o16_is_active:
                     template_update_vals['active'] = o16_is_active
                     logging.info(f"  -> Estado Active difiere. Se actualizará.")
                # Sincronizar imagen solo si existe en O16 y difiere o no existe en O17
                if o16_image_1920 and o16_image_1920 != current_o17_image:
                    template_update_vals['image_1920'] = o16_image_1920
                    logging.info(f"  -> Imagen difiere o está presente en Odoo 16 pero no en Odoo 17. Se actualizará.")
                elif o16_image_1920:
                    logging.debug(f"  -> Imagen idéntica. No se actualizará.")
                # Nota: default_code no se actualiza aquí, se fuerza al final


                if template_update_vals:
                    logging.info(f"  Actualizando campos iniciales del template Odoo 17 ID {o17_template_id}: {list(template_update_vals.keys())}")
                    write_ok = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'write', [[o17_template_id], template_update_vals])
                    if write_ok is True:
                        logging.info(f"  -> Update inicial del template Odoo 17 OK.")
                        # Actualizar el nombre en el resumen si se actualizó
                        template_summary['o17_sync']['name_final'] = template_update_vals.get('name', current_o17_name)
                    elif write_ok is False:
                         logging.error(f"  -> Fallo lógico al actualizar campos iniciales del template Odoo 17 ID {o17_template_id}. Resultado: False.")
                         # No fatal para todo el script, pero sí para este template si falla algo importante?
                         # Decidimos que no es fatal para la sync de variantes, pero lo logueamos como error.
                    elif write_ok is None:
                         raise Exception("Fallo RPC al actualizar campos iniciales del template Odoo 17.") # Error RPC es fatal para este template
                    else:
                         logging.warning(f"  -> Resultado inesperado ({write_ok}) al actualizar campos iniciales del template Odoo 17 ID {o17_template_id}.")

                else:
                    logging.info(f"  -> Template Odoo 17 ID {o17_template_id} ya sincronizado (nombre/activo/imagen). No se necesita update inicial.")

            else:
                # --- NO ENCONTRADO EN O17 ---
                logging.info(f"Template '{template_default_code_to_sync}' no existe en Odoo 17. Creando...")
                # Usar el código de Odoo 16 si existe, de lo contrario False (Odoo lo dejará vacío)
                code_for_create = o16_template_code_cleaned if o16_template_code_cleaned else False
                template_vals_to_create = {
                    'name': o16_template_name or '', # Nombre de Odoo 16
                    'default_code': code_for_create,
                    'type': 'product', # Por defecto 'product'
                    'active': o16_is_active, # Estado activo de Odoo 16
                    'attribute_line_ids': [], # Las líneas se añadirán/actualizarán después
                    'image_1920': o16_image_1920 or False # Imagen de Odoo 16
                }
                logging.debug(f"  Valores para crear template Odoo 17: {template_vals_to_create.keys()}") # No loguear valores grandes como imagen

                new_o17_template_id = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'create', [template_vals_to_create])

                if new_o17_template_id and isinstance(new_o17_template_id, int):
                    o17_template_id = new_o17_template_id
                    template_summary['o17_sync']['id'] = o17_template_id # Registrar ID O17

                    created_code = template_vals_to_create.get('default_code')
                    logging.info(f"  Template creado en Odoo 17 con ID: {o17_template_id} y code '{created_code}'.")

                    # Guardar el default_code inicial (el que se usó para crear) para forzarlo al final
                    initial_o17_default_codes[o17_template_id] = created_code
                    logging.info(f"  [PRESERVE INITIAL] Guardando default_code inicial (creado) de Odoo 17 ID {o17_template_id}: '{created_code}'")


                    # Opcional: leer el template recién creado para confirmar campos y obtener attribute_line_ids
                    o17_template_data_list_after_create = find_record(models_o17, o17_db, uid_o17, o17_pass, 'product.template', [('id', '=', o17_template_id)], fields_o17_template, limit=1)
                    if o17_template_data_list_after_create:
                         o17_template_data = o17_template_data_list_after_create[0]
                         existing_o17_attr_line_ids = o17_template_data.get('attribute_line_ids', [])
                         template_summary['o17_sync']['name_final'] = o17_template_data.get('name') # Registrar nombre final O17
                         logging.debug(f"  Leído template Odoo 17 ID {o17_template_id} después de crear. Current name: '{o17_template_data.get('name')}', Current code: '{o17_template_data.get('default_code')}'")

                    else:
                         # Esto sería raro si el create fue OK, pero es un fallo de lectura
                         raise Exception("Fallo RPC al leer el template Odoo 17 después de crearlo.") # Fatal para este template


                else:
                     # Fallo al crear el template
                     raise Exception(f"Fallo al crear template en Odoo 17. Resultado RPC: {new_o17_template_id}") # Fatal para este template


            # Asegurarse de que tenemos un ID de Odoo 17 válido antes de continuar
            if not o17_template_id:
                 # Esto no debería pasar si los bloques anteriores manejan los errores correctamente,
                 # pero es una salvaguarda.
                 raise Exception("ID de template Odoo 17 no válido después de intentar encontrarlo/crearlo.") # Fatal para este template


            # 3. Sincronización Atributos/Valores
            # Este paso sincroniza las líneas de atributo y los valores.
            # Los atributos y valores se crean on-the-fly en get_or_create_attribute/value.
            # Las líneas de atributo en el template de Odoo 17 se crean/actualizan/eliminan
            # para que coincidan con las de Odoo 16.

            logging.info(f"Sincronizando atributos y líneas para template Odoo 17 ID {o17_template_id}...")

            o16_attribute_line_ids = o16_template.get('attribute_line_ids', [])
            existing_o17_lines_map = {} # Mapea O17 attr_id a O17 attribute_line_id

            # Obtener líneas existentes en Odoo 17 para mapearlas por atributo
            if existing_o17_attr_line_ids:
                o17_existing_lines_details = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template.attribute.line', 'read', existing_o17_attr_line_ids, {'fields': ['id', 'attribute_id']})
                if o17_existing_lines_details is None:
                     logging.warning(f"Fallo RPC al leer líneas de atributo existentes {existing_o17_attr_line_ids} en Odoo 17. Procediendo, pero el cleanup podría no ser perfecto.") # No fatal, intentar continuar
                elif o17_existing_lines_details:
                     # Crear el mapeo: {attribute_id: attribute_line_id}
                     existing_o17_lines_map = {line['attribute_id'][0]: line['id'] for line in o17_existing_lines_details if line.get('attribute_id') and isinstance(line['attribute_id'],(list, tuple)) and len(line['attribute_id']) > 0 and isinstance(line['attribute_id'][0], int)}


            o17_attribute_line_commands = [] # Lista de comandos (0,0,vals), (1,id,vals), (2,id)
            o17_pav_mapping = {} # Cache de mapeo O16 PAV ID -> O17 PAV ID
            map_success = True # Resetear flag, si falla el mapeo aquí, no se sincronizan variantes

            if o16_attribute_line_ids:
                # Leer detalles de las líneas de atributo de Odoo 16
                o16_attribute_lines = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.template.attribute.line', 'read', o16_attribute_line_ids, {'fields': ['id', 'attribute_id', 'value_ids']})

                if o16_attribute_lines is None:
                     map_success = False
                     logging.error(f"Fallo RPC al leer detalles de líneas de atributo {o16_attribute_line_ids} en Odoo 16.")
                     raise Exception("Fallo RPC Odoo 16 al leer líneas de atributo") # Fatal para este template

                elif not o16_attribute_lines and o16_attribute_line_ids:
                     logging.warning(f"No se obtuvieron detalles para las líneas de atributo {o16_attribute_line_ids} de Odoo 16.")


                if map_success and o16_attribute_lines: # Continuar solo si la lectura O16 fue OK
                    processed_o17_line_ids_from_o16 = set() # IDs de líneas O17 que se crearon/actualizaron desde O16

                    # Pre-cargar nombres de valores de Odoo 16 para evitar múltiples RPCs por cada valor
                    all_o16_value_ids_needed = list(set(val_id for line in o16_attribute_lines for val_id in line.get('value_ids', [])))
                    o16_value_details_map = {} # Mapea O16 PAV ID -> O16 PAV Name
                    if all_o16_value_ids_needed:
                        o16_all_value_details = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.attribute.value', 'read', all_o16_value_ids_needed, {'fields': ['id', 'name']})
                        if o16_all_value_details is None:
                             map_success = False
                             logging.error(f"  -> Fallo RPC al leer detalles de valores de atributo {all_o16_value_ids_needed} en Odoo 16.")
                             raise Exception("Fallo RPC Odoo 16 al leer valores de atributo") # Fatal para este template
                        elif o16_all_value_details:
                             o16_value_details_map = {val['id']: val['name'] for val in o16_all_value_details if val.get('id') and val.get('name')}


                    if map_success: # Continuar solo si la lectura O16 de valores fue OK
                        # Procesar cada línea de atributo de Odoo 16
                        for o16_line in o16_attribute_lines:
                            o16_line_id = o16_line['id']
                            o16_attribute_info = o16_line.get('attribute_id') # [ID, Name]

                            if not o16_attribute_info or not isinstance(o16_attribute_info,(list, tuple)) or len(o16_attribute_info) < 2:
                                logging.warning(f"Línea de atributo Odoo 16 ID:{o16_line_id} sin información de atributo válida. Saltando.")
                                continue

                            o16_attr_id, o16_attr_name = o16_attribute_info
                            o16_value_ids_for_this_line = o16_line.get('value_ids', [])

                            logging.debug(f"  Procesando línea de atributo Odoo 16 ID {o16_line_id}: Attr '{o16_attr_name}' ({o16_attr_id}), Valores IDs: {o16_value_ids_for_this_line}")

                            # Obtener o crear el atributo correspondiente en Odoo 17
                            o17_attr_id = get_or_create_attribute(models_o17, o17_db, uid_o17, o17_pass, o16_attr_name)

                            if not o17_attr_id:
                                map_success = False # Marcar que el mapeo falló para este template
                                logging.error(f"  -> Fallo al obtener/crear atributo '{o16_attr_name}' en Odoo 17. Esta línea de atributo y sus variantes no se sincronizarán correctamente.")
                                continue # Continuar con la siguiente línea O16, pero map_success=False asegura que las variantes no se procesarán

                            o17_value_ids_for_line = [] # Lista de IDs de valores de atributo en Odoo 17 para esta línea

                            # Procesar cada valor de atributo de Odoo 16 para esta línea
                            if o16_value_ids_for_this_line:
                                for o16_value_id in o16_value_ids_for_this_line:
                                    o16_value_name = o16_value_details_map.get(o16_value_id)
                                    if not o16_value_name:
                                        logging.warning(f"  -> No se encontró nombre para el valor de atributo Odoo 16 ID {o16_value_id}. Saltando este valor.")
                                        continue # Saltar solo este valor, no la línea entera

                                    # Obtener o crear el valor de atributo correspondiente en Odoo 17 (usando cache)
                                    if o16_value_id in o17_pav_mapping:
                                         o17_value_id = o17_pav_mapping[o16_value_id]
                                         logging.debug(f"  -> Valor '{o16_value_name}' (O16 ID {o16_value_id}) encontrado en cache O17 ID: {o17_value_id}")
                                    else:
                                         o17_value_id = get_or_create_attribute_value(models_o17, o17_db, uid_o17, o17_pass, o16_value_name, o17_attr_id)
                                         o17_pav_mapping[o16_value_id] = o17_value_id # Guardar en cache
                                         if o17_value_id:
                                            logging.debug(f"  -> Valor '{o16_value_name}' (O16 ID {o16_value_id}) obtenido/creado en O17 ID: {o17_value_id}")


                                    if o17_value_id:
                                        o17_value_ids_for_line.append(o17_value_id)
                                    else:
                                        map_success = False # Marcar que el mapeo falló para este template
                                        logging.error(f"  -> Fallo al obtener/crear valor '{o16_value_name}' (AttrID O17 {o17_attr_id}) en Odoo 17. Marcando map_success=False.")
                                        # No break, procesar otros valores en la misma línea si es posible


                            # Determinar si la línea de atributo ya existe en Odoo 17 (por atributo)
                            o17_line_id_existing = existing_o17_lines_map.get(o17_attr_id)

                            # Preparar comando de escritura M2M para la línea de atributo
                            # Usamos [(6, 0, [ids])] para reemplazar completamente los valores
                            line_values_m2m = {
                                'attribute_id': o17_attr_id,
                                'value_ids': [(6, 0, sorted(list(set(o17_value_ids_for_line))))] # Usar IDs de Odoo 17, asegurarse únicos y ordenados
                            }

                            if o17_line_id_existing:
                                # Si la línea existe, preparar comando de actualización
                                logging.debug(f"  Preparando comando (1, {o17_line_id_existing}, ...) para actualizar línea de atributo Odoo 17.")
                                o17_attribute_line_commands.append((1, o17_line_id_existing, line_values_m2m))
                                processed_o17_line_ids_from_o16.add(o17_line_id_existing) # Marcar como procesada

                            else:
                                # Si la línea no existe, preparar comando de creación
                                logging.debug(f"  Preparando comando (0, 0, ...) para crear línea de atributo Odoo 17.")
                                o17_attribute_line_commands.append((0, 0, line_values_m2m))

                    # Después de procesar todas las líneas de Odoo 16, identificar las obsoletas en Odoo 17
                    lines_to_delete_in_o17 = [line_id for line_id in existing_o17_attr_line_ids if line_id not in processed_o17_line_ids_from_o16]
                    if lines_to_delete_in_o17:
                        logging.info(f"  {len(lines_to_delete_in_o17)} líneas de atributo obsoletas encontradas en Odoo 17. Preparando para eliminar.")
                        for line_id_to_delete in lines_to_delete_in_o17:
                             logging.debug(f"  Preparando comando (2, {line_id_to_delete}) para eliminar línea de atributo Odoo 17.")
                             o17_attribute_line_commands.append((2, line_id_to_delete))

            else:
                # Odoo 16 no tiene líneas de atributo para este template
                logging.info(f"Template Odoo 16 ID {o16_template_id} no tiene líneas de atributo.")
                if existing_o17_attr_line_ids:
                     # Si Odoo 16 no tiene líneas pero Odoo 17 sí, eliminar las de Odoo 17
                     logging.info(f"  Template Odoo 16 no tiene líneas, pero Odoo 17 sí. Preparando para eliminar líneas obsoletas en Odoo 17.")
                     for line_id_to_delete in existing_o17_attr_line_ids:
                          logging.debug(f"  Preparando comando (2, {line_id_to_delete}) para eliminar línea de atributo Odoo 17.")
                          o17_attribute_line_commands.append((2, line_id_to_delete))


            # Aplicar los comandos a la lista attribute_line_ids del template en Odoo 17
            if o17_attribute_line_commands:
                if map_success: # Aplicar cambios solo si el mapeo de atributos/valores fue exitoso
                    logging.info(f"  Aplicando {len(o17_attribute_line_commands)} comandos a 'attribute_line_ids' del template Odoo 17 ID {o17_template_id}...")

                    # Leer el default_code antes de actualizar las líneas (Odoo puede borrarlo)
                    o17_code_before_attr_update = False
                    template_read_result = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'read', [o17_template_id], {'fields': ['default_code']})
                    if template_read_result and isinstance(template_read_result, list) and template_read_result:
                         o17_code_before_attr_update = template_read_result[0].get('default_code', False)
                         logging.info(f"  [PRESERVE CHECK] default_code de Odoo 17 ID {o17_template_id} antes de actualizar attrs: '{o17_code_before_attr_update}'")
                    else:
                         logging.error(f"  [PRESERVE CHECK] No se pudo leer el default_code antes de actualizar attrs. Resultado: {template_read_result}") # No fatal para la sync, pero impide el fix


                    attr_line_update_ok = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'write', [[o17_template_id], {'attribute_line_ids': o17_attribute_line_commands}])

                    if attr_line_update_ok is True:
                        logging.info(f"  -> Update de líneas de atributo Odoo 17 OK.")
                        # Verificar si Odoo borró el default_code y restaurarlo si es necesario
                        o17_code_after_attr_update = False # Initialize variable
                        template_read_result_after = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'read', [o17_template_id], {'fields': ['default_code']})

                        if template_read_result_after and isinstance(template_read_result_after, list) and template_read_result_after:
                             o17_code_after_attr_update = template_read_result_after[0].get('default_code', False) # Assign variable if read succeeds
                             logging.info(f"  [POST CHECK] default_code de Odoo 17 ID {o17_template_id} después de actualizar attrs: '{o17_code_after_attr_update}'")
                             initial_code_for_this_template = initial_o17_default_codes.get(o17_template_id, None)

                             # Logic to check if restoration is needed
                             has_value_after = bool(o17_code_after_attr_update)
                             had_value_initially = bool(initial_code_for_this_template)

                             if not has_value_after and had_value_initially:
                                 logging.warning(f"  -> [FIX DETECTADO] Code perdido después de actualizar attrs (era '{initial_code_for_this_template}', ahora '{o17_code_after_attr_update}'). Intentando restaurar.")
                                 rewrite_code_ok = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'write', [[o17_template_id], {'default_code': initial_code_for_this_template}])
                                 if rewrite_code_ok is True:
                                     logging.info(f"  -> [FIX] Restauración de default_code a '{initial_code_for_this_template}' OK.")
                                     # Re-read to confirm restoration?
                                     final_code_check_data = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'read', [o17_template_id], {'fields': ['default_code']})
                                     final_code_after_fix = final_code_check_data[0].get('default_code') if final_code_check_data and isinstance(final_code_check_data, list) and final_code_check_data else 'ERROR_READ'
                                     logging.info(f"  [FINAL CHECK Fix] default_code post-restauración: '{final_code_after_fix}'")
                                 else:
                                     logging.error(f"  -> [FIX] Fallo al restaurar default_code a '{initial_code_for_this_template}'. Resultado: {rewrite_code_ok}") # No fatal?
                             elif has_value_after and o17_code_after_attr_update != initial_code_for_this_template:
                                # If it has a value, but it's different from the original
                                logging.warning(f"  -> [CHECK] default_code cambió de '{initial_code_for_this_template}' a '{o17_code_after_attr_update}' después de actualizar attrs, pero no fue borrado completamente.")
                             elif has_value_after and o17_code_after_attr_update == initial_code_for_this_template:
                                 # ---> CAMBIO: Corregir UnboundLocalError - Usar la variable correcta aquí
                                 logging.info(f"  [CHECK] default_code ('{o17_code_after_attr_update}') ya coincide con el inicial. No se necesitó restauración.")
                                 # Fin CAMBIO UnboundLocalError


                        else:
                             logging.error(f"  [POST CHECK] No se pudo leer el default_code después de actualizar attrs. No se pudo verificar si se perdió.") # No fatal


                    elif attr_line_update_ok is False:
                         logging.error(f"  -> Fallo lógico al actualizar líneas de atributo del template Odoo 17 ID {o17_template_id}. Resultado: False.")
                         map_success = False # Marcar fallo, no procesar variantes

                    elif attr_line_update_ok is None:
                         logging.error(f"  -> Fallo RPC/inesperado al actualizar líneas de atributo del template Odoo 17 ID {o17_template_id}.")
                         map_success = False # Marcar fallo, no procesar variantes
                         raise Exception("Fallo RPC Odoo 17 al actualizar líneas de atributo") # Fatal para este template
                    else:
                         logging.warning(f"  -> Resultado inesperado ({attr_line_update_ok}) al actualizar líneas de atributo del template Odoo 17 ID {o17_template_id}.")
                         map_success = False # Considerar fallo, no procesar variantes

                elif not map_success:
                    logging.warning("  Mapeo de atributos/valores falló antes. No se aplicarán comandos a las líneas de atributo.")

            elif not o17_attribute_line_commands:
                logging.info("  No hay comandos para aplicar a las líneas de atributo en Odoo 17.")


            # 4. Sincronización de Variantes (product.product)
            # Este paso crea/actualiza/elimina (archiva) las variantes en Odoo 17
            # para que coincidan con las de Odoo 16, basándose en el mapeo de PTAVs.

            if map_success: # Proceder solo si el mapeo de atributos/valores fue exitoso
                logging.info(f"Iniciando sincronización de variantes para template Odoo 17 ID {o17_template_id}...")

                # --- OBTENER VARIANTE(S) DE ODOO 16 ---
                # Para productos con atributos, product_variant_ids contiene los IDs de las variantes.
                # Para productos sin atributos (plantilla = variante), product_variant_ids es [template_id].
                # fields_o16_variant_read = ['id', 'default_code', 'legacy_default_code', 'barcode', 'product_template_attribute_value_ids', 'active', 'image_1920']
                # ---> CAMBIO: Sincronización lst_price - Añadir 'lst_price' a los campos a leer de Odoo 16 product.product
                fields_o16_variant_read = ['id', 'default_code', 'legacy_default_code', 'barcode', 'product_template_attribute_value_ids', 'active', 'image_1920', 'lst_price']
                # Fin CAMBIO lst_price

                o16_variant_ids = o16_template.get('product_variant_ids', [])

                # Manejar el caso especial de productos sin atributos donde la plantilla es la variante
                is_simple_product_o16 = not o16_attribute_line_ids and len(o16_variant_ids) == 1 and o16_variant_ids[0] == o16_template_id

                if not o16_attribute_line_ids and not o16_variant_ids:
                    # Caso de producto simple recién creado en Odoo 16 que aún no tiene variantes en product_variant_ids
                    logging.info(f"  Template Odoo 16 ID {o16_template_id} parece ser un producto simple (sin líneas attr). Asumiendo ID {o16_template_id} es la variante.")
                    is_simple_product_o16 = True
                    o16_variant_ids = [o16_template_id]


                if not o16_variant_ids:
                    logging.info("  Template Odoo 16 sin variantes encontradas. Saltando sincronización de variantes.")
                else:
                    o16_variants_data = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.product', 'read', o16_variant_ids, {'fields': fields_o16_variant_read})

                    if o16_variants_data is None:
                        logging.error(f"Fallo RPC al leer datos de variantes {o16_variant_ids} desde Odoo 16. Saltando sincronización de variantes para este template.")
                        # No fatal para todo el script, pero sí para este template
                        template_summary['processed_ok'] = False # Este template falló parcialmente/totalmente
                        # No contar en templates_failed si ya se contó antes por fallo de atributo
                        # if template_summary.get('o17_sync', {}).get('status') != 'attribute_mapping_failed': # Evitar doble conteo
                        #     pass # Ya map_success controla este bloque

                    elif not o16_variants_data and o16_variant_ids:
                        logging.warning(f"No se obtuvieron datos para las variantes {o16_variant_ids} de Odoo 16. Saltando sincronización de variantes.")
                        template_summary['processed_ok'] = False # Marcar fallo para este template

                    else: # Tenemos datos de variantes de Odoo 16
                        counters['o16_variants_found'] += len(o16_variants_data)
                        # Guardar detalles O16 para el resumen
                        # ---> CAMBIO: Sincronización lst_price - Añadir precio O16 al resumen O16
                        template_summary['o16_data']['variants'] = [{'id': v['id'], 'code': v.get('default_code'), 'barcode': v.get('barcode'), 'legacy': v.get('legacy_default_code'), 'price': v.get('lst_price', 0.0)} for v in o16_variants_data] # Añadir precio al resumen O16
                        # Fin CAMBIO lst_price


                        # --- OBTENER VARIANTE(S) EXISTENTES EN ODOO 17 ---
                        logging.info(f"  Obteniendo variantes existentes en Odoo 17 para template ID {o17_template_id}...")
                        # fields_o17_variant = ['id', 'default_code', 'legacy_default_code', 'barcode', 'product_template_attribute_value_ids', 'active', 'image_1920']
                        # ---> CAMBIO: Sincronización lst_price - Añadir 'lst_price' a los campos a leer de Odoo 17 product.product
                        fields_o17_variant = ['id', 'default_code', 'legacy_default_code', 'barcode', 'product_template_attribute_value_ids', 'active', 'image_1920', 'lst_price']
                        # Fin CAMBIO lst_price

                        existing_o17_variants_data = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'search_read', [[('product_tmpl_id', '=', o17_template_id), ('active', 'in', [True, False])]], {'fields': fields_o17_variant})

                        o17_variants_map_by_ptavs = {} # Mapea tuple(sorted(PTAV IDs O17)) -> variant_data O17
                        o17_variant_ids_to_clean_potential = set() # Todos los IDs existentes en Odoo 17 para este template

                        if existing_o17_variants_data is None:
                             logging.error(f"Fallo RPC al leer variantes existentes en Odoo 17 para template ID {o17_template_id}. No se realizará la sincronización de variantes ni el archivado de obsoletas.")
                             template_summary['processed_ok'] = False # Marcar fallo para este template

                        elif existing_o17_variants_data:
                             counters['o17_variants_existing'] += len(existing_o17_variants_data)
                             logging.info(f"  {len(existing_o17_variants_data)} variantes existentes encontradas en Odoo 17. Mapeando por combinación de atributos...")
                             for variant_data in existing_o17_variants_data:
                                 o17_variant_id_inner = variant_data['id']
                                 o17_variant_ids_to_clean_potential.add(o17_variant_id_inner) # Añadir a la lista potencial de limpieza

                                 o17_variant_ptav_ids = variant_data.get('product_template_attribute_value_ids', [])
                                 key_ptav_ids = tuple(sorted(o17_variant_ptav_ids)) # Crear clave única (tuple de IDs ordenados)

                                 # Manejar posibles variantes duplicadas con la misma combinación de atributos (raro pero posible)
                                 if key_ptav_ids in o17_variants_map_by_ptavs:
                                     existing_variant_id = o17_variants_map_by_ptavs[key_ptavs]['id']
                                     logging.warning(f"  Variante duplicada encontrada en Odoo 17 con la misma combinación de PTAVs {key_ptav_ids} para template {o17_template_id}. Variante existente ID: {existing_variant_id}, Nueva variante encontrada ID: {o17_variant_id_inner}. Se usará la última encontrada para mapeo.")

                                 # ---> CAMBIO: Corregir NameError - Usar 'key_ptav_ids' en lugar de 'key_ptavs'
                                 o17_variants_map_by_ptavs[key_ptav_ids] = variant_data # Mapear la combinación de PTAVs a la variante O17 encontrada
                                 # Fin CAMBIO NameError


                        else:
                            logging.info("  No se encontraron variantes existentes en Odoo 17 para este template.")


                        # --- PROCESAR CADA VARIANTE DE ODOO 16 ---
                        processed_o17_variant_ids = set() # IDs de variantes en Odoo 17 que se corresponden con una variante de Odoo 16 y se procesaron (crearon/actualizaron)
                        logging.info(f"  Procesando {len(o16_variants_data)} variantes desde Odoo 16...")

                        for o16_variant in o16_variants_data:
                             o16_variant_id = o16_variant['id']
                             o16_variant_code = o16_variant.get('default_code')
                             o16_variant_legacy = o16_variant.get('legacy_default_code')
                             o16_variant_barcode = o16_variant.get('barcode')
                             o16_variant_is_active = o16_variant.get('active', True)
                             o16_ptav_ids_o16 = o16_variant.get('product_template_attribute_value_ids', [])
                             o16_variant_image = o16_variant.get('image_1920')
                             # ---> CAMBIO: Sincronización lst_price - Leer lst_price del origen (Odoo 16)
                             o16_variant_price = o16_variant.get('lst_price', 0.0) # Usar 0.0 si es False o None
                             # Fin CAMBIO lst_price

                             logging.debug(f"  Procesando variante Odoo 16 ID {o16_variant_id}: Code='{o16_variant_code}', Active={o16_variant_is_active}, PTAVs O16: {o16_ptav_ids_o16}, Has Image: {bool(o16_variant_image)}, Price: {o16_variant_price}")

                             o17_target_ptav_ids = [] # Lista de IDs de PTAVs en Odoo 17 para esta variante
                             variant_mapping_failed = False # Flag para esta variante específica

                             # Mapear los PTAVs de Odoo 16 a los PTAVs correspondientes en Odoo 17
                             if o16_ptav_ids_o16:
                                 # Leer detalles de PTAVs de Odoo 16
                                 o16_ptav_details = execute_odoo_kw(models_o16, o16_db, uid_o16, o16_pass, 'product.template.attribute.value', 'read', o16_ptav_ids_o16, {'fields': ['id', 'product_attribute_value_id']})

                                 if o16_ptav_details is None:
                                      logging.error(f"    Fallo RPC al leer detalles de PTAVs {o16_ptav_ids_o16} para variante Odoo 16 ID {o16_variant_id}. Saltando esta variante.")
                                      continue # Saltar esta variante específica, no todo el template

                                 if not o16_ptav_details and o16_ptav_ids_o16:
                                      logging.warning(f"    No se obtuvieron detalles para los PTAVs {o16_ptav_ids_o16} de Odoo 16 para variante {o16_variant_id}. Saltando esta variante.")
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
                                                  logging.error(f"    Valor de atributo (PAV) Odoo 16 ID {o16_pav_id} mapeado a None en cache Odoo 17 para variante {o16_variant_id}. Esto significa que su creación/búsqueda falló. Saltando esta variante.")
                                                  variant_mapping_failed = True
                                                  break # Salir del bucle de valores, esta variante no se puede mapear

                                         else:
                                              # Esto indica que el PAV O16 no fue encontrado en el mapeo (raro si el mapeo fue exitoso en paso 3)
                                              logging.error(f"    Valor de atributo (PAV) Odoo 16 ID {o16_pav_id} no encontrado en cache de mapeo Odoo 17 para variante {o16_variant_id}. Saltando esta variante.")
                                              variant_mapping_failed = True
                                              break # Salir del bucle de valores

                                     else:
                                         logging.warning(f"    PTAV Odoo 16 ID {ptav_detail_o16.get('id')} para variante {o16_variant_id} no tiene información de PAV válida. Saltando este PTAV.")
                                         # No marcar variant_mapping_failed aquí, solo loguear y saltar este PTAV específico


                                 if variant_mapping_failed:
                                     continue # Saltar a la siguiente variante Odoo 16

                                 if o17_mapped_pav_ids:
                                      # Buscar los PTAVs correspondientes en Odoo 17 que pertenecen a este template y tienen estos PAVs
                                      # La combinación de product_tmpl_id y product_attribute_value_id (PAV) identifica un PTAV único
                                      domain_ptav_combo_o17 = [('product_tmpl_id', '=', o17_template_id), ('product_attribute_value_id', 'in', o17_mapped_pav_ids)]
                                      o17_ptavs_for_combo_data = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template.attribute.value', 'search_read', [domain_ptav_combo_o17], {'fields': ['id', 'product_attribute_value_id']}) # Necesitamos el product_attribute_value_id para mapear PAV a PTAV

                                      if o17_ptavs_for_combo_data is None:
                                           logging.error(f"    Fallo RPC al buscar PTAVs en Odoo 17 para PAVs {o17_mapped_pav_ids} y template {o17_template_id}. Saltando esta variante.")
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
                                           logging.error(f"    No se encontraron PTAVs en Odoo 17 para TODOS los PAVs mapeados ({len(o17_target_ptav_ids)}/{len(o17_mapped_pav_ids)}) para variante Odoo 16 ID {o16_variant_id}. Saltando esta variante.")
                                           continue # Saltar esta variante específica

                                      # La clave para identificar la variante en Odoo 17 es la tupla ordenada de sus PTAV IDs
                                      variant_key_ptavs = tuple(sorted(o17_target_ptav_ids))
                                      logging.debug(f"    PTAVs destino Odoo 17 para variante Odoo 16 ID {o16_variant_id}: {o17_target_ptav_ids}. Clave de mapeo: {variant_key_ptavs}")

                                 else:
                                     # Esto no debería pasar si o16_ptav_ids_o16 no estaba vacío y el mapeo de PAVs fue exitoso
                                     logging.error(f"    Variante Odoo 16 ID {o16_variant_id} tenía PTAVs ({o16_ptav_ids_o16}) pero no se mapearon PAVs válidos a Odoo 17. Saltando esta variante.")
                                     continue # Saltar esta variante específica


                             else:
                                 # Si la variante de Odoo 16 NO tiene PTAVs, es la variante por defecto del producto simple
                                 # Su clave de PTAVs en Odoo 17 es una tupla vacía ().
                                 # Esto aplica si el template Odoo 16 tampoco tiene líneas de atributo.
                                 if not o16_template.get('attribute_line_ids'):
                                     variant_key_ptavs = ()
                                     o17_target_ptav_ids = [] # Lista vacía de PTAVs para productos simples
                                     logging.debug(f"    Variante Odoo 16 ID {o16_variant_id} es un producto simple. Clave PTAV: {variant_key_ptavs}")
                                 else:
                                     # Si la variante O16 no tiene PTAVs pero el template sí tiene atributos, es una variante inesperada o un error en O16
                                     logging.warning(f"    Variante Odoo 16 ID {o16_variant_id} no tiene PTAVs pero el template Odoo 16 ID {o16_template_id} sí tiene atributos. Saltando esta variante.")
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
                                 # ---> CAMBIO: Sincronización lst_price - Añadir precio O16 al resumen O16
                                 'price': o16_variant_price
                                 # Fin CAMBIO lst_price
                                 }
                             variant_sync_result = {
                                 'o16': o16_variant_summary,
                                 'o17_id': None, # Se llenará si se encuentra o crea
                                 'status': 'pending', # created, updated, found_unchanged, create_failed, update_failed
                                 'details_o17': None # Se llenará al final del procesamiento de la variante
                                 }


                             if o17_variant_match:
                                 # --- Variante Encontrada en Odoo 17 ---
                                 o17_variant_id_match = o17_variant_match['id']
                                 processed_o17_variant_ids.add(o17_variant_id_match) # Marcar como procesada (no obsoleta)
                                 variant_sync_result['o17_id'] = o17_variant_id_match

                                 logging.info(f"    Variante encontrada en Odoo 17 con ID: {o17_variant_id_match} (Clave PTAVs: {variant_key_ptavs}).")

                                 # Obtener valores actuales de Odoo 17 para comparar
                                 current_o17_code = o17_variant_match.get('default_code', False)
                                 current_o17_legacy = o17_variant_match.get('legacy_default_code', False)
                                 current_o17_barcode = o17_variant_match.get('barcode', False)
                                 current_o17_active = o17_variant_match.get('active', True)
                                 current_o17_image = o17_variant_match.get('image_1920')
                                 # ---> CAMBIO: Sincronización lst_price - Leer lst_price del destino (Odoo 17)
                                 current_o17_price = o17_variant_match.get('lst_price', 0.0) # Usar 0.0 si es False o None
                                 # Fin CAMBIO lst_price


                                 needs_update = False
                                 update_vals = {}

                                 # Comparar campos y añadir a update_vals si difieren
                                 code1 = (current_o17_code or '').strip()
                                 code2 = (o16_variant_code or '').strip()
                                 if code1 != code2:
                                     needs_update = True
                                     update_vals['default_code'] = o16_variant_code or False # Usar False si está vacío/None en O16
                                     logging.info(f"      -> Variant Default Code difiere: '{code1}' vs '{code2}'. Se actualizará.")

                                 legacy1 = (current_o17_legacy or '').strip()
                                 legacy2 = (o16_variant_legacy or '').strip()
                                 if legacy1 != legacy2:
                                     needs_update = True
                                     update_vals['legacy_default_code'] = o16_variant_legacy or False
                                     logging.info(f"      -> Variant Legacy Code difiere: '{legacy1}' vs '{legacy2}'. Se actualizará.")

                                 barcode1 = (current_o17_barcode or '').strip()
                                 barcode2 = (o16_variant_barcode or '').strip()
                                 if barcode1 != barcode2:
                                     needs_update = True
                                     update_vals['barcode'] = o16_variant_barcode or False
                                     logging.info(f"      -> Variant Barcode difiere: '{barcode1}' vs '{barcode2}'. Se actualizará.")

                                 if current_o17_active != o16_variant_is_active:
                                     needs_update = True
                                     update_vals['active'] = o16_variant_is_active
                                     logging.info(f"      -> Variant Active status difiere: {current_o17_active} vs {o16_variant_is_active}. Se actualizará.")

                                 if o16_variant_image and o16_variant_image != current_o17_image:
                                     needs_update = True
                                     update_vals['image_1920'] = o16_variant_image # Odoo gestiona la imagen base64
                                     logging.info(f"      -> Variant Image difiere o está presente en Odoo 16 pero no en Odoo 17. Se actualizará.")
                                 elif o16_variant_image:
                                     logging.debug(f"      -> Variant Image idéntica. No se actualizará.")
                                 # Si o16_variant_image es False/None, no hacemos nada para borrar la imagen en O17 (comportamiento actual)

                                 # ---> CAMBIO: Sincronización lst_price - Comparar y actualizar lst_price
                                 # Comparación robusta de floats (considerando precisión)
                                 # Usar math.isclose para comparar floats de forma segura
                                 if not math.isclose(o16_variant_price or 0.0, current_o17_price or 0.0, rel_tol=1e-9, abs_tol=0.000001):
                                     needs_update = True
                                     update_vals['lst_price'] = float(o16_variant_price or 0.0) # Asegurarse de que sea float, usar 0.0 por defecto
                                     logging.info(f"      -> Variant Price difiere: {current_o17_price} vs {o16_variant_price}. Se actualizará.")
                                 else:
                                     logging.debug(f"      -> Variant Price idéntico: {current_o17_price}.")
                                 # Fin CAMBIO lst_price comparacion


                                 # Realizar la actualización si hay cambios
                                 if needs_update:
                                     counters['o17_variants_updated'] += 1
                                     variant_sync_result['status'] = 'updated'
                                     logging.info(f"    Actualizando variante Odoo 17 ID: {o17_variant_id_match} con: {list(update_vals.keys())}")

                                     write_ok = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'write', [[o17_variant_id_match], update_vals])

                                     if write_ok is True:
                                         logging.info(f"      -> Update de variante Odoo 17 ID {o17_variant_id_match} OK.")
                                     elif write_ok is None and 'barcode' in update_vals:
                                          # Manejo específico de fallo al actualizar barcode (posible duplicado)
                                          variant_sync_result['status'] = 'update_failed (barcode?)'
                                          logging.warning(f"      -> Fallo al actualizar variante Odoo 17 ID {o17_variant_id_match}, posible conflicto de barcode. Reintentando sin el campo 'barcode'...")
                                          update_vals_no_barcode = update_vals.copy()
                                          del update_vals_no_barcode['barcode']
                                          if update_vals_no_barcode:
                                              write_ok_no_barcode = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'write', [[o17_variant_id_match], update_vals_no_barcode])
                                              if write_ok_no_barcode is True:
                                                  logging.info(f"        -> Update de variante Odoo 17 ID {o17_variant_id_match} (sin barcode) OK.")
                                                  variant_sync_result['status'] = 'updated (no barcode)' # Estado más específico
                                              else:
                                                  logging.error(f"        -> Fallo al actualizar variante Odoo 17 ID {o17_variant_id_match} incluso sin barcode. Resultado: {write_ok_no_barcode}")
                                          else:
                                               logging.info("        -> No hay otros campos para actualizar aparte del barcode.")

                                     elif write_ok is False:
                                         variant_sync_result['status'] = 'update_failed (logic)'
                                         logging.error(f"      -> Fallo lógico al actualizar variante Odoo 17 ID {o17_variant_id_match}. Resultado: False.")

                                     elif write_ok is None:
                                         variant_sync_result['status'] = 'update_failed (rpc)'
                                         logging.error(f"      -> Fallo RPC/inesperado general al actualizar variante Odoo 17 ID {o17_variant_id_match}.")
                                         # Podríamos considerar levantar una excepción aquí si queremos que falle todo el template
                                         # raise Exception(f"Fallo RPC al actualizar variante {o17_variant_id_match}")

                                     else:
                                         variant_sync_result['status'] = 'update_failed (unknown)'
                                         logging.warning(f"      -> Resultado inesperado ({write_ok}) al actualizar variante Odoo 17 ID {o17_variant_id_match}.")
                                 else:
                                     variant_sync_result['status'] = 'found_unchanged'
                                     logging.info(f"    Variante Odoo 17 ID: {o17_variant_id_match} ya sincronizada (no hay campos que requieran actualización).")

                                 # Guardar detalles FINALES de Odoo 17 para el resumen
                                 variant_sync_result['details_o17'] = {
                                     'code': update_vals.get('default_code', current_o17_code), # Usar el valor actualizado si existe, sino el actual
                                     'barcode': update_vals.get('barcode', current_o17_barcode),
                                     'legacy': update_vals.get('legacy_default_code', current_o17_legacy),
                                     # ---> CAMBIO: Sincronización lst_price - Guardar precio final en el resumen
                                     'price': update_vals.get('lst_price', current_o17_price) # Usar el valor actualizado si existe, sino el actual
                                     # Fin CAMBIO lst_price resumen
                                     }
                                 # Añadir resultado de esta variante al resumen del template
                                 template_summary['o17_sync']['variants_synced'].append(variant_sync_result)


                             else:
                                 # --- Variante No Encontrada en Odoo 17 ---
                                 counters['o17_variants_created'] += 1
                                 variant_sync_result['status'] = 'created'
                                 logging.info(f"    Variante Odoo 17 con clave PTAVs {variant_key_ptavs} no encontrada. Creando...")

                                 create_vals = {
                                     'product_tmpl_id': o17_template_id, # Asociar al template Odoo 17
                                     'default_code': o16_variant_code or False,
                                     'legacy_default_code': o16_variant_legacy or False,
                                     'barcode': o16_variant_barcode or False,
                                     'product_template_attribute_value_ids': [(6, 0, sorted(o17_target_ptav_ids))], # Relación M2M con los PTAVs mapeados
                                     'type': 'product', # Tipo por defecto
                                     'active': o16_variant_is_active, # Estado activo de Odoo 16
                                     'image_1920': o16_variant_image or False, # Imagen de Odoo 16
                                     # ---> CAMBIO: Sincronización lst_price - Añadir lst_price a los valores de creación
                                     'lst_price': float(o16_variant_price or 0.0) # Asegurarse de que sea float, usar 0.0 por defecto
                                     # Fin CAMBIO lst_price creacion
                                 }
                                 logging.debug(f"    Valores para crear variante Odoo 17: {create_vals.keys()} (excluyendo M2M y Binario)") # No loguear valores grandes

                                 new_variant_id = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'create', [create_vals])

                                 if new_variant_id and isinstance(new_variant_id, int):
                                     logging.info(f"    ✔ Variante creada en Odoo 17 con ID: {new_variant_id} (Clave PTAVs: {variant_key_ptavs})")
                                     processed_o17_variant_ids.add(new_variant_id) # Marcar como procesada
                                     variant_sync_result['o17_id'] = new_variant_id
                                     # ---> CAMBIO: Sincronización lst_price - Guardar precio creado en el resumen
                                     variant_sync_result['details_o17'] = {
                                         'code': create_vals['default_code'],
                                         'barcode': create_vals['barcode'],
                                         'legacy': create_vals['legacy_default_code'],
                                         'price': create_vals['lst_price']
                                     }
                                     # Fin CAMBIO lst_price resumen creacion

                                 elif new_variant_id is None and 'barcode' in create_vals and create_vals['barcode']:
                                      # Manejo específico de fallo al crear con barcode (posible duplicado)
                                      variant_sync_result['status'] = 'create_failed (barcode?)'
                                      logging.warning(f"    -> Fallo al crear variante Odoo 17 con clave {variant_key_ptavs}, posible conflicto de barcode. Reintentando creación sin el campo 'barcode'...")
                                      create_vals_no_barcode = create_vals.copy()
                                      create_vals_no_barcode['barcode'] = False # Intentar crear sin barcode

                                      new_variant_id_no_barcode = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'create', [create_vals_no_barcode])

                                      if new_variant_id_no_barcode and isinstance(new_variant_id_no_barcode, int):
                                           logging.info(f"      ✔ Variante creada en Odoo 17 con ID: {new_variant_id_no_barcode} (Clave: {variant_key_ptavs}, SIN BARCODE)")
                                           processed_o17_variant_ids.add(new_variant_id_no_barcode) # Marcar como procesada
                                           variant_sync_result['status'] = 'created (no barcode)' # Estado más específico
                                           variant_sync_result['o17_id'] = new_variant_id_no_barcode
                                           # ---> CAMBIO: Sincronización lst_price - Guardar precio creado en el resumen (sin barcode)
                                           variant_sync_result['details_o17'] = {
                                               'code': create_vals_no_barcode['default_code'],
                                               'barcode': False,
                                               'legacy': create_vals_no_barcode['legacy_default_code'],
                                               'price': create_vals_no_barcode['lst_price']
                                           }
                                           # Fin CAMBIO lst_price resumen creacion
                                      else:
                                           logging.error(f"      -> Fallo al crear variante Odoo 17 con clave {variant_key_ptavs} incluso sin barcode. Resultado: {new_variant_id_no_barcode}")

                                 elif new_variant_id is False:
                                     variant_sync_result['status'] = 'create_failed (logic)'
                                     logging.error(f"    -> Fallo lógico al crear variante Odoo 17 con clave {variant_key_ptavs}. Resultado: False.")

                                 elif new_variant_id is None:
                                     variant_sync_result['status'] = 'create_failed (rpc)'
                                     logging.error(f"    -> Fallo RPC/inesperado general al crear variante Odoo 17 con clave {variant_key_ptavs}.")

                                 else:
                                     variant_sync_result['status'] = 'create_failed (unknown)'
                                     logging.warning(f"    -> Resultado inesperado ({new_variant_id}) al crear variante Odoo 17 con clave {variant_key_ptavs}.")

                                 # Añadir resultado de esta variante al resumen del template
                                 template_summary['o17_sync']['variants_synced'].append(variant_sync_result)


                        # --- Limpieza de Variantes Obsoletas en Odoo 17 ---
                        # Las variantes existentes en Odoo 17 para este template que no fueron encontradas
                        # y procesadas desde Odoo 16 (no están en processed_o17_variant_ids) se consideran obsoletas.
                        o17_variant_ids_to_clean = o17_variant_ids_to_clean_potential - processed_o17_variant_ids

                        if o17_variant_ids_to_clean:
                             logging.info(f"  Encontradas {len(o17_variant_ids_to_clean)} variantes obsoletas en Odoo 17 para template ID {o17_template_id}. Obteniendo detalles para archivado...")
                             # Leer detalles de las variantes obsoletas (solo id, active, code, barcode para el log/resumen)
                             o17_variants_to_clean_details = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'read', list(o17_variant_ids_to_clean), {'fields': ['id', 'active', 'default_code', 'barcode']})

                             if o17_variants_to_clean_details is None:
                                  logging.error(f"  Fallo RPC al leer detalles de variantes obsoletas {o17_variant_ids_to_clean} en Odoo 17. No se realizará el archivado.")

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
                                           # No incluimos precio aquí ya que el resumen se enfoca en códigos/estado
                                           })

                                  if o17_ids_to_archive:
                                       counters['o17_variants_archived'] += len(o17_ids_to_archive)
                                       logging.info(f"  Archivando {len(o17_ids_to_archive)} variantes obsoletas activas en Odoo 17 ({o17_ids_to_archive})...")
                                       archive_success = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.product', 'write', [o17_ids_to_archive, {'active': False}])

                                       if archive_success is True:
                                            logging.info("    -> Archivado de variantes obsoletas OK.")
                                       elif archive_success is False:
                                            logging.error("    -> Fallo lógico al archivar variantes obsoletas. Resultado: False.")
                                       elif archive_success is None:
                                            logging.error("    -> Fallo RPC/inesperado al archivar variantes obsoletas.")
                                            # raise Exception(f"Fallo RPC al archivar variantes obsoletas {o17_ids_to_archive}") # Fatal si se desea
                                       else:
                                            logging.warning(f"    -> Resultado inesperado ({archive_success}) al archivar variantes obsoletas.")

                                  else:
                                       logging.info("  No se encontraron variantes obsoletas activas que archivar para este template.")

                             else:
                                logging.warning(f"  No se obtuvieron detalles para las variantes obsoletas {o17_variant_ids_to_clean} en Odoo 17. No se realizará el archivado.")

                        else:
                             logging.info("  No se encontraron variantes obsoletas en Odoo 17 para este template.")

                # ---> CAMBIO: Corregir SyntaxError moviendo el else al lugar correcto
            else: # Si map_success fue False para attrs, no se procesan variantes
                 logging.error(f"Sincronización de atributos falló o fue incompleta para template '{template_default_code_to_sync}'. Saltando sincronización de variantes.")
                 template_summary['processed_ok'] = False # Marcar fallo para este template si no se marcó antes (ya lo está por el check inicial de map_success)
            # --- Fin sección Variantes (Condicional) --- # Este comentario también estaba mal posicionado.


        # --- Final del bloque TRY para este Template ---
        except Exception as template_error:
            logging.error(f"Error CRÍTICO no controlado durante el procesamiento del template '{template_default_code_to_sync}': {template_error}", exc_info=True)
            template_summary['processed_ok'] = False # Marcar como fallido

            # Incrementar contador de templates fallidos, pero solo si no se contó ya (ej. por 'not found')
            # original: if template_summary['o16_data']['id']: counters['templates_failed'] += 1
            # Mejorar: solo si no se marcó processed_ok antes de entrar a este except, y si la plantilla fuente fue encontrada o el código fuente era válido
            if template_summary['o16_data']['id'] or template_summary['code'] != 'N/A':
                 counters['templates_failed'] += 1
                 if template_summary['o16_data']['id']:
                      logging.debug(f"Contando fallo para template con O16 ID {template_summary['o16_data']['id']} ('{template_summary['code']}') debido a error crítico.")
                 else:
                      logging.debug(f"Contando fallo para template con código '{template_summary['code']}' que no se encontró en O16 pero falló el procesamiento inicial.")


        # --- ESCRITURA FINAL GARANTIZADA DEL DEFAULT_CODE DEL TEMPLATE ---
        # Esto se hace siempre que tengamos un Odoo 17 template ID válido
        # para asegurar que el default_code original de Odoo 17 no se pierda
        # si Odoo lo resetea al actualizar atributos.
        if o17_template_id and o17_template_id in initial_o17_default_codes:
            code_to_force = initial_o17_default_codes[o17_template_id]
            logging.info(f"  [FINAL WRITE CHECK] Verificando default_code final en Odoo 17 ID {o17_template_id}. El código original era: '{code_to_force}'")

            # Leer el código y nombre actuales por si cambiaron justo al final
            final_read_data = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'read', [o17_template_id], {'fields': ['default_code', 'name']})
            if final_read_data and isinstance(final_read_data, list) and final_read_data:
                final_data_dict = final_read_data[0]
                final_current_code_after_template_loop = final_data_dict.get('default_code', False) # Usar un nombre de variable diferente
                # Actualizar el nombre final en el resumen por si se cambió al final
                template_summary['o17_sync']['name_final'] = final_data_dict.get('name')
                # Guardar el código final actual de Odoo 17 en el resumen
                template_summary['o17_sync']['default_code_final'] = final_current_code_after_template_loop

                logging.info(f"  [FINAL WRITE CHECK] default_code actual ANTES de la escritura final: '{final_current_code_after_template_loop}'")

                if final_current_code_after_template_loop != code_to_force:
                    logging.warning(f"  [FINAL WRITE] default_code actual ('{final_current_code_after_template_loop}') difiere del código inicial guardado ('{code_to_force}'). Forzando escritura para restaurar.")
                    final_write_ok = execute_odoo_kw(models_o17, o17_db, uid_o17, o17_pass, 'product.template', 'write', [[o17_template_id], {'default_code': code_to_force}])
                    if final_write_ok is True:
                        logging.info(f"    -> Escritura final del default_code '{code_to_force}' en Odoo 17 OK.")
                        template_summary['o17_sync']['default_code_final'] = code_to_force # Actualizar el resumen con el valor forzado
                    else:
                        logging.error(f"    -> FALLO al forzar la escritura final del default_code '{code_to_force}' en Odoo 17. Resultado: {final_write_ok}")
                else:
                    logging.info(f"  [FINAL WRITE CHECK] default_code actual ('{final_current_code_after_template_loop}') ya coincide con el código inicial guardado. No se necesita escritura final.")

            else:
                 logging.error(f"  [FINAL WRITE CHECK] No se pudo leer el default_code actual en Odoo 17 ID {o17_template_id} para la verificación final. No se forzará la escritura.")
                 template_summary['o17_sync']['default_code_final'] = 'N/A (Error lectura final?)'

        elif o17_template_id:
             # Si tenemos un ID de Odoo 17 pero no teníamos un código inicial guardado (ej. por fallo muy temprano)
             template_summary['o17_sync']['default_code_final'] = 'N/A (Sin código inicial guardado)'
             logging.warning(f"  [FINAL WRITE CHECK] No se guardó el default_code inicial para Odoo 17 ID {o17_template_id}. Saltando verificación y escritura final del código.")


        # --- Guardar resumen de este template ---
        # Asegurarse de que processed_ok está False si hubo un error crítico en el try
        # (ya se maneja dentro del except, pero doble chequeo no hace daño)
        # if not template_summary['processed_ok'] and template_summary.get('o17_sync',{}).get('status') == 'ok':
        #      pass # Mantener processed_ok=False si algo falló, aunque el status de sync_template sea 'ok'

        summary_data.append(template_summary)


        logging.info(f"\n--- Finalizado procesamiento de Template con default_code: {template_default_code_to_sync} ---")


    # --- FIN Bucle Principal de Templates ---

    # Devolver datos para el resumen final a main_sync.py
    return summary_data, counters