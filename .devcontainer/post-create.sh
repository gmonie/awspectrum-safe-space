#!/usr/bin/env bash
#
# post-create.sh — se ejecuta una vez, cuando el Codespace termina de construirse.
#
# Lo que este script NO hace, a propósito: no inicia sesión en AWS, no crea recursos, no
# despliega, no carga el seed, no toca tu configuración de AWS y no te pide ningún secreto.
# Solo deja el espacio de trabajo listo y te dice cuál es el siguiente comando.
#
# Tampoco puede romper la creación del Codespace: si algo aquí falla, es preferible entrar
# a un entorno con un aviso que quedarse fuera del taller.
set -uo pipefail

UPSTREAM_URL="https://github.com/itsebasvz/awspectrum-safe-space.git"

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; GREEN=""; DIM=""; RESET=""
fi

# Git no marca el bit de ejecución al clonar en algunos entornos, y sin él
# `./scripts/preflight.sh` responde "Permission denied" en el primer paso del taller.
chmod +x scripts/*.sh 2>/dev/null || true

# Codespaces normalmente ya configura 'origin' (tu fork) y 'upstream' (el repo del
# taller). Esto es solo una red por si abres el contenedor de otra forma. Nunca sobrescribe
# un remoto que ya exista: si 'upstream' está configurado, se respeta tal cual.
if ! git remote get-url upstream >/dev/null 2>&1; then
  git remote add upstream "$UPSTREAM_URL" 2>/dev/null || true
fi

version() { "$@" 2>&1 | head -1 || echo "no disponible"; }

printf '\n%s🌈 Safe Space · entorno listo%s\n\n' "$BOLD" "$RESET"

printf '  Python:    %s\n' "$(version python3 --version)"
printf '  AWS CLI:   %s\n' "$(version aws --version)"
printf '  SAM CLI:   %s\n' "$(version sam --version)"
printf '  Git:       %s\n' "$(version git --version)"
printf '  Node:      %s\n' "$(version node --version)"
printf '  gh:        %s\n' "$(version gh --version)"

printf '\n%s  origin   = tu fork · upstream = el repo del taller%s\n' "$DIM" "$RESET"
printf '%s  %s%s\n' "$DIM" "$(git remote get-url origin 2>/dev/null || echo 'sin configurar')" "$RESET"

printf '\n%sTienes las herramientas, pero AWS todavía no sabe quién eres.%s\n\n' "$BOLD" "$RESET"

printf '  1. Inicia sesión (se abre en tu navegador, dura 12 horas):\n\n'
printf '       %saws login --remote --region us-east-1%s\n\n' "$GREEN" "$RESET"
printf '  2. Comprueba tu identidad:\n\n'
printf '       %saws sts get-caller-identity%s\n\n' "$GREEN" "$RESET"
printf '  3. Revisa que tu cuenta puede desplegar:\n\n'
printf '       %s./scripts/preflight.sh%s\n\n' "$GREEN" "$RESET"

printf '%s  ¿Tu cuenta usa IAM Identity Center o un usuario IAM? Lee la sección\n' "$DIM"
printf '  "Si tu cuenta no es así" del README antes del paso 1.%s\n\n' "$RESET"
