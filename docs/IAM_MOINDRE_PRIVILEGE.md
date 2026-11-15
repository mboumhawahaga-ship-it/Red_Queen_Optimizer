# Permissions IAM : moindre privilège

Les fichiers locaux ont été renforcés. Aucun changement n'a été appliqué sur AWS.
Le moindre privilège consiste à accorder à chaque fonction les seules opérations
nécessaires, sur un périmètre choisi explicitement.

## Changement important avant un futur déploiement

`governance_scope` est vide par défaut : aucune ressource métier n'est autorisée.
L'auto-tagger et l'évaluateur ignorent les ressources hors liste avant d'appeler AWS.
Les logs, la table de gouvernance et les services internes gardent leurs permissions
propres. Un dry-run ne contourne pas IAM : les lectures nécessitent aussi une liste.

Dans `infra/terraform.tfvars`, il faudra renseigner les ARN approuvés :

```hcl
governance_scope = {
  ec2_instance_arns     = ["arn:aws:ec2:eu-west-1:123456789012:instance/i-0123456789abcdef0"]
  s3_bucket_arns        = ["arn:aws:s3:::my-approved-bucket"]
  rds_db_arns           = ["arn:aws:rds:eu-west-1:123456789012:db:my-approved-db"]
  lambda_function_arns = ["arn:aws:lambda:eu-west-1:123456789012:function:my-approved-function"]
}
```

Ces identifiants sont fictifs. Utiliser le compte et la région du déploiement.
Terraform refuse les ARN EC2/RDS/Lambda d'un autre compte ou d'une autre région,
les volumes EC2 et les ARN S3 d'objets. Les jokers de compte/région et le joker S3
global sont refusés. Les suffixes avec `*` ou `?` restent possibles dans les noms
de ressources pour accueillir de futures créations ; ils élargissent explicitement
le périmètre. Préférer des ARN exacts pour le premier pilote.

Le filtre Python aide à éviter les appels inutiles. IAM reste le contrôle de
sécurité : modifier le code Python ne donne pas de droits supplémentaires.
Pour compatibilité avec les anciens appels locaux, l'absence totale de variable
`GOVERNANCE_SCOPE` conserve l'ancien comportement Python. Terraform la fournit
toujours aux deux fonctions, même lorsque la liste est vide.

## Droits par rôle

| Rôle | Droits conservés | Restrictions ajoutées |
|---|---|---|
| Auto-tagger | Lire les métadonnées nécessaires, ajouter les tags d'attribution, écrire l'historique | ARN autorisés ; quatre clés de tags ; DynamoDB `PutItem` uniquement |
| Évaluateur | Lire les tags, ajouter le marquage de quarantaine, notifier et gérer les délais | ARN autorisés ; trois clés de quarantaine ; topic SNS et plannings propres au projet |
| Feedback | Lire et écrire l'historique | Une table, `Query` et `PutItem` ; aucun accès aux ressources métier |
| Scheduler | Invoquer l'évaluateur | Une fonction ; confiance limitée au compte et au groupe Scheduler dédié |

Les trois Lambdas écrivent uniquement dans leurs groupes de logs respectifs.
`dynamodb:GetItem` a été retiré car le code utilise `Query`.
Les droits EC2 sur les volumes et les actions de lecture non utilisées ont été retirés.
`lambda:GetFunction` a été remplacé dans IAM et Python par
`lambda:GetFunctionConfiguration`, qui suffit à résoudre un nom en ARN sans
accorder le téléchargement du code de la fonction.

Sur EC2, RDS et Lambda, `aws:TagKeys` et une condition de présence restreignent :

- auto-tagger : `Owner`, `Squad`, `CostCenter`, `Environment` ;
- évaluateur : `Status`, `QuarantinedAt`, `QuarantineReason`.

## Exceptions explicites

**EC2 Describe.** Ces API ne permettent pas de cibler les instances par ARN dans
IAM. Le joker `Resource="*"` subsiste uniquement pour `DescribeInstances` de
l'auto-tagger et `DescribeTags` de l'évaluateur, avec une condition de région.
Ces droits sont absents lorsque le périmètre EC2 est vide. Une fonction compromise
pourrait donc lire des métadonnées EC2 hors liste dans cette région, mais ne peut
pas modifier leurs tags avec les politiques fournies.

**CloudWatch PutMetricData.** Cette API nécessite aussi `Resource="*"`, restreint
au namespace `RedQueen/Governance` et à la région choisie.

**S3.** `PutBucketTagging` remplace tous les tags. Une liste ne contenant que les
quatre clés d'attribution ou les trois clés de quarantaine serait incompatible
avec la conservation des autres tags. Les accès sont donc limités aux buckets
explicitement autorisés et à `aws:ResourceAccount` du compte de déploiement,
sans prétendre limiter les clés remplacées. Une compromission permettrait de
modifier tous les tags des buckets autorisés, pas de lire leurs objets.
Les ARN S3 ne portent pas de région : choisir des buckets de la région du pilote.

## Chiffrement et appels entre services

- DynamoDB : `kms:Decrypt` sur la clé du projet, via DynamoDB uniquement, avec
  contexte limité au nom de table et à l'identifiant du compte. La création de
  clé, de grants et la génération de clé de table restent au provisionnement.
- Notifications : génération de clé de données et déchiffrement réservés au
  rôle évaluateur, via SNS et pour le seul topic du projet.
- Politique de clé KMS : CloudWatch Logs limité aux quatre groupes de logs du
  projet ; SNS limité au contexte du topic. La délégation administrative du
  compte (`kms:*` sur cette clé dans sa politique) est conservée pour ne pas
  verrouiller son administration ; aucun rôle Lambda ne reçoit `kms:*`.
- Scheduler : groupe `${project}-${environment}-sla`, plannings `rq-sla-*`, rôle
  transmissible uniquement à `scheduler.amazonaws.com`, invocation de la seule
  Lambda évaluatrice. Le groupe est fourni au code Python pour création, lecture
  et suppression. La confiance utilise l'ARN du **groupe**, conformément à AWS.
- L'ancienne permission Lambda au principal Scheduler sur tous les plannings
  par défaut est retirée : l'invocation dans le même compte utilise le rôle dédié.
- EventBridge : règles précises et compte source. API Gateway : uniquement
  GET/POST `/feedback`, stage `$default`, compte et API précis.

Si une ancienne version est déjà déployée, les plannings SLA du groupe `default`
doivent être inventoriés et migrés ou achevés avant de basculer la confiance sur
le nouveau groupe. Ce lot ne migre et ne supprime aucun planning AWS existant.

## Vérifications locales et limites

```powershell
terraform -chdir=infra fmt -recursive -check
terraform -chdir=infra validate
terraform -chdir=infra test -no-color
python -B -m pytest lambdas -q -p no:cacheprovider --cov=lambdas --cov-branch
```

Les tests Terraform nécessitent Terraform 1.7 ou ultérieur. Tous leurs fournisseurs
sont simulés (`mock_provider`). Le mot `apply` dans le fichier de tests ne crée
que des ressources fictives en mémoire ; ce n'est pas un déploiement AWS.
Ne pas utiliser `terraform apply` pour simplement lancer ces tests.

Les tests vérifient les politiques générées et leurs garde-fous : périmètre vide,
droits par rôle, compte/région, clés de tags, limites KMS et invocations. Les tests
Python vérifient le filtrage avant appel AWS et le nouveau groupe Scheduler.

Ces tests ne sont pas un simulateur complet du moteur IAM. Ils ne prouvent pas
les décisions réelles d'AWS, notamment KMS et les conditions de tags RDS/S3.
Avant un pilote, il faudra contrôler les politiques avec IAM Access Analyzer,
puis tester les opérations autorisées et refusées dans un compte de test, y compris
la publication SNS chiffrée et les reprises Scheduler.

L'autorisation métier de l'API feedback, les autres politiques qu'un administrateur
pourrait attacher, les permissions de l'identité qui exécute Terraform et les clés
AWS locales de Grafana ne sont pas gérées par ces rôles d'exécution.

## Références AWS utilisées

- [Actions EC2 et conditions de tags](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ec2.html)
- [Actions RDS](https://docs.aws.amazon.com/service-authorization/latest/reference/list_rds.html)
- [Actions Lambda](https://docs.aws.amazon.com/service-authorization/latest/reference/list_lambda.html)
- [Actions S3](https://docs.aws.amazon.com/service-authorization/latest/reference/list_s3.html)
- [Confiance Scheduler et ARN de groupe](https://docs.aws.amazon.com/scheduler/latest/UserGuide/cross-service-confused-deputy-prevention.html)
- [Chiffrement DynamoDB et contexte KMS](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/encryption.usagenotes.html)
- [Chiffrement des topics SNS](https://docs.aws.amazon.com/sns/latest/dg/sns-key-management.html)

## Resultats verifies localement

- Terraform: 7 scenarios passes, 0 echec, fournisseurs tous simules.
- Python: 147 tests passes, 0 echec, en 62,44 secondes avec couverture.
- Couverture totale lignes et branches: 84 %.
- Validation Terraform, terraform fmt, flake8 et git diff --check: reussis.
- 333 avertissements de depreciation subsistent dans les anciens tests et botocore.
- Aucun appel au compte AWS ni deploiement effectue. Commit local uniquement, sans push.
