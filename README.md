# 🌈 Safe Space

**Directorio de recursos inclusivos con procedencia visible · Ciudad de México**

Workshop de [AWSpectrum LATAM](https://linktr.ee/awspectrum.latam) · *Cloud • Community • Diversity*

Safe Space ayuda a encontrar organizaciones, servicios de apoyo, centros comunitarios y canales de
derivación. Algunos recursos tienen una ubicación pública y aparecen en el mapa; otros funcionan por
teléfono, chat o canalización y se muestran únicamente como fichas de contacto.

> **Safe Space no certifica que un recurso sea universalmente seguro ni que esté disponible en todo
> momento.** El directorio muestra la fuente, la fecha de revisión y el estado de publicación. Las
> derivaciones a refugios nunca publican la dirección protegida.

## Qué vas a construir

En tres horas vas a desplegar, inspeccionar, entender y modificar una aplicación serverless real en
AWS. No la escribes desde cero: el objetivo es poder contar la historia de una petición y explicar
por qué cada servicio existe.

```mermaid
flowchart TB
    U["👤 Persona"] --> F["🌈 Safe Space<br>HTML + CSS + JS"]
    S3["Amazon S3<br>sitio estático"] --> F
    F --> LOC["Amazon Location<br>mapa opcional"]
    F --> API["API Gateway<br>HTTP API"]
    API --> RF["PlacesFunction<br>AWS Lambda"]
    API --> SF["SearchFunction<br>AWS Lambda"]
    RF --> DB["Amazon DynamoDB<br>recursos"]
    SF --> BR["Amazon Bedrock<br>Nova Micro"]
```

| Servicio | Para qué lo usamos |
| --- | --- |
| **Amazon S3** | Alojar la interfaz estática. |
| **Amazon Location** | Dibujar ubicaciones públicas, fuera del flujo de datos de la API. |
| **API Gateway HTTP API** | Exponer `/resources` y `/search`. |
| **AWS Lambda** | Validar propuestas y extraer intención de búsqueda. |
| **Amazon DynamoDB** | Persistir recursos aprobados y propuestas pendientes. |
| **Amazon Bedrock · Nova Micro** | Convertir lenguaje natural en criterios permitidos. |
| **AWS SAM** | Describir y desplegar toda la infraestructura. |

Todo vive en **`us-east-1`**.

## Puesta en marcha

No hace falta instalar nada: ni AWS CLI, ni SAM, ni Python, ni Git, ni Docker, ni VS Code.

**1. Haz un fork de este repositorio** a tu cuenta de GitHub, con el botón *Fork*.

**2. Desde tu fork**, abre **Code ▸ Codespaces ▸ Create codespace on main**, con la máquina
de **2 núcleos**. GitHub construye el entorno del taller y abre VS Code en el navegador con
todo listo.

**3. En la terminal del Codespace, inicia sesión en AWS:**

```bash
aws login --remote --region us-east-1
```

Se abre una URL en tu navegador; al terminar, pegas el código en la terminal.

**4. Comprueba quién eres:**

```bash
aws sts get-caller-identity
```

**5. Despliega:**

```bash
./scripts/preflight.sh
sam build
sam deploy
./scripts/publish-frontend.sh
python3 scripts/seed.py
```

`publish-frontend.sh` imprime la URL de tu aplicación. El seed contiene recursos aprobados con
fuentes directas revisadas; las propuestas nuevas no se publican automáticamente.

Para reemplazar expresamente todo el contenido anterior de la tabla:

```bash
python3 scripts/seed.py --replace
```

`--replace` es deliberado: elimina los items existentes antes de cargar la semilla nueva.

### Qué acaba de pasar en el paso 3

Tu Codespace ya traía la AWS CLI instalada, pero AWS todavía no sabía quién eras.
`aws login --remote` abrió una sesión temporal —dura 12 horas— y a partir de ahí la terminal
puede actuar con tu identidad de AWS.

Es la diferencia que conviene retener: **tener la AWS CLI no es lo mismo que estar
autenticada en AWS**. El `--remote` está porque el Codespace es una máquina remota sin
navegador propio: en vez de abrirlo él, te da una URL para que la abras tú.

Este taller nunca te pide copiar una access key. Las credenciales que usa son temporales y
caducan solas.

### Si tu cuenta no es así

`aws login` funciona con el usuario **root** de tu cuenta y con **usuarios IAM**. Dos casos
necesitan otro camino:

| Tu cuenta | Qué hacer |
|---|---|
| Usa **IAM Identity Center** (típico en cuentas de empresa o escuela) | `aws login` no sirve. Usa `aws configure sso` una vez y después `aws sso login` |
| Eres un **usuario IAM** y `aws login` da un error de permisos | Necesitas la policy gestionada `SignInLocalDevelopmentAccess`. Pídesela a quien administre la cuenta |
| Eres **root** | No necesitas permisos adicionales |

Si ninguno funciona, sigue por el **plan B** del final de esta página: en AWS CloudShell las
credenciales son automáticas y no hace falta iniciar sesión.

## Editar el proyecto

La razón de trabajar en Codespaces y no en una terminal suelta es esta: a partir de aquí vas
a **cambiar código**, y VS Code te da el explorador de archivos, pestañas, búsqueda en todo
el repositorio, resaltado de sintaxis, el panel de *Source Control* y la terminal integrada,
todo en la misma ventana.

Tu Codespace tiene dos remotos:

| Remoto | Qué es |
|---|---|
| `origin` | **tu fork** — aquí subes tus cambios |
| `upstream` | el repositorio original del taller — de aquí vienen las actualizaciones |

Trabaja en una rama y sube a tu fork:

```bash
git switch -c mi-rama

# editas desde VS Code…

git status
git diff

python3 -m unittest discover -s tests -v      # si tocaste las funciones
node --check frontend/app.js                  # si tocaste el frontend
sam validate --lint                           # si tocaste template.yaml

git add .
git commit -m "describe tu cambio"
git push -u origin mi-rama
```

Puedes hacer todo esto desde la terminal o desde el panel de *Source Control*; es el mismo
Git. Abrir un pull request hacia `upstream` es opcional.

## Al terminar

```bash
./scripts/cleanup.sh   # borra el stack y todo lo que creaste en AWS
aws logout             # cierra la sesión temporal
```

Después, **detén o elimina tu Codespace** desde
[github.com/codespaces](https://github.com/codespaces). Un Codespace encendido sigue
consumiendo tu cuota de horas aunque no lo estés usando.

## 🛟 Plan B — AWS CloudShell

Si no puedes crear el Codespace —te quedaste sin cuota, GitHub tiene problemas o el entorno
falla al construirse— el taller entero funciona igual desde **AWS CloudShell**, que trae AWS
CLI, SAM, `python3`, `boto3` y `git` ya instalados, y donde las credenciales son automáticas:
no hace falta `aws login`.

Consola de AWS → región **N. Virginia (`us-east-1`)** → icono de CloudShell:

```bash
git clone https://github.com/itsebasvz/awspectrum-safe-space.git
cd awspectrum-safe-space
```

Y sigue desde el paso 5. Lo único que pierdes es comodidad para editar: el editor de
CloudShell es mucho más básico que VS Code.

## Las rutas de la API

```text
GET  /resources  → devuelve recursos aprobados
POST /resources  → guarda una propuesta como pending
POST /search     → convierte lenguaje natural en criterios
```

Obtén la URL de tu stack:

```bash
API=$(aws cloudformation describe-stacks --stack-name safe-space \
      --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)
```

Lista recursos:

```bash
curl "$API/resources"
```

Busca apoyo:

```bash
curl -X POST "$API/search" -H 'content-type: application/json' \
  -d '{"query":"necesito apoyo psicológico y quiero saber a dónde llamar"}'
```

Una respuesta típica tiene esta forma:

```json
{
  "query": "necesito apoyo psicológico y quiero saber a dónde llamar",
  "criteria": {
    "category": "support_service",
    "services": ["psychological_support"],
    "signals": []
  },
  "source": "bedrock"
}
```

Si Bedrock falla, `source` cambia a `fallback` y la extracción determinista mantiene el flujo.

## Contrato de un recurso

```json
{
  "id": "usipt-cdmx",
  "name": "USIPT · Unidad de Salud Integral para Personas Trans",
  "category": "support_service",
  "services": ["psychological_support", "legal_support", "healthcare"],
  "signals": ["lgbtq_affirming"],
  "address": "solo si la ubicación es pública",
  "latitude": 19.4545577,
  "longitude": -99.1509918,
  "contact": {
    "phone": "55 5132 1250",
    "website": "https://…"
  },
  "provenance": {
    "type": "direct_source",
    "sourceUrl": "https://…",
    "checkedAt": "2026-08-19"
  },
  "publicationStatus": "approved"
}
```

`latitude` y `longitude` son opcionales, pero deben aparecer juntas. Una `shelter_referral` no
puede tener dirección ni coordenadas: la seguridad de quien usa el refugio está por encima de la
completitud del mapa.

Los registros aprobados requieren `provenance.type = "direct_source"`, `sourceUrl` y `checkedAt`.
Una propuesta enviada desde el formulario recibe `publicationStatus = "pending"` y
`provenance.type = "community_submission"`; nunca se presenta como verificada.

## Qué hace —y qué no hace— la IA

Bedrock convierte una frase en `{category, services, signals}`. No consulta DynamoDB, no elige
recursos, no inventa teléfonos y no afirma que una organización esté abierta o disponible.

La allowlist de `template.yaml` valida la respuesta antes de que llegue al navegador. Después,
`frontend/app.js` filtra los recursos aprobados. La IA interpreta; el código decide.

## Taxonomía

La fuente de verdad vive en `template.yaml`:

- categorías: `organization`, `support_service`, `community_center`, `shelter_referral`;
- servicios: `psychological_support`, `legal_support`, `healthcare`, `referral`,
  `community_network`, `shelter_support`;
- señales: `lgbtq_affirming`, `free`, `open_24_7`, `contact_only`.

La plantilla pasa las tres listas a las Lambdas y `publish-frontend.sh` las escribe en
`frontend/config.js`. Ese archivo está en `.gitignore` porque contiene la API key de Amazon
Location y nunca debe commitearse.

## Probar cambios

Comprobaciones locales sin tocar AWS:

```bash
python3 -m unittest discover -s tests -v
node --check frontend/app.js
sam validate --lint
```

Para modificar código de Lambda durante el workshop:

```bash
sam sync --code
```

Para modificar la infraestructura o la taxonomía:

```bash
sam deploy
./scripts/publish-frontend.sh
```

El reto recomendado es añadir una ficha de contacto o derivación sin coordenadas y demostrar que
aparece en el directorio, pero no como pin.

## Workshop vs. producción

| En el workshop | En producción |
| --- | --- |
| Sitio estático público de S3 | HTTPS con CloudFront o Amplify y bucket privado |
| API sin autenticación | Autenticación, autorización y límites de tasa |
| `Scan` de DynamoDB sobre pocos recursos | Índices y patrones de acceso diseñados |
| Seed aprobado + propuestas `pending` | Moderación, auditoría y proceso de actualización |
| Coordenadas solo cuando son públicas | Revisión de privacidad y amenaza por recurso |
| Una base de datos por participante | Backend comunitario compartido |

## Limpieza

El cleanup es parte del workshop:

```bash
./scripts/cleanup.sh
```

Vacía el bucket y ejecuta `sam delete`. Antes de borrar recursos de una cuenta compartida, confirma
que el stack no esté siendo utilizado.

## Licencia

[MIT](LICENSE)
