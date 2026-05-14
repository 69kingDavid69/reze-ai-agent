# Deploy de Reze a Fly.io (free tier)

**Costo esperado:** $0/mes mientras te alcance el crédito gratis de $5/mes que Fly.io te da.
Con 1 VM 512MB durmiéndose cuando no hay tráfico → consumo real ~$1-3/mes (sobra crédito).

---

## 1. Instalación de `flyctl` (una sola vez)

**macOS:**
```bash
brew install flyctl
```

**Linux / WSL:**
```bash
curl -L https://fly.io/install.sh | sh
```

**Windows (PowerShell):**
```powershell
iwr https://fly.io/install.ps1 -useb | iex
```

---

## 2. Login en Fly.io

```bash
flyctl auth signup    # primera vez (te abre el browser)
# o si ya tenés cuenta:
flyctl auth login
```

> **Sin tarjeta**: Fly.io no exige tarjeta para el free tier. Si te la pide cuando creás la app, podés tirar `flyctl orgs create <nombre>` antes y agregar tarjeta solo si querés ir más allá del free.

---

## 3. Crear la app (primera vez)

Parate en la raíz del proyecto:
```bash
cd /Users/user/Desktop/AI-automatisacion/semana-7/anime-agent/.claude/worktrees/hopeful-driscoll-00a825
```

Lanza el wizard:
```bash
flyctl launch --no-deploy --copy-config --region scl
```

Te va a preguntar:
- **App name**: elegí algo único, ej. `reze-tuusuario`. Va a ser `reze-tuusuario.fly.dev`.
- **Org**: la tuya personal.
- **Region**: `scl` (Santiago) — ya lo seteamos en `fly.toml`.
- **Set up PostgreSQL?** → **NO**.
- **Set up Redis?** → **NO**.
- **Deploy now?** → **NO** (todavía falta el volumen y los secrets).

> El comando va a actualizar `fly.toml` con tu `app = "reze-tuusuario"`. Eso queda commiteado en el repo.

---

## 4. Crear el volumen persistente (1GB free)

El volumen guarda `reze_state.db` (memoria de Reze) y el cache del modelo de embeddings. Sin esto, cada deploy borra la memoria.

```bash
flyctl volumes create reze_data --size 1 --region scl --yes
```

Te va a advertir que un solo volumen no tiene HA — ignoralo, es tu app personal.

---

## 5. Setear las API keys como secrets

```bash
flyctl secrets set \
    DEEPSEEK_API_KEY=tu_deepseek_key_aqui \
    ELEVENLABS_API_KEY=tu_elevenlabs_key_aqui
```

> Los secrets se inyectan como variables de entorno, **no quedan commiteados** y sobreviven entre deploys.

Para verificarlos:
```bash
flyctl secrets list
```

---

## 6. Primer deploy

```bash
flyctl deploy
```

La primera vez tarda **5-10 min** (build de imagen + pre-descarga del modelo de embeddings + push a Fly).

Al final te muestra:
```
Visit your newly deployed app at https://reze-tuusuario.fly.dev/
```

---

## 7. Probarlo

Abrí esa URL en cualquier browser (PC, celular, tablet) y ya está. Reze responde.

> **Si la primera respuesta tarda 10-15s**: es porque la app estaba durmiendo (config `auto_stop_machines = stop` para ahorrar créditos). La siguiente request ya está caliente.

---

## Comandos útiles

| Qué querés hacer | Comando |
|---|---|
| Ver logs en vivo | `flyctl logs` |
| Reiniciar | `flyctl machine restart` |
| Re-deploy después de cambios | `flyctl deploy` |
| Ver tu URL pública | `flyctl info` |
| Ver consumo / créditos restantes | `flyctl billing` |
| Mantener despierta (sin sleep) | Editar `fly.toml`: `min_machines_running = 1` |
| Apagar la app (no consume más) | `flyctl scale count 0` |
| Volver a prenderla | `flyctl scale count 1` |
| Borrar la app entera | `flyctl apps destroy reze-tuusuario` |

---

## Troubleshooting

### "Out of memory" en deploy
La VM 512MB es justa con el modelo de embeddings cargado. Si pasa, bumpeá:
```bash
flyctl scale memory 1024
```
Eso te lleva a ~$5.50/mes. Apenas sobre el free tier — si tenés tarjeta agregada te cobran ~$1/mes; sin tarjeta, Fly apaga la app.

### "Volume not mounted"
Verificá:
```bash
flyctl volumes list
```
Si el volumen existe pero no se monta, revisar `[[mounts]]` en `fly.toml` y re-deploy.

### Cold start lento
La app se duerme tras unos minutos sin tráfico (config actual). Si querés que esté siempre caliente, cambiá en `fly.toml`:
```toml
[http_service]
  auto_stop_machines = "off"
  min_machines_running = 1
```
Eso consume créditos 24/7 (~$3-5/mes en lugar de ~$1-2/mes).

### "Embeddings model failed to load"
El modelo se baja en build time pero a veces hay fallos transitorios. Re-deploy:
```bash
flyctl deploy --no-cache
```

---

## Costos reales esperados

| Componente | Costo |
|---|---|
| Fly.io VM 512MB shared CPU, durmiendo en idle | $1-3/mes (free tier cubre) |
| Fly.io volumen 1GB | $0.15/mes (free tier cubre) |
| DeepSeek API (LLM) | <$1/mes uso personal |
| ElevenLabs free tier | $0 (10k chars/mes incluidos) |
| **Total realista** | **$0-1/mes** |

Si pasás los 10k chars/mes en TTS, ElevenLabs te empieza a cobrar $5/mes por su plan Starter (30k chars).
