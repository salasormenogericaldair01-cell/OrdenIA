# OrdenIA

OrdenIA es una aplicación de escritorio para Windows que vigila carpetas elegidas por el usuario y sugiere dónde clasificar los archivos. Funciona localmente. La versión **V0.1** usa reglas por extensión; todavía no utiliza inteligencia artificial.

## Estado actual: V0.1

- Vigila en tiempo real carpetas activadas con `watchdog`, incluidas sus subcarpetas.
- Detecta archivos nuevos y modificados cuando su tamaño y fecha dejan de cambiar brevemente.
- Ignora carpetas y archivos temporales comunes, y evita volver a detectar los archivos dentro de `OrdenIA/`.
- Clasifica por extensión en Documentos, Hojas de cálculo, Presentaciones, Imágenes, Videos, Audio, Comprimidos, Código, Instaladores y Otros.
- Muestra archivos, estado, estadísticas e historial en una interfaz oscura con PySide6.
- Propone un destino `<carpeta vigilada>/OrdenIA/<Categoría>/` y **solo mueve tras una confirmación explícita**.
- Verifica que el archivo llegue al destino y desaparezca del origen antes de registrar una operación como **Completado**. Los intentos fallidos quedan como **Fallido** sin cambiar el archivo a Organizado.
- Evita reemplazar nombres existentes mediante sufijos `(1)`, `(2)`, etc.; permite deshacer movimientos si el archivo sigue en destino. El movimiento original cambia a **Deshecho**.
- Al pulsar una ruta en Archivos detectados o Historial, copia la ruta completa al portapapeles. El tooltip muestra la ruta completa aunque la tabla la recorte.
- Guarda carpetas, archivos, movimientos y preferencias en SQLite. Las carpetas eliminadas se ocultan, pero se conserva su referencia para el historial.

OrdenIA no elimina archivos ni organiza automáticamente. No analiza contenido, no busca mediante lenguaje natural y no se conecta a servicios externos.

## Requisitos e instalación

- Windows y Python **3.12 o posterior**.
- PowerShell (los comandos también se pueden adaptar a otra terminal).

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Si no hay Python 3.12, puedes crear el entorno con `python -m venv .venv` usando otra versión compatible posterior. La instalación editable trae PySide6 y watchdog; el extra `dev` añade pytest.

## Ejecutar

```powershell
ordenia
```

O bien:

```powershell
python -m ordenia.main
```

En **Carpetas vigiladas**, añade cualquier carpeta con el selector. Solo los eventos que ocurran mientras esté activa se registran; esta versión no hace un escaneo histórico al añadirla. En **Archivos detectados**, selecciona un pendiente y confirma **Organizar** para moverlo. En **Historial**, selecciona el movimiento y pulsa **Deshacer** para restaurarlo. Si la ruta original ya contiene otro archivo, la restauración elige un nombre libre.

La base SQLite de la V0.1 anterior se migra al abrirla. Las operaciones antiguas sin verificación física se marcan como **Fallido** si el destino no existe o todavía existe el origen; no se borran archivos ni registros.

La base de datos y el log se guardan en `%LOCALAPPDATA%\OrdenIA\` (o en `~/AppData/Local/OrdenIA` si la variable no existe). No hace falta elegir una ruta fija como Escritorio o Descargas.

## Tests

```powershell
python -m pytest
```

## Arquitectura

| Área | Responsabilidad |
| --- | --- |
| `src/ordenia/core/` | Reglas de clasificación, validaciones y movimientos seguros |
| `src/ordenia/database/` | Esquema, modelos y repositorio SQLite |
| `src/ordenia/monitoring/` | Eventos de watchdog y espera de estabilidad en segundo plano |
| `src/ordenia/services/` | Casos de uso y coordinación entre hilos, archivos y base de datos |
| `src/ordenia/ui/` | Ventana, páginas y componentes PySide6 |
| `tests/` | Clasificación, colisiones, movimientos, reversión y persistencia |

El watcher y los movimientos trabajan fuera del hilo de la interfaz. La interfaz se actualiza mediante señales de Qt. El motor de clasificación expone un protocolo para cambiar las reglas por un clasificador futuro.

## Limitaciones actuales

- La clasificación se basa solo en la extensión; no comprueba el contenido.
- No hay indexación de archivos ya existentes ni búsqueda avanzada.
- Si una copia está en curso más de unos segundos, se omite ese evento; una modificación posterior puede volver a detectarla.
- Deshacer requiere que el archivo permanezca en el destino registrado.
- No incluye instalador ejecutable; se ejecuta desde Python.

## Roadmap

| Versión | Objetivo |
| --- | --- |
| V0.1 | Monitorización y organización manual |
| V0.2 | Análisis de contenido |
| V0.3 | IA local |
| V0.4 | Búsqueda semántica |
| V0.5 | Detección de duplicados |
| V0.6 | Detección de proyectos |
| V0.7 | Automatizaciones |
| V1.0 | Asistente inteligente completo |

Licencia: Apache 2.0; consulta [LICENSE](LICENSE).
