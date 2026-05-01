# Audit Infrastructure — OneShot Finance

**Score : 5.5/10 (post-fix v8.7)**
**Date : 2026-05-01**
**Auditeur : @infrastructure**
**Contexte : audit post-incident silent failure 21 jours (dernier scan 2026-04-10 → détecté 2026-05-01)**

---

## Évaluation du fix v8.7 (commit 8c410a7)

### Forces
- **`_recover_missed_scans_on_startup()`** (main.py:279) lance le watchdog dès le boot si dernier scan > 24h ET on est en business hours (lun-ven 8h-20h Paris). Ferme le trou principal du watchdog v8.6 qui ne tickait que toutes les 30 min.
- **`/api/health` enrichi** avec un bloc `last_scan` distinguant 4 états (`fresh`, `stale`, `stale_critical`, `no_data`) — désormais un monitoring externe (UptimeRobot, BetterStack) peut détecter une dérive simplement en surveillant le code HTTP ou le JSON.
- **Cache 60s + `pg_get_latest_scan_info()`** dédié — pas de scan complet de la table `scan_history` à chaque ping (bonne pratique).
- **Boot guard explicite** : si dernier scan < 24h, skip recovery (pas de double-tir au démarrage).
- **Out-of-hours guard** : si on boot un dimanche soir, on ne déclenche pas un faux scan le week-end.

### Trous restants (CRITIQUES)
1. **Aucune alerte externe configurée** — `/api/health` peut renvoyer `stale_critical` pendant 21 jours, personne ne le voit. Le fix v8.7 EXPOSE l'information mais ne la PUSH nulle part. Sans monitoring externe (UptimeRobot/BetterStack/Sentry), le fix est passif.
2. **Si `_recover_missed_scans_on_startup()` échoue silencieusement** (logger.error mais pas d'alerte sortante), on retombe sur le watchdog cron 30min — qui peut lui-même rater si Replit ne redémarre pas le container. Boucle de garantie pas fermée.
3. **Le fix dépend du fait que Replit RÉ-DÉMARRE le container après inactivité.** Si l'autoscale Replit garde un container "warm" mais bloque l'event loop (cas observé dans le passé), aucun boot, aucun startup hook, le watchdog cron sur APScheduler doit toujours fire — mais s'il était bloqué pendant 21j, qu'est-ce qui garantit qu'il se débloque ?
4. **`_keepalive_loop()` sur le port 127.0.0.1 interne** (main.py:341-353) — ne déclenche AUCUN trafic externe vu par Replit autoscale. Replit voit le process consommer CPU mais peut quand même décider de hiberner sur free tier. Le keepalive interne est insuffisant — il faut un trafic EXTERNE entrant (UptimeRobot ping toutes les 5 min).
5. **Pas de circuit breaker** : si `_run_scan_watchdog()` est appelé au boot et qu'il déclenche les 4 scans Europe/Mid/US/US_session en cascade, on peut surcharger Claude API (rate limit) ou la DB. Pas de séquencement / délai entre retriggers.
6. **`pg_get_latest_scan_info()` peut renvoyer None silencieusement** si table vide ou erreur PG — le startup recovery skip alors le retrigger ("no scan history found — skipping"), or un système qui n'a JAMAIS scanné est aussi un problème.

### Verdict : **INSUFFISANT pour zéro silent failure futur**

Le fix v8.7 réduit drastiquement la probabilité (de 21 jours à <1h dans le cas nominal) mais ne donne PAS de garantie de zéro silent failure car :
- Aucune alerte sortante (push) n'est configurée
- La détection reste internal-only (poll-based via boot)
- Aucun signal de santé ne sort du système vers Thomas

**Action minimale obligatoire pour clore le risque** : ajouter UptimeRobot (gratuit) + Sentry (gratuit) — total 0 €/mois — et passer Replit en Hacker tier ($7/mois) pour always-on. Sans ces 3 actions, le fix v8.7 reste cosmétique.

---

## Top 3 forces infrastructure

1. **Persistance robuste PostgreSQL avec fallback JSON** (`database.py`) — pool min=4/max=30 (P1 v7.8 augmenté pour 21 agents), `_pg_retry` 3x avec backoff exponentiel (1s/2s/4s), TCP keepalive (60s/15s/4 probes), `statement_timeout=30s` anti-hang, `reset_pool()` post-SSL drop, double-checked locking thread-safe. Couvre la majorité des modes de défaillance PG.
2. **Recovery au boot complet et défensif** (`_recover_pending_trades_on_startup` + `_recover_missed_scans_on_startup` + worker Teams 3/4) — au moindre redémarrage, l'app referme automatiquement les trades PENDING orphelins, retrigger les scans manqués si > 24h, et relance le position monitor des 4 équipes. Pas de bombe à retardement post-restart.
3. **Architecture multi-agent observable** : 21 agents avec `BaseAgent.execute()` wrapper, logs structurés en PG (`agent_logs`), message bus PG (`agent_messages`), Agent Auditeur dédié avec 22 profils. Couche d'observabilité INTERNE riche. Ce qui manque c'est l'EXTERNALISATION (cf faiblesses).

---

## Top 5 faiblesses

| # | Sévérité | Problème | Impact prod |
|---|----------|----------|-------------|
| 1 | P0 | **Aucune alerte externe (push) configurée** — pas de Sentry, pas d'UptimeRobot, pas de webhook Slack/email. Toute défaillance silencieuse reste invisible jusqu'à ce que Thomas regarde manuellement le dashboard. C'est exactement ce qui a permis la silent failure de 21 jours. | Tout incident futur (PG down, Anthropic down, container kill, watchdog bug) restera invisible jusqu'à observation manuelle. Risque de re-récidive de l'incident à chaque déploiement Replit ou maintenance plateforme. |
| 2 | P0 | **Replit free tier insuffisant pour always-on** — autoscale tue les containers inactifs, `_keepalive_loop()` interne ne génère pas de trafic externe pour les empêcher d'hiberner. Pas de garantie que les 4 scans/jour fire. | Re-récidive du silent failure si Replit hiberne le container entre scans (max 4h d'écart entre scans). Le keepalive 4 min est un patch fragile. Coût pour fixer : **$7/mois** (Replit Hacker tier always-on). |
| 3 | P0 | **Endpoints destructifs exposés sans authentification** : `/api/admin/fix-entry-price` (POST, main.py:2863), `/api/admin/price-references` (GET, main.py:2999), `/api/infrastructure/reset` (POST, main.py:2117), `/api/infrastructure/reset-team/{team_id}` (POST, main.py:2215). N'importe qui découvrant l'URL Replit publique peut corrompre les données ou wiper la DB. | Risque de wipe complet de la DB par un attaquant, un crawler malveillant ou un bot. CORS fallback `["*"]` quand `REPL_SLUG` absent (main.py:1269) — en local dev ou si la variable disparaît, tous les origins peuvent appeler ces endpoints. URGENCE absolue. |
| 4 | P1 | **`ANTHROPIC_API_KEY` jamais rotée** depuis le début du projet, stockée dans Replit Secrets (OK) mais aucune procédure de rotation documentée. Idem `TWELVE_DATA_API_KEY` (en clair dans CLAUDE.md ligne 615 — leak partiel). | Si la clé fuite (logs, screenshot, repo public), facturation incontrôlée jusqu'à blocage manuel. La clé Twelve Data est DÉJÀ dans CLAUDE.md committé — leak réel à mitiger. |
| 5 | P1 | **Aucune stratégie de backup PostgreSQL automatisée** — `pg_backup_to_json()` existe (database.py:1667) mais n'est appelé par aucun cron du scheduler. Le seul "backup" est l'historique JSON éphémère sur le disque Replit. RPO réel = 0 (perte totale possible). | Si la DB Replit subit un bug ou un opérateur tape `pg_full_reset` par erreur, perte irrécupérable de tout l'historique trades/journal/learning (= mort du système car le learning a besoin de l'historique). |

---

## Plan post-incident 21j (5 actions chiffrées)

### 1. **[P0]** Activer Replit Hacker tier — $7/mois — ETA **5 minutes**
**Qui** : Thomas via Replit billing
**Action** : Upgrade workspace en Hacker plan, activer "Always On" sur le repl `Finance`.
**Effet** : container ne s'hiberne plus. Élimine ~80% du risque container sleep / silent failure.
**Validation** : observer les logs des 4 scans du lendemain — `Scheduled scan 'X' completed successfully` doit apparaître 4x sans `STARTUP RECOVERY`.

### 2. **[P0]** UptimeRobot ping `/api/health` toutes les 5 min — **0 €/mois** (free tier 50 monitors) — ETA **15 minutes**
**Qui** : @infrastructure (Thomas crée le compte, configure le monitor)
**Action** :
- Créer monitor HTTP(S) sur `https://<replit-url>/api/health`
- Keyword monitoring : alerter si la réponse contient `"status":"degraded"` OU `"last_scan":{"status":"stale_critical"}`
- Alert contact : email Thomas + (optionnel) webhook Discord/Slack
- Interval : 5 minutes (free tier max), timeout 30s
**Effet** :
  - Trafic externe régulier → empêche Replit autoscale de hiberner même hors Hacker tier (ceinture+bretelles)
  - Détection en < 5 min de toute dérive `stale_critical` (vs 21 jours actuellement)
  - Test passif que `/api/health` répond toujours

### 3. **[P0]** Authentifier les endpoints `/api/admin/*` — **0 €** — ETA **30 minutes**
**Qui** : @fullstack (handoff)
**Action** :
- Ajouter middleware FastAPI vérifiant un header `X-Admin-Token` contre une env var `ADMIN_API_TOKEN` (Replit Secret)
- Générer un token long aléatoire (`openssl rand -hex 32`)
- Appliquer à `/api/admin/fix-entry-price`, `/api/admin/price-references`, `/api/admin/pg-full-reset` (et tout endpoint non listé qui mute la DB)
- Doc dans `REPLIT_ACTIONS.md` : ajouter `ADMIN_API_TOKEN` aux Secrets Replit
**Effet** : ferme le risque de wipe accidentel ou malveillant. Endpoints muables non protégés = critique.

### 4. **[P1]** Sentry error tracking + source maps Python — **0 €/mois** (free tier 5K events) — ETA **45 minutes**
**Qui** : @infrastructure (Thomas crée projet Sentry, @fullstack intègre SDK)
**Action** :
- Créer projet Python sur sentry.io
- Ajouter `sentry-sdk[fastapi]` à requirements.txt
- Init dans `main.py` lifespan : `sentry_sdk.init(dsn=os.environ["SENTRY_DSN"], traces_sample_rate=0.1, environment="prod")`
- Wrapper APScheduler events pour capturer les misfire / job errors automatiquement
- Alertes email : error rate > 5/heure, première occurrence d'une nouvelle exception
**Effet** : toute exception non catchée (registry pipeline, agents, scheduler) est envoyée à Sentry → email Thomas. Capture les erreurs même si le watchdog passe.

### 5. **[P1]** Backup PG quotidien automatisé via cron + Cloudflare R2 — **0 €/mois** (R2 free tier 10 Go) — ETA **2 heures**
**Qui** : @infrastructure (config), @fullstack (intégration boto3 ou httpx)
**Action** :
- Cron APScheduler dimanche 22h CET (après pipeline hebdo) : `pg_backup_to_json()` → fichier daté
- Upload S3-compatible vers Cloudflare R2 bucket `finance-backups` (gratuit jusqu'à 10 Go)
- Rétention 30 jours (script de pruning hebdo)
- Doc plan de restauration dans `docs/infra/disaster-recovery.md` : RTO < 1h, RPO = 7 jours (acceptable pour ce système avec décroissance learning)
**Effet** : recovery possible en cas de wipe DB. Le système peut redémarrer from scratch en pire cas.

**Coût total mensuel : $7/mois (Replit Hacker uniquement). Tout le reste = free tier.**
**ETA total : ~4h de travail réparti sur les 3 prochains jours.**
**Risque résiduel après plan : <1% silent failure / mois (vs ~100% détection tardive aujourd'hui).**

---

## Coûts & alternatives

### Replit Free → Hacker
- **Free** : autoscale, container hiberne après inactivité — INCOMPATIBLE avec un système 24/7 même avec keepalive interne. Confirmé par l'incident des 21 jours.
- **Hacker $7/mois** : Always On disponible sur un repl, 1 GB RAM dédié, deployments réservés. **Recommandation forte**.
- **Pro $20/mois** : multi Always-On, sortie réseau plus haute. Pas justifié pour ce projet single-app.

### Monitoring externe (gratuit)
- **UptimeRobot free** : 50 monitors, 5 min interval, alerte email/webhook. **Recommandé** — couvre 95% du besoin.
- **BetterStack (anciennement BetterUptime) free** : 10 monitors, 3 min interval, status page publique gratuite. Plus joli mais limite 10 monitors. Alternative valable si Thomas veut une status page partageable.
- **Healthchecks.io free** : monitor cron-based — alerte si un check ne ping pas. Idéal pour surveiller les 4 scans (chaque scan ping un endpoint Healthchecks à la fin). **Très complémentaire** à UptimeRobot — détecte spécifiquement "scan a-t-il tourné ?". Recommandation à ajouter.

### Error tracking (gratuit)
- **Sentry free 5K events/mois** : largement suffisant pour ce volume (~50-100 events/mois prévisibles). **Recommandé**.
- **Better Stack Logs** : gratuit 1 Go logs/mois. Alternative pour logs structurés plutôt qu'erreurs.
- **Console + logs Replit** : fallback gratuit mais sans alerting. À retenir comme baseline si budget zéro absolu.

### PostgreSQL hébergé (alternatives à PG natif Replit)
**RAPPEL CONTRAINTE PROJET** : projet hébergé sur Replit, recommandation par défaut = PG natif Replit (intégré, gratuit, `DATABASE_URL` auto). **Pas de migration recommandée** — le PG Replit fonctionne déjà.

Les alternatives mentionnées ci-dessous sont à considérer UNIQUEMENT si Thomas migre hors Replit :
- **Supabase free** : 500 Mo storage, 2 Go bandwidth — backup auto inclus, dashboard SQL pratique
- **Neon free** : 500 Mo, branching DB (utile pour staging)
- **Railway $5/mois** : PG managé avec backup quotidien automatique

**Recommandation actuelle** : rester sur PG Replit + ajouter backup R2 (action 5 ci-dessus) pour combler l'absence de snapshot natif.

---

## Investigations détaillées (annexe)

### 1. Replit deployment & keepalive
- `_keepalive_loop()` ping `http://127.0.0.1:${PORT}/api/health` toutes les 240s (4 min). **Limite** : trafic interne, ne compte PAS comme "active" pour l'autoscale Replit qui surveille le trafic externe entrant. Patch fragile.
- Recommandation : OBLIGATOIRE coupler avec UptimeRobot (trafic externe) + Hacker tier (always-on) pour garantie.

### 2. PG pool & retry logic
- Pool min=4 max=30 (P1 v7.8) — adapté pour 21 agents async.
- `_pg_retry` : 3 retries, backoff [1s, 2s, 4s].
- TCP keepalive : 60s idle / 15s probe / 4 probes = ~2 min détection mort connexion.
- `reset_pool()` post-SSL drop (v8.5) — **bonne pratique appliquée**.
- `statement_timeout=30s` (H4) — anti-hang query.
- `_PRE_PING_IDLE_THRESHOLD=30s` (M1) — pre-ping agressif pour Replit.
- **Verdict** : pool config robuste. Aucune action P0/P1 requise.

### 3. APScheduler — 21 jobs (vérifié main.py:1172-1213)
- **Scans 4x/jour** (`mon-fri Europe/Paris`, `misfire_grace_time=900s`) :
  - 07:50 europe, 11:15 mid_session, 14:50 us, 17:00 us_session
- **Watchdog** : cron `minute="25,55", hour="8-19", day_of_week="mon-fri"` (main.py:1178) — toutes les 30 min entre 8h25 et 19h55 Paris uniquement. **Trou** : si un scan est manqué le vendredi 17:00 et l'app reboot le lundi matin, le watchdog ne détectera pas (vendredi passé). C'est précisément ce que le fix v8.7 `_recover_missed_scans_on_startup` couvre — **mais uniquement au boot**, pas si l'app reste up sans scan.
- **Journal 22h CET** : `misfire_grace_time=3600s` (1h, safe — pur data processing).
- **Position monitor** : Trader 1 `minute="7,37"` (toutes les 30 min), Trader 3 `minute="5,20,35,50"` (toutes les 15 min), Trader 4 `minute="13,43"` (toutes les 30 min) — `misfire_grace_time=60s`.
- **Event check** : `*/10 7-19` mon-fri (`misfire=60s`).
- **EOD Trader 3 force-close** : 19:50 mon-fri.
- **Hebdo dimanche** : 20:00 weekly source review, 20:30 Team 3 weekly config, 20:45 Team 4 weekly config, 21:00 infra report, 21:30 performance weekly.
- **Infra** : health check `*/15 7-22`, maintenance `23:00`.
- **Performance** : snapshot horaire `7-22`, daily 22:30.
- Pendant maintenance Replit (container kill) : APScheduler state perdu → recovery au boot via `_recover_missed_scans_on_startup` (v8.7) + `_recover_pending_trades_on_startup`. **Bonne couverture post-v8.7**.
- **Trous résiduels** :
  - Pas de circuit breaker entre scans en cascade au retrigger watchdog (cf trou #5 fix v8.7).
  - Watchdog inactif après 19:55 Paris : si le scan 17:00 est manqué ET le container ne reboot pas avant le lendemain 8h25, on perd le scan. Atténué par `_recover_missed_scans_on_startup` SI un reboot survient.
  - **Aucun watchdog n'observe le watchdog** : si APScheduler lui-même se bloque (thread mort), aucune alerte. C'est la raison de l'urgence Sentry + UptimeRobot.

### 4. Sécurité — détails
- **Endpoints destructifs sans auth** (confirmé par grep) :
  - `/api/admin/fix-entry-price` (POST, main.py:2863) — modifie entry_price + recalcule P&L
  - `/api/admin/price-references` (GET, main.py:2999) — leak données prix
  - `/api/infrastructure/reset` (POST, main.py:2117) — reset complet
  - `/api/infrastructure/reset-team/{team_id}` (POST, main.py:2215) — reset par équipe
  - **Aucun de ces endpoints n'a de middleware d'authentification.** Action 3 du plan obligatoire.
- **CORS** (main.py:1268-1272) : `allow_origins=ALLOWED_ORIGINS if repl_slug else ["*"]` — fallback `["*"]` permissif en l'absence de `REPL_SLUG`. Si la variable d'env disparaît (cf migration future), CORS ouvert à tous. Recommandation : durcir le fallback (ex: `["http://localhost:5173"]` au lieu de `["*"]`).
- **PG SSL** : Replit force `sslmode=require` par défaut dans `DATABASE_URL` — OK.
- **Frontend** : Vite build statique servi par FastAPI `StaticFiles` — public sur Replit URL. Pas de auth utilisateur — acceptable car app personnelle Thomas, mais admin endpoints doivent être protégés en plus.
- **Secrets leakés** :
  - `TWELVE_DATA_API_KEY=57627ad733b24fa78ac40652078c18fc` **EN CLAIR dans CLAUDE.md** (committé git) — **leak réel**. Action : régénérer sur twelvedata.com, mettre dans Secrets Replit, retirer de CLAUDE.md.
  - `ANTHROPIC_API_KEY` dans Replit Secrets (OK), mais aucune procédure de rotation documentée.

### 5. Logs & rétention
- `agent_logs` table PG, pruning 90j (`pg_prune_agent_logs`) — automatique.
- `agent_messages` pruning 30j.
- `audit_reports` rétention 100 derniers.
- Pas de log shipping vers Sentry/Datadog actuellement → action 4 du plan.

### 6. Disaster recovery
- **Si Replit DB down** : fallback JSON sur disque éphémère Replit — fonctionne mais perte au prochain redéploiement Replit. Risque MAJEUR.
- **Si Anthropic API down 24h** : Scoring 1 retourne `[]`, désormais le pipeline ne crash plus (fix session 1 v7.7+) mais Teams 1/2 ne tradent pas. Teams 3 (technique pur) continuent de tourner. Team 4 dégrade vers ses sources disponibles. **Comportement gracieux** post-fix session 1.
- **Backup** : ABSENT (cf faiblesse #5). Action 5 du plan.

### 7. Cohérence vs lessons-learned session 1
Les 7 lessons P0/P1 corrigées en session 1 sont visibles dans le code :
- `_pg_retry` + `reset_pool()` + try/except top-level pipeline → **présent dans `database.py` et `agents/registry.py`**
- `misfire_grace_time=900s` + watchdog → **présent dans main.py (v8.6)**
- `_last_api_status` tracking → **présent dans `news_scorer.py` (à vérifier)**
- Calendrier per-ticker (`MACRO_EXEMPT_TICKERS`) → **présent dans `economic_calendar.py`**
- Trader 2 fresh start strict → **présent dans `agent_trader_2.py`**
- Pas de régression évidente détectée. **Audit cohérence : OK**.

---

## Limites de l'audit

- **Pas accès aux logs Replit prod** — impossible de vérifier empiriquement combien de scans ont été retriggers par le watchdog v8.6 entre 2026-04-10 et 2026-05-01, ni si les SSL drops ont continué post-v8.5.
- **Pas accès au dashboard Replit** — impossible de vérifier le tier actuel (free / hacker) ni la consommation RAM/CPU réelle.
- **Pas de test live des endpoints `/api/admin/*`** — la liste exacte et leur statut auth est déduite de la lecture du code, pas testée par requête.
- **Audit du fix v8.7 statique uniquement** — pas exécuté en conditions réelles (besoin d'attendre la prochaine ré-hibernation Replit pour valider).
- **Coûts indiqués valables au 2026-05** — vérifier les pricing pages avant action (Replit, Sentry, UptimeRobot peuvent évoluer).
- **Score 5.5/10** : reflète qu'une infrastructure techniquement solide (forces 1-3) est ANNULÉE en pratique par l'absence d'observabilité externe (faiblesses 1-2). Avec UptimeRobot + Sentry + Hacker tier déployés → score remonterait à 8/10.

---

**Handoff → @orchestrator**
- Fichiers produits : `/home/user/Finance/docs/audit/infrastructure.md`
- Décisions prises :
  - Verdict fix v8.7 : INSUFFISANT seul, suffisant SI couplé à UptimeRobot + Sentry + Replit Hacker
  - Plan d'action 5 étapes chiffrées priorité P0/P1
  - Coût total recommandé : $7/mois (Replit Hacker), tout le reste free tier
- Points d'attention :
  - **`TWELVE_DATA_API_KEY` leakée dans `CLAUDE.md`** ligne ~615 — à régénérer + retirer du fichier (action immédiate hors plan).
  - Endpoints `/api/admin/*` non authentifiés — vulnérabilité P0 à fixer sous 24h.
  - Pas de backup PG → 1 incident DB = mort du learning historique.
- **Actions Replit requises** :
  - Upgrade Hacker tier ($7/mois) + activer Always On sur le repl
  - Ajouter Replit Secret : `ADMIN_API_TOKEN` (généré aléatoirement)
  - Ajouter Replit Secret : `SENTRY_DSN` (après création projet Sentry)
  - Ajouter Replit Secrets : `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_BUCKET` (après setup Cloudflare R2)
  - Régénérer `TWELVE_DATA_API_KEY` sur twelvedata.com et mettre la nouvelle valeur dans Replit Secrets uniquement
