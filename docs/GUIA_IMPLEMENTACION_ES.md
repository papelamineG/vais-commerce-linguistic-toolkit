# Guía de Implementación (Español): Solución de Género Gramatical, Acentos y Sinónimos en Vertex AI Search for Commerce (VAIS:C)

Esta guía explica de forma sencilla y práctica **por qué** ocurren diferencias de resultados al buscar productos con distinto género gramatical (por ejemplo, **`"cadeira preto"` vs. `"cadeira preta"`** en portugués o **`"silla negro"` vs. `"silla negra"`** en español), sin acentos (**`"sofa"` vs. `"sofá"`**, **`"cadeirao"` vs. `"cadeirão"`**) o con sinónimos locales (**`"poltrona"` vs. `"cadeirão"`**), y cómo solucionarlo de forma definitiva con este repositorio.

---

## 1. ¿Por qué ocurre este comportamiento? (Explicación Sencilla)

En los catálogos de e-commerce en lenguas romances (portugués `pt-PT`, español `es-ES`, francés `fr-FR`, italiano `it-IT`), los atributos de color y acabado suelen guardarse en **un único género** (habitualmente masculino singular):
* **En el catálogo**: El producto tiene `title: "Cadeira de jantar ... Preto Claro"` y `colorInfo.colors: ["Preto Claro"]`. La palabra `"preta"` (femenino) no aparece escrita literalmente en la ficha del producto.
* **Cómo busca el usuario**: Como *cadeira* o *mesa* son sustantivos femeninos, el usuario busca **`"cadeira preta"`** o **`"mesa branca"`**; en móvil suele omitir tildes (**`"sofa preto"`**, **`"armario"`**, **`"candeeiro de pe"`**) o emplear sinónimos (**`"poltrona"`**).

Cuando el usuario busca **`"cadeira preto"`**, el buscador encuentra una **coincidencia exacta (exact match)** con `"Preto"` en el título y en el atributo `colorInfo.colors`.  
En cambio, cuando busca **`"cadeira preta"`**:
1. **Pérdida de coincidencia exacta**: `"preta"` no coincide literalmente con `"Preto"`.
2. **Reescritura interna de consultas (`qrewrite`)**: Al no encontrar `"preta"` literalmente en `colorInfo.colors`, el motor interno de expansión de consultas intenta traducir o expandir el término (por ejemplo, inyectando internamente sinónimos en otros idiomas como `"black"` o `"red"`), lo que altera el orden de relevancia (ranking) frente a `"cadeira preto"`.

---

## 2. La Solución en 3 Pilares

Para resolver tanto los **géneros gramaticales (`preto` / `preta`)** como la **carga masiva de sinónimos y acentos desde CSV**, este repositorio implementa tres componentes complementarios:

### Pilar 1: Carga Masiva de Sinónimos Bidireccionales por API desde CSV/JSON (`sync-controls`)
* **Resuelve la limitación de la consola web**: La interfaz gráfica de Merchandising Console no permite subir un archivo CSV con reglas de sinónimos de forma masiva.
* **Cómo funciona**:
  * Hemos preparado un diccionario completo para mobiliario y decoración en portugués (`data/pt_pt_furniture_dictionary.csv`) y español (`data/es_es_furniture_dictionary.csv`) que podéis abrir y editar directamente en **Excel o Google Sheets**.
  * El comando `sync-controls` lee ese archivo CSV y crea/actualiza automáticamente los controles **`TwowaySynonymsAction`** en Vertex AI Search for Commerce a través de la API oficial (`ControlService` + `ServingConfigService`), asociándolos a `default_search`.
  * Además, podéis usar el comando `generate` (con **Gemini**) para extraer automáticamente nuevos colores, materiales o categorías de vuestro catálogo y ampliar el CSV sin trabajo manual.

### Pilar 2: Enriquecimiento del Feed de Catálogo (`enrich-catalog`)
* **Por qué es fundamental**: Añadir el sinónimo bidireccional `preto ↔ preta` ayuda al buscador a recuperar los productos, pero incluir **ambos géneros** directamente en `colorInfo.colors` del producto garantiza un **100% de coincidencia exacta nativa**, evitando que el reescritor interno (`qrewrite`) altere el ranking.
* **Qué hace automáticamente**:
  1. Asegura que todos los productos tengan `"languageCode": "pt-PT"`.
  2. Mapea el color a las 13 familias estándar de Google en **`colorInfo.colorFamilies`** (`"Black"`, `"White"`, `"Gray"`, `"Brown"`, etc.).
  3. Expande **`colorInfo.colors`** con masculino, femenino, plural y variantes compuestas:
     * Entrada: `{"colors": ["Preto Claro"]}`
     * Salida enriquecida: `{"colorFamilies": ["Black"], "colors": ["Preto Claro", "Preto", "Preta", "Pretos", "Pretas", "Negro", "Negra", "Negros", "Negras", "Preta Clara"]}`
  4. Añade el atributo buscable `attributes.linguistic_search_terms` con términos sin tilde (`moveis`, `cadeirao`, `sofa`) y sinónimos.

### Pilar 3: Diagnóstico A/B (`attributionToken`) y Miniatura de Variante en el Frontend
* **Miniatura correcta al buscar un color (`"cadeira preta"`)**:
  * Cuando un usuario busca `"cadeira preta"`, la API de búsqueda devuelve el producto `PRIMARY` junto con las variantes que coinciden con el color dentro de `results[].product.variants`.
  * La imagen principal del producto padre (`results[].product.images[0].uri`) se selecciona de forma aleatoria entre sus variantes (por eso en la vista previa de la consola puede aparecer la foto de la silla en color madera o blanco aunque el título sea el correcto).
  * **Solución en el Frontend**: En la página de resultados de búsqueda, renderizar siempre **`results[].product.variants[0].images[0].uri`** cuando exista, usando `results[].product.images[0].uri` como fallback (ver función `resolve_display_image` en `src/diagnostics.py`).

---

## 3. Paso a Paso de Ejecución

### Paso 1: Instalación y Configuración
```bash
git clone https://github.com/papelamineG/vais-commerce-linguistic-toolkit.git
cd vais-commerce-linguistic-toolkit

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```
Edita el archivo `.env` con los datos del proyecto de Google Cloud:
```ini
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_PROJECT_NUMBER=123456789012
GEMINI_MODEL=gemini-3.8-flash
GOOGLE_CLOUD_LOCATION=global
VAIS_LOCATION=global
VAIS_CATALOG=default_catalog
VAIS_BRANCH=0
VAIS_SERVING_CONFIG=default_search
VAIS_DEFAULT_LANGUAGE_CODE=pt-PT
```

### Paso 2: Previsualizar y Publicar los Sinónimos desde el CSV (`sync-controls`)
1. Puedes revisar o añadir filas en [`data/pt_pt_furniture_dictionary.csv`](../data/pt_pt_furniture_dictionary.csv) con cualquier editor de hojas de cálculo.
2. Ejecuta primero en modo **Dry-Run** (sin cambios en producción) para verificar los payloads:
   ```bash
   python3 -m src.cli sync-controls \
     --dictionary data/pt_pt_furniture_dictionary.csv
   ```
3. Ejecuta con `--apply` para crear/actualizar los controles en Vertex AI Search for Commerce y vincularlos a `default_search`:
   ```bash
   python3 -m src.cli sync-controls \
     --dictionary data/pt_pt_furniture_dictionary.csv \
     --apply
   ```
   *(Nota: Los controles tardan unos **5 minutos** en propagarse a producción y a la pestaña Evaluate de la consola).*

### Paso 3: Enriquecer el Feed del Catálogo (`enrich-catalog`)
Antes de importar el catálogo en VAIS:C (o como paso en vuestro pipeline de ingesta), ejecuta:
```bash
python3 -m src.cli enrich-catalog \
  --input examples/sample_catalog_pt.jsonl \
  --output examples/enriched_catalog_pt.jsonl \
  --dictionary data/pt_pt_furniture_dictionary.csv \
  --language pt-PT
```

### Paso 4: Comparar `"cadeira preto"` vs. `"cadeira preta"` y Obtener `attributionToken` (`diagnose`)
Para verificar la similitud del Top-20 entre dos búsquedas y capturar el `attributionToken` de cada una:
```bash
python3 -m src.cli diagnose \
  --query-a "cadeira preto" \
  --query-b "cadeira preta"
```
Si tras aplicar los Pilares 1 y 2 existiera algún término específico donde el reescritor interno de Google (`qrewrite`) siguiera inyectando una expansión no deseada, comparte los dos valores de `attribution_token` generados por este comando con el equipo de Google Cloud para bloquear esa expansión interna.
