#!/bin/bash
# Tylko do testow: buduje obrazy wersji podanych jako argumenty.
set -euo pipefail
. "$(dirname "$0")/_wspolne.sh"
for w in "$@"; do zbuduj_obrazy "$w"; done
