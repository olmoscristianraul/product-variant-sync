# -*- coding: utf-8 -*-
import configparser
import logging
import os
import sys

CONFIG_FILENAME = 'config.ini'
config = configparser.ConfigParser()
config_values = {} # Diccionario para exportar valores

def load_config():
    """Lee config.ini, valida claves y devuelve un diccionario con valores."""
    global config_values
    # Asume que config.ini está en el mismo directorio que este loader
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, CONFIG_FILENAME)

    files_read = config.read(config_path, encoding='utf-8')
    if not files_read:
        # Usar print porque el logging puede no estar listo
        print(f"CRITICAL ERROR: No se pudo leer el archivo de configuración '{config_path}'.", file=sys.stderr)
        sys.exit(1)

    loaded_config = {}
    try:
        # Extraer valores de las secciones
        loaded_config['O16_URL'] = config.get('ODOO16', 'URL', fallback=None)
        loaded_config['O16_DB'] = config.get('ODOO16', 'DB', fallback=None)
        loaded_config['O16_USER'] = config.get('ODOO16', 'USER', fallback=None)
        loaded_config['O16_PASS'] = config.get('ODOO16', 'PASS', fallback=None)

        loaded_config['O17_URL'] = config.get('ODOO17', 'URL', fallback=None)
        loaded_config['O17_DB'] = config.get('ODOO17', 'DB', fallback=None)
        loaded_config['O17_USER'] = config.get('ODOO17', 'USER', fallback=None)
        loaded_config['O17_PASS'] = config.get('ODOO17', 'PASS', fallback=None)

        loaded_config['CSV_FILENAME'] = config.get('SYNC_SETTINGS', 'CSV_FILENAME', fallback='products_to_sync.csv')

        # Validar claves requeridas
        required_keys = [k for k in loaded_config if k != 'CSV_FILENAME']
        missing = [k for k in required_keys if loaded_config[k] is None]
        if missing:
            msg = f"Error: Faltan claves de configuración requeridas en '{CONFIG_FILENAME}': {', '.join(missing)}"
            try: logging.error(msg) # Intentar loguear si ya está configurado
            except NameError: print(f"ERROR: {msg}", file=sys.stderr)
            sys.exit(1)

        # Loguear éxito si es posible
        try: logging.debug(f"Configuración cargada exitosamente desde {CONFIG_FILENAME}")
        except NameError: pass

        return loaded_config

    except configparser.NoSectionError as e:
        msg = f"Error: Falta la sección '{e.section}' en '{CONFIG_FILENAME}'."
        try: logging.error(msg)
        except NameError: print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        msg = f"Error inesperado al leer la configuración: {e}"
        try: logging.error(msg, exc_info=True)
        except NameError: print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(1)

# Cargar la configuración cuando se importa el módulo
config_values = load_config()