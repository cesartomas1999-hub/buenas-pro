# Buenas pro del día

Página personal que cada mañana muestra las buenas pro (adjudicaciones) del día anterior en todo el Perú.

- **Fuente:** API pública OCDS del OECE (`/api/v1/recordsAfter`).
- **`descargar.py`:** baja los datos y los guarda en `data/` (un archivo por día, 30 días de historial).
- **`index.html`:** la página (buscador, filtros, copiar a Excel, selector de día).
- **`.github/workflows/actualizar.yml`:** GitHub lo ejecuta todos los días a las 6:00 a. m. de Lima y publica la página en GitHub Pages.

Probar en la PC:

```
python descargar.py --fecha 2026-10-29
python -m http.server 8765
```

y abrir http://localhost:8765

Límites de la API del OECE: no publica los integrantes de los consorcios ni el teléfono de los ganadores.
