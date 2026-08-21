# syntax=docker/dockerfile:1
#
# Entorno del workshop Safe Space.
#
# La imagen base fija Python 3.13 porque es el `Runtime` que declara template.yaml.
# No es una preferencia: `sam build` compara el intérprete local con el runtime de la
# función y aborta con MisMatchRuntimeError si no coinciden.
FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

# Versiones fijas para que las 71 personas del taller obtengan exactamente el mismo
# entorno. Sin esto, dos Codespaces creados con horas de diferencia pueden traer
# herramientas distintas, y una release rota la mañana del evento nos afecta a todas a la
# vez. Para actualizar: cambia el número aquí y reconstruye.
#
# AWS CLI 2.32.0 es el mínimo absoluto: es la versión que introdujo `aws login`, que es
# como el taller se autentica desde Codespaces. Por debajo de ahí el comando no existe.
ARG AWSCLI_VERSION=2.36.28
ARG SAM_VERSION=1.165.0
ARG BOTO3_VERSION=1.42.91

# Todo en una capa: en un Codespace de 2 núcleos cada capa extra se paga en tiempo de
# arranque, y aquí no hay prebuild que lo amortigüe (cada persona crea el Codespace sobre
# su propio fork, así que el build se ejecuta entero cada vez).
#
# Sin `apt upgrade`: la imagen base ya viene parcheada y actualizar el sistema entero
# añadiría minutos sin aportar nada al taller.
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
      x86_64)  awscli_arch="x86_64";  sam_arch="x86_64" ;; \
      aarch64) awscli_arch="aarch64"; sam_arch="arm64"  ;; \
      *) echo "Arquitectura no soportada: $arch" >&2; exit 1 ;; \
    esac; \
    \
    # La imagen base declara un repositorio de yarn cuya clave GPG ya no valida, así que
    # `apt-get update` aborta con "The repository is not signed" y se lleva por delante el
    # build entero. El taller no usa yarn —el binario que hay ni siquiera arranca, porque
    # la imagen no trae Node—, así que se retira la lista antes de actualizar.
    rm -f /etc/apt/sources.list.d/yarn.list; \
    \
    apt-get update; \
    # `unzip` lo necesitan los instaladores de AWS CLI y SAM CLI; `jq` es para inspeccionar
    # respuestas de la API a mano; `nodejs` solo se usa para `node --check frontend/app.js`,
    # así que la versión de Debian sobra y evita descargar un runtime entero.
    apt-get install -y --no-install-recommends unzip jq nodejs; \
    \
    # --- GitHub CLI, desde su repositorio oficial ---------------------------------------
    mkdir -p -m 755 /etc/apt/keyrings; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends gh; \
    \
    # --- AWS CLI v2 ---------------------------------------------------------------------
    cd /tmp; \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${awscli_arch}-${AWSCLI_VERSION}.zip" -o awscli.zip; \
    unzip -q awscli.zip; \
    ./aws/install; \
    rm -rf awscli.zip aws; \
    \
    # --- AWS SAM CLI --------------------------------------------------------------------
    curl -fsSL "https://github.com/aws/aws-sam-cli/releases/download/v${SAM_VERSION}/aws-sam-cli-linux-${sam_arch}.zip" -o sam.zip; \
    unzip -q sam.zip -d sam-installation; \
    ./sam-installation/install; \
    rm -rf sam.zip sam-installation; \
    \
    # --- boto3 --------------------------------------------------------------------------
    # scripts/seed.py lo necesita en el host. Las Lambdas NO: ahí lo aporta el runtime
    # gestionado, y por eso el proyecto no tiene requirements.txt.
    pip install --no-cache-dir "boto3==${BOTO3_VERSION}"; \
    \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Comprobación en tiempo de build. Preferimos romper aquí, donde el error se lee entero,
# antes que entregar un Codespace en el que `aws login` no existe y la persona descubre el
# problema en mitad del taller.
RUN set -eux; \
    aws_version="$(aws --version 2>&1 | sed -n 's#^aws-cli/\([0-9.]*\).*#\1#p')"; \
    test "${aws_version%%.*}" = "2" \
      || { echo "Se esperaba AWS CLI v2, hay $aws_version" >&2; exit 1; }; \
    # Comparación de versiones con sort -V: si la menor de las dos no es 2.32.0, la
    # instalada es más antigua y `aws login` no existiría.
    test "$(printf '%s\n2.32.0\n' "$aws_version" | sort -V | head -1)" = "2.32.0" \
      || { echo "AWS CLI $aws_version es anterior a 2.32.0 y no trae 'aws login'" >&2; exit 1; }; \
    # Sonda que no renderiza documentación: `aws login help` necesitaría groff, que esta
    # imagen no trae. Con un flag inventado, la CLI responde "Invalid choice" si el
    # comando no existe y "Unknown options" si existe pero el flag no.
    probe="$(aws login --flag-que-no-existe 2>&1 || true)"; \
    case "$probe" in \
      *"Invalid choice"*|*"invalid choice"*) \
        echo "'aws login' no existe en esta AWS CLI ($aws_version)" >&2; exit 1 ;; \
    esac; \
    sam --version; \
    gh --version; \
    node --version; \
    python3 -c "import boto3, botocore; print('boto3', boto3.__version__)"

# Región del taller. No son secretos: solo evitan tener que repetir --region en cada
# comando. AWS_PAGER vacío desactiva el paginador de la CLI v2, que en una terminal web
# obliga a pulsar 'q' después de cada salida.
ENV AWS_REGION=us-east-1 \
    AWS_DEFAULT_REGION=us-east-1 \
    AWS_PAGER=""

# Aquí NO se copian credenciales. Nada de COPY .aws, ni ARG con claves, ni ENV con
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN. La identidad la aporta
# `aws login --remote` en tiempo de ejecución y dura 12 horas.
