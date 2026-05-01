#!/usr/bin/env bash
set -euo pipefail

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# gradient-agents — Script de mise à jour
# Usage : bash update.sh [--all] [--rollback]
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REPO_URL="https://github.com/thomasissa-png/Agent-Team"
AGENTS_DIR=".claude/agents"
BACKUP_DIR=".claude/agents/.backup"
TEMP_DIR=$(mktemp -d)
UPDATE_ALL=false
ROLLBACK=false

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

cleanup() { rm -rf "$TEMP_DIR"; }
trap cleanup EXIT

# ─── Parsing des arguments ───────────────────────────
for arg in "$@"; do
  case "$arg" in
    --all) UPDATE_ALL=true ;;
    --rollback) ROLLBACK=true ;;
  esac
done

if [ ! -d "$AGENTS_DIR" ]; then
  echo -e "${YELLOW}Aucun agent installé. Lance install.sh d'abord.${NC}"
  exit 1
fi

# ─── Mode rollback ───────────────────────────────────
if [ "$ROLLBACK" = true ]; then
  if [ ! -d "$BACKUP_DIR" ]; then
    echo -e "${RED}✗ Aucune sauvegarde trouvée dans ${BACKUP_DIR}/${NC}"
    echo -e "  Le rollback n'est possible qu'après une mise à jour."
    exit 1
  fi

  backup_count=$(ls "$BACKUP_DIR"/*.md 2>/dev/null | wc -l | tr -d ' ')
  echo -e "${BOLD}gradient-agents — Rollback${NC}"
  echo -e "${YELLOW}→ Restauration de ${backup_count} agent(s) depuis la sauvegarde...${NC}"

  for backup_file in "$BACKUP_DIR"/*.md; do
    agent_name=$(basename "$backup_file")
    cp "$backup_file" "$AGENTS_DIR/$agent_name"
    echo -e "  ${GREEN}✓ Restauré : ${agent_name}${NC}"
  done

  rm -rf "$BACKUP_DIR"
  echo ""
  echo -e "${GREEN}Rollback terminé. La sauvegarde a été supprimée.${NC}"
  exit 0
fi

# ─── Mode mise à jour ────────────────────────────────
echo -e "${BOLD}gradient-agents — Mise à jour${NC}"
echo ""
echo -e "${BLUE}→ Récupération des dernières versions...${NC}"

# Clone avec fallback pour repos privés
if git clone --filter=blob:none --sparse --quiet -b master "$REPO_URL" "$TEMP_DIR/repo" 2>/dev/null; then
  cd "$TEMP_DIR/repo"
  git sparse-checkout set .claude/agents .claude/settings.json CLAUDE.md .githooks
else
  if git clone --quiet -b master "$REPO_URL" "$TEMP_DIR/repo" 2>/dev/null; then
    cd "$TEMP_DIR/repo"
  else
    echo -e "${RED}✗ Impossible de cloner le repo.${NC}"
    echo -e "${RED}  Vérifie l'URL et les droits d'accès.${NC}"
    exit 1
  fi
fi

echo -e "${GREEN}✓ Dernières versions récupérées${NC}"
echo ""

# Créer une sauvegarde avant mise à jour
mkdir -p "$OLDPWD/$BACKUP_DIR"
cp "$OLDPWD/$AGENTS_DIR"/*.md "$OLDPWD/$BACKUP_DIR/" 2>/dev/null || true
echo -e "${BLUE}→ Sauvegarde créée dans ${BACKUP_DIR}/ (rollback : bash update.sh --rollback)${NC}"
echo ""

updated=0
skipped=0
new_agents=0

for remote_agent in "$TEMP_DIR/repo/.claude/agents"/*.md; do
  agent_name=$(basename "$remote_agent")
  local_agent="$OLDPWD/$AGENTS_DIR/$agent_name"

  if [ ! -f "$local_agent" ]; then
    cp "$remote_agent" "$local_agent"
    echo -e "  ${GREEN}+ Nouvel agent installé : ${agent_name}${NC}"
    ((new_agents++))
    continue
  fi

  remote_hash=$(md5sum "$remote_agent" | cut -d' ' -f1)
  local_hash=$(md5sum "$local_agent" | cut -d' ' -f1)

  if [ "$remote_hash" == "$local_hash" ]; then
    ((skipped++))
    continue
  fi

  if [ "$UPDATE_ALL" = true ]; then
    cp "$remote_agent" "$local_agent"
    echo -e "  ${GREEN}✓ Mis à jour : ${agent_name}${NC}"
    ((updated++))
  else
    echo -e "  ${YELLOW}↑ Mise à jour disponible : ${agent_name}${NC}"
    read -r -p "    Mettre à jour ? [o/N] " response
    if [[ "$response" =~ ^[oO]$ ]]; then
      cp "$remote_agent" "$local_agent"
      echo -e "    ${GREEN}✓ Mis à jour${NC}"
      ((updated++))
    else
      ((skipped++))
    fi
  fi
done

# ─── Mise à jour de settings.json ─────────────────
if [ -f "$TEMP_DIR/repo/.claude/settings.json" ]; then
  cp "$TEMP_DIR/repo/.claude/settings.json" "$OLDPWD/.claude/settings.json"
  echo -e "  ${GREEN}✓ .claude/settings.json mis à jour${NC}"
fi

# ─── Mise à jour de update.sh lui-même ─────────────
if [ -f "$TEMP_DIR/repo/update.sh" ]; then
  cp "$TEMP_DIR/repo/update.sh" "$OLDPWD/update.sh"
  chmod +x "$OLDPWD/update.sh"
  echo -e "  ${GREEN}✓ update.sh mis à jour${NC}"
fi

# ─── Mise à jour des hooks git ─────────────────────
if [ -d "$TEMP_DIR/repo/.githooks" ]; then
  mkdir -p "$OLDPWD/.githooks"
  cp "$TEMP_DIR/repo/.githooks"/* "$OLDPWD/.githooks/" 2>/dev/null || true
  chmod +x "$OLDPWD/.githooks"/* 2>/dev/null || true
  cd "$OLDPWD" && git config core.hooksPath .githooks && cd "$TEMP_DIR/repo"
  echo -e "  ${GREEN}✓ .githooks/ synchronisé (CLAUDE.md guard + pre-commit)${NC}"
fi

# ─── Mise à jour de CLAUDE.md (fusion avec marqueurs) ─
if [ -f "$TEMP_DIR/repo/CLAUDE.md" ]; then
  local_claude="$OLDPWD/CLAUDE.md"
  source_claude="$TEMP_DIR/repo/CLAUDE.md"

  if [ ! -f "$local_claude" ]; then
    cp "$source_claude" "$local_claude"
    echo -e "  ${GREEN}✓ CLAUDE.md installé${NC}"
  elif grep -q "GRADIENT-AGENTS-START" "$local_claude"; then
    # Remplacement de la section Gradient entre les marqueurs
    gradient_content=$(sed -n '/<!-- GRADIENT-AGENTS-START -->/,/<!-- GRADIENT-AGENTS-END -->/p' "$source_claude")
    tmp_merged="$TEMP_DIR/claude_md_merged"
    sed '/<!-- GRADIENT-AGENTS-START -->/,/<!-- GRADIENT-AGENTS-END -->/d' "$local_claude" > "$tmp_merged"
    echo "$gradient_content" | cat - "$tmp_merged" > "$local_claude"
    echo -e "  ${GREEN}✓ CLAUDE.md mis à jour (section Gradient remplacée, contenu custom préservé)${NC}"
  else
    echo -e "  ${YELLOW}⚠ CLAUDE.md sans marqueurs Gradient — ajout en fin de fichier${NC}"
    echo "" >> "$local_claude"
    cat "$source_claude" >> "$local_claude"
    echo -e "  ${GREEN}✓ CLAUDE.md fusionné${NC}"
  fi
fi

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  Résumé :${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${GREEN}${updated}${NC} agents mis à jour"
echo -e "  ${GREEN}${new_agents}${NC} nouveaux agents"
echo -e "  ${BLUE}${skipped}${NC} déjà à jour"
echo -e "  ${GREEN}✓${NC} settings.json + CLAUDE.md synchronisés"
echo ""
echo -e "${YELLOW}Note : project-context.md n'est jamais écrasé.${NC}"
echo -e "${YELLOW}Rollback : bash update.sh --rollback${NC}"
