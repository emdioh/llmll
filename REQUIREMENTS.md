# LLMLL — Requisiti

> Documento vivo. Raccoglie le decisioni prese finora e le domande ancora aperte.
> Stato: bozza v0.2 (ottobre 2026).

## 1. Visione

Un'app per imparare le lingue che unisce:
- un **curriculum strutturato** (grammatica e lessico in un ordine sensato),
- **ripetizione dilazionata** (spaced repetition) su *tutto* ciò che si impara,
- un **LLM** che genera esercizi e testi, corregge frasi libere e spiega la grammatica.

L'idea centrale: **un unico modello di ciò che lo studente sa**. Flashcard, frasi da
produrre e testi da leggere sono solo modi diversi di mettere alla prova e aggiornare
quel modello.

## 2. Ambito

### v1: uso personale
- **Lingua da imparare:** tedesco.
- **Lingue note:** italiano (madrelingua) e inglese. Le spiegazioni sono in italiano.
- **Livello di partenza:** A2–B1, con lacune probabilmente irregolari.
- **Tempo disponibile:** 1–2 ore a settimana, in sessioni da circa 10 minuti, non tutti i giorni.
- **Piattaforma:** web, usabile da telefono (responsive / PWA).

### Fuori ambito per la v1
- Parlato, ascolto, pronuncia (niente TTS né riconoscimento vocale).
- Account multiutente, pagamenti, altre lingue.

### Più avanti: prodotto commerciale
Le scelte della v1 non devono precluderlo (vedi §13).

## 3. Principi

1. **Tutto è corpus.** Parole, grammatica e costrutti sono *knowledge item* con uno stato di memoria.
2. **Contano anche i successi, non solo gli errori.** Ogni uso corretto è una prova di conoscenza.
3. **Un errore isolato non cancella una storia di successi.** Svista ≠ errore sistematico.
4. **Il correttore può sbagliare.** Le sue valutazioni hanno un livello di confidenza, e si possono contestare.
5. **Il contenuto deve essere interessante.** Con poco tempo a disposizione, la motivazione è il collo di bottiglia.
6. **Contenuti come dati, non come codice.** Curriculum, item ed esercizi stanno in file o DB, non nel codice.

## 4. Modello di conoscenza

### 4.1 Knowledge item
Tipi:
- **Lemma:** voce lessicale con un significato preciso. Per i sostantivi include sempre articolo e plurale (`der Tisch, -e`).
  Significati diversi sono item diversi (`die Bank` panchina / banca).
- **Punto grammaticale:** es. "dativo dopo `mit`", "verbo finale nella subordinata", "desinenze dell'aggettivo".
- **Costrutto / collocazione:** es. `Lust haben auf + Akk`, `es gibt + Akk`.

Metadati per item:
- livello CEFR indicativo, frequenza;
- prerequisiti (archi del grafo, §5);
- **interferenza con le lingue note**: es. genere diverso da quello italiano
  (`il sole → die Sonne`, `il mare → das Meer`), falsi amici (`bekommen ≠ become`),
  affinità utili (`haben/sein` ≈ `avere/essere` nel passato prossimo);
- per i lemmi, la **direzione**: il riconoscimento (DE→IT) e la produzione (IT→DE)
  sono stati di memoria separati.

**Regola di granularità: item grossolani, tag diagnostici fini.**
Un punto grammaticale è un solo item per curriculum e scheduling (es. "desinenze
dell'aggettivo"). Ogni errore porta però **tag diagnostici** sulla sotto-dimensione
coinvolta, ad esempio per le desinenze: caso × genere/numero × tipo di declinazione
(forte / debole / mista). I tag servono a:
- spiegazioni mirate al sotto-caso sbagliato, non alla tabella intera;
- esercizi di recupero sul sotto-caso;
- distinguere "non so le desinenze" da "sbaglio solo il dativo femminile".

Se un sotto-caso risulta sistematicamente sbagliato, si può promuovere a item a sé
(o sotto-item) senza rifare il modello.

### 4.2 Due livelli di stato per item

| Livello | Domanda | Modello |
|---|---|---|
| **Memoria** | Quando va ripassato? | FSRS (stabilità, difficoltà, recuperabilità) |
| **Padronanza** | Lo so usare in modo affidabile? L'errore è casuale o sistematico? | Media mobile pesata (v1), poi eventualmente Beta con decadimento |

**FSRS** (Free Spaced Repetition Scheduler, open source, usato da Anki): per ogni item
stima la *stabilità* (giorni prima che la probabilità di ricordo scenda al 90%), la
*difficoltà* e la *recuperabilità* attuale. Ogni ripasso riceve un voto
Again / Hard / Good / Easy.

**Padronanza:** media mobile pesata sui risultati recenti. Ogni osservazione pesa in base a:
- **forza della prova**: flashcard di riconoscimento < flashcard di produzione < frase guidata < frase libera;
- **recentezza.**

La v1 deve anche tenere traccia del **numero di osservazioni**, per distinguere "3 su 3" da "30 su 30".

### 4.3 Dall'esito dell'esercizio al voto FSRS
Il voto non è un'autovalutazione: lo deriva l'app.

| Esito | Padronanza | Voto FSRS | Azione extra |
|---|---|---|---|
| Corretto, senza esitazione | qualsiasi | Good / Easy | — |
| Corretto con aiuto (suggerimento, glossario) | qualsiasi | Hard | — |
| Errore | alta (storia di successi) | **Hard** (svista) | — |
| Errore | bassa o in calo | **Again** | spiegazione mirata + 1–2 esercizi di recupero |
| Valutazione incerta (§8) | qualsiasi | nessun aggiornamento o aggiornamento attenuato | — |

Le soglie si tarano usando l'app.

### 4.4 Ripassi impliciti
Usare correttamente un item in una frase, o leggerlo in un testo senza consultare il
glossario, conta come ripasso. Un ripasso anticipato dà meno guadagno di stabilità
(FSRS lo gestisce già). Molte flashcard non andranno mai mostrate, perché l'item è già
stato "ripassato" usandolo.

## 5. Curriculum

- **Grafo dei prerequisiti**, non una sequenza lineare. Gli item si possono "superare"
  con un test, e il percorso si adatta.
- **Ordine delle parole secondo la sequenza di acquisizione documentata** (progetto ZISA,
  Clahsen/Meisel/Pienemann, studiato proprio su apprendenti italiani e spagnoli):
  1. SVO canonico
  2. avverbio in prima posizione
  3. separazione dei verbi separabili / parentesi verbale
  4. inversione (V2)
  5. verbo finale nella subordinata
- **Fonti di riferimento per la v1:** Profile Deutsch, liste lessicali Goethe A1–B1
  (circa 650 / 1300 / 2400 voci). ⚠️ Sono protette da copyright: vanno bene per uso
  personale, ma un prodotto commerciale richiede un curriculum proprio (§13).
- Il curriculum si costruisce **offline** (con l'aiuto dell'LLM, con revisione) e
  l'LLM **non** lo inventa durante le sessioni.

## 6. Valutazione iniziale

Partendo da A2–B1 ci sono già centinaia o migliaia di item noti. **Non vanno messi tutti
in coda di ripasso**: ne uscirebbe una valanga di flashcard.

- Gli item sotto il livello stimato partono come **"presunti noti"**: stato a priori,
  non schedulati.
- Ci entrano solo quando: (a) emerge un errore, (b) un campionamento casuale li verifica,
  (c) vengono usati o letti, cioè un ripasso implicito.
- **Test iniziale breve:** stima del vocabolario per fasce di frequenza, più 3–5 frasi da
  produrre, valutate rispetto al grafo.
- Ogni sessione affina la stima, quindi un piazzamento sbagliato si corregge in pochi giorni.
- Riconoscimento e produzione si stimano separatamente.

## 7. Attività

### 7.1 Esercizi di produzione
In ordine di vincolo decrescente:
1. **Traduzione IT→DE:** il significato è fissato, quindi è il più facile da valutare.
2. **Frase guidata:** "usa X per dire Y" (es. "usa `weil` per spiegare perché sei in ritardo").
   Serve contro l'*evitamento*: senza vincoli, lo studente aggira le strutture che non sa.
3. **Riformulazione / trasformazione:** es. da principale a subordinata, da presente a Perfekt.
4. **Produzione libera:** su un tema, o in risposta a un testo letto (§7.3).

Ogni esercizio dichiara quali item mette alla prova e con quale peso.

### 7.2 Flashcard
- Lemmi con articolo, plurale e frase d'esempio.
- Entrambe le direzioni, schedulate separatamente.
- Priorità alle parole con interferenza di genere rispetto all'italiano.

### 7.3 Lettura: il motore della motivazione
È la funzione centrale, non un extra.

- **Sorgenti:**
  - URL o testo incollato (v1);
  - in seguito, feed RSS su temi scelti.
  - **Temi iniziali:** cronaca contemporanea, tecnologia, scienza (da rifinire).
  - Candidati da verificare: tagesschau.de, Deutsche Welle, nachrichtenleicht.de
    (notizie in tedesco semplificato, utile anche come riferimento di stile), heise.de
    (tecnologia), Spektrum.de (scienza), Wikipedia DE.
  - Da preferire articoli tedeschi originali (tedesco autentico); in alternativa,
    traduzione di articoli italiani o inglesi.
- **Semplificazione al livello**, con **copertura lessicale misurata**: dopo la
  lemmatizzazione (es. spaCy `de`), almeno il 95–98% dei token deve essere già noto.
  Se l'LLM non rispetta la soglia, si rigenera. Le parole nuove sono poche e scelte.
- **Glossario al tocco:** traduzione nel contesto, lemma, genere, plurale. Toccare una
  parola è un segnale: per quell'item vale come ripasso fallito o assistito. Le parole
  nuove si possono aggiungere al corpus.
- **Dopo la lettura:** 1–2 domande di comprensione e/o un breve riassunto o commento da
  scrivere. Così la lettura alimenta gli esercizi di produzione.
- **Testi generati da zero**, con le parole da ripassare, come alternativa quando non c'è
  un articolo.

## 8. Correzione

- **Output strutturato** dall'LLM. Per ogni errore:
  - span;
  - correzione;
  - item del curriculum coinvolto;
  - gravità;
  - confidenza.

  Anche gli usi *corretti* degli item vanno segnalati (§4.4).
- **Non correggere tutto.** Mostrare per primi gli errori sugli item in esercizio o
  sistematici; gli altri, se presenti, vanno in un'area secondaria.
- **Accettare le varianti.** Più risposte giuste sono normali, e le preferenze
  stilistiche non sono errori.
- **Doppio controllo:** LanguageTool (open source, buone regole per il tedesco) come
  verifica deterministica di morfologia e accordi. Se LanguageTool e l'LLM non sono
  d'accordo, la valutazione è **incerta** e pesa meno sullo stato.
- **Pulsante "secondo me era giusto":** le contestazioni finiscono in un log per la revisione.
- **Log completo** di ogni valutazione (input, output, versione del prompt e del modello).
  Diventa il dataset per misurare il correttore; da far controllare a campione a un madrelingua.

## 9. Spiegazioni

- **Sugli errori:** brevi, legate all'item. Si possono espandere.
- **Su richiesta:** per ogni punto grammaticale.
- **Ancorate a un testo di riferimento curato** per ogni punto grammaticale, per ridurre
  le regole inventate dall'LLM.
- **Coerenti con il percorso:** non usano concetti che lo studente non ha ancora incontrato.
- **Sfruttano le lingue note:** analogie con l'italiano (`avere/essere` ↔ `haben/sein`) e
  con l'inglese (affini lessicali), avvisi sulle interferenze.

## 10. Sessioni e carico

Con 1–2 ore a settimana in modo irregolare, il problema principale è **l'arretrato**.

- **Sessione da circa 10 minuti, interrompibile e riprendibile.** Nessuna penalità se si salta un giorno.
- **Retention target di FSRS più bassa** (es. 85% invece di 90%), per ridurre il carico; regolabile.
- **Tetto ai ripassi per sessione**, con priorità per:
  - recuperabilità bassa;
  - importanza dell'item (frequenza, prerequisito di altri).
- **Introduzione di item nuovi limitata dal budget di tempo.** Indicativamente 5–8 per
  sessione, meno se c'è arretrato.
- **Composizione tipica di una sessione:**
  - ripassi in scadenza (flashcard ed esercizi);
  - 1–2 esercizi di produzione;
  - oppure una sessione di lettura.

## 11. Piattaforma e stack

Criterio: **tecnologie mainstream e mantenibili**, niente di esotico.

| Parte | Scelta | Motivo |
|---|---|---|
| Backend | **Python + FastAPI** | L'ecosistema NLP per il tedesco (spaCy, `py-fsrs`, trafilatura) è in Python |
| Database | **SQLite** via SQLAlchemy (+ Alembic per le migrazioni) | Un file, backup banale; passaggio a PostgreSQL senza riscrivere |
| Frontend | **React + TypeScript (Vite)**, come PWA responsive | Il più diffuso; funziona da telefono senza store |
| Correzione deterministica | **LanguageTool** self-hosted (container Docker) | L'API pubblica ha limiti di uso |
| LLM | Claude API dietro un'interfaccia propria | Provider sostituibile |
| Deploy | Docker Compose (app + LanguageTool), in locale o su una piccola VPS | Un comando per avviare tutto |

Costo dell'LLM trascurabile per un singolo utente; da rivalutare per il prodotto commerciale.

## 12. Come capire se funziona

- Precisione del correttore sul dataset annotato (falsi positivi e falsi negativi).
- Retention effettiva rispetto a quella target di FSRS.
- Progresso sul grafo nel tempo; ogni tanto un test esterno (es. modello d'esame Goethe B1/B2).
- Uso reale: quante sessioni a settimana, quante letture concluse.

## 13. Preparazione al commerciale

**Da fare già nella v1** (costa poco ora, molto dopo):
- contenuti separati dal codice;
- coppia di lingue come parametro;
- profilo "lingue note" (non una sola L1);
- provider dell'LLM astratto;
- log completo degli eventi.

**Da rimandare:** multiutente, autenticazione, pagamenti, altre lingue, rifinitura della UI.

**Problemi noti:**
- **Copyright del curriculum** (Profile Deutsch, liste Goethe): serve un curriculum proprio.
- **Copyright degli articoli:** distribuire versioni semplificate di articoli protetti è
  un'opera derivata. Opzioni:
  - l'utente porta il proprio URL (elaborazione per uso personale);
  - fonti libere (Wikipedia DE, CC BY-SA);
  - accordi con editori.
- **Mercato affollato** (Duolingo Max, Babbel, Speak, tutor LLM). Possibili elementi distintivi:
  - spiegazioni che sfruttano le lingue già note;
  - modello di memoria unificato su tutti i tipi di attività;
  - letture adattate al vocabolario personale.

## 14. Rischi principali

1. **Affidabilità del correttore.** Lo studente non può verificarlo da solo. Mitigazioni in §8.
2. **Attribuzione degli errori agli item** (credit assignment) nelle frasi libere.
3. **Costo di costruzione del curriculum.**
4. **Arretrato e abbandono** con un uso irregolare.
5. **Semplificazione dei testi:** l'LLM può alterare il significato o non rispettare il
   vocabolario. Mitigazione: misura della copertura e rigenerazione.

## 15. Domande aperte

- Come stimare la padronanza di un punto grammaticale a partire da prove su item diversi?
- Fonti di articoli definitive.
- Formato e strumenti per scrivere il curriculum.
- Peso relativo di flashcard, produzione e lettura in una sessione.
