# OrdenIA V0.3.1

OrdenIA es una aplicación de escritorio para Windows que vigila carpetas, ayuda a organizar archivos con confirmación manual y ahora permite **buscar dentro de documentos**. La extracción de contenido es local y determinista; no utiliza modelos de IA.

## Privacidad

- El contenido se procesa en el equipo del usuario.
- No se utilizan APIs ni se envían documentos a Internet.
- El texto extraído y el índice de búsqueda se guardan en la base SQLite local, en `%LOCALAPPDATA%\OrdenIA\`.
- Quien tenga acceso a esa base podrá leer el texto indexado. Protege la cuenta de Windows y sus copias de seguridad como protegerías los documentos originales.

## Funciones

- Vigila carpetas con `watchdog` y puede analizar archivos existentes, con o sin subcarpetas.
- Excluye archivos temporales, archivos del sistema y destinos administrados. **Analizar ahora** reconcilia archivos ausentes o excluidos sin borrar historial.
- Clasifica por extensión y propone destinos dentro de la carpeta vigilada, en una biblioteca central o en una carpeta personalizada. Al añadir o editar una carpeta se muestran las tres rutas y un ejemplo del destino propuesto.
- Solo mueve archivos tras confirmación explícita; verifica el movimiento, evita sobrescrituras y permite **Deshacer**.
- Busca por nombre/ruta y por contenido, con filtros de estado y categoría, paginación y fragmentos cortos de coincidencia.
- Muestra estado del análisis, metadatos, palabras clave detectadas mediante frecuencias locales y una vista previa. Son heurísticas, no resúmenes de IA.
- Permite analizar uno o varios archivos en segundo plano con progreso y cancelación. La cancelación detiene los trabajos aún no iniciados.
- Reintenta con backoff los archivos que continúan escribiéndose, sin detener el watcher durante la espera.
- Usa SQLite WAL para que la interfaz, el escáner, el watcher y los workers de contenido puedan leer y escribir con menor contención.

## Formatos de contenido

| Tipo | Extensiones | Datos extraídos |
| --- | --- | --- |
| PDF | `.pdf` | Texto página por página, páginas, título, autor y asunto; sin OCR |
| Word | `.docx` | Párrafos, encabezados, tablas y propiedades básicas |
| Excel | `.xlsx` | Nombres de hojas y valores de celdas; `read_only`, sin evaluar fórmulas |
| PowerPoint | `.pptx` | Texto de diapositivas, formas, tablas y notas disponibles |
| Texto | `.txt`, `.md`, `.log`, `.csv` | Contenido textual |
| Código | `.py`, `.js`, `.ts`, `.tsx`, `.jsx`, `.java`, `.c`, `.cpp`, `.h`, `.hpp`, `.ino`, `.html`, `.css`, `.json`, `.yaml`, `.yml`, `.toml`, `.sql`, `.sh`, `.ps1`, `.bat`, `.xml` | Contenido como texto; nunca se ejecuta |

Los formatos heredados `.doc`, `.xls` y `.ppt` pueden registrarse y organizarse, pero indican **«No compatible con análisis de contenido en V0.3»**. Un PDF sin texto extraíble muestra ese estado sin tratarlo como error fatal. Los PDF dañados o cifrados muestran una razón de fallo. No hay OCR ni conversión con otras aplicaciones.

## Límites y estados

El análisis manual admite archivos de hasta **50 MB**; para `.docx`, `.xlsx` y `.pptx` el límite es **20 MB**, con un máximo de **100 MB descomprimidos** y **10.000 entradas** por archivo Office. Se guardan como máximo **250.000 caracteres** por archivo; los extractores limitan además PDF a **250 páginas**, PPTX a **300 diapositivas** y XLSX a **50.000 celdas y 200 columnas por hoja**. Cuando se llega a un límite, el resultado indica truncamiento u omisión. Los archivos grandes no se cargan completos deliberadamente.

Los estados del contenido son **Pendiente**, **Analizando**, **Indexado**, **Sin texto**, **No compatible**, **Omitido**, **Error** y **Desactualizado**. Son independientes de Pendiente/Organizado/Ignorado y de activo/ausente/excluido. Un cambio de tamaño o fecha de modificación de alta precisión invalida el contenido anterior y lo retira de los resultados hasta reanalizarlo. Organizar y Deshacer conservan el índice cuando el archivo no cambió.

En **Configuración**, el análisis automático de nuevos archivos compatibles está **desactivado por defecto**. Puede activarse y fijarse un máximo entre 1 y 50 MB; siguen aplicando los límites generales. Hay dos workers de extracción para evitar miles de hilos. El análisis de archivos ya existentes solo se solicita mediante el flujo de escaneo o la acción manual; el escaneo nunca mueve archivos.

## Búsqueda local

La barra de **Archivos detectados** permite combinar **Nombre y ruta** y **Contenido**. Para buscar solo dentro de documentos, desmarca Nombre y ruta. Por ejemplo, `ESP32`, `sensor ultrasónico`, `inventario` o `"gestión de residuos"` pueden encontrar archivos cuyo nombre no contiene esos términos, siempre que su contenido ya esté indexado. Los filtros de categoría y estado se mantienen.

SQLite FTS5 indexa texto y metadatos cuando está disponible; esta distribución de Python lo incluye. OrdenIA guarda el texto una sola vez en `file_analysis` y usa FTS5 con contenido externo. Si otro SQLite no trae FTS5, la búsqueda sigue funcionando mediante comparación textual local, con menor rendimiento. Los registros desactualizados, ausentes o excluidos no aparecen en los resultados normales.

## Desarrollo

Requiere Python 3.12 o posterior. Ejemplo en PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
ordenia
```

También puedes ejecutar `python -m ordenia.main`. Para los tests: `python -m pytest`.

PyInstaller se mantiene fuera de las dependencias de ejecución. Para preparar una máquina de empaquetado:

```powershell
python -m pip install -e ".[packaging]"
```

## Build Windows

Desde PowerShell, en la raíz del repositorio:

```powershell
.\packaging\build_windows.ps1
```

El script elimina únicamente `build/` y `dist/` dentro del proyecto, genera metadata de Windows a partir de `ordenia.__version__`, construye una distribución **one-folder** y ejecuta el binario real en modo smoke test. El smoke test abre y cierra Qt, migra una base V0.2 aislada, abre SQLite, comprueba WAL y detecta FTS5.

La salida principal es:

```text
dist\OrdenIA\OrdenIA.exe
```

No requiere PowerShell, un entorno virtual ni Python instalado en la máquina donde se ejecuta.

### Instalador

Si Inno Setup 6 está instalado, el mismo script genera:

```text
dist\installer\OrdenIA-Setup-0.3.1.exe
```

Si Inno Setup no está disponible, el build de `OrdenIA.exe` termina correctamente y muestra cómo completar el instalador más adelante. El instalador admite actualización sobre una versión anterior, crea una entrada del menú Inicio y ofrece un acceso directo opcional en el escritorio. La desinstalación no elimina los datos locales.

El icono es opcional. Cuando exista `packaging/assets/ordenia.ico`, PyInstaller lo incorporará; su ausencia no bloquea el build.

## Datos locales

La aplicación instalada conserva base, índice, configuración y logs en:

```text
%LOCALAPPDATA%\OrdenIA
```

Nada se guarda dentro de `Program Files` ni de `dist/`. Reinstalar o actualizar los binarios no elimina `ordenia.sqlite3`.

## Uso del análisis

1. Añade una carpeta vigilada y acepta analizar los archivos existentes, o pulsa **Analizar ahora** más tarde.
2. En **Archivos detectados**, selecciona una o varias filas y pulsa **Analizar contenido**. Puedes cancelar los análisis aún pendientes de la cola.
3. Revisa estado, extractor, metadatos, palabras clave y vista previa en los detalles. **Reanalizar** actualiza un archivo tras cambios o fallos.
4. Busca con la opción **Contenido** activada. OrdenIA consulta el índice SQLite, no vuelve a abrir cada documento durante la búsqueda.

## Migración desde V0.2

Conserva la base `ordenia.sqlite3`; no hace falta borrar ni exportar datos. Al iniciar V0.3 se añaden la fecha de modificación precisa y `file_analysis`, junto con su índice FTS5 cuando está disponible. Permanecen carpetas, archivos, estados, operaciones, preferencias, rutas e historial de V0.2. Los archivos previos comienzan con análisis **Pendiente** hasta que el usuario los analice; el contenido no se extrae masivamente al migrar.

## Arquitectura

| Módulo | Responsabilidad |
| --- | --- |
| `analysis/` | Registro y extractores locales, límites, palabras clave y vista previa |
| `database/` | Migraciones SQLite, registros de contenido, FTS5 y consultas |
| `services/` | Cola de dos workers, coordinación con vigilancia, escaneo y movimientos |
| `monitoring/` y `scanning/` | Eventos y recorrido del sistema de archivos |
| `core/` | Clasificación, exclusiones, destinos y movimientos seguros |
| `ui/` | Interfaz PySide6; solicita acciones al servicio, sin extraer contenido |

Las estrategias eligen la **raíz** del destino. Una ruta relativa validada determina el grupo bajo esa raíz: V0.3 usa categorías generales como `Documentos`; la misma validación admite subcarpetas para sugerencias futuras sin permitir salir de la raíz. No se clasifican proyectos automáticamente en V0.3.

## Roadmap

| Versión | Objetivo |
| --- | --- |
| V0.1 | Monitorización y organización segura — completado |
| V0.2 | Escaneo, búsqueda, destinos y reconciliación — completado |
| V0.3 | Análisis e indexación local de contenido — completado |
| V0.3.1 | Robustez en Windows y empaquetado — actual |
| V0.4 | IA local |
| V0.5 | Búsqueda semántica y proyectos |
| V1.0 | Asistente inteligente completo |

Licencia: Apache 2.0; consulta [LICENSE](LICENSE).
