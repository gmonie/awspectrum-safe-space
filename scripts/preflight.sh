#!/usr/bin/env bash
#
# preflight.sh — comprueba que tu entorno puede desplegar Safe Space.
#
# Este script SOLO LEE. No crea, no modifica y no borra nada, y en particular
# nunca relaja la seguridad de tu cuenta: si encuentra un bloqueo, te lo dice
# para que tú decidas qué hacer.
#
# Uso:
#   ./scripts/preflight.sh
#
set -euo pipefail

STACK_NAME="${STACK_NAME:-safe-space}"
AWS_REGION="${AWS_REGION:-us-east-1}"
EXPECTED_REGION="us-east-1"
MODEL_ID="${MODEL_ID:-amazon.nova-micro-v1:0}"

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; GREEN=""; YELLOW=""; RED=""; DIM=""; RESET=""
fi

failures=0
warnings=0

# El entorno oficial del taller es GitHub Codespaces; CloudShell es el plan B. Los mensajes
# de ayuda cambian según dónde estés, porque el comando que resuelve el problema no es el
# mismo: en Codespaces hay que autenticarse a mano y en CloudShell no.
if [[ -n "${CODESPACES:-}" ]]; then
  ENVIRONMENT="codespaces"
elif [[ "${AWS_EXECUTION_ENV:-}" == *CloudShell* ]]; then
  ENVIRONMENT="cloudshell"
else
  ENVIRONMENT="otro"
fi

pass()  { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn()  { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; warnings=$((warnings + 1)); }
fail()  { printf '  %s✗%s %s\n' "$RED" "$RESET" "$1"; failures=$((failures + 1)); }
hint()  { printf '    %s%s%s\n' "$DIM" "$1" "$RESET"; }

printf '\n%s🌈 Safe Space · preflight%s\n' "$BOLD" "$RESET"
printf '%sRegión objetivo: %s · Stack: %s%s\n\n' "$DIM" "$AWS_REGION" "$STACK_NAME" "$RESET"

# --------------------------------------------------------------------------
printf '%sHerramientas%s\n' "$BOLD" "$RESET"
# --------------------------------------------------------------------------
if command -v aws >/dev/null 2>&1; then
  pass "AWS CLI $(aws --version 2>&1 | cut -d' ' -f1)"

  # 'aws login' —cómo se autentica el taller desde Codespaces— existe a partir de la
  # versión 2.32.0. Es aviso y no bloqueo: en CloudShell las credenciales ya vienen dadas y
  # allí nunca se ejecuta ese comando.
  aws_version="$(aws --version 2>&1 | sed -n 's#^aws-cli/\([0-9.]*\).*#\1#p')"
  if [[ "${aws_version%%.*}" != "2" ]]; then
    warn "Tienes AWS CLI ${aws_version:-desconocida}; el taller usa la v2."
    hint "'aws login' solo existe en la v2. Sin ella tendrás que usar CloudShell."
  elif [[ "$(printf '%s\n2.32.0\n' "$aws_version" | sort -V | head -1)" != "2.32.0" ]]; then
    warn "AWS CLI $aws_version es anterior a 2.32.0 y no trae 'aws login'."
    hint "Actualízala, o continúa desde AWS CloudShell (plan B del README)."
  fi
else
  fail "No se encontró la AWS CLI."
  hint "El Dev Container del taller ya la trae. ¿Abriste el Codespace del repositorio?"
fi

if command -v sam >/dev/null 2>&1; then
  pass "AWS SAM CLI $(sam --version 2>&1 | awk '{print $NF}')"
else
  fail "No se encontró la SAM CLI."
  hint "El Dev Container del taller ya la trae. ¿Abriste el Codespace del repositorio?"
fi

if command -v python3 >/dev/null 2>&1; then
  pass "Python $(python3 --version 2>&1 | awk '{print $2}')"
  if python3 -c "import boto3" >/dev/null 2>&1; then
    pass "boto3 disponible (lo necesita scripts/seed.py)"
  else
    fail "boto3 no está instalado para python3."
    hint "El Dev Container ya lo trae. Fuera de él: pip3 install --user boto3"
  fi
else
  fail "No se encontró python3."
fi

# --------------------------------------------------------------------------
printf '\n%sCuenta y región%s\n' "$BOLD" "$RESET"
# --------------------------------------------------------------------------
if identity="$(aws sts get-caller-identity --output json 2>/dev/null)"; then
  account="$(printf '%s' "$identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])')"
  arn="$(printf '%s' "$identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Arn"])')"
  pass "Credenciales activas · cuenta ${account}"
  hint "$arn"
else
  fail "AWS todavía no sabe quién eres."
  case "$ENVIRONMENT" in
    codespaces)
      hint ""
      hint "Desde este Codespace, inicia sesión con:"
      hint "    aws login --remote --region ${EXPECTED_REGION}"
      hint ""
      hint "Después comprueba tu identidad:"
      hint "    aws sts get-caller-identity"
      hint ""
      hint "Y vuelve a ejecutar:"
      hint "    ./scripts/preflight.sh"
      hint ""
      hint "Si tu cuenta usa IAM Identity Center, el comando es 'aws sso login'."
      ;;
    cloudshell)
      hint "En CloudShell las credenciales deberían ser automáticas."
      hint "Si no lo son, comprueba que iniciaste sesión en la consola de AWS."
      ;;
    *)
      hint "Inicia sesión con: aws login --region ${EXPECTED_REGION}"
      hint "En una máquina remota o sin navegador: aws login --remote --region ${EXPECTED_REGION}"
      hint "Si tu cuenta usa IAM Identity Center: aws sso login"
      ;;
  esac
  account=""
fi

effective_region="$(aws configure get region 2>/dev/null || true)"
effective_region="${AWS_REGION:-$effective_region}"
if [[ "$effective_region" == "$EXPECTED_REGION" ]]; then
  pass "Región ${EXPECTED_REGION}"
else
  fail "La región efectiva es '${effective_region:-vacía}', pero el workshop usa ${EXPECTED_REGION}."
  hint "Exporta la región antes de continuar:"
  hint "    export AWS_REGION=${EXPECTED_REGION}"
fi

# --------------------------------------------------------------------------
printf '\n%sServicios%s\n' "$BOLD" "$RESET"
# --------------------------------------------------------------------------
if aws bedrock-runtime converse \
      --region "$AWS_REGION" \
      --model-id "$MODEL_ID" \
      --messages '[{"role":"user","content":[{"text":"ping"}]}]' \
      --inference-config '{"maxTokens":5,"temperature":0}' >/dev/null 2>&1; then
  pass "Amazon Bedrock · ${MODEL_ID} responde"
else
  warn "No se pudo invocar ${MODEL_ID} en ${AWS_REGION}."
  hint "El workshop continúa igual: la búsqueda cae al plan B por palabras clave."
  hint "Revisa Bedrock > Model access en la consola si quieres la búsqueda con IA."
fi

if aws location list-keys --region "$AWS_REGION" >/dev/null 2>&1; then
  pass "Amazon Location accesible"
else
  fail "No se pudo consultar Amazon Location en ${AWS_REGION}."
  hint "Sin Location no hay mapa. Revisa permisos del rol o usuario que estás usando."
fi

if [[ -n "$account" ]]; then
  bpa="$(aws s3control get-public-access-block --account-id "$account" --output json 2>/dev/null || true)"
  if [[ -z "$bpa" ]]; then
    pass "S3 Block Public Access · sin bloqueo a nivel de cuenta"
  elif printf '%s' "$bpa" | grep -q '"BlockPublicPolicy": true\|"RestrictPublicBuckets": true'; then
    fail "Tu cuenta bloquea las policies públicas de S3 a nivel de cuenta."
    hint "El sitio del workshop se publica con el endpoint de sitio web de S3 y no podrá desplegarse."
    hint "Este script NO cambia ese ajuste por ti: es una protección de tu cuenta y la decisión es tuya."
    hint "Alternativa sin tocarlo: pídele a quien facilita el workshop el frontend de rescate."
  else
    pass "S3 Block Public Access · no bloquea policies de bucket"
  fi
fi

# --------------------------------------------------------------------------
printf '\n%sEstado previo%s\n' "$BOLD" "$RESET"
# --------------------------------------------------------------------------
if aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  status="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" \
            --query 'Stacks[0].StackStatus' --output text)"
  warn "Ya existe un stack '${STACK_NAME}' en estado ${status}."
  hint "'sam deploy' la actualizará en vez de crearla. Para empezar de cero: ./scripts/cleanup.sh"
else
  pass "No hay un stack '${STACK_NAME}' previo · deploy limpio"
fi

# --------------------------------------------------------------------------
printf '\n%s─────────────────────────────────────────%s\n' "$DIM" "$RESET"
if (( failures > 0 )); then
  printf '%s✗ %d bloqueo(s) y %d aviso(s).%s Resuelve los bloqueos antes de continuar.\n\n' \
    "$RED" "$failures" "$warnings" "$RESET"
  exit 1
fi

if (( warnings > 0 )); then
  printf '%s! %d aviso(s), ningún bloqueo.%s Puedes continuar el workshop.\n\n' \
    "$YELLOW" "$warnings" "$RESET"
  exit 0
fi

printf '%s✓ Todo listo.%s Continúa el workshop.\n\n' "$GREEN" "$RESET"
