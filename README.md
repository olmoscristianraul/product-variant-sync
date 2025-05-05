# Script Avanzado de Sincronización de Productos Odoo 16 -> Odoo 17 (XML-RPC)

**Autor:** Cristián R. Olmos
**Empresa:** HC Sinergia S.A.
**Fecha:** 2025-05-03
**Versión:** 1.4 (Fase 1 Completa, Inicio Fase 2: run_changed, sync_fields parameter)

## Resumen

Este conjunto de scripts Python utiliza XML-RPC para conectar una instancia de Odoo 16 (origen) y una de Odoo 17 (destino) con el fin de sincronizar datos de productos (`product.template` y `product.product`). La sincronización es unidireccional (Odoo 16 -> Odoo 17).

A diferencia de versiones anteriores, este script ahora utiliza **parámetros de línea de comandos** para controlar su comportamiento y una **base de datos SQLite** para gestionar la lista de ítems a procesar, su estado y el historial básico de sincronización. Esto permite ejecutar diferentes **modos** (cargar desde CSV, sincronizar pendientes, sincronizar fallidos, sincronizar códigos específicos, sincronizar cambiados por fecha) y **reanudar** operaciones tras un fallo.

Sincroniza campos clave del `product.template` (nombre, activo, imagen principal) y `product.product` (código interno, referencia legacy, código de barras, estado activo, imagen principal, **precio de lista**), creando ítems en Odoo 17 si no existen o actualizándolos si difieren. Permite el archivado de variantes obsoletas y la sincronización **selectiva de campos de variante** (`all` o `price_only`).

El script mantiene logs detallados por ejecución en un subdirectorio `logs` y utiliza la base de datos para un estado persistente consultable.

## Descripción Detallada

El objetivo principal es migrar o mantener sincronizados productos entre las dos versiones de Odoo de manera controlable y reanudable. La sincronización es unidireccional (Odoo 16 -> Odoo 17) y se basa en el campo `default_code` del `product.template` como clave principal.

La refactorización en Fases ha introducido los siguientes conceptos y flujos:

1.  **Base de Datos SQLite (`sync_state.db`):** Este archivo local almacena la lista de productos que el script debe conocer o procesar. Cada entrada (`sync_item`) en la tabla `sync_items` representa un `default_code` de `product.template` (o variante simple) y guarda su estado (`pending`, `processing`, `succeeded`, `failed`, `skipped`), tipo de origen (`csv`, `changed_o16`, etc.), mensajes de error, y timestamps de intentos/éxitos. La base de datos se creará automáticamente si no existe.
2.  **Interfaz de Línea de Comandos (`argparse`):** Permite controlar el modo de ejecución y opciones de filtrado pasando argumentos al script `main_sync.py`.
3.  **Modos de Operación (`--mode`):** El script ahora tiene diferentes propósitos controlados por parámetros:
    * **Modos de Carga (`load_*`):** Añaden ítems a la base de datos `sync_items` con estado `pending` para ser procesados posteriormente.
    * **Modos de Ejecución (`run_*`):** Seleccionan ítems de la base de datos basándose en su estado (`pending`, `failed`) o en criterios específicos (`codes`, `changed`) y ejecutan la lógica de sincronización para cada uno, actualizando su estado en la DB al finalizar.
    * **Modos de Utilidad (`clear_db`, `status`):** Realizan operaciones de mantenimiento o consulta sobre la base de datos.
4.  **Sincronización Selectiva (`--sync-fields`):** En los modos `run_*`, puedes especificar qué campos de las variantes (`product.product`) deben ser comparados y actualizados durante la sincronización. Las opciones actuales son `all` (todos los campos implementados) o `price_only` (solo el precio de lista `lst_price`).
5.  **Sincronización de Cambios por Fecha (`--mode run_changed --days`):** Un modo de ejecución eficiente que primero consulta Odoo 16 para identificar solo los productos (templates o variantes) que han sido modificados (`write_date`) en un número específico de días (`--days`) y luego procesa esos productos (añadiéndolos a la DB si son nuevos o re-procesándolos si ya estaban en la lista).
6.  **Reanudación y Estado Persistente:** Si una ejecución falla (por ejemplo, se cae una conexión Odoo), los ítems que no se completaron quedarán en estado `failed` (si el fallo ocurrió durante su procesamiento) o `pending` (si el fallo ocurrió antes de intentar procesarlos). Puedes reanudar ejecutando `--mode run_pending` o `--mode run_failed`.

El flujo de sincronización detallado para cada ítem *una vez que ha sido cargado de la base de datos para ser procesado* incluye la lectura, comparación y posible escritura de los campos de `product.template` y `product.product` (`default_code`, `legacy_default_code`, `barcode`, `active`, `image_1920`, `lst_price`), la gestión de atributos y líneas de atributo, y el archivado de variantes obsoletas en Odoo 17. El estado de cada ítem procesado se registra en la base de datos.

## Características Principales

* **Interfaz de Línea de Comandos Avanzada (`argparse`):** Control granular del comportamiento del script mediante parámetros.
* **Base de Datos SQLite (`sync_state.db`):** Almacenamiento persistente de la lista de ítems a sincronizar, su estado y errores.
* **Modos de Operación Flexibles:** `load_csv`, `load_all` (futuro), `run_pending`, `run_failed`, `run_codes`, `run_changed`, `clear_db`, `status`.
* **Funcionalidad de Reanudación:** Retoma la sincronización desde el punto donde falló (`--mode run_failed` o re-ejecutando `--mode run_pending`).
* **Sincronización Eficiente de Cambios:** Identifica y procesa solo los ítems modificados en Odoo 16 (`--mode run_changed --days`).
* **Sincronización Selectiva de Campos de Variante (`--sync-fields {all, price_only}`):** Permite elegir si se sincronizan todos los campos implementados o solo el precio de lista.
* Creación/Actualización de `product.template` en O17 (Nombre, Activo, Imagen).
* Creación/Actualización de `product.attribute` y `product.attribute.value` en O17 (Búsqueda case-insensitive).
* Sincronización completa de `product.template.attribute.line`.
* **Preservación robusta del `default_code` del Template** en O17.
* Sincronización de `product.product` (Variantes): Creación/Actualización (Codes, Barcode, Legacy, Activo, Imagen, Precio de Lista), Archivado de obsoletas.
* Sincronización de Imagen Principal (`image_1920`) en Templates y Variantes.
* Logging Detallado (`DEBUG` en archivo, `INFO` en consola) en `logs/`.
* Nombre de archivo de log por ejecución, indicando modo y estado final (`.log`, `_ERROR.log`, `_STATUS.log`).
* Resumen Final en Consola: Información de la ejecución actual y estado total de la base de datos.
* Manejo de Errores robusto a nivel de ítem (registra en DB) y global.
* Código modularizado en `main_sync.py`, `sync_db.py`, `sync_logic.py`, `odoo_xmlrpc.py`, `config_loader.py`.

## Requisitos

* Python 3.x.
* Módulos estándar de Python: `xmlrpc.client`, `logging`, `configparser`, `datetime`, `os`, `sys`, `time`, `math`, `sqlite3`, `argparse`.
* Acceso a los endpoints XML-RPC de Odoo 16 (origen) y Odoo 17 (destino).
* Credenciales de usuario Odoo con permisos suficientes (lectura en O16 modelos `product.template`, `product.product`, `product.attribute`, `product.attribute.value`, `product.template.attribute.line`; lectura/escritura en O17 modelos `product.template`, `product.product`, `product.attribute`, `product.attribute.value`, `product.template.attribute.line`).
* Herramienta `sqlite3` de línea de comandos (generalmente incluida en sistemas Linux/macOS, disponible para Windows) para consultar la base de datos manualmente.

## Configuración

Necesitas tener los siguientes archivos en el mismo directorio que `main_sync.py`:

1.  **`config.ini`**: Define las conexiones a las instancias de Odoo.

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
    # Nombre del archivo CSV que se usa por defecto para el modo 'load_csv' si no se especifica con --csv-file
    CSV_FILENAME = products_to_sync.csv
    ```

    **¡IMPORTANTE: Protege este archivo `config.ini`, especialmente las contraseñas! No lo subas a repositorios públicos sin un `.gitignore` adecuado.**

2.  **`.gitignore` (Opcional pero Recomendado):** Para evitar subir archivos sensibles o generados localmente.

    ```gitignore
    # Ignorar archivos de configuración con credenciales
    config.ini

    # Ignorar base de datos SQLite
    *.db

    # Ignorar directorio de logs
    logs/

    # Ignorar archivos Python compilados
    __pycache__/
    *.pyc
    ```

3.  **`products_to_sync.csv`** (o el nombre definido en `config.ini` y/o especificado con `--csv-file`):

    * Para el modo `load_csv`, este archivo debe contener una sola línea con los `default_code` de los templates (o variantes simples) que quieres añadir a la base de datos para su procesamiento, separados por coma.
    * **Ejemplo de Códigos Específicos:**
        ```csv
        CODE1,CODE2,CODE3,62626262,ABC-123
        ```

4.  **Archivos Python:** Asegúrate de tener los 5 archivos `.py` con el código completo y correcto:
    * `config_loader.py`
    * `odoo_xmlrpc.py`
    * `sync_db.py`
    * `sync_logic.py`
    * `main_sync.py`

5.  **Base de Datos SQLite (`sync_state.db`):** Este archivo (`sync_state.db` por defecto, o el nombre especificado con `--db-file`) se creará automáticamente la primera vez que ejecutes un modo que interactúe con la DB. No necesitas crearlo manualmente.

## Uso Detallado y Ejemplos

Abre tu terminal y navega al directorio donde se encuentran los archivos del script. Luego ejecuta `python3 main_sync.py` seguido del modo y las opciones deseadas.

**Comando Básico:**

```bash
cd /ruta/a/tu/directorio/del/script/
python3 main_sync.py --mode <modo> [opciones]
```

## Ejemplos de Modos de Operación:

**1. Cargar ítems desde CSV (--mode load_csv):** Lee un archivo CSV y añade los códigos a la base de datos con estado pending. Si un código ya existe, no lo duplica.

```bash
# Carga usando el archivo CSV por defecto (definido en config.ini o 'products_to_sync.csv')
python3 main_sync.py --mode load_csv

# Carga usando un archivo CSV específico
python3 main_sync.py --mode load_csv --csv-file mi_lista_productos.csv
```

**2. Cargar TODOS los ítems activos de Odoo 16 (--mode load_all):** (Funcionalidad planeada para futuras fases).

```bash
# python3 main_sync.py --mode load_all # No implementado en v1.4
```

**3. Ejecutar la sincronización para ítems Pendientes (--mode run_pending):** Procesa todos los ítems en la base de datos con estado pending.

```bash
# Ejecutar con sincronización de todos los campos (por defecto)
python3 main_sync.py --mode run_pending

# Ejecutar sincronizando SOLO el precio de lista
python3 main_sync.py --mode run_pending --sync-fields price_only
```
**4. Ejecutar la sincronización para ítems Fallidos (--mode run_failed):** Procesa todos los ítems en la base de datos con estado failed. Útil tras corregir errores.

```bash
# Reintentar fallidos con sincronización de todos los campos
python3 main_sync.py --mode run_failed

# Reintentar fallidos sincronizando SOLO el precio de lista
python3 main_sync.py --mode run_failed --sync-fields price_only

```
**5. Ejecutar la sincronización para ítems con códigos específicos (--mode run_codes --codes):** Procesa ítems con códigos dados que ya existen en la base de datos. Si un código no está en la DB, lo ignora en esta ejecución (pero no lo elimina).

```bash
# Sincronizar códigos específicos (all fields)
python3 main_sync.py --mode run_codes --codes CODE1 CODE2 62626262

# Sincronizar códigos específicos (price_only)
python3 main_sync.py --mode run_codes --codes ITEM-XYZ ABC-123 --sync-fields price_only

```

**6. Ejecutar la sincronización para ítems cambiados recientemente en Odoo 16 (--mode run_changed --days):** Consulta Odoo 16, identifica cambios por write_date en los últimos --days días, añade esos ítems a la DB (si no existen) con estado pending y tipo de origen changed_o16, y luego procesa todos los ítems con estado pending (incluyendo los recién añadidos).

```bash
# Sincronizar ítems cambiados en el último día (all fields)
python3 main_sync.py --mode run_changed --days 1

# Sincronizar ítems cambiados en los últimos 7 días (price_only)
python3 main_sync.py --mode run_changed --days 7 --sync-fields price_only
```
**7. Limpiar la tabla de ítems en la base de datos (--mode clear_db):** Borra todos los registros de la tabla sync_items. ¡Úsalo con precaución! Esto elimina el historial y la capacidad de reanudar fallidos o pendientes cargados previamente.

```bash
python3 main_sync.py --mode clear_db
```
Te pedirá confirmación antes de ejecutar.

**8. Mostrar el estado actual de la base de datos (--mode status):** Cuenta cuántos ítems hay en cada estado (pending, processing, succeeded, failed, skipped).

```bash
python3 main_sync.py --mode status

```

**9. Especificar una base de datos diferente (--db-file):** Todos los modos aceptan --db-file si no quieres usar el archivo sync_state.db por defecto.

```bash
python3 main_sync.py --mode load_csv --db-file /mi/ruta/otra_sync.db
python3 main_sync.py --mode status --db-file /mi/ruta/otra_sync.db
```

## Ejemplo de Flujo Completo con 62626262:

Usaremos el producto con default_code 62626262 como ejemplo. Asegúrate de que exista en Odoo 16 y tenga variantes. Tu config.ini y products_to_sync.csv (con 62626262 dentro) deben estar en tu directorio de script.

**1. Cargar el ítem a la base de datos (desde CSV):**

```bash
cd /home/cristian/dev/docker-odoo-dev_17/custom_addons/pos/product-variant-sync/ # Navega a tu directorio
python3 main_sync.py --mode load_csv
# Verifica el log y el resumen. La DB debe mostrar 1 item 'pending'.
# Puedes ejecutar 'python3 main_sync.py --mode status' para confirmar.
```
**2. Ejecutar la primera sincronización (modo run_pending, all fields):**
Asegúrate de que Odoo 16 y Odoo 17 estén corriendo y accesibles.

```bash
python3 main_sync.py --mode run_pending
# Verifica el log y el resumen detallado por template. Debería procesar 1 template.
# Si es la primera sync de este producto a O17, puede crear variantes.
# Si ya existía, puede actualizar variantes si hay diferencias.
# El item en la DB pasará a 'succeeded'.
# Puedes ejecutar 'python3 main_sync.py --mode status' de nuevo para confirmar el estado 'Succeeded: 1'.
```
**3.Simular un cambio de Precio en Odoo 16 y sincronizar solo precio:**
Ve a Odoo 16, edita 62626262 o una de sus variantes, cambia solo el Precio de Lista (lst_price) y guarda.
En la terminal, ejecuta:

```bash
python3 main_sync.py --mode run_changed --days 1 --sync-fields price_only
```
**Verifica el log y resumen:** El script detectará el cambio, añadirá 62626262 a la lista pending en la DB (si no estaba ya allí), procesará el ítem, y actualizará solo el precio de la variante en Odoo 17. El log de debug confirmará que solo el campo lst_price fue enviado en el write. El resumen mostrará "Variantes Actualizadas: X".

**4.Verificar en Odoo 17:* Abre el producto 62626262 en Odoo 17 y confirma que el precio se actualizó, pero otros campos (como el barcode, si lo cambiaste manualmente en Odoo 16 sin querer) no se actualizaron en este paso 3 si eran diferentes.

Simular otro cambio y sincronizar TODOS los campos:

**5. Vuelve a Odoo 16. Haz otro cambio en 62626262 o sus variantes (ej: cambia el barcode a un valor nuevo y único, y cambia también el precio). Guarda.
En la terminal:**

```bash
python3 main_sync.py --mode run_changed --days 1 --sync-fields all

```
**Verifica el log y resumen:** El script detectará los cambios, procesará 62626262, y actualizará todos los campos (precio, barcode, etc.) en Odoo 17 que difieran. El log de debug mostrará múltiples campos en el write.

## Consulta de Estado y Historial (Base de Datos SQLite)

La base de datos SQLite (sync_state.db por defecto) almacena el estado persistente de cada ítem sincronizado y un historial básico. Puedes consultarla usando la herramienta de línea de comandos sqlite3.

**1. Abre la terminal.**

**2. Navega al directorio donde se encuentra el archivo de base de datos (sync_state.db).**

```bash
cd /home/cristian/dev/docker-odoo-dev_17/custom_addons/pos/product-variant-sync/ # O tu ruta correcta

```
**3. Conéctate a la base de datos:**
```bash
sqlite3 sync_state.db # Usa --db-file si usaste otro nombre/ruta
```
Entrarás en un prompt sqlite>.

**4. Comandos Básicos dentro del prompt sqlite>:**

Ver las tablas:
```bash
.tables
```
Deberías ver sync_items.

Ver el esquema de la tabla sync_items:
```bash
.schema sync_items
```
Esto te mostrará las columnas (code, status, error_message, last_sync_attempt, etc.) y sus tipos.

Salir del prompt de sqlite3:
```bash
.quit
```
**5. Ejemplos de Consultas SQL (dentro del prompt sqlite>):**

Ver todos los ítems en la tabla:
```SQL
SELECT * FROM sync_items;
```
Contar ítems por estado (alternativa al modo status del script):
```SQL
SELECT status, COUNT(*) FROM sync_items GROUP BY status;
```
Ver todos los ítems que fallaron en alguna ejecución y su último error:
```SQL
SELECT code, status, error_message, last_sync_attempt FROM sync_items WHERE status = 'failed';
```
Ver ítems pendientes de procesar:
```SQL
SELECT code, source_type FROM sync_items WHERE status = 'pending';
```

Ver la última sincronización de un producto específico por su default_code (ej: 62626262):
```SQL
SELECT * FROM sync_items WHERE code = '62626262';
```

```SQL
SELECT * FROM sync_items WHERE code = '62626262';
```
Esto te dará su estado actual, último intento y último éxito registrado en la DB.

Ver qué productos se actualizaron con éxito en una fecha específica (ej: 3 de mayo de 2025):
```SQL
SELECT code, last_sync_success FROM sync_items WHERE status = 'succeeded' AND last_sync_success LIKE '2025-05-03%';
```
(Nota: la fecha en last_sync_success está en formato ISO 8601 TEXT, así que puedes usar LIKE 'YYYY-MM-DD%' para buscar por día).

Buscar un producto por parte de su default_code (ej: que contenga '626'):
```SQL
SELECT code, status FROM sync_items WHERE code LIKE '%626%';
```

Ver ítems añadidos desde CSV que aún no se han sincronizado con éxito:
```SQL
SELECT code, status FROM sync_items WHERE source_type = 'csv' AND status != 'succeeded';

```
Ver ítems detectados como cambiados en Odoo 16 (en modo run_changed) que fallaron al sincronizar:
```SQL
SELECT code, status, error_message FROM sync_items WHERE source_type = 'changed_o16' AND status = 'failed';
```

**Nota sobre variantes en la base de datos:** La tabla sync_items rastrea el estado y el historial de procesamiento a nivel del template (default_code). No almacena cada variante individualmente en esta tabla en esta fase. Los detalles sobre cuántas variantes fueron creadas, actualizadas o archivadas durante una ejecución específica de un template solo se encuentran en el log de archivo de esa ejecución (logs/YYYYMMDD_HHMMSS.log) o en el resumen de consola que se imprime al final de un modo run_*. La base de datos te dice el estado persistente del proceso de sincronización para el template completo.


## Posibles Mejoras / Limitaciones
**Rendimiento:** La sincronización de imágenes y la lectura detallada de datos para el resumen añaden sobrecarga. Puede ser lento con muchos datos. La consulta de cambios por fecha (run_changed) en Odoo 16 también puede ser lenta si hay millones de productos o si se especifica un rango de días muy amplio sin índices adecuados en write_date en Odoo.
**default_code como Clave:** El script confía en que default_code sea único en ambas instancias (y estable en O16) para encontrar correspondencias.
Campos Sincronizados: Aunque se añadió sincronización selectiva, el conjunto de campos sincronizados actualmente (default_code, legacy_default_code, barcode, active, image_1920, lst_price en variantes) sigue siendo fijo. Ampliar esto requiere modificar el código en sync_logic.py.
**Errores Parciales:** Un fallo dentro del procesamiento de un template (try...except interno en sync_logic) marca ese ítem en la DB como failed. El script continúa con el siguiente template. El estado general del script en main_sync será ERROR si hubo al menos un template que falló en la ejecución, o si hubo un error crítico fuera del bucle de templates (ej: conexión, error de DB general). El resumen detallado y la base de datos son necesarios para ver qué ítems específicos fallaron.
**Relaciones Complejas:** No maneja la sincronización de otras relaciones M2M/O2M complejas más allá de las líneas de atributo (product.template.attribute.line). No sincroniza, por ejemplo, proveedores de producto, rutas de almacén, notas internas, impuestos, etc.
**load_all no Implementado:** El modo para cargar todos los productos activos de Odoo 16 a la DB (--mode load_all) aún no está implementado en esta fase.
**Optimización de Carga (run_changed):** Para bases de datos Odoo 16 muy grandes con muchos cambios, obtener todos los códigos cambiados de una vez en get_changed_item_codes podría consumir mucha memoria o tiempo. Una mejora futura sería implementar paginación (lotes) en esta consulta.
**Historial de Variantes en DB:** La DB solo almacena el estado del template. Para un historial detallado de cambios por variante (qué variante específica cambió cuándo y cómo), sería necesario modificar el esquema de la DB y la lógica de sync_logic para registrar eventos a nivel de variante.


## Paso a paso detallado de cómo usar cada una de las funcionalidades del script de sincronización, incluyendo la interacción con la base de datos SQLite:

Pre-requisitos:

Asegúrate de tener tus instancias de Odoo 16 y Odoo 17 corriendo y configuradas correctamente en el archivo config.ini.
Verifica que los archivos del script (main_sync.py, sync_logic.py, sync_db.py, odoo_xmlrpc.py, config_loader.py) estén en el mismo directorio.
Paso a Paso para Usar las Funcionalidades:

1. Cargar ítems desde un archivo CSV (--mode load_csv):

Prepara el archivo CSV: Crea un archivo llamado products_to_sync.csv en el mismo directorio que los scripts. Este archivo debe contener una sola línea con los default_code de los productos que deseas sincronizar, separados por comas. Por ejemplo:
Fragmento de código

CODE001,CODE002,DEMO_PRODUCT
Ejecuta el script:
Bash

python3 main_sync.py --mode load_csv
Verifica:
Revisa el log en el directorio logs/. Busca líneas que indiquen que los códigos fueron leídos del CSV y añadidos a la base de datos con estado pending.
Consulta la base de datos SQLite (sync_state.db) para verificar los ítems pendientes:
Bash

sqlite3 sync_state.db
SELECT code, status, source_type FROM sync_items WHERE status = 'pending' AND source_type = 'csv';
.quit
2. Ejecutar la sincronización para ítems Pendientes (--mode run_pending):

Ejecuta el script:
Bash

python3 main_sync.py --mode run_pending
Verifica:
Revisa el log para ver el procesamiento de los ítems pendientes. Busca mensajes de conexión a Odoo 16 y Odoo 17, la búsqueda de productos y la creación/actualización en Odoo 17.
Consulta la base de datos para verificar el estado actualizado de los ítems:
Bash

sqlite3 sync_state.db
SELECT code, status FROM sync_items WHERE code IN ('CODE001', 'CODE002', 'DEMO_PRODUCT');
.quit
El estado debería ser succeeded si la sincronización fue exitosa, o failed si hubo algún problema.
Verifica en Odoo 17: Accede a tu instancia de Odoo 17 y comprueba que los productos correspondientes a los default_code se hayan creado o actualizado correctamente.
3. Ejecutar la sincronización para ítems Fallidos (--mode run_failed):

Simula un fallo (opcional): Si no tienes ítems en estado failed, puedes cambiar el estado de alguno manualmente en la base de datos:
Bash

sqlite3 sync_state.db
UPDATE sync_items SET status = 'failed', error_message = 'Simulating failure' WHERE code = 'CODE001';
.quit
Ejecuta el script:
Bash

python3 main_sync.py --mode run_failed
Verifica:
Revisa el log para ver el intento de re-procesamiento del ítem fallido.
Consulta la base de datos para verificar el estado actualizado:
Bash

sqlite3 sync_state.db
SELECT code, status, error_message FROM sync_items WHERE code = 'CODE001';
.quit
4. Ejecutar la sincronización para ítems con códigos específicos (--mode run_codes --codes):

Asegúrate de que los códigos existan en la DB (puedes cargarlos con load_csv primero).
Ejecuta el script:
Bash

python3 main_sync.py --mode run_codes --codes CODE002 DEMO_PRODUCT
Verifica:
Revisa el log para ver el procesamiento específico de los códigos proporcionados.
Consulta la base de datos para verificar el estado:
Bash

sqlite3 sync_state.db
SELECT code, status FROM sync_items WHERE code IN ('CODE002', 'DEMO_PRODUCT');
.quit
Verifica en Odoo 17.
5. Ejecutar la sincronización para ítems cambiados recientemente en Odoo 16 (--mode run_changed --days):

Haz un cambio en un producto de Odoo 16: Edita el nombre, precio u otro campo de un producto (por ejemplo, DEMO_PRODUCT) en tu instancia de Odoo 16 y guarda.
Ejecuta el script:
Bash

python3 main_sync.py --mode run_changed --days 1
Verifica:
Revisa el log. Busca mensajes que indiquen la consulta a Odoo 16 por cambios y el posterior procesamiento del producto modificado. El source_type en la base de datos para este ítem podría ser changed_o16.
Consulta la base de datos:
Bash

sqlite3 sync_state.db
SELECT code, status, source_type FROM sync_items WHERE code = 'DEMO_PRODUCT';
.quit
Verifica en Odoo 17: Comprueba que el cambio realizado en Odoo 16 se haya reflejado en Odoo 17.
Prueba con sincronización selectiva de precio: Cambia solo el precio en Odoo 16 y ejecuta:
Bash

python3 main_sync.py --mode run_changed --days 1 --sync-fields price_only
Verifica en el log que solo el precio se intenta actualizar en Odoo 17.
6. Limpiar la tabla de ítems en la base de datos (--mode clear_db):

Ejecuta el script:
Bash

python3 main_sync.py --mode clear_db
Confirma la acción cuando se te pregunte en la terminal.
Verifica:
Consulta la base de datos:
Bash

sqlite3 sync_state.db
SELECT COUNT(*) FROM sync_items;
.quit
El resultado debería ser 0.
7. Mostrar el estado actual de la base de datos (--mode status):

Ejecuta el script:
Bash

python3 main_sync.py --mode status
Verifica: La salida en la terminal mostrará un resumen de cuántos ítems hay en cada estado (pending, processing, succeeded, failed, skipped). Puedes comparar estos números con los resultados de tus consultas directas a la base de datos con sqlite3.
Interacción Adicional con la Base de Datos SQLite:

Marcar un ítem como pendiente para re-sincronizar:

Bash

sqlite3 sync_state.db
UPDATE sync_items SET status = 'pending', error_message = NULL, last_sync_success = NULL WHERE code = 'DEMO_PRODUCT';
.quit
Luego puedes ejecutar python3 main_sync.py --mode run_pending.

Ver ítems cargados desde CSV que fallaron:

Bash

sqlite3 sync_state.db
SELECT code, error_message FROM sync_items WHERE status = 'failed' AND source_type = 'csv';
.quit
Ver la última vez que un producto se sincronizó con éxito:

Bash

sqlite3 sync_state.db
SELECT code, last_sync_success FROM sync_items WHERE status = 'succeeded' AND code = 'DEMO_PRODUCT';
.quit
