# AiHead skill — τι άλλαξε (2026-11-16)

Ανεξάρτητη αναφορά αλλαγών για review. Δεν έγινε commit ή push — όλα τα παρακάτω είναι uncommitted working-tree state, εκτός αν δηλωθεί αλλιώς.

## 1. Context

- Ο AiHead skill είναι **διχασμένος σε δύο αρχεία**: το canonical στο repo και ένα παλαιότερο, διαφορετικό copy στο `~/.hermes`.
- Αποφάσεις χρήστη: (1) upstream του μοναδικού περιεχομένου στο repo, (2) επιλογή **(a)** = upstream πρώτα, μετά symlink με backup.

## 2. Canonical repo — `/home/pank/projects/AiHead-Orchestrator` (branch `develop`)

Αρχείο: `skills/aihead/SKILL.md` (+ new `skills/aihead/references/`)

### Προστέθηκε (μοναδικό περιεχόμενο που υπήρχε μόνο στο Hermes copy)
- **`## HARD RULE`** για τα user-facing tabs (nuovo: `Plan:`/`Update:`/`Info:`/`Q:` όχι απλή απάντηση στο chat).
- **`## When to Use`** ενότητα.
- **`Parent outside Herdr (Telegram/CLI-origin runs)`** block στο §3.
- **Pane launch verification**: αντικατάσταση του stale `/models` recipe με το σωστό **ctrl+p → Switch model** flow (το `--kind hermes` fallback για tab agents).
- **5 recovery paragraphs**: `agent_not_ready` bootstrap race, trust-dialog blocked startup, pi OAuth 401, **provider quota stall** (PONG probe, idempotent resume, one-shot cron), isolated-session death.
- **Automode bullet**: πληρέστερη εκδοχή (resolve από config + launch override, smoke-test κάθε route, same-family warning).
- **§4 Confusion recovery** bullet.
- **§6 Verification**: πλήρες "acceptance report is a CLAIM, not evidence" κείμενο + link στο `references/verifying-pipeline-results.md`.
- **§5 tab protocol**: harness-agnostic agent start ( hardcoded `--kind pi` → `<harness>` με Hermes fallback) + νέο bullet "After matching the current conversation... use the protocol even if `$HERDR_PANE_ID` was absent from the tool shell".
- **Νέο `skills/aihead/references/`**: `coordinator-bindings.md`, `verifying-pipeline-results.md`, αντιγραμμένα από το Hermes copy.

### Διατηρήθηκε το repo (νεότερο)
- Όλη η μετανάστευση `herdm` → `aihead`.
- Το νεότερο inspection-tab πρωτόκολλο (`Plan:`/`Info:`) και οι σχετικές ενότητες §4/§5/§7.
- Frontmatter description.
- Η πρόταση naming source bindings (`[<pipeline>.binding]` κ.λπ.).

### Δεν άλλαξε
- `README.md` του skill, tests, catalog/installer code.
- Οι συντομογραφίες `opencode`/`pi` στο `.config/opencode` και `.pi/agent` — είναι ήδη **symlink** στο canonical και το παίρνουν ζωντανά.

## 3. Installed copy — `/home/pank/.hermes/skills/aihead/`

- Το `SKILL.md` (50.492 B, πριν τη μετεγκατάσταση) **μετονομάστηκε atomically** (mv -n, no-clobber) σε:
  `/home/pank/.hermes/skills_backup_20261116/aihead-SKILL.md`
  → πλήρης ανάκτηση: `mv -n ~/.hermes/skills_backup_20261116/aihead-SKILL.md ~/.hermes/skills/aihead/SKILL.md`
- Το `SKILL.md` τώρα είναι **symlink** → `/home/pank/projects/AiHead-Orchestrator/skills/aihead/SKILL.md`
- `references/` (τοπικό copy) παραμένει ως ελεύθερο αρχείο — είναι duplicate του πλέον canonical repo `references/`, δεν αφαιρέθηκε.

## 4. Επιβεβαίωση που έγινε

- `sha256sum` symlink == repo file (`fcbd06f6504516fa...`).
- `skill_view(name='aihead')` production lookup: φορτώνει το merged περιεχόμενο, `linked_files` δίνει και τα δύο `references/*.md` → resolve OK.
- Preflight πριν τη μετεγκατάσταση: όλα regular files/dirs, owned by pank, κανένα symlink, destination backup κενό.
- Οι opencode/pi συνδέσεις έλεγχον ότι δείχνουν στο canonical.

## 5. Εκκρεμεί / για έλεγχο

- **Δεν έγινε commit** στο repo (άκυρη απόφαση 1 του χρήστη) — το working tree είναι dirty με τα παραπάνω.
- Ρίσκα που πρέπει να ελεγχθεί: το merge μπορεί να έχει αφήσει διαφορετικές εκφράσεις να συγκρούονται (π.χ. §5 γράφει πλέον "verified harness" ενώ κάπου αλλού υποθέτει `pi`), να έχει χαθεί περιεχόμενο του repo στα replacements, ή broken αναφορές (§N, references).
- Επίσης: σημειώστε αν κάποια προσθήκη είναι πλέον redundant/contradictory με το νεότερο repo πρωτόκολλο.
