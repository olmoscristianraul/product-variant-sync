# Script de Sincronización de Productos Odoo 16 -> Odoo 17 (XML-RPC)

**Autor:** Cristián R. Olmos
**Empresa:** HC Sinergia S.A.
**Fecha:** 2025-05-02
**Versión:** 1.3 (Incluye Sync de Precio de Lista)

## Resumen

Este conjunto de scripts Python utiliza XML-RPC para conectar una instancia de Odoo 16 (origen) y una de Odoo 17 (destino) con el fin de sincronizar datos de productos (`product.template` y `product.product`). Lee la configuración de conexión desde `config.ini` y la lista de `default_code` de productos a procesar desde un archivo CSV (por defecto `products_to_sync.csv`).

Permite sincronizar todos los productos activos con `default_code` (usando '*' en el CSV) o una lista específica. Durante el proceso, sincroniza el `product.template` (nombre, activo, imagen principal), sus atributos y valores (creándolos si no existen en destino), las líneas de atributo, y las variantes (`product.product`) correspondientes (código, código legacy, código de barras, activo, imagen principal, **precio de lista**), archivando variantes obsoletas en destino.

Incluye mecanismos para preservar la Referencia Interna (`default_code`) del producto plantilla en Odoo 17 y genera logs detallados de la ejecución en un subdirectorio `logs`, indicando el estado final (OK/ERROR) en el nombre del archivo. Al finalizar, imprime un resumen detallado en consola con información comparativa de O16 y O17, junto con tiempos de ejecución.

## Descripción Detallada

El objetivo principal es migrar o mantener sincronizados productos entre las dos versiones de Odoo. La sincronización es unidireccional (Odoo 16 -> Odoo 17) y se basa en el campo `default_code` del `product.template` como clave principal.

El flujo general para cada `default_code` a procesar es:

1.  **Leer Origen (O16):** Se busca el `product.template` en Odoo 16 por su `default_code`. Se leen sus datos básicos, imagen, líneas de atributo, IDs de variantes y nombres de atributos/valores asociados.
2.  **Buscar/Crear Destino (O17):** Se busca el `product.template` correspondiente en Odoo 17.
    * Si existe, se actualiza su nombre, estado activo e imagen si es necesario. Se guarda su `default_code` inicial para preservarlo.
    * Si no existe, se crea con los datos básicos y la imagen de O16. Se guarda su `default_code` inicial.
3.  **Sincronizar Atributos/Valores:** Se procesan los atributos y valores leídos de O16. Se buscan o crean en O17 (de forma insensible a mayúsculas) y se mapean los IDs.
4.  **Sincronizar Líneas de Atributo:** Se actualiza el campo `attribute_line_ids` del template O17 para que coincida con las combinaciones Atributo/Valores de O16, usando comandos M2M (`(0,0,{})`, `(1,ID,{})`, `(2,ID)`).
5.  **Preservar Default Code (Template):** Se implementa una doble verificación:
    * Justo después de actualizar `attribute_line_ids` (operación que a veces lo borra), se comprueba si el `default_code` se ha perdido y se restaura si es necesario.
    * Al final del procesamiento del template, se vuelve a leer el `default_code` y se compara con el valor inicial guardado, forzando una escritura final si no coinciden.
6.  **Sincronizar Variantes:** Si el mapeo de atributos fue exitoso:
    * Se leen los detalles de las variantes (`product.product`) de O16 (incluyendo imagen **y precio de lista**).
    * Se leen las variantes existentes de O17 para ese template (incluyendo **precio de lista**).
    * Para cada variante O16, se mapean sus valores de atributo a los IDs de O17 y se busca la variante correspondiente en O17.
    * Si se encuentra, se comparan los campos (`default_code`, `legacy_default_code`, `barcode`, `active`, `image_1920`, **`lst_price`**) y se actualiza si hay diferencias.
    * Si no se encuentra, se crea la variante en O17 con los datos correspondientes (incluyendo **`lst_price`**).
    * Se archivan las variantes existentes en O17 que no tuvieron correspondencia en O16.
7.  **Logging y Resumen:** Se registran detalladamente las operaciones en un archivo y se muestra un resumen en consola al finalizar (incluyendo los precios de lista de las variantes sincronizadas).

## Características Principales

* Conexión segura (configurable) a instancias Odoo 16 y Odoo 17 vía XML-RPC.
* Configuración externalizada en `config.ini` y `products_to_sync.csv`.
* Selección de productos a sincronizar: Lista específica o Todos (`*`).
* Creación/Actualización de `product.template` en O17 (Nombre, Activo, Imagen).
* Creación/Actualización de `product.attribute` y `product.attribute.value` en O17 (Búsqueda case-insensitive).
* Sincronización completa de `product.template.attribute.line`.
* **Preservación robusta del `default_code` del Template** en O17.
* Sincronización de `product.product` (Variantes): Creación/Actualización (Codes, Barcode, Activo, Imagen), Archivado de obsoletas.
* **Sincronización de Precio de Lista (`lst_price`) en Variantes.**
* **Sincronización de Imagen Principal (`image_1920`) en Templates y Variantes**.
* Logging Detallado (`DEBUG` en archivo, `INFO` en consola) en `logs/`.
* Nombre de archivo de log con estado final (`OK`/`ERROR`).
* **Resumen Final Detallado:** Comparativa O16 vs O17 por template/variante (incluyendo precios), estadísticas y tiempos de ejecución.
* Manejo de Errores por template y global.
* Código refactorizado en módulos para mejor organización.

## Requisitos

* Python 3.x.
* Acceso a los endpoints XML-RPC de Odoo 16 y Odoo 17.
* Credenciales de usuario Odoo con permisos suficientes (lectura/escritura en modelos de producto, atributos, valores de atributo, líneas de atributo, variantes).
* **No requiere bibliotecas externas** (usa módulos estándar de Python como `xmlrpc.client`, `logging`, `configparser`, `datetime`, `os`, `sys`, `time`, `math`).

## Configuración

Necesitas crear/configurar 3 archivos en el mismo directorio que `main_sync.py`:

1.  **`config.ini`**: Define las conexiones y el nombre del archivo CSV.
    ```ini
    [ODOO16]
    URL = https://tu_instancia_o16.odoo.com
    DB = nombre_base_datos_o16
    USER = usuario_o16
    PASS = contraseña_o16

    [ODOO17]
    URL = http://tu_instancia_o17.com:8069
    DB = nombre_base_datos_o17
    USER = usuario_o17
    PASS = contraseña_o17

    [SYNC_SETTINGS]
    # Nombre del archivo CSV que contiene los códigos a sincronizar
    CSV_FILENAME = products_to_sync.csv
    ```
    **¡IMPORTANTE: Protege este archivo, especialmente las contraseñas!**

2.  **`products_to_sync.csv`** (o el nombre definido en `CSV_FILENAME`):
    * **Códigos Específicos:** Una sola línea con `default_code` separados por coma.
        ```csv
        CODE1,CODE2,CODE3,ABC-123
        ```
    * **Todos los Productos:** Una sola línea conteniendo únicamente `*`.
        ```csv
        *
        ```

3.  **Archivos Python:** Asegúrate de tener los 4 archivos `.py` en el mismo directorio:
    * `config_loader.py`
    * `odoo_xmlrpc.py`
    * `sync_logic.py`
    * `main_sync.py`

## Uso

1.  Verifica que tienes Python 3 instalado.
2.  Prepara los archivos `config.ini` y `products_to_sync.csv`.
3.  Asegúrate de que los 4 archivos `.py` estén presentes.
4.  Asegúrate de que ambas instancias de Odoo (Odoo 16 origen y Odoo 17 destino) estén **corriendo y sean accesibles** desde donde ejecutas el script, con las credenciales y bases de datos correctas configuradas en `config.ini`.
5.  Abre una terminal o consola en el directorio que contiene todos estos archivos.
6.  Ejecuta el script principal:
    ```bash
    python3 main_sync.py
    ```
7.  Sigue el progreso en la consola (mensajes INFO/WARNING/ERROR).
8.  Revisa el archivo de log detallado (DEBUG) en la carpeta `logs/` que se creará automáticamente. El nombre del archivo indicará si hubo errores (`_ERROR.log`) o no (`.log`).
9.  Analiza el resumen final impreso en la consola para verificar el estado y los resultados de la sincronización, incluyendo los precios.

## Logging

* **Directorio:** Se crea `./logs/` si no existe.
* **Archivo:** Se genera un archivo por ejecución con formato `YYYYMMDD_HHMMSS[_ERROR].log`. Contiene todos los detalles (`DEBUG` level).
* **Consola:** Muestra mensajes `INFO`, `WARNING`, `ERROR` por defecto.
* **Estado:** El nombre final del archivo (`.log` o `_ERROR.log`) y el código de salida del script (0 para OK, distinto de 0 para ERROR) indican el resultado general.

## Desglose Detallado del Script

El script está organizado en los siguientes módulos:

1.  **`config_loader.py`:**
    * Responsable único de leer y validar el archivo `config.ini` usando el módulo `configparser`.
    * Verifica la presencia de secciones y claves requeridas.
    * Expone los valores de configuración en un diccionario `config_values` para ser importado por otros módulos.
    * Termina el script si la configuración es inválida.

2.  **`odoo_xmlrpc.py`:**
    * Contiene las funciones de bajo nivel para interactuar con Odoo vía XML-RPC.
    * `connect_odoo()`: Establece y autentica la conexión.
    * `execute_odoo_kw()`: Función centralizada para ejecutar cualquier método (`search_read`, `read`, `write`, `create`, etc.). Incluye:
        * Manejo robusto de errores (RPC Faults y excepciones generales).
        * Logging detallado de llamadas y resultados (truncando datos largos como imágenes base64).
        * Ajuste específico para el método `read`.
        * Sintaxis corregida para evitar `SyntaxError`.
    * `find_record()`: Helper para buscar registros usando `search_read`, forzando `active_test=False`.
    * `get_or_create_attribute()` / `get_or_create_attribute_value()`: Helpers para buscar atributos/valores en O17 por nombre (case-insensitive, usando `ilike`) y crearlos si no existen.

3.  **`sync_logic.py`:**
    * Contiene la lógica principal de la sincronización.
    * `load_codes_to_process()`: Implementa la lectura del archivo CSV.
        * Maneja `FileNotFoundError`.
        * Detecta el caso `*` y realiza la consulta `search_read` a Odoo 16 para obtener todos los `default_code` aplicables (`active=True`, `default_code` no vacío).
        * Si no es `*`, procesa la línea como códigos separados por comas.
        * Devuelve la lista final de códigos a procesar.
    * `sync_products()`: Orquesta el proceso completo por cada template.
        * Recibe las conexiones y la configuración.
        * Llama a `load_codes_to_process`.
        * Inicializa estructuras para el resumen (`summary_data`, `counters`).
        * Itera sobre cada `template_default_code_to_sync`.
        * **Bloque `try...except` por Template:** Para robustez, permite que un fallo en un template no detenga todo el script.
        * **Pasos de Sincronización:** Ejecuta la lógica de búsqueda, creación/actualización de templates, atributos/valores/líneas y variantes, llamando a las funciones de `odoo_xmlrpc.py`. Recopila datos para el resumen (incluyendo datos O16, estado de sync O17, detalles O17, **precios de variante**). Implementa los fixes para `default_code`. Actualiza `map_success` si ocurren errores no críticos en el mapeo.
        * **Escritura Final `default_code`:** Se ejecuta después del `try/except` para asegurar el estado final del código del template O17, restaurándolo si fue borrado.
        * **Guardar Resumen Template:** Añade la información recopilada para el template actual a `summary_data`.
        * Devuelve `summary_data` y `counters`.

4.  **`main_sync.py`:**
    * **Punto de Entrada:** Es el script que se debe ejecutar.
    * **Importaciones:** Importa los otros módulos y librerías estándar.
    * **Logging:** Configura los handlers de archivo (temporal) y consola.
    * **Timing:** Registra `start_time_global` y `end_time_global`.
    * **Bloque `try...except...finally` Global:**
        * `try`: Llama a `connect_odoo` para ambas instancias. **Verifica que ambas conexiones fueron exitosas** antes de proceder. Luego llama a `sync_products`. Evalúa el resultado devuelto por `sync_products` (`templates_failed`) para determinar el `script_status` general.
        * `except`: Captura cualquier error no manejado en este nivel global, loguea y marca `script_status = "ERROR"`.
        * `finally`: Se ejecuta siempre. Calcula la duración. Imprime el resumen final detallado usando los datos devueltos por `sync_products` (incluyendo los detalles de las variantes y sus precios). Llama a `logging.shutdown()`. Renombra el archivo de log según `script_status`. Sale con `sys.exit(0)` (OK) o `sys.exit(1)` (ERROR).

## Posibles Mejoras / Limitaciones

* **Rendimiento:** La sincronización de imágenes y la lectura detallada de datos para el resumen añaden sobrecarga. Podría ser lento con muchos datos.
* **`default_code` como Clave:** El script confía en que `default_code` sea único en ambas instancias (y estable en O16) para encontrar correspondencias.
* **Campos Sincronizados:** Solo sincroniza un conjunto específico de campos (nombre, activo, codes/barcode/legacy/lst_price en variantes, imagen). Otros datos (stock, costos, otras tarifas, proveedores, notas internas, etc.) no se incluyen.
* **Errores Parciales:** Algunos errores durante el procesamiento de un template o sus variantes pueden marcarlo como fallido en el resumen (`processed_ok: False`), pero el script continuará con el siguiente template y puede terminar con estado general "OK" (`.log`) si la mayoría se procesó correctamente y no hubo errores críticos globales. El resumen detallado es clave para identificar estos fallos parciales.
* **Relaciones Complejas:** No maneja la sincronización de relaciones M2M/O2M complejas más allá de las líneas de atributo (`product.template.attribute.line`). No sincroniza, por ejemplo, proveedores de producto, rutas de almacén, etc.

---
**Nota:** Realiza pruebas exhaustivas en entornos de desarrollo/staging antes de ejecutar este script en producción.