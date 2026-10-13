# Capturas de evidencia (completar antes de entregar)

Guardá acá las capturas del dashboard (Phoenix o LangSmith). Nombres sugeridos:

| Archivo | Qué debe verse |
|---|---|
| `01_trazas.png` | Lista de trazas del proyecto, con un trace abierto mostrando los spans por nodo (supervisor → researcher → analyst → validator → synthesizer) |
| `02_costo_por_ejecucion.png` | Costo de cada una de las 5 ejecuciones concurrentes (columna *Cost* de la tabla de traces, o el total del proyecto) |
| `03_latencia_p95.png` | Latencia de la corrida de 5 peticiones (cuantiles de latencia del proyecto / tabla ordenada por latencia) |
| `04_hitl_pausado.png` *(opcional)* | Traza de una tarea crítica que quedó pausada hasta la aprobación |

Cómo generar la corrida: ver README, sección "Prueba de carga y capturas".
