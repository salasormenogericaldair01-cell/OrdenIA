# OrdenIA V0.4

OrdenIA es una aplicación de escritorio para Windows que vigila, indexa y ayuda a organizar archivos con confirmación humana. V0.4 añade **análisis inteligente local opcional mediante Ollama**: interpreta el contenido ya extraído, propone tipo, tema, etiquetas y una ruta jerárquica, y explica brevemente la propuesta.

La IA solo propone. Nunca mueve, elimina ni sobrescribe archivos. Todo movimiento continúa pasando por el diálogo de confirmación, `FileService` y el organizador seguro y reversible.

## Privacidad

- La extracción, búsqueda y clasificación inteligente se ejecutan en el equipo del usuario.
- OrdenIA V0.4 no usa APIs cloud, claves API, telemetría ni servicios externos.
- El cliente Ollama acepta únicamente un endpoint HTTP de loopback (`127.0.0.1`, `localhost` o `::1`).
- No se envían documentos a Internet. Ollama no está incluido en OrdenIA y debe instalarse por separado.
- Texto, sugerencias, feedback e índice FTS se guardan localmente en `%LOCALAPPDATA%\OrdenIA\ordenia.sqlite3`.
- Quien tenga acceso a la base puede leer el texto indexado. Protege la cuenta de Windows y sus copias de seguridad como los documentos originales.

## Funciones

- Monitorización con `watchdog`, escaneo de archivos existentes y reconciliación del índice.
- Exclusión centralizada de temporales, archivos del sistema y destinos administrados.
- Extracción local de PDF, DOCX, XLSX, PPTX, texto y código, sin ejecutar contenido.
- Búsqueda por nombre, ruta y contenido con SQLite FTS5 y fallback local.
- Clasificación por extensión y destinos dentro de la carpeta vigilada, biblioteca central o carpeta personalizada.
- Movimiento manual verificado, sin sobrescrituras y con **Deshacer**.
- Sugerencias IA locales con tipo de documento, tema, etiquetas, ruta relativa, confianza orientativa y razón.
- Feedback de rutas corregidas usado como contexto en sugerencias posteriores; no hay entrenamiento ni fine-tuning.
- Reintentos con backoff del watcher y SQLite WAL para mejorar la concurrencia.

## IA local con Ollama

### Qué hace

OrdenIA construye un contexto compacto a partir del nombre, extensión, categoría, metadatos, palabras clave, fragmentos del contenido, carpetas existentes y unos pocos feedback anteriores relevantes. El contexto está limitado a aproximadamente 6.000 caracteres; no se envían documentos completos innecesariamente.

El modelo devuelve JSON estructurado. OrdenIA valida tipos, longitudes, confianza y ruta antes de persistir la sugerencia. Se rechazan rutas absolutas, unidades, UNC, variables de entorno, `..` y caracteres peligrosos.

El contenido se delimita como **datos no confiables**. Frases como “ignore previous instructions”, “delete the file” o “return C:\Windows” no se ejecutan y no pueden saltarse la validación ni la confirmación humana.

### Instalación manual

1. Instala Ollama desde su distribución oficial y ejecútalo localmente.
2. Instala un modelo pequeño apropiado para 16 GB de RAM. Recomendación inicial:

```powershell
ollama pull qwen3:4b-instruct
```

OrdenIA nunca ejecuta ese comando ni descarga modelos automáticamente. Para instalaciones nuevas recomienda `qwen3:4b-instruct`, una variante orientada a instrucciones y salida breve. Una configuración existente, por ejemplo `qwen3:4b`, se conserva y puede seguir utilizándose.

3. Abre **Configuración → IA local**, escribe el nombre del modelo y pulsa **Comprobar conexión**.
4. En **Archivos detectados**, selecciona un archivo y pulsa **Analizar con IA**. Si hace falta, OrdenIA prepara primero su índice de contenido.
5. Revisa la sugerencia. **Usar sugerencia** permite editar la ruta relativa y abre la confirmación normal de organización.

Si Ollama está cerrado o el modelo no está instalado, OrdenIA muestra una explicación y todas las funciones de V0.3 siguen disponibles.

### Estados de IA

Los estados son **Sin analizar**, **Analizando**, **Listo**, **Error**, **Desactualizado** e **IA local no disponible**. Son independientes del estado del archivo y del análisis de contenido. Un cambio de tamaño o `mtime` marca la sugerencia como desactualizada; mover o deshacer un archivo sin cambiarlo conserva la sugerencia.

La confianza **Baja / Media / Alta** es un score orientativo producido por el modelo, no una probabilidad científica.

## Formatos de contenido

| Tipo | Extensiones | Datos extraídos |
| --- | --- | --- |
| PDF | `.pdf` | Texto por página, páginas, título, autor y asunto; sin OCR |
| Word | `.docx` | Párrafos, encabezados, tablas y propiedades básicas |
| Excel | `.xlsx` | Nombres de hojas y valores; `read_only`, sin evaluar fórmulas |
| PowerPoint | `.pptx` | Texto, formas, tablas y notas disponibles |
| Texto | `.txt`, `.md`, `.log`, `.csv` | Contenido textual |
| Código | `.py`, `.js`, `.ts`, `.tsx`, `.jsx`, `.java`, `.c`, `.cpp`, `.h`, `.hpp`, `.ino`, `.html`, `.css`, `.json`, `.yaml`, `.yml`, `.toml`, `.sql`, `.sh`, `.ps1`, `.bat`, `.xml` | Texto; nunca se ejecuta |

Los formatos heredados `.doc`, `.xls` y `.ppt` pueden organizarse, pero no se extraen en V0.4. No hay OCR ni conversión mediante aplicaciones externas.

## Límites

El análisis de contenido admite hasta 50 MB; Office moderno hasta 20 MB, 100 MB descomprimidos y 10.000 entradas. Se guardan hasta 250.000 caracteres por archivo. PDF se limita a 250 páginas, PPTX a 300 diapositivas y XLSX a 50.000 celdas y 200 columnas por hoja.

La cola de IA usa **un solo worker** para evitar cargar varios modelos o saturar equipos sin GPU dedicada. La conexión local usa un timeout corto y la inferencia un máximo de cinco minutos. Cancelar detiene trabajos pendientes; una inferencia activa termina de forma segura y su resultado se descarta si el trabajo fue cancelado.

## Desarrollo

Requiere Python 3.12 o posterior:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
ordenia
```

Tests normales, sin Ollama:

```powershell
python -m pytest
```

Prueba opcional del servicio local:

```powershell
$env:ORDENIA_RUN_OLLAMA_TESTS = "1"
python -m pytest -m ollama
```

## Benchmark local opcional

Con Ollama y el modelo instalados:

```powershell
python scripts\benchmark_local_ai.py --model qwen3:4b-instruct
```

Para comparar dos modelos instalados en una sola ejecución:

```powershell
python scripts\benchmark_local_ai.py --model qwen3:4b-instruct --model qwen3:4b
```

Usa seis documentos sintéticos y registra tiempo, caracteres enviados, carga del modelo, validez JSON y resultado por caso. Un timeout no detiene los casos restantes. No realiza assertions de calidad ni usa documentos personales.

## Build Windows

PyInstaller es una dependencia separada de empaquetado:

```powershell
python -m pip install -e ".[packaging]"
.\packaging\build_windows.ps1
```

Salida principal:

```text
dist\OrdenIA\OrdenIA.exe
```

El build one-folder ejecuta un smoke test real: abre y cierra Qt, migra SQLite, comprueba WAL, FTS5 y las tablas V0.4. El EXE abre sin Ollama; Ollama y los modelos no se empaquetan.

Si Inno Setup 6 está instalado, también genera:

```text
dist\installer\OrdenIA-Setup-0.4.0.exe
```

Los datos permanecen en `%LOCALAPPDATA%\OrdenIA`; instalar, actualizar o desinstalar los binarios no elimina esa carpeta.

## Migración desde V0.3.1

No borres `ordenia.sqlite3`. Al iniciar V0.4 se crean de forma determinista `ai_suggestions`, `ai_feedback` y `ai_settings`, junto con el trigger de invalidación. Se conservan carpetas, archivos, estados, contenido, FTS5, operaciones, historial y configuración previa. No se analiza ningún documento con IA automáticamente durante la migración.

## Arquitectura

| Módulo | Responsabilidad |
| --- | --- |
| `analysis/` | Extracción objetiva, límites, metadatos, palabras clave y vista previa |
| `ai/` | Contexto acotado, prompts, parser estricto, modelos y proveedores locales |
| `services/content_service.py` | Cola de extracción de contenido |
| `services/ai_service.py` | Cola IA de un worker, cancelación, estados y validación de fingerprint |
| `database/` | Migraciones, FTS5, sugerencias, feedback y preferencias |
| `core/` | Clasificación, exclusiones, destinos y movimientos seguros |
| `monitoring/` y `scanning/` | Watcher y recorrido del sistema de archivos |
| `ui/` | Presentación y confirmación humana; no extrae contenido ni llama al organizador directamente |

Flujo de seguridad:

```text
Archivo → ContentService → AIService → AISuggestion → UI
        → confirmación humana → FileService → Organizer
```

`AIService` no importa `Organizer` ni tiene acceso a operaciones de archivos.

## Limitaciones V0.4

- La calidad depende del modelo local elegido y del contenido extraíble.
- No hay OCR, embeddings, base vectorial, RAG complejo ni búsqueda semántica.
- No hay organización automática ni por lotes sin confirmación.
- No hay APIs cloud, fine-tuning, telemetría, voz ni agentes autónomos.
- La cancelación no interrumpe brutalmente una inferencia ya iniciada por Ollama.

## Roadmap

| Versión | Objetivo |
| --- | --- |
| V0.1 | Monitorización y organización segura — completado |
| V0.2 | Escaneo, búsqueda, destinos y reconciliación — completado |
| V0.3 | Análisis e indexación local de contenido — completado |
| V0.3.1 | Robustez Windows y empaquetado — completado |
| V0.4 | IA local con sugerencias y feedback — actual |
| V0.5 | Búsqueda semántica y proyectos |
| V1.0 | Asistente inteligente completo |

Licencia: Apache 2.0; consulta [LICENSE](LICENSE).
