# Retrieval ranking audit

## UC-01: Article UG.3.2.1 — "Plan général des hauteurs" subsection

Query: *Je cherche la hauteur maximale autorisée pour une construction neuve en zone UG dans le 11e arrondissement de Paris, le terrain est en secteur DG5.*

Expected chunk (best-ranked among 16 candidate(s) matching the predicate):
- `idx=12417 score=n/a [reglement_ecrit] REG1.pdf p75 sec='UG.3.2.4' — 'ment) Dans le secteur Bartholomé Brancion, par exception aux dispositi'`

**Baseline (no query expansion), corpus=4438:**
- FAISS: rank 2/4438, score 0.7662
- BM25:  rank 3/4438, score 55.3875
- RRF:   rank 1/4438, score 0.03200

Top-8 RRF winners (baseline) — who beats it:
1. `idx=12417 score=0.0320 [reglement_ecrit] REG1.pdf p75 sec='UG.3.2.4' — 'ment) Dans le secteur Bartholomé Brancion, par exception aux dispositi'`  <-- EXPECTED
2. `idx=12404 score=0.0301 [reglement_ecrit] REG1.pdf p70 sec='UG.3.2.2' — 'UG.3.2.2 Terrains* concernés par une Hauteur maximale des construction'`
3. `idx=12425 score=0.0299 [reglement_ecrit] REG1.pdf p78 sec='UG.3.2.5' — 'rticale limitée par une horizontale située à la hauteur plafond défini'`
4. `idx=12432 score=0.0289 [reglement_ecrit] REG1.pdf p81 sec='UG.3.2.7' — 'UG.3.2.7 Terrains* concernés par une Emprise constructible maximale (E'`
5. `idx=12401 score=0.0272 [reglement_ecrit] REG1.pdf p69 sec='UG.3.2.1' — 'UG.3.2.1 Limitation générale des hauteurs 1° Plan général des hauteurs'`
6. `idx=12443 score=0.0265 [reglement_ecrit] REG1.pdf p84 sec='UG.3.3.3' — 'e 2 mètres minimum de la verticale de la façade* sur voie* de la const'`
7. `idx=12440 score=0.0259 [reglement_ecrit] REG1.pdf p83 sec='UG.3.3.2' — 'UG.3.3.2 Dispositions particulières applicables dans certains secteurs'`
8. `idx=12397 score=0.0249 [reglement_ecrit] REG1.pdf p68 sec='UG.3.1.4' — 'UG.3.1.4 Emprise au sol* et emprise géométrique* des constructions 1° '`

**Category (baseline): not a loss (ranks in top-8)**

**eval.py-equivalent scoring (baseline, {article: substring_found}):** {'UG.3.2': True, 'Plan général des hauteurs': True}

**With query expansion (alpha=0.5):**
- Inferred articles: ['UG.3.2', 'UG.3.2.1', 'UG.3.2.2']
- Expansion query string: `'UG.3.2 UG.3.2.1 UG.3.2.2'`

- Expansion-query-alone RRF rank for expected chunk: 231/4438
- Combined (alpha-weighted) rank: 7/4438, score 0.01948
- Baseline-only rank was: 1/4438
- Expansion effect: rank WORSENED by 6 positions

Top-8 combined winners (with expansion) — who beats it:
1. `idx=12404 score=0.0233 [reglement_ecrit] REG1.pdf p70 sec='UG.3.2.2' — 'UG.3.2.2 Terrains* concernés par une Hauteur maximale des construction'`
2. `idx=12399 score=0.0219 [reglement_ecrit] REG1.pdf p68 sec='UG.3.2' — 'UG.3.2 Hauteur et volumétrie des constructions La hauteur et la volumé'`
3. `idx=12448 score=0.0215 [reglement_ecrit] REG1.pdf p86 sec='UG.3.3.5' — 'UG.3.3.5 Terrains* situés en limite d’équipement sportif de plein air,'`
4. `idx=12440 score=0.0209 [reglement_ecrit] REG1.pdf p83 sec='UG.3.3.2' — 'UG.3.3.2 Dispositions particulières applicables dans certains secteurs'`
5. `idx=12432 score=0.0205 [reglement_ecrit] REG1.pdf p81 sec='UG.3.2.7' — 'UG.3.2.7 Terrains* concernés par une Emprise constructible maximale (E'`
6. `idx=12451 score=0.0202 [reglement_ecrit] REG1.pdf p87 sec='UG.3.3.7' — 'UG.3.3.7 Créations de surfaces de plancher* dans le volume d’une const'`
7. `idx=12417 score=0.0195 [reglement_ecrit] REG1.pdf p75 sec='UG.3.2.4' — 'ment) Dans le secteur Bartholomé Brancion, par exception aux dispositi'`  <-- EXPECTED
8. `idx=12443 score=0.0189 [reglement_ecrit] REG1.pdf p84 sec='UG.3.3.3' — 'e 2 mètres minimum de la verticale de la façade* sur voie* de la const'`

**eval.py-equivalent scoring (with expansion, {article: substring_found}):** {'UG.3.2': True, 'Plan général des hauteurs': True}

**Category (expansion contribution): expansion misdirection (expansion query itself ranks the expected chunk worse than the original query did)**

## UC-02: Article UG.3.1.1

Query: *Je cherche la règle de retrait par rapport à la voie publique pour une construction en zone UG dans le 15e arrondissement de Paris. Le projet est à l'alignement ou nécessite-t-il un retrait minimum de 3 mètres ?*

Expected chunk (best-ranked among 7 candidate(s) matching the predicate):
- `idx=12383 score=n/a [reglement_ecrit] REG1.pdf p63 sec='UG.3.1.1' — 'etrait par rapport à la limite de la voie* peuvent comporter des saill'`

**Baseline (no query expansion), corpus=4438:**
- FAISS: rank 5/4438, score 0.7556
- BM25:  rank 1/4438, score 86.0570
- RRF:   rank 1/4438, score 0.03178

Top-8 RRF winners (baseline) — who beats it:
1. `idx=12383 score=0.0318 [reglement_ecrit] REG1.pdf p63 sec='UG.3.1.1' — 'etrait par rapport à la limite de la voie* peuvent comporter des saill'`  <-- EXPECTED
2. `idx=12391 score=0.0272 [reglement_ecrit] REG1.pdf p65 sec='UG.3.1.2' — 'doit être implanté à une distance minimale de 3 mètres par rapport à l'`
3. `idx=12379 score=0.0255 [reglement_ecrit] REG1.pdf p61 sec='UG.3.1.1' — 'le retrait doivent bénéficier d’un traitement architectural de qualité'`
4. `idx=12388 score=0.0251 [reglement_ecrit] REG1.pdf p64 sec='UG.3.1.2' — 'ation en retrait, le nu extérieur de la façade* doit être implanté à u'`
5. `idx=12749 score=0.0238 [reglement_ecrit] REG1.pdf p182 sec='UV.3.1.1' — 'luvial Les dispositions qui suivent s’appliquent aux constructions de '`
6. `idx=12748 score=0.0233 [reglement_ecrit] REG1.pdf p182 sec='UV.3.1.1' — 'UV.3.1.1 Implantation des constructions par rapport aux voies* 1° Disp'`
7. `idx=12378 score=0.0223 [reglement_ecrit] REG1.pdf p61 sec='UG.3.1.1' — 'UG.3.1.1 Implantation des constructions par rapport aux voies* 1° Disp'`
8. `idx=12381 score=0.0220 [reglement_ecrit] REG1.pdf p62 sec='UG.3.1.1' — 'te construction à édifier en bordure ou en vis-à-vis d’une voie* doit '`

**Category (baseline): not a loss (ranks in top-8)**

**eval.py-equivalent scoring (baseline, {article: substring_found}):** {'UG.3.1.1': True, 'UG.3.1': True}

**With query expansion (alpha=0.5):**
- Inferred articles: ['UG.3.1', 'UG.3.1.2', 'UG.3.1.3']
- Expansion query string: `'UG.3.1 UG.3.1.2 UG.3.1.3'`

- Expansion-query-alone RRF rank for expected chunk: 1988/4438
- Combined (alpha-weighted) rank: 6/4438, score 0.01643
- Baseline-only rank was: 1/4438
- Expansion effect: rank WORSENED by 5 positions

Top-8 combined winners (with expansion) — who beats it:
1. `idx=12441 score=0.0176 [reglement_ecrit] REG1.pdf p84 sec='UG.3.3.3' — 'UG.3.3.3 Surélévations* destinées à l’Habitation 1° Dispositions génér'`
2. `idx=12391 score=0.0175 [reglement_ecrit] REG1.pdf p65 sec='UG.3.1.2' — 'doit être implanté à une distance minimale de 3 mètres par rapport à l'`
3. `idx=12267 score=0.0171 [reglement_ecrit] REG1.pdf p25 sec='annexe I du tome 2 du règlement écrit indique les références des' — '[Section: annexe I du tome 2 du règlement écrit indique les références'`
4. `idx=12388 score=0.0171 [reglement_ecrit] REG1.pdf p64 sec='UG.3.1.2' — 'ation en retrait, le nu extérieur de la façade* doit être implanté à u'`
5. `idx=12378 score=0.0168 [reglement_ecrit] REG1.pdf p61 sec='UG.3.1.1' — 'UG.3.1.1 Implantation des constructions par rapport aux voies* 1° Disp'`
6. `idx=12383 score=0.0164 [reglement_ecrit] REG1.pdf p63 sec='UG.3.1.1' — 'etrait par rapport à la limite de la voie* peuvent comporter des saill'`  <-- EXPECTED
7. `idx=12446 score=0.0164 [reglement_ecrit] REG1.pdf p85 sec='UG.3.3.4' — 'UG.3.3.4 Adossements en limite séparative* 1° Dispositions générales A'`
8. `idx=12410 score=0.0159 [reglement_ecrit] REG1.pdf p72 sec='UG.3.2.4' — 'UG.3.2.4 Gabarit-enveloppe* en bordure de voie* 1° Dispositions généra'`

**eval.py-equivalent scoring (with expansion, {article: substring_found}):** {'UG.3.1.1': True, 'UG.3.1': True}

**Category (expansion contribution): expansion misdirection (expansion query itself ranks the expected chunk worse than the original query did)**

## UC-04: Article UG.1.3

Query: *Je cherche si un changement de destination de bureaux vers hôtel est autorisé en zone UG dans le 8e arrondissement de Paris. Quelles sont les conditions réglementaires applicables ?*

Expected chunk (best-ranked among 6 candidate(s) matching the predicate):
- `idx=12306 score=n/a [reglement_ecrit] REG1.pdf p43 sec='UG.1.3.3' — 'UG.1.3.3 Autres hébergements touristiques Sur les terrains* comportant'`

**Baseline (no query expansion), corpus=4438:**
- FAISS: rank 17/4438, score 0.6156
- BM25:  rank 6/4438, score 46.7400
- RRF:   rank 1/4438, score 0.02814

Top-8 RRF winners (baseline) — who beats it:
1. `idx=12306 score=0.0281 [reglement_ecrit] REG1.pdf p43 sec='UG.1.3.3' — 'UG.1.3.3 Autres hébergements touristiques Sur les terrains* comportant'`  <-- EXPECTED
2. `idx=13253 score=0.0272 [reglement_ecrit] REG2A10_1DE2.pdf p444 sec='ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 7ÈME ARRONDISSEMENT' — '[Section: ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 7ÈME ARRON'`
3. `idx=12560 score=0.0247 [reglement_ecrit] REG1.pdf p124 sec='UG.8.5' — 'UG.8.5 Dispositions particulières applicables dans certains secteurs a'`
4. `idx=12304 score=0.0220 [reglement_ecrit] REG1.pdf p42 sec='UG.1.3.1' — 'UG.1.3.1 Entrepôt Les locaux relevant de la sous-destination* Entrepôt'`
5. `idx=13119 score=0.0191 [reglement_ecrit] REG2A1.pdf p42 sec='Annexe V : Liste des emplacements' — '[Section: Annexe V : Liste des emplacements] ogements locatifs sociaux'`
6. `idx=12317 score=0.0184 [reglement_ecrit] REG1.pdf p45 sec='UG.1.4.2' — 'UG.1.4.2 Protection du commerce et de l’artisanat 1° Protection des li'`
7. `idx=12272 score=0.0183 [reglement_ecrit] REG1.pdf p27 sec='annexe I du tome 2 du règlement écrit indique les références des' — '[Section: annexe I du tome 2 du règlement écrit indique les références'`
8. `idx=12830 score=0.0183 [reglement_ecrit] REG1.pdf p206 sec='annexe X du tome 2 du règlement écrit recense par adresse les protections patrimoniales' — '[Section: annexe X du tome 2 du règlement écrit recense par adresse le'`

**Category (baseline): not a loss (ranks in top-8)**

**eval.py-equivalent scoring (baseline, {article: substring_found}):** {'UG.1.3': True, 'UG.1': True}

**With query expansion (alpha=0.5):**
- Inferred articles: ['UG.2.3', 'UG.2.3.4', 'UG.2.3.5']
- Expansion query string: `'UG.2.3 UG.2.3.4 UG.2.3.5'`

- Expansion-query-alone RRF rank for expected chunk: 150/4438
- Combined (alpha-weighted) rank: 1/4438, score 0.01867
- Baseline-only rank was: 1/4438
- Expansion effect: rank unchanged by 0 positions

Top-8 combined winners (with expansion) — who beats it:
1. `idx=12306 score=0.0187 [reglement_ecrit] REG1.pdf p43 sec='UG.1.3.3' — 'UG.1.3.3 Autres hébergements touristiques Sur les terrains* comportant'`  <-- EXPECTED
2. `idx=12560 score=0.0186 [reglement_ecrit] REG1.pdf p124 sec='UG.8.5' — 'UG.8.5 Dispositions particulières applicables dans certains secteurs a'`
3. `idx=12304 score=0.0171 [reglement_ecrit] REG1.pdf p42 sec='UG.1.3.1' — 'UG.1.3.1 Entrepôt Les locaux relevant de la sous-destination* Entrepôt'`
4. `idx=12305 score=0.0166 [reglement_ecrit] REG1.pdf p42 sec='UG.1.3.2' — 'UG.1.3.2 Industrie Les locaux relevant de la sous-destination* Industr'`
5. `idx=12267 score=0.0163 [reglement_ecrit] REG1.pdf p25 sec='annexe I du tome 2 du règlement écrit indique les références des' — '[Section: annexe I du tome 2 du règlement écrit indique les références'`
6. `idx=12287 score=0.0160 [reglement_ecrit] REG1.pdf p34 sec='annexe IX du tome 2 du règlement écrit.' — '[Section: annexe IX du tome 2 du règlement écrit.] Pièce destinée au s'`
7. `idx=12317 score=0.0157 [reglement_ecrit] REG1.pdf p45 sec='UG.1.4.2' — 'UG.1.4.2 Protection du commerce et de l’artisanat 1° Protection des li'`
8. `idx=13096 score=0.0156 [reglement_ecrit] REG2A1.pdf p5 sec='UG.1.4.1' — 'UG.1.4.1 Sous-section énonçant des dispositions particulières (hors UG'`

**eval.py-equivalent scoring (with expansion, {article: substring_found}):** {'UG.1.3': True, 'UG.1': True}

**Category (expansion contribution): expansion neutral-to-positive (not the cause of the loss)**

## Table-1er: Annexe V — 1er arrondissement address row (rue d'Argenteuil)

Query: *quels emplacements réservés pour logements dans le 1er arrondissement*

Expected chunk (best-ranked among 1 candidate(s) matching the predicate):
- `idx=13120 score=n/a [reglement_ecrit] REG2A1.pdf p42 sec='Annexe V : Liste des emplacements' — '[Section: Annexe V : Liste des emplacements] S 100-100 1er 7 avenue de'`

**Baseline (no query expansion), corpus=4438:**
- FAISS: rank 2247/4438, score 0.4829
- BM25:  rank 21/4438, score 16.1861
- RRF:   rank 53/4438, score 0.01278

Top-8 RRF winners (baseline) — who beats it:
1. `idx=12331 score=0.0320 [reglement_ecrit] REG1.pdf p49 sec='UG.1.5.2' — 'UG.1.5.2 Emplacements réservés en vue de la réalisation de certains ty'`
2. `idx=13156 score=0.0283 [reglement_ecrit] REG2A1.pdf p74 sec='A NNEXE V : LISTE DES EMPLACEMENTS RÉSERVÉS EN VUE DE LA RÉALISATION DE' — '[Section: A NNEXE V : LISTE DES EMPLACEMENTS RÉSERVÉS EN VUE DE LA RÉA'`
3. `idx=15331 score=0.0229 [reglement_ecrit] REG2A10_2DE2_MS1.pdf p701 sec='ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 18ÈME ARRONDISSEMENT' — '[Section: ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 18ÈME ARRO'`
4. `idx=12725 score=0.0198 [reglement_ecrit] REG1.pdf p175 sec='UV.1.5.1' — 'UV.1.5.1 Emplacements réservés pour équipements En application de l’ar'`
5. `idx=12825 score=0.0193 [reglement_ecrit] REG1.pdf p204 sec='N.1.3.1' — 'N.1.3.1 Emplacements réservés pour équipements En application de l’art'`
6. `idx=13149 score=0.0187 [reglement_ecrit] REG2A1.pdf p9 sec='Annexe III : Liste des emplacements' — '[Section: Annexe III : Liste des emplacements] ages publics, installat'`
7. `idx=16647 score=0.0180 [reglement_ecrit] REG2A10_2DE2_MS1.pdf p46 sec='ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 11ÈME ARRONDISSEMENT' — '[Section: ANNEXE X - LISTE DES PROTECTIONS PATRIMONIALES DU 11ÈME ARRO'`
8. `idx=12537 score=0.0179 [reglement_ecrit] REG1.pdf p115 sec='UG.7.2.1' — 'aux accueillant du public des administrations publiques et assimilés, '`

**Category (baseline): semantic miss (BM25 finds it, embedding similarity doesn't distinguish it)**

**eval.py-equivalent scoring (baseline, {article: substring_found}):** {}
