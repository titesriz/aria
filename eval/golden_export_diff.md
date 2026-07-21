# Golden dataset export diff — legacy vs Notion v2

- Legacy cases: 10
- New (Notion v2) cases: 12
- Added (in Notion v2, absent from legacy): ['CH-02', 'CH-05']
- Dropped (in legacy, absent from Notion v2): none

## Changed expectations (question or articles_attendus differs)

### CH-03
- expected articles (legacy): ['UG.2.2.3', 'Annexe X']
- articles_attendus (v2): ['UG.2.2.3 (couronnement/couverture)', 'Annexe X']

### CH-04
- question (legacy): 'Donne moi la liste des occupations et utilisations du sol interdites'
- question (v2): 'Donne moi la liste des occupations et utilisations du sol interdites.'
- expected articles (legacy): ['UG.1.1', 'N.1.1', 'UV.1.1', 'UGSU.1.1']
- articles_attendus (v2): ['UG.1.1', 'UGSU.1.1', 'UV.1.1', 'N.1.1']

### CH-06
- question (legacy): 'Peux tu me donner la liste des adresses du 1er arrondissement ?'
- question (v2): 'Peux-tu me donner la liste des adresses du 1er arrondissement (emplacements réservés logement) ?'
- expected articles (legacy): ['Annexe V']
- articles_attendus (v2): ['Annexe V (emplacements réservés logement)']

### UC-01
- question (legacy): 'Je cherche la hauteur maximale autorisée pour une construction neuve en zone UG dans le 11e arrondissement de Paris, le terrain est en secteur DG5.'
- question (v2): 'Connaître la hauteur maximale constructible sur une parcelle en zone UG à Paris 11e, rue de la Roquette, pour un projet de logements neufs de 800m² (surface de plancher).'

### UC-02
- question (legacy): "Je cherche la règle de retrait par rapport à la voie publique pour une construction en zone UG dans le 15e arrondissement de Paris. Le projet est à l'alignement ou nécessite-t-il un retrait minimum de 3 mètres ?"
- question (v2): "Savoir si ma construction neuve peut être implantée en retrait de 3m par rapport à l'alignement de la voie, en zone UG, pour un programme mixte logements/commerces à Paris 15e."
- expected articles (legacy): ['UG.3.1.1', 'UG.3.1']
- articles_attendus (v2): ['UG.3.1.1']

### UC-03
- question (legacy): 'Je cherche la règle de prospect par rapport à une limite séparative latérale droite en zone UG. Mon bâtiment a des baies principales (PP) sur ce pignon, la hauteur est de 8 mètres. Quelle distance minimale dois-je respecter ?'
- question (v2): 'Comprendre la règle de prospect à respecter vis-à-vis de la limite séparative droite, sachant que la façade projetée comprend des baies de pièces principales à 8m de haut.'
- expected articles (legacy): ['UG.3.1.2', 'UG.3.1']
- articles_attendus (v2): ['UG.3.1.2 (à confirmer)', 'figure FNE (à confirmer)']

### UC-04
- question (legacy): 'Je cherche si un changement de destination de bureaux vers hôtel est autorisé en zone UG dans le 8e arrondissement de Paris. Quelles sont les conditions réglementaires applicables ?'
- question (v2): "Vérifier si la création d'un hôtel de 40 chambres est autorisée en zone UG Paris 8e, sur une parcelle actuellement à usage de bureaux, et quelles conditions s'appliquent."
- expected articles (legacy): ['UG.1.3', 'UG.1']
- articles_attendus (v2): ['UG.1.3 (destinations)']

### UC-05
- question (legacy): 'Je cherche les règles de mixité fonctionnelle pour un projet de 2000 m² de surface plancher mêlant bureaux et logements en zone UG. Quelle proportion de logements est imposée ?'
- question (v2): 'Savoir quelle proportion minimale de surface de plancher doit être affectée au logement dans un programme mixte bureaux + logements en zone UG, secteur nord-ouest de Paris.'
- expected articles (legacy): ['UG.1.4.1', 'UG.1.4']
- articles_attendus (v2): ['UG.1.4.1 3°']

### UC-16
- question (legacy): 'Je cherche à surélever un bâtiment existant R+4 culminant à 17 mètres en zone UG dans le 9e arrondissement. Le plan des hauteurs DG5 autorise 18 mètres à cet emplacement. La surélévation est-elle possible et sous quelles conditions ?'
- question (v2): "Puis-je surélever d'un niveau (R+1) mon immeuble R+4 existant à 17m en zone UG Paris 9e, où le plafond de hauteur DG5 est à 18m ? Sous quelles conditions ?"
