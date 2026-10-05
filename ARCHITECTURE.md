# LLMLL — Architettura

> Documento vivo, complementare a [`REQUIREMENTS.md`](REQUIREMENTS.md): i rimandi `R§n`
> si riferiscono alle sezioni dei requisiti.
> Stato: bozza v0.1 (ottobre 2026).

## 1. Principi architetturali

1. **Storia degli eventi come fonte di verità.** Ogni risultato di apprendimento è un
   evento in sola aggiunta. Lo stato di ogni item (memoria FSRS, padronanza) è una
   **proiezione** ricalcolabile dagli eventi. Questo permette di annullare le
   contestazioni (R§8), ottimizzare FSRS e cambiare il modello di padronanza senza
   perdere dati.
2. **Dominio puro, I/O ai bordi.** Scheduling, voto FSRS, padronanza, selezione degli
   item: funzioni pure, senza database né LLM, testabili in isolamento.
3. **L'LLM è un componente, non il cervello.** Decide l'app *cosa* esercitare; l'LLM
   genera testi e valuta risposte entro schemi strutturati. Ogni chiamata è registrata.
4. **Contenuti come dati.** Curriculum in YAML versionato, prompt in file versionati.
5. **Stack mainstream** (R§11): Python/FastAPI, SQLite/SQLAlchemy, React/TypeScript.

## 2. Vista d'insieme

```
┌──────────────────────────── Frontend (React + TS, PWA) ────────────────────────────┐
│  Sessione ripasso │ Lettura │ Corpus │ Grammatica │ Impostazioni                   │
└───────────────────────────────────────┬────────────────────────────────────────────┘
                                        │ REST/JSON
┌───────────────────────────── Backend (FastAPI) ────────────────────────────────────┐
│  api/            endpoint HTTP, validazione (Pydantic)                             │
│  services/       orchestrazione dei casi d'uso                                     │
│    session_builder · exercise_service · grading_service · reading_service         │
│    contest_service · placement_service                                             │
│  domain/         funzioni pure                                                     │
│    scheduling (FSRS) · mastery · grade_mapping · new_item_budget · word_classifier │
│  llm/            interfaccia LLMClient + task tipizzati + prompt versionati        │
│  nlp/            lemmatizzazione (spaCy) · composti · frequenze · LanguageTool     │
│  store/          modelli SQLAlchemy · event log · proiezioni                       │
└──────────┬──────────────────────┬──────────────────────┬───────────────────────────┘
           │                      │                      │
     SQLite (file)        LanguageTool (container)   Claude API
           ▲
   curriculum/*.yaml ──(script di import)
```

## 3. Struttura del repository

```
llmll/
├── REQUIREMENTS.md  ARCHITECTURE.md
├── docker-compose.yml          # backend + languagetool (+ frontend in build statica)
├── curriculum/de/              # contenuti, versionati in git
│   ├── lexicon/a1.yaml a2.yaml b1.yaml
│   ├── grammar/*.yaml          # un file per punto grammaticale, con testo di riferimento
│   └── constructions.yaml
├── backend/
│   ├── pyproject.toml
│   ├── app/{api,services,domain,llm,nlp,store}/
│   ├── app/llm/prompts/        # template versionati (es. grade_sentence.v3.md)
│   ├── migrations/             # Alembic
│   ├── scripts/                # import_curriculum, replay_events, eval_grader
│   └── tests/
└── frontend/
    ├── package.json
    └── src/
```

## 4. Modello dati

### 4.1 Contenuti (importati dal curriculum)

**`items`**: tutti i knowledge item (R§4.1).

| Campo | Note |
|---|---|
| `id` | slug stabile, mai riutilizzato: `lex:tisch`, `lex:bank#money`, `gram:adj-endings`, `cx:lust-haben-auf` |
| `kind` | `lemma` · `grammar` · `construction` |
| `cefr_level` | A1…C2 |
| `payload` | JSON specifico del tipo: articolo, plurale, traduzioni, esempi; per la grammatica il testo di riferimento e i **tag diagnostici ammessi** |
| `interference` | JSON: genere IT diverso, falso amico, affinità |
| `frequency_rank` | da `wordfreq`, per lemmi |
| `curriculum_version` | hash del file sorgente |

**`item_prerequisites`** `(item_id, requires_item_id)`: archi del grafo (R§5).

Per i lemmi, le **due direzioni** (riconoscimento / produzione) non sono item separati
nel curriculum ma **due tracce di memoria** nella proiezione (`facet = recognition | production`).

### 4.2 Stato dello studente

**`learner`**: una riga in v1. Lingue note, livello corrente, impostazioni (limiti
settimanali, retention target, pesi delle prove).

**`learner_items`**: ciclo di vita di un item per lo studente (R§7.4).

| Campo | Note |
|---|---|
| `status` | `unseen` · `presumed_known` · `candidate` · `introduced` · `suspended` |
| `candidate_source` | `optin` · `article` · `wordlist` (determina la priorità) |
| `candidate_since`, `introduced_at` | |

### 4.3 Attività

- **`texts`**: articoli e testi generati. URL, testo originale, lingua, versioni semplificate
  (`text_versions`: livello, testo, copertura misurata, tentativi).
- **`exercises`**: tipo (`flashcard`, `translation`, `guided`, `transform`, `free`,
  `reading_summary`), prompt mostrato, soluzione di riferimento (se c'è), `targets`
  `[(item_id, facet, weight)]`, riferimento al testo, versione del prompt LLM che l'ha generato.
- **`attempts`**: risposta, tempo impiegato, aiuti usati (suggerimento, glossario).
- **`evaluations`**: valutazione strutturata di un tentativo (§6.2). Una valutazione non si
  modifica mai: una contestazione accettata crea una nuova valutazione con
  `supersedes = <id>`.
- **`contests`**: valutazione contestata, motivazione, stato, risoluzione, resolver usato.

### 4.4 Event log e proiezioni

**`learning_events`**: in sola aggiunta.

| Campo | Note |
|---|---|
| `id`, `ts` | |
| `item_id`, `facet` | |
| `kind` | `review` (esercizio esplicito) · `implicit` (uso corretto in frase, lettura) · `lookup` (glossario toccato) · `introduce` · `status_change` |
| `outcome` | `correct` · `assisted` · `error` |
| `evidence_weight` | dal tipo di esercizio (R§4.2) |
| `diagnostic_tags` | per errori su punti grammaticali (R§4.1) |
| `evaluation_id` | da quale valutazione deriva |
| `voided_by` | null, oppure l'id della valutazione che la sostituisce |

**`item_memory`**: **proiezione**, ricostruibile in qualsiasi momento.

| Campo | Note |
|---|---|
| `item_id`, `facet` | |
| `fsrs_state` | stabilità, difficoltà, ultimo ripasso, scadenza |
| `mastery`, `n_effective` | media mobile pesata e numero effettivo di osservazioni |
| `tag_error_counts` | JSON `{tag: count}` con decadimento |
| `projection_version` | versione di FSRS e dei parametri usati |

**Ricalcolo.** `replay(item_id, facet)` rilegge gli eventi non annullati in ordine di `ts`
e riapplica le funzioni pure del dominio. Si usa:
- incrementalmente: un evento nuovo aggiorna la proiezione senza rileggere tutto;
- in modo puntuale: dopo una contestazione, si rielaborano gli item toccati;
- in blocco: dopo un cambio di parametri FSRS o del modello di padronanza (script `replay_events`).

La proiezione deve essere **deterministica**: stessi eventi e stessa configurazione danno
lo stesso stato. È verificato da test.

### 4.5 Log LLM

**`llm_calls`**: task, versione del prompt, modello, input, output, token, latenza, esito
(incluso `stop_reason`). È la base per il dataset di valutazione del correttore (R§8).

## 5. Dominio (funzioni pure)

### 5.1 Padronanza
Media mobile esponenziale pesata dalla forza della prova:

```
m ← m + α · w · (x − m)          x ∈ {1 corretto, 0.5 assistito, 0 errore}
n_eff ← λ · n_eff + w            λ < 1: le osservazioni vecchie contano meno
```

`α`, `λ` e i pesi `w` per tipo di esercizio stanno nella configurazione. L'interfaccia
`MasteryModel` permette di sostituirla in seguito (es. Beta con decadimento) e di
rielaborare la storia.

### 5.2 Dall'esito al voto FSRS (R§4.3)
`grade(outcome, mastery, n_eff, confidence) → Rating | None`

- `None` = nessun aggiornamento di FSRS. Si usa per valutazioni incerte; la padronanza
  si aggiorna comunque, con peso ridotto.
- Errore con `mastery ≥ soglia` e `n_eff ≥ minimo` → `Hard`; altrimenti `Again` e
  segnalazione `needs_remediation`.

### 5.3 Scheduling
Si usa `py-fsrs` per lo scheduler. La retention target è configurabile, di default 0.85
(R§10). I ripassi impliciti passano dallo stesso scheduler: FSRS gestisce già i ripassi
anticipati.

### 5.4 Budget di item nuovi (R§7.4)
`new_item_budget(events_last_7d, backlog, settings) → {lemmas: n, grammar: n}`.
Finestra mobile di 7 giorni senza accumulo, ridotta in proporzione all'arretrato.

### 5.5 Classificazione delle parole lette (R§7.3)
`classify(lemma, learner_state, current_level) → known | presumed_known | auto_candidate | optin | ignore`.
Nomi propri e composti trasparenti con parti note vanno in `ignore` (o rimandano alle parti).

## 6. Flussi principali

### 6.1 Sessione di ripasso
1. `session_builder` sceglie gli item da trattare:
   - quelli scaduti, ordinati per recuperabilità e importanza, fino al tetto;
   - i nuovi, presi dalla coda delle candidate entro il budget settimanale.
2. Per ogni gruppo di item:
   - lemmi scaduti → flashcard;
   - punti grammaticali scaduti e item nuovi → esercizio di produzione generato
     (§6.2), che li **combina**. Un esercizio introduce le parole nuove usandole, con glossa.
3. Risposta → valutazione → eventi → aggiornamento della proiezione → feedback.

### 6.2 Generazione e valutazione di un esercizio di produzione

**Generazione** (`llm.generate_exercise`)
- Input: item obiettivo, vocabolario noto (campione), livello, tipo di esercizio,
  lingua delle istruzioni (IT).
- Output strutturato: prompt per lo studente, soluzioni di riferimento, `targets` con pesi.
- Un controllo post-generazione (lemmatizzazione) verifica che il prompt non usi parole
  ignote oltre a quelle volute.

**Valutazione** (`grading_service`)
1. **LanguageTool** sulla risposta: errori deterministici di morfologia e accordi.
2. **LLM** (`llm.grade_sentence`). Riceve esercizio, risposta, item obiettivo, tag
   diagnostici ammessi ed esito di LanguageTool. Restituisce uno schema strutturato:

```json
{
  "overall": "correct | minor_errors | major_errors | off_task",
  "accepted_variants_note": "…",
  "errors": [
    {"span": [12, 18], "original": "die Tisch", "correction": "den Tisch",
     "item_id": "gram:accusative-articles", "diagnostic_tags": ["acc", "masc"],
     "severity": "major", "confidence": 0.9}
  ],
  "correct_uses": [{"item_id": "lex:tisch", "facet": "production"}],
  "corrected_sentence": "…",
  "feedback_it": "…"
}
```

3. **Riconciliazione:**
   - accordo tra LanguageTool e LLM → confidenza piena;
   - disaccordo → `confidence` ridotta e item marcati come incerti (R§8);
   - `item_id` non presenti nel curriculum vengono scartati e registrati.
4. Si salva `evaluation`, si emettono gli eventi (errori → `review/error`, usi corretti →
   `implicit/correct` o `review/correct`), si aggiorna la proiezione.
5. Se scatta `needs_remediation`, `llm.explain` produce una spiegazione mirata al tag
   diagnostico, ancorata al testo di riferimento dell'item (R§9), e si accodano 1–2
   esercizi di recupero.

### 6.3 Lettura
1. **Acquisizione:** URL → estrazione con `trafilatura`; se fallisce, testo incollato.
2. **Analisi:** spaCy (`de_core_news_md` o simile) per lemmi e POS, scomposizione dei
   composti, classificazione delle parole (§5.5).
3. **Semplificazione** (`llm.simplify_text`). Riceve il testo, il livello target, la
   lista delle parole ammesse (note + candidate) e uno stile di riferimento. Poi:
   - si misura la copertura;
   - se è sotto soglia (R§7.3), si rigenera indicando le parole da sostituire, per al
     massimo N tentativi;
   - poi si accetta la versione migliore, segnalando la copertura effettiva.
4. **Lettura** con glossario al tocco (`llm.gloss`, con cache per lemma e contesto).
   Il tocco emette un evento `lookup`.
5. **Fine lettura:**
   - eventi `implicit` per gli item noti non consultati (con peso basso);
   - le candidate entrano in coda;
   - si propone il riassunto o commento, cioè un esercizio `reading_summary` valutato come in §6.2.

### 6.4 Contestazione (R§8)
1. Lo studente contesta una valutazione → `contests` (stato `open`).
2. `ContestResolver.resolve(contest) → Resolution`.

```python
class ContestResolver(Protocol):
    def resolve(self, contest: Contest, evaluation: Evaluation) -> Resolution: ...

@dataclass
class Resolution:
    verdict: Literal["accepted", "rejected", "partial"]
    replacement: EvaluationResult | None   # nuova valutazione, se cambia
    rationale: str
```

   In v1 c'è `AcceptAllResolver`: verdetto `accepted`, con una valutazione sostitutiva in
   cui gli errori contestati sono rimossi e i relativi item contano come `correct_uses`.
3. **Applicazione:**
   - nuova `evaluation` con `supersedes`;
   - eventi vecchi marcati `voided_by`;
   - nuovi eventi emessi;
   - `replay` degli item coinvolti.
4. Tutto resta nel log: le contestazioni sono il materiale più prezioso per la valutazione
   del correttore.

### 6.5 Valutazione iniziale (R§6)
1. Il livello dichiarato (A2–B1) imposta `presumed_known` sugli item dei livelli inferiori.
2. Un test breve affina la stima:
   - un campione di lemmi per fascia di frequenza (riconoscimento);
   - 3–5 frasi guidate su punti grammaticali chiave, in particolare gli stadi di ordine
     delle parole (R§5).
3. Gli esiti diventano eventi come gli altri: il piazzamento è solo storia iniziale.

## 7. Livello LLM

### 7.1 Interfaccia
I servizi usano **task tipizzati**, non chiamate generiche:

```python
class LLMClient(Protocol):
    def generate_exercise(self, req: ExerciseRequest) -> GeneratedExercise: ...
    def grade_sentence(self, req: GradeRequest) -> GradeResult: ...
    def simplify_text(self, req: SimplifyRequest) -> SimplifiedText: ...
    def explain(self, req: ExplainRequest) -> Explanation: ...
    def gloss(self, req: GlossRequest) -> Gloss: ...
```

Input e output sono modelli Pydantic. L'implementazione `AnthropicLLMClient` usa l'SDK
ufficiale `anthropic` per Python. Un `FakeLLMClient` con risposte registrate serve per i test.

### 7.2 Implementazione con la Claude API
- **Output strutturato.** Si usa `client.messages.parse()` con il modello Pydantic come
  schema, invece di fare parsing di testo libero.
- **Modello.** È configurabile **per task**. Default per tutti i task: `claude-opus-5-5`.
  Usare modelli più economici per task semplici (glossa, generazione) è una scelta da fare
  dopo averli misurati sul dataset di valutazione, non a priori.
- **Effort.** È configurabile per task. Va alto per la valutazione (la correttezza conta),
  più basso per glossa e generazione.
- **Prompt caching.** Le parti stabili vanno all'inizio del prompt: istruzioni di sistema,
  testo di riferimento del punto grammaticale, profilo dello studente. Le parti variabili
  (risposta, testo) vanno alla fine. Utile soprattutto per sessioni con più esercizi sullo
  stesso item.
- **Rifiuti e errori.** Si controlla sempre `stop_reason` prima di leggere il contenuto.
  Gli errori dell'API si gestiscono distinguendo quelli ritentabili (429, 5xx) dagli altri.
- **Batch API.** Si usa per lavori offline, a costo ridotto: bozze del curriculum,
  pre-generazione di esempi.
- I **prompt** stanno in `app/llm/prompts/*.vN.md`. La versione viene registrata in
  `llm_calls` e `evaluations`.

## 8. Livello NLP

| Componente | Strumento | Note |
|---|---|---|
| Lemmi, POS, nomi propri | spaCy, modello tedesco | Lemmatizzazione affidabile ma non perfetta: errori registrati |
| Composti | libreria di splitting (candidata: CharSplit) + verifica sulle parti nel lessico | Da valutare |
| Frequenze | `wordfreq` | Rango per lemma |
| Controllo grammaticale | LanguageTool, server in container | Chiamato via HTTP locale |
| Estrazione articoli | `trafilatura` | |

## 9. Curriculum: formato YAML

```yaml
# curriculum/de/lexicon/a2.yaml
- id: lex:tisch
  lemma: Tisch
  pos: noun
  gender: m
  plural: Tische
  level: A1
  translations: {it: tavolo, en: table}
  interference: {it_gender: m}          # coincide: nessun avviso
- id: lex:sonne
  lemma: Sonne
  pos: noun
  gender: f
  plural: Sonnen
  level: A1
  translations: {it: sole, en: sun}
  interference: {it_gender: m}          # diverso → priorità e avviso
```

```yaml
# curriculum/de/grammar/adj-endings.yaml
id: gram:adj-endings
title_it: Desinenze dell'aggettivo
level: A2
requires: [gram:cases-overview, gram:definite-articles]
diagnostic_tags:
  case: [nom, acc, dat, gen]
  gender_number: [masc, fem, neut, plural]
  declension: [strong, weak, mixed]
reference_it: |
  Testo di riferimento curato: regole, tabella, esempi, errori tipici di chi parla italiano.
```

Lo script `import_curriculum`:
- valida lo schema;
- controlla che gli id siano unici e che i prerequisiti esistano, senza cicli;
- aggiorna `items` senza toccare lo stato dello studente.

Gli id rimossi diventano `suspended`, non vengono cancellati.

## 10. Frontend

- **Viste:** Sessione (flashcard + esercizi), Lettura (testo con glossario al tocco),
  Corpus (stato degli item, filtri), Grammatica (spiegazioni su richiesta), Impostazioni.
- **PWA:** manifest e service worker per installarla sul telefono. Niente funzionamento
  offline in v1, perché la valutazione richiede l'LLM.
- **Prima il telefono:** sessioni da 10 minuti pensate per lo schermo piccolo.
- Il client API è generato dallo schema OpenAPI di FastAPI (es. `openapi-typescript`),
  così i tipi restano allineati.

## 11. Test e qualità

- **Dominio:** unit test sulle funzioni pure (padronanza, voto, budget, classificazione).
- **Determinismo del ricalcolo:** test che ricostruiscono le proiezioni dagli eventi e le
  confrontano con lo stato incrementale.
- **Servizi:** test con `FakeLLMClient` e LanguageTool simulato.
- **Valutazione del correttore:** `scripts/eval_grader.py` fa girare il correttore su un
  set di frasi annotate (e sulle contestazioni). Misura falsi positivi e falsi negativi
  per item. Si esegue a ogni cambio di prompt o modello.

## 12. Deploy

- `docker compose up`:
  - `backend`: FastAPI, che serve anche il frontend compilato;
  - `languagetool`;
  - un volume per SQLite.
- Configurazione via variabili d'ambiente (`ANTHROPIC_API_KEY`, percorsi, parametri).
- Backup: copia periodica del file SQLite. Il curriculum è già in git.
- In locale o su una piccola VPS. Serve HTTPS per la PWA sul telefono (es. reverse proxy con certificati automatici).

## 13. Tappe

| Tappa | Contenuto | Risultato usabile |
|---|---|---|
| **M0** | Scheletro: repo, compose, FastAPI, React, DB, migrazioni, CI con test | App vuota che parte |
| **M1** | Curriculum (import YAML, lessico A1–B1), event log, proiezioni, FSRS, flashcard | Flashcard con scheduling |
| **M2** | Livello LLM, esercizi di produzione, correzione (LLM + LanguageTool), spiegazioni | Sessione di ripasso completa |
| **M3** | Lettura: acquisizione, semplificazione con copertura, glossario, candidate | Leggere articoli |
| **M4** | Coda delle candidate e budget settimanale, contestazioni, valutazione iniziale | Ciclo completo dei requisiti |
| **M5** | Dataset e script di valutazione del correttore, ottimizzazione FSRS | Misura della qualità |

## 14. Decisioni aperte

- Granularità degli eventi impliciti dalla lettura: peso e quanti item per testo.
- Libreria di scomposizione dei composti: da valutare su un campione.
- Modello spaCy: `md` o `lg`, a seconda dell'accuratezza dei lemmi sul campione.
- Valori iniziali di `α`, `λ`, pesi delle prove e soglie del voto: da tarare con l'uso
  (l'event log permette di ricalcolare tutto).
