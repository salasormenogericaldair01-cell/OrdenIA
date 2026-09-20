# OrdenIA

## Consistencia del índice en V0.2

**Analizar ahora** registra archivos nuevos y reconcilia los existentes. El resumen separa archivos encontrados por el escáner, nuevos, ya registrados, activos, ausentes y excluidos. El escáner omite destinos administrados por OrdenIA; un archivo organizado en uno de esos destinos sigue siendo un registro activo asociado a su carpeta vigilada. Por ello, el total de activos puede superar el total encontrado durante el análisis.

SQLite conserva las filas y operaciones históricas. La migración agrega `files.index_state` (`active`, `missing`, `excluded`) sin borrar datos; el estado visible (`Pendiente`, `Organizado`, `Ignorado`) sigue separado. Al abrir la aplicación se ocultan registros heredados que incumplen la política de exclusión; **Analizar ahora** comprueba además si los archivos siguen presentes. Las vistas normales y las estadísticas muestran solamente activos. El tooltip de **Archivos registrados** desglosa activos, ausentes y excluidos. Ninguna reconciliación borra archivos físicos.

Los eventos de renombre externo de watchdog conservan el mismo ID cuando la operación puede seguirse con seguridad. Si un archivo previamente organizado se mueve externamente, se conserva su historial y el registro anterior queda ausente; la nueva ubicación se indexa por separado si es elegible. Los movimientos internos siguen coordinados con el watcher.

OrdenIA es una aplicación de escritorio local para Windows que registra, clasifica y ayuda a organizar archivos con aprobación del usuario. La **V0.2** incorpora escaneo de carpetas existentes, búsqueda y destinos configurables. No utiliza IA ni servicios externos.

## Funciones de V0.2

- Vigilancia en tiempo real con `watchdog` y escaneo manual o inicial de archivos ya existentes.
- Recursividad configurable por carpeta. El escaneo y la vigilancia usan la misma política de exclusión.
- Progreso durante el escaneo en segundo plano; los archivos solo se registran y clasifican, nunca se mueven durante el análisis.
- Destino por carpeta: `OrdenIA/` dentro de la vigilada, biblioteca central configurable o carpeta personalizada.
- Búsqueda mientras se escribe por nombre, extensión, categoría y ruta; filtros combinables por estado y categoría.
- Tabla ordenable y paginada, panel de detalles, tooltips, copia de rutas completas y apertura de ubicación en Explorer.
- Doble clic para abrir con la aplicación predeterminada; los instaladores `.exe` y `.msi` piden confirmación.
- Inicio con estadísticas por categoría, actividad reciente y una guía para añadir la primera carpeta.
- Organización **solo con confirmación manual**, nombres libres sin sobrescritura, verificación física, historial y Deshacer.

## Requisitos e instalación

- Windows y Python 3.12 o posterior. La aplicación también degrada las acciones de abrir ubicación en macOS y Linux.
- PowerShell para los ejemplos.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
ordenia
```

Si tu equipo tiene otra versión compatible de Python, crea el entorno con `python -m venv .venv`. Para ejecutar sin el comando instalado: `python -m ordenia.main`.

## Actualizar desde V0.1

Mantén tu base de datos. Al abrir V0.2, SQLite añade las columnas de recursividad, destino y clave de ruta sin borrar archivos, preferencias ni historial. La base y el log continúan en `%LOCALAPPDATA%\OrdenIA\`. Si una carpeta vigilada ya existía en V0.1, conserva el destino dentro de esa carpeta y la vigilancia recursiva. Usa **Editar** para cambiar estas opciones y **Analizar ahora** para indexar archivos anteriores a la actualización.

## Uso

1. En **Carpetas vigiladas**, pulsa **Añadir carpeta**, elige recursividad y destino, y guarda.
2. Si hay archivos existentes, elige **Analizar archivos**. **Omitir** deja la carpeta vigilada sin escanear el contenido anterior; **Cancelar** cancela la incorporación de la carpeta. Puedes usar **Analizar ahora** más tarde.
3. En **Archivos detectados**, combina búsqueda y filtros. Selecciona una fila para revisar nombre, ruta, fechas, estado y destino sugerido.
4. Pulsa **Organizar** y confirma el destino. OrdenIA moverá el archivo solo entonces. En **Historial**, **Deshacer** intenta restaurarlo con un nombre libre si el original está ocupado.

El botón **Copiar ruta** y las celdas de ruta copian la ruta completa al portapapeles. **Abrir ubicación** selecciona el archivo en Explorer cuando está disponible.

## Exclusiones

Escáner y watcher ignoran `desktop.ini`, `Thumbs.db`, `ehthumbs.db`, `.DS_Store`, nombres `~$*`, temporales como `.tmp`, `.temp`, `.part` y `.crdownload`, y directorios `$RECYCLE.BIN`, `System Volume Information`, `__pycache__`, `.git`, `.venv`, `node_modules` y los destinos administrados por OrdenIA. No se siguen enlaces simbólicos.

## Arquitectura y seguridad

| Módulo | Responsabilidad |
| --- | --- |
| `core/` | Clasificación, exclusiones, destinos y movimientos sin sobrescritura |
| `database/` | Esquema, migración y consultas SQLite |
| `monitoring/` | Eventos del sistema de archivos |
| `scanning/` | Recorrido de archivos existentes sin leer su contenido |
| `services/` | Casos de uso, lotes y coordinación de hilos |
| `platform/` | Abrir archivos y ubicaciones según el sistema operativo |
| `ui/` | Ventana, páginas y widgets PySide6 |

Los registros usan una **clave de ruta normalizada** como identidad. Tamaño y fecha de modificación se actualizan si cambia el mismo archivo; no se calculan hashes. Escáner y watcher pueden detectar simultáneamente la misma ruta, pero SQLite conserva una sola fila. Las escrituras del escáner se hacen en lotes de 200 y la tabla presenta 200 resultados por página, por lo que no necesita cargar 10.000 filas en un widget. El escaneo, el conteo y los movimientos trabajan fuera del hilo de la UI; señales de Qt comunican progreso y cambios.

Ningún escaneo mueve archivos. Cada organización necesita confirmación explícita. Antes de registrar **Completado**, se verifica que el destino existe y el origen ya no. Los fallos quedan en el historial y los movimientos completados pueden deshacerse mientras el archivo siga en su destino. No se sobrescriben nombres existentes.

## Tests

```powershell
python -m pytest
```

Los tests usan carpetas temporales para escaneo, exclusiones, destinos, búsqueda, migración, movimientos, Deshacer, watchdog y UI sin pantalla.

## Limitaciones y roadmap

- La clasificación sigue basada solo en la extensión; no analiza contenido ni calcula hashes.
- Una carpeta inaccesible puede producir omisiones registradas en el log. Un cierre abrupto entre mover el archivo y escribir SQLite todavía puede requerir revisión manual.
- No hay instalador ejecutable ni organización automática.

| Versión | Objetivo |
| --- | --- |
| V0.1 | Monitorización y organización manual — completado |
| V0.2 | Escaneo, destinos, búsqueda y UX — actual |
| V0.3 | IA local |
| V0.4 | Búsqueda semántica |
| V0.5 | Detección de duplicados |
| V0.6 | Detección de proyectos |
| V0.7 | Automatizaciones |
| V1.0 | Asistente inteligente completo |

Licencia: Apache 2.0; consulta [LICENSE](LICENSE).
