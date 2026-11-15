# Corrections locales — 13 novembre 2026

Branche : `fix/local-governance-safety`. Les modifications sont enregistrées dans
un commit local. Aucun push ni déploiement AWS n'a été effectué.
Le premier patch `red-queen-fixes.patch` reste introuvable. Les corrections ont été
adaptées au dépôt actuel ; le second patch n'a pas été appliqué aveuglément.

Un lot ultérieur renforce IAM : consulter [IAM_MOINDRE_PRIVILEGE.md](IAM_MOINDRE_PRIVILEGE.md).
Il ajoute notamment une liste de ressources autorisées vide par défaut, des droits
distincts par rôle et un groupe Scheduler dédié. Les résultats ci-dessous décrivent
le premier lot ; ceux du lot IAM sont consignés dans ce document complémentaire.

## Changements

- Les valeurs vides, non textuelles et les valeurs de remplissage connues ne
  constituent plus des tags conformes. La liste est dans `lambdas/shared/constants.py`.
- Owner, Squad et CostCenter sont vides par défaut. Une valeur fictive dans la
  requête n'empêche plus de retenir une identité de créateur valable.
- S3 conserve les tags existants lorsqu'on ajoute des tags obligatoires.
- Les résultats séparent `tags_planned`, `tags_added` et `tags_unresolved`.
  Une erreur d'écriture remonte à l'appelant au lieu d'annoncer un succès.
- `dry_run=true` autorise les lectures et les logs, mais pas les écritures de tags,
  les notifications, les plannings, DynamoDB ou les métriques personnalisées.
  Les logs et métriques natifs du service Lambda restent gérés par AWS.
- Les sources EventBridge CloudTrail sont les services EC2, S3, RDS et Lambda.
- Le parseur lit les identifiants des événements de changement de conformité
  Config et récupère les tags courants via les API du service concerné.
- Les noms Lambda peuvent être résolus en ARN avant lecture ou modification des tags.
- Les noms Scheduler respectent 64 caractères et utilisent un condensat lorsque
  nécessaire pour éviter les collisions dues au remplacement ou à la troncature.
- Le planning est créé avant l'enregistrement d'une alerte réussie. Une reprise
  après conflit réutilise son échéance d'origine (permission `GetSchedule` ajoutée).
- Une alerte déjà enregistrée ne redémarre pas le délai lors d'une répétition
  séquentielle. Le contrôle SLA relit le dernier feedback et respecte `handled`.
- La recherche de feedback parcourt les pages DynamoDB ; les lectures sont cohérentes.
- Les erreurs de lecture des tags et d'écriture en quarantaine ne deviennent plus
  des succès. Un échec de notification configurée interrompt le traitement.
- L'API rejette les corps JSON qui ne sont pas des objets et indique `dry_run`.
- Les tests bloquent les connexions réseau et utilisent des identifiants fictifs.
  Les nouveaux tests utilisent les vraies fonctions partagées avec moto.
- La CI mesure les lignes et branches couvertes. Les tests et leur configuration
  sont exclus du calcul pour mesurer le code applicatif.

## Vérifier dans VS Code

1. Ouvrir le panneau **Contrôle de code source** : les fichiers modifiés s'y affichent.
2. Cliquer sur un fichier pour comparer la version initiale et la version proposée.
3. Dans le terminal, depuis la racine du projet :

```powershell
python -B -m pytest lambdas -q -p no:cacheprovider --cov=lambdas --cov-branch --cov-report=term-missing
python -B -m flake8 lambdas scripts
terraform fmt -recursive -check infra/
terraform -chdir=infra validate
```

La dernière commande nécessite les fournisseurs Terraform initialisés. Leur
téléchargement local ne déploie rien. Ne pas lancer `terraform apply` à ce stade.

## Limites conservées : prototype, pas encore production

- Les doublons simultanés ne sont pas verrouillés par une transaction atomique.
  Un crash entre notification et enregistrement peut provoquer une notification
  répétée. Le comportement n'est pas « exactement une fois ».
- L'état courant reste déduit d'un historique ; il manque une machine à états
  durable, un identifiant d'incident et une clé intégrant compte/région/type.
  Une exemption est liée au dernier feedback et expire avec l'historique (TTL).
- Une exemption est respectée à l'échéance ; le planning n'est pas annulé
  immédiatement par l'API de feedback.
- Le contrôle Config ne détectera pas nécessairement les valeurs fictives si
  sa règle vérifie seulement la présence des clés. Il faut une règle adaptée et
  une stratégie de réévaluation des ressources déjà existantes.
- CloudTrail, Config et les règles restent des prérequis externes au Terraform.
  Lambda nécessite une règle compatible, pas uniquement `required-tags`.
- `Environment` reste issu de l'environnement de déploiement par défaut.
  Les valeurs acceptées ne sont pas validées dans un référentiel métier.
- L'API utilise IAM mais ne vérifie pas le propriétaire métier. L'action `tag`
  reste déclarative. Les permissions de tagging doivent encore être réduites.
- Les notifications restent centrales ; la quarantaine est un marquage, sans
  isolation réseau, arrêt, suppression ni économie automatique.
- Pas encore de file d'échecs, d'alarmes opérationnelles ou de validation AWS
  de bout en bout. Secrets Slack et état Terraform restent à renforcer.
- Le dashboard n'est pas alimenté en dry-run. Le script de démonstration écrit
  volontairement dans CloudWatch : ce n'est pas un benchmark hors ligne.
- La configuration Docker/Grafana et les règles d'agrégation des panneaux n'ont
  pas été corrigées dans ce lot.

## Benchmark à préparer séparément, sans compte AWS

Préparer 200 puis 1 000 événements reproductibles pour les quatre services, avec
créations, conformité, valeurs fictives, doublons, exemptions et erreurs injectées.
Utiliser le vrai code métier, moto/Stubber aux frontières AWS et un faux Slack.
Bloquer le réseau. Faire avancer l'horloge pour simuler les SLA.

Vérifier les effets, pas seulement les codes de retour. Mesurer avec une horloge
monotone les erreurs, le taux de réussite, la moyenne, P50, P95 et le débit global.
Séparer préparation, échauffement et traitement chronométré. Répéter chaque lot.
Mesurer la couverture dans une exécution distincte, car elle ralentit le programme.

Aucun résultat de performance AWS ne peut être déduit de ce benchmark simulé.
Les tests unitaires exécutés pendant ce lot ne constituent pas ce benchmark.
