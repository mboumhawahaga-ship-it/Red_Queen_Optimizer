# Estimation des coûts AWS de Red Queen Optimizer

**État :** modèle indicatif, reproductible et non contractuel. Aucun appel au
compte AWS, benchmark, déploiement, commit ou push n'a été effectué pour cette
analyse.

**Consultation des tarifs : 15 novembre 2026.** Les prix sont exprimés en USD,
hors taxes. Les tarifs régionaux ci-dessous ont été lus dans le catalogue
officiel AWS Price List pour `eu-west-3` (Europe, Paris). Les pages tarifaires
AWS font foi en cas d'écart avec le catalogue. Le catalogue est mis à jour :
recontrôler chaque prix le jour d'une présentation ou d'un devis.

## 1. Ce que ce document chiffre

Une facture AWS ressemble à un compteur d'eau : le coût dépend des volumes,
des options activées et des compteurs déjà utilisés dans le compte. Le nombre
de ressources, à lui seul, ne suffit pas à calculer la facture.

Les trois scénarios sont des **sous-totaux de services attribuables au modèle**,
pas des devis AWS. Ils comprennent les appels de contrôle AWS provoqués par
Red Queen quand un tarif public régional est disponible. Les quotas gratuits,
la région réellement déployée, les volumes observés, les coûts du compte déjà
consommés et certaines opérations auxiliaires peuvent changer le montant.
Les prix inconnus sont exclus explicitement, jamais remplacés par zéro.

L'analyse porte sur le code actuel, notamment [les variables Terraform](../infra/variables.tf),
[les règles EventBridge](../infra/eventbridge.tf), [les handlers Lambda](../infra/lambdas.tf),
[l'auto-tagger](../lambdas/auto-tagger/handler.py),
[l'évaluateur](../lambdas/compliance-evaluator/handler.py),
[l'API feedback](../lambdas/feedback-api/handler.py) et
[les utilitaires partagés](../lambdas/shared/utils.py).

### Écarts à régler avant toute comparaison avec une facture

- Le modèle tarifaire demandé est en **Paris (`eu-west-3`)**, mais
  `infra/variables.tf` fixe actuellement **`eu-west-1`** par défaut. Aucun
  `infra/terraform.tfvars` local n'a été trouvé lors de l'inspection. Un
  déploiement sans surcharge de région ne correspond donc pas aux prix de ce
  document.
- `governance_scope` est vide par défaut dans Terraform. Avec cette valeur,
  les fonctions ignorent les ressources métier. Les calculs supposent que les
  ressources indiquées sont explicitement autorisées.
- `dry_run` vaut `true` par défaut. Les scénarios chiffrés supposent
  `dry_run = false`, c'est-à-dire des écritures, notifications, métriques et
  échéances réellement activées. En mode `dry_run`, les lectures, invocations
  et journaux subsistent, mais la plupart de ces écritures et appels ne sont
  pas effectués.

## 2. Services réellement utilisés

| Service | Usage constaté dans le dépôt | Facturation à retenir |
|---|---|---|
| AWS Lambda | Trois fonctions : auto-tagger et compliance-evaluator à 256 Mo (concurrence réservée à 10 chacune); feedback API à 128 Mo (concurrence réservée à 5). Délais maximum configurés : 60, 120 et 30 secondes. | Requêtes et durée d'exécution. La concurrence réservée n'est pas modélisée comme une capacité toujours allumée. |
| Amazon DynamoDB | Une table `PAY_PER_REQUEST`, clés de partition et de tri, TTL de 90 jours, restauration à un instant donné (PITR), chiffrement KMS. | Unités de lecture/écriture consommées, stockage et stockage PITR. |
| AWS Config | Émet les changements `NON_COMPLIANT` qui déclenchent l'évaluateur. Terraform ne crée ni recorder ni règle Config. | Évaluations de règles et éléments de configuration; coût incrémental attribuable à confirmer selon les règles réellement activées. |
| Amazon EventBridge | Deux règles dirigent les événements CloudTrail de création et les événements Config vers des fonctions différentes. | Les événements de gestion AWS sur le bus par défaut sont indiqués comme gratuits. Les événements personnalisés ne font pas partie du scénario. |
| EventBridge Scheduler | Un planning à usage unique est créé pour chaque alerte et déclenche une vérification SLA. | Nombre de déclenchements planifiés. Les appels de gestion du planning ne sont pas chiffrés séparément dans ce modèle. |
| Amazon CloudWatch | Quatre groupes de journaux chiffrés, rétention configurée à 14 jours, métriques personnalisées. Un appel de métrique contient deux points de données. | Ingestion et stockage des journaux, séries de métriques uniques et appels API `PutMetricData`. |
| AWS Key Management Service (KMS) | Une clé gérée par le client chiffre DynamoDB, les journaux et le topic SNS. La rotation automatique est activée. | Clé par mois et appels KMS; les appels dépendant des intégrations AWS ne sont pas déductibles un pour un du code applicatif. |
| Amazon SNS | Un topic Standard chiffré publie les alertes sur une souscription email. | Requêtes de publication et livraisons email. Une publication Slack éventuelle utilise une destination externe. |
| Amazon API Gateway | Une API HTTP v2 avec `GET /feedback` et `POST /feedback`, qui invoque la troisième Lambda. | Requêtes et transfert de données de réponse. |
| Amazon S3 | Le code appelle `GetBucketTagging` et `PutBucketTagging` sur des buckets clients autorisés. Red Queen ne crée pas ces buckets. | Requêtes S3 de lecture et d'écriture; le stockage des objets du client est exclu. |
| Amazon EC2, Amazon RDS et AWS Lambda du client | Appels de lecture de tags/configuration et d'écriture de tags sur les ressources surveillées. | Les ressources elles-mêmes (instances, bases, fonctions client) sont préexistantes et exclues. **Tarif unitaire non vérifié** pour les appels de contrôle non-S3 dans ce modèle. |
| AWS CloudTrail | Source des événements de création CloudTrail consommés par EventBridge. Le dépôt ne crée pas de trail. | La première copie des événements de gestion est tarifée à 0 USD par AWS; copies supplémentaires et stockage des journaux ne sont pas inclus. |
| IAM | Rôles et politiques pour les Lambdas et Scheduler. | Aucun poste IAM séparé n'est ajouté au calcul; les pages tarifaires consultées n'ont pas fourni de mesure unitaire à appliquer ici. |

Grafana/Docker est fourni pour un lancement local. Héberger Grafana sur des
ressources AWS ajouterait des coûts qui ne sont ni définis ni mesurés ici.
Les appels du script de démonstration de métriques ne font pas partie des
volumes des scénarios.

## 3. Tarifs officiels consultés

Tous les tarifs régionaux de cette section concernent **`eu-west-3`**, sauf
les quotas indiqués comme mondiaux ou applicables au compte entier. Date de
consultation de chaque ligne : **15 novembre 2026**. Les liens vers
`pricing.us-east-1.amazonaws.com` sont les fichiers régionaux officiels du
catalogue AWS; `us-east-1` est le point de téléchargement du catalogue, pas la
région tarifée.

| Service et poste | Tarif unitaire vérifié | Unité | Quotas gratuits/conditions pertinentes | Source officielle |
|---|---:|---|---|---|
| Lambda, requêtes x86 | 0,20 USD | par million de requêtes | 1 million de requêtes gratuites par mois, quota global mensuel. | [Tarifs Lambda](https://aws.amazon.com/lambda/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/current/eu-west-3/index.json) |
| Lambda, calcul x86, premier palier | 0,0000166667 USD | par Go-seconde | 400 000 Go-secondes gratuites par mois, quota global mensuel. Après le premier palier régional : 0,000015 USD/Go-s, puis 0,0000133334 USD/Go-s selon les volumes du catalogue. | [Tarifs Lambda](https://aws.amazon.com/lambda/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/current/eu-west-3/index.json) |
| DynamoDB, lectures à la demande | 0,1487 USD | par million de Read Request Units (RRU) | Le prix dépend de la taille lue et de la cohérence; une requête `Query` n'est pas nécessairement une seule RRU. | [Tarifs DynamoDB](https://aws.amazon.com/dynamodb/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonDynamoDB/current/eu-west-3/index.json) |
| DynamoDB, écritures à la demande | 0,7423 USD | par million de Write Request Units (WRU) | Une écriture d'un élément de 1 Ko consomme une WRU; les éléments plus grands peuvent en consommer plusieurs. | [Tarifs DynamoDB](https://aws.amazon.com/dynamodb/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonDynamoDB/current/eu-west-3/index.json) |
| DynamoDB, stockage Standard | 0,29715 USD | par Go-mois au-delà des premiers 25 Go-mois affichés comme gratuits | Le quota dépend de l'offre et de l'utilisation du compte; les totaux « conditionnels » supposent que ce quota reste disponible. | [Tarifs DynamoDB](https://aws.amazon.com/dynamodb/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonDynamoDB/current/eu-west-3/index.json) |
| DynamoDB, stockage PITR | 0,23772 USD | par Go-mois de sauvegarde PITR | Facturé selon le volume de sauvegarde réel; le calcul suppose, à titre de proxy, une sauvegarde égale au volume de la table. | [Tarifs DynamoDB](https://aws.amazon.com/dynamodb/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonDynamoDB/current/eu-west-3/index.json) |
| AWS Config, élément enregistré en continu | 0,003 USD | par élément de configuration enregistré | L'enregistrement quotidien périodique est tarifé séparément à 0,012 USD par élément quotidien; le modèle choisit le tarif continu. | [Tarifs AWS Config](https://aws.amazon.com/config/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSConfig/current/eu-west-3/index.json) |
| AWS Config, évaluations de règles | 0,001 USD les 100 000 premières; 0,0008 USD les 400 000 suivantes; 0,0005 USD au-delà | par évaluation | Paliers mensuels par compte/région selon l'offre tarifaire; pas de remise supposée autre que ces paliers. | [Tarifs AWS Config](https://aws.amazon.com/config/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSConfig/current/eu-west-3/index.json) |
| CloudWatch Logs, ingestion Standard | 0,5985 USD | par Go ingéré | Le catalogue affiche 5 Go mensuels de données ingérées gratuits, quota à vérifier selon l'utilisation du compte. | [Tarifs CloudWatch](https://aws.amazon.com/cloudwatch/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/eu-west-3/index.json) |
| CloudWatch Logs, stockage Standard | 0,0315 USD | par Go-mois conservé | Dépend du volume réellement conservé et de la rétention; ce modèle applique 14 jours. | [Tarifs CloudWatch](https://aws.amazon.com/cloudwatch/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/eu-west-3/index.json) |
| CloudWatch, métriques personnalisées | 0,30 USD pour les 10 000 premières; 0,10 USD pour les 240 000 suivantes; 0,05 USD pour les 750 000 suivantes; 0,02 USD au-delà | par métrique-mois | Le catalogue affiche 10 métriques gratuites par mois; les métriques sont des séries distinctes par nom et ensemble exact de dimensions, pas des points de données. | [Tarifs CloudWatch](https://aws.amazon.com/cloudwatch/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/eu-west-3/index.json) |
| CloudWatch, appels API | 0,01 USD | par 1 000 requêtes | Le catalogue affiche le premier million d'appels mensuels gratuits; quota partagé à confirmer. | [Tarifs CloudWatch](https://aws.amazon.com/cloudwatch/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/eu-west-3/index.json) |
| EventBridge Scheduler | 1,20 USD | par million de déclenchements planifiés au-delà de 14 millions gratuits par mois | Le quota gratuit s'applique par mois et est partagé entre régions, hors GovCloud, selon l'offre AWS. | [Tarifs EventBridge](https://aws.amazon.com/eventbridge/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSEvents/current/eu-west-3/index.json) |
| API Gateway HTTP API | 1,17 USD par million de requêtes pour les 300 premiers millions; 1,05 USD par million au palier suivant | par requête | L'offre gratuite indique 1 million d'appels HTTP par mois pendant 12 mois pour les nouveaux clients AWS éligibles. | [Tarifs API Gateway](https://aws.amazon.com/api-gateway/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonApiGateway/current/eu-west-3/index.json) |
| SNS Standard, requêtes API | 0,50 USD | par million de requêtes après le premier million gratuit par mois | Pour les petits messages modélisés, une publication = une requête. Chaque bloc de 64 Ko compte comme une requête. | [Tarifs SNS](https://aws.amazon.com/sns/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonSNS/current/eu-west-3/index.json) |
| SNS, livraison email | 2,00 USD | par 100 000 notifications après les 1 000 premières gratuites par mois | Une souscription email; chaque bloc de 64 Ko compte comme une livraison. | [Tarifs SNS](https://aws.amazon.com/sns/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonSNS/current/eu-west-3/index.json) |
| KMS, clé gérée par le client | 1,00 USD | par clé-mois, proratisée selon AWS | La rotation automatique de la clé ajoute 1 USD/mois pour chacune des deux premières rotations, selon la page tarifaire; le mois de rotation n'est pas connu ici. | [Tarifs KMS](https://aws.amazon.com/kms/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/awskms/current/eu-west-3/index.json) |
| KMS, appels symétriques standards | 0,03 USD | par 10 000 requêtes | 20 000 requêtes mensuelles gratuites, quota global; le nombre réel d'appels de service intégrés n'est pas déductible un pour un du code. | [Tarifs KMS](https://aws.amazon.com/kms/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/awskms/current/eu-west-3/index.json) |
| S3, `GetBucketTagging` | 0,0042 USD | par 10 000 requêtes GET/autres | Catégorie de requête estimée d'après l'opération; valider la classification dans Cost and Usage Report (CUR). | [Tarifs S3](https://aws.amazon.com/s3/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/eu-west-3/index.json) |
| S3, `PutBucketTagging` | 0,0053 USD | par 1 000 requêtes PUT | Catégorie de requête estimée d'après l'opération; valider la classification dans CUR. | [Tarifs S3](https://aws.amazon.com/s3/pricing/) · [catalogue Paris](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/eu-west-3/index.json) |
| CloudTrail, première copie d'événements de gestion | 0 USD | par événement de gestion, pour la première copie livrée au trail | Toute copie supplémentaire, événement de données, CloudTrail Lake ou stockage S3 est hors modèle. | [Tarifs CloudTrail](https://aws.amazon.com/cloudtrail/pricing/) |

Les frais de transfert de données vers la réponse API ou le webhook Slack,
l'abonnement éventuel à AWS Support, la TVA, les opérations de contrôle EC2/RDS/
Lambda et les données de livraison AWS Config dans S3/SNS ne sont pas chiffrés
ci-dessous : **tarif non vérifié dans ce modèle** ou volume absent. Ils ne sont
pas assimilés à 0 USD.

AWS décrit le catalogue Price List comme informatif et précise que la page
tarifaire du service prévaut en cas d'écart : [documentation AWS Price List](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/price-changes.html).

## 4. Hypothèses mensuelles des scénarios

Chaque scénario suppose un mois de 30 jours et **toutes les ressources dans le
périmètre IAM autorisé**. Les ressources sont réparties à parts égales entre
EC2, S3, RDS et Lambda. L'équilibre des types sert à compter les séries de
métriques et les appels S3; un parc réel peut être très différent.

| Paramètre de modèle | Hypothèse explicite | Pourquoi ce n'est pas un fait mesuré |
|---|---|---|
| Ressources surveillées | 1 000, 10 000 ou 100 000, actives tout le mois. | Taille demandée; aucun inventaire client n'a été fourni. |
| Créations CloudTrail (`C`) | `0,10 × N` par mois, soit 10 % de renouvellement; un événement EC2 crée un seul serveur. | Une création `RunInstances` peut contenir plusieurs instances; les regroupements et retries augmentent les volumes. |
| Évaluations AWS Config (`E`) | `4 × N + C`: une règle périodique compatible, évaluée chaque semaine sur chaque ressource, plus une évaluation par création. | Terraform ne définit ni recorder ni règle ni fréquence Config; vérifier que la règle retenue permet ce calendrier. |
| Éléments de configuration enregistrés (`I`) | `1 × N` par mois en enregistrement continu. | Modélise une observation/changement par ressource; les changements réels et l'enregistrement quotidien peuvent augmenter ce poste. |
| Alertes distinctes (`Q`) | `0,10 × N` transitions `NON_COMPLIANT` par mois, une seule notification/alerte par ressource. | Les répétitions d'évaluation ne sont pas comptées comme nouvelles alertes; les redéliveries simultanées sont exclues. |
| SLA et quarantaine | Une échéance Scheduler par alerte; chaque ressource est encore non conforme à l'échéance, donc une vérification et un marquage de quarantaine. | Une correction réelle avant échéance diminuerait les écritures de tags, mais la fonction effectue toujours la vérification SLA. |
| Feedback API | Pour `25 % × Q`, un POST et un GET; chaque POST ajoute un enregistrement. Les actions ne sont pas `approve` et la ressource reste non conforme jusqu'à l'échéance SLA. | Le taux de clics/feedback n'existe pas dans le dépôt. Les commentaires sont supposés courts, sans dépassement de 1 Ko par enregistrement. |
| Auto-tagging | Une invocation par création; `80 %` des créations nécessitent une écriture de tags groupée. | La part de ressources sans tags ou valeurs fictives n'a pas été mesurée. |
| Lectures de tags des ressources | À la création : EC2 1, S3 1, RDS 2, Lambda 1 appel en moyenne. Par alerte et vérification SLA : EC2 1, S3 1, RDS 2, Lambda 2. | Déduit des chemins de code. Le fallback `GetFunctionConfiguration` sur événement incomplet, pages, retries et erreurs sont exclus. |
| Écriture à la création | Un `PutItem` DynamoDB par création dans le périmètre, même si les tags sont déjà conformes. | Le code enregistre l'événement après le traitement; `dry_run` doit être désactivé pour persister. |
| Écriture d'alerte / SLA | Un `PutItem` par alerte et un par résultat SLA. | On suppose que chaque alerte atteint l'échéance et que le traitement est achevé. |
| DynamoDB | Enregistrement moyen de 1 Ko, TTL de 90 jours, volume stable sur trois mois; PITR estimé à 1 fois la taille de table. | Taille de données, surcoût des attributs, commentaire, croissance, changements PITR et délai d'expiration TTL non mesurés. |
| Journaux | 2 Kio par invocation Lambda et 1 Kio par requête API, 14 jours de rétention, ingestion uniforme. | Le niveau de journalisation, événements sérialisés et tailles réelles sont inconnus. |
| SNS et Slack | Une publication SNS vers une souscription email par alerte; un envoi Slack optionnel par alerte. | Fan-out réel, configuration du webhook et tailles de messages inconnus. Le coût Slack n'est pas AWS. |
| Métriques personnalisées | Au plus 16 séries : 2 résultats (`alerted`, `quarantined`), 7 combinaisons type/criticité (RDS toujours critique; les trois autres types ont deux niveaux), plus les 2 séries globales. | Nombre effectif de combinaisons de dimensions dépendant des résultats, du compte et du mois. |
| Redéliveries et échecs | Zéro doublon, retry, échec AWS ou reprise dans le scénario de base. | Ce n'est pas garanti en exploitation; voir les risques en section 8. |

### Volumes dérivés du code

`Q = 0,10N`, `C = 0,10N`, `P = G = 0,25Q` (POST et GET).

- Invocations Lambda = `C + Q (évaluation) + Q (SLA) + P + G = 0,35N`.
- Écritures DynamoDB = `C + Q + Q + P = 0,325N` éléments/mois.
- Lectures DynamoDB : `1,25N` RRU modélisées. Par alerte, le code lance deux
  recherches initiales, une recherche SLA et, pour le feedback, une recherche
  POST et une GET. Les requêtes avec filtre sont budgétées jusqu'à 5 RRU
  (20 éléments de 1 Ko lus, lecture cohérente); les lectures simples valent
  1 RRU. Les octets réellement lus font foi.
- Stockage DynamoDB stable à 90 jours = `3 × écritures mensuelles × 1 Ko`.
- Journaux estimés = `0,75N Kio`, soit `0,75 × N / 1 048 576 Go` (base 1 024).
  Stockage moyen à 14 jours = `volume ingéré × 14/30`.
- Requêtes S3 : GET = `0,075N`; PUT = `0,045N`. Les premières incluent les
  lectures à la création, à l'alerte et à l'échéance; les dernières incluent
  80 % des créations supposées à corriger et la quarantaine des alertes.
- `PutMetricData` = `2Q` requêtes, chacune contenant 2 points de données, soit
  `4Q` points. La facturation de métriques dépend des séries distinctes, pas
  du nombre de points.
- Evaluations Config = `4,1N`; éléments enregistrés Config = `N`.

| Volumes estimés/mois | Ressources `N` | Créations `C` | Évaluations Config `E` | Éléments Config `I` | Alertes/SLA `Q` | Invocations Lambda | Écritures DynamoDB | RRU DynamoDB | Feedback POST / GET | Appels S3 GET / PUT | Appels métriques / points | Log ingéré estimé |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Petit | 1 000 | 100 | 4 100 | 1 000 | 100 | 350 | 325 | 1 250 | 25 / 25 | 75 / 45 | 200 / 400 | 0,00072 Go |
| Moyen | 10 000 | 1 000 | 41 000 | 10 000 | 1 000 | 3 500 | 3 250 | 12 500 | 250 / 250 | 750 / 450 | 2 000 / 4 000 | 0,00715 Go |
| Grand | 100 000 | 10 000 | 410 000 | 100 000 | 10 000 | 35 000 | 32 500 | 125 000 | 2 500 / 2 500 | 7 500 / 4 500 | 20 000 / 40 000 | 0,07153 Go |

**Autres volumes du scénario :** publications SNS et livraisons email = `Q`
(100, 1 000, 10 000); plannings Scheduler = `Q`; stockage DynamoDB sur 90 jours
= environ 0,00093 / 0,00930 / 0,09298 Go; appels CloudWatch API de métriques =
`2Q`. Les appels de lecture aux ressources surveillées sont évalués à environ
`0,425N` au total, dont les lectures S3 listées ci-dessus; les API EC2, RDS et
Lambda du client ne sont pas tarifées ici.

## 5. Calcul reproductible

Le calcul principal suppose que les seuils gratuits pertinents du compte sont
déjà consommés ou ne sont pas disponibles. Il applique les paliers de prix
AWS, mais facture les unités du scénario au tarif marginal indiqué. Les
totaux sont des **sous-totaux modélisés** : les tarifs non vérifiés de la
section 3, les appels KMS intégrés et les transferts non quantifiés restent
en sus, s'ils sont facturés.

Formules mensuelles (USD) :

```text
Coût Config = I × 0,003
            + prix_progressif(E; 100 000 × 0,001; 400 000 × 0,0008; reste × 0,0005)

Lambda = invocations × 0,20 / 1 000 000
       + Go-secondes × 0,0000166667
Go-secondes = 0,25 × (0,25 × C + 0,50 × Q + 0,40 × Q)
              + 0,125 × 0,10 × (P + G)

DynamoDB = RRU × 0,1487 / 1 000 000
         + WRU × 0,7423 / 1 000 000
         + Go-mois-table × 0,29715
         + Go-mois-PITR × 0,23772

S3 = GET / 10 000 × 0,0042 + PUT / 1 000 × 0,0053
API Gateway = (P + G) × 1,17 / 1 000 000
SNS = Q × (0,50 / 1 000 000 + 2,00 / 100 000)
Scheduler = Q × 1,20 / 1 000 000
CloudWatch métriques = 16 × 0,30
CloudWatch API = (2 × Q) / 1 000 × 0,01
CloudWatch Logs = Go ingérés × 0,5985
                  + Go stockés moyens × 0,0315
KMS fixe = 1 clé × 1,00 / mois
KMS API = appels_symétriques × 0,03 / 10 000 (quota gratuit épuisé)
          ou max(0, appels_symétriques - 20 000) × 0,03 / 10 000
          si le quota global de 20 000 appels est encore disponible
```

Les appels KMS API sont tarifés mais exclus des sous-totaux : le nombre exact
de requêtes faites par DynamoDB, SNS et CloudWatch dépend de leurs intégrations
et de la réutilisation des clés de données côté service. Ne pas supposer une
requête KMS par opération applicative.

Le scénario « sans quotas gratuits disponibles » suppose que le quota de
stockage DynamoDB de 25 Go, les métriques CloudWatch gratuites, les quotas
Lambda/CloudWatch, le quota KMS d'appels, le million de publications SNS et
les 14 millions d'invocations Scheduler sont déjà consommés dans le compte.
Le seuil SNS de 1 000 emails est traité au-delà comme un seuil mensuel de
livraison. Cette convention est volontairement distincte du scénario
conditionnel ci-dessous.

| Poste modélisé (USD/mois) | 1 000 ressources | 10 000 ressources | 100 000 ressources |
|---|---:|---:|---:|
| KMS : une clé, hors appels API | 1,00 | 1,00 | 1,00 |
| AWS Config : éléments enregistrés + évaluations | 7,10 | 71,00 | 648,00 |
| Lambda : requêtes + durée | 0,00056 | 0,00560 | 0,05596 |
| DynamoDB : requêtes, table et PITR | 0,00092 | 0,00925 | 0,09245 |
| S3 : opérations de tags | 0,00027 | 0,00270 | 0,02700 |
| API Gateway HTTP | 0,00006 | 0,00059 | 0,00585 |
| SNS : publication et email | 0,00205 | 0,02050 | 0,20500 |
| EventBridge Scheduler | 0,00012 | 0,00120 | 0,01200 |
| CloudWatch : 16 séries de métriques | 4,80 | 4,80 | 4,80 |
| CloudWatch : appels `PutMetricData` | 0,00200 | 0,02000 | 0,20000 |
| CloudWatch : journaux ingérés et stockés | 0,00044 | 0,00439 | 0,04386 |
| **Sous-total modélisé** | **12,91** | **76,86** | **654,44** |

### Variante conditionnelle avec quotas gratuits disponibles

À titre de comparaison seulement, si les quotas correspondants sont encore
disponibles, que les conditions d'éligibilité sont satisfaites et que les
consommations d'autres applications ne les ont pas utilisés, les frais
modélisés pourraient être réduits comme suit :

| Ressources | Sous-total avec les quotas gratuits supposés disponibles |
|---:|---:|
| 1 000 | environ 9,90 USD/mois |
| 10 000 | environ 73,81 USD/mois |
| 100 000 | environ 651,07 USD/mois |

Cette variante suppose notamment : Lambda dans ses quotas gratuits mensuels;
ingestion CloudWatch Logs sous 5 Go; appels CloudWatch sous 1 million; 10 séries
CloudWatch gratuites; stockage DynamoDB sous le quota de 25 Go; SNS sous le
million de requêtes gratuites et les emails au-delà de 1 000 seulement facturés;
Scheduler sous 14 millions; API Gateway HTTP dans son offre gratuite de 12 mois
pour un nouveau compte éligible. Elle garde le coût de la clé KMS, le PITR,
les requêtes DynamoDB, AWS Config, les opérations S3 et les métriques au-delà
des 10 séries gratuites. Les quotas sont partagés selon les règles AWS et
peuvent déjà être consommés.

Le coût AWS Config domine ces exemples parce que l'hypothèse ajoute un
enregistrement continu par ressource et quatre évaluations périodiques par
mois. Ce n'est pas un coût observé dans le dépôt : le recorder et les règles
ne sont pas provisionnés ici. Pour un chiffrage client, remplacer `I` et `E`
par la fréquence et les volumes réellement retenus dans AWS Config.

### Exemple manuel : 100 000 ressources

1. `C = 0,10 × 100 000 = 10 000`; `Q = 10 000`;
   `E = 4 × 100 000 + 10 000 = 410 000`.
2. Config : `100 000 × 0,003 = 300 USD` pour les éléments et
   `100 000 × 0,001 + 310 000 × 0,0008 = 348 USD` pour les évaluations.
3. Lambda : 35 000 invocations; avec les durées et mémoires hypothétiques,
   2 937,5 Go-s. Au tarif catalogue, cela donne environ 0,05596 USD avant les
   quotas Lambda.
4. DynamoDB : 125 000 RRU et 32 500 WRU, environ 0,0427 USD; stockage
   90 jours estimé à 0,09298 Go, environ 0,0497 USD combinés pour table et
   PITR si le stockage table est facturé.
5. Ajouter KMS, métriques, journaux, email, Scheduler, API et appels S3 selon
   le tableau. La somme donne le sous-total modélisé de 654,44 USD/mois,
   **avant les postes non vérifiés**.

Les arrondis d'affichage sont au centime; refaire les additions avec les
formules et les nombres non arrondis pour comparer de petits postes.

## 6. Coûts fixes, variables et préexistants

| Catégorie | Éléments | Traitement |
|---|---|---|
| Fixe identifié | Une clé KMS client : 1 USD/mois. La rotation peut ajouter jusqu'à deux versions facturées au fil du temps selon la page KMS. | Inclus : 1 USD/mois initial; coût futur de rotation exclu faute de calendrier de déploiement. |
| Faible coût récurrent piloté par configuration | Les séries CloudWatch créées par les combinaisons de dimensions actives. | Inclus : jusqu'à 16 séries, indépendamment du nombre de points envoyés; le compte peut disposer de 10 séries gratuites. |
| Variable avec le volume | Config, Lambda, DynamoDB, journaux, appels métriques, Scheduler, SNS, API Gateway et requêtes S3. | Inclus selon les formules et hypothèses mensuelles. |
| Service client préexistant | Ressources EC2, buckets S3, bases RDS, fonctions Lambda client, trail CloudTrail et système AWS Config. | Le coût de possession et d'exploitation de ces ressources est exclu; seules les opérations Red Queen tarifables et Config supposées incrémentales sont comptées. |
| Dépendances non provisionnées par Terraform | Recorder/règles Config, bucket de livraison Config/CloudTrail, éventuels canaux et abonnements du client. | Volume et coût additionnel non vérifiés; à ajouter à partir de la configuration réelle et du CUR. |
| Hors AWS / transfert | Webhook Slack, réseau sortant, Grafana si hébergé, taxes et support. | Non chiffré; tarif ou volume non vérifié. |

Ne pas comparer directement la ligne AWS Config à une facture existante sans
déterminer si la règle aurait déjà été exécutée pour une autre politique du
client. Seule la différence réellement causée par l'ajout de Red Queen est
incrémentale.

## 7. Ce qui est mesuré, estimé ou non démontré

| Indicateur | État |
|---|---|
| Coûts AWS | **Estimations** selon le catalogue AWS consulté et les hypothèses déclarées. Ce ne sont ni un devis ni une facture vérifiée. |
| Benchmarks locaux | Aucun benchmark de 200 ou 1 000 événements n'a été lancé. Les temps de tests unitaires ne sont pas des mesures de performance. |
| Latences AWS | Aucune mesure. Moto, mocks ou Stubber mesureront principalement le code et les simulateurs locaux. |
| Économies humaines potentielles | Non calculées faute de temps de revue manuel, coût horaire, taux de correction et nombre de tickets mesurés. |
| Économies AWS démontrées | **Aucune à ce jour** : le code marque les ressources pour revue et ne les arrête ni ne les supprime; aucune baisse de facture n'a été mesurée. |

Pour estimer les économies humaines séparément, mesurer :
`alertes traitées × minutes manuelles économisées / 60 × coût horaire chargé`.
Ne pas mélanger cette valeur à une baisse de dépenses AWS. Pour démontrer une
économie AWS, comparer les factures avant/après à périmètre et période
équivalents, avec données réelles Cost Explorer/CUR et une méthode d'attribution.

## 8. Limites et risques pouvant fausser les coûts

- **Concurrence et doublons :** EventBridge peut redélivrer un événement et le
  flux n'est pas transactionnel. Deux traitements simultanés peuvent chacun
  créer un planning, publier une alerte ou écrire une ligne avant que l'autre
  ne voie l'état. Les volumes `Q` ignorent ces doublons et peuvent donc
  sous-estimer Lambda, DynamoDB, SNS, métriques et appels Scheduler.
- **Perte de tags S3 :** `PutBucketTagging` remplace toute la liste. Le code
  relit les tags et les fusionne, mais une mise à jour concurrente entre lecture
  et écriture peut effacer les tags ajoutés par l'autre traitement. Le risque
  concerne également la quarantaine. La charge de base suppose aucune collision.
- **Lectures DynamoDB :** la recherche feedback filtre des éléments et pagine.
  `Limit=20` peut lire davantage de données que la réponse retournée. Les 5 RRU
  estimées pour une requête filtrée sont une hypothèse bornée à 20 éléments de
  1 Ko, pas une mesure garantie; une taille ou un historique plus grands
  augmentent le coût.
- **Tailles et durées inconnues :** la taille des éléments, logs, emails,
  commentaires et temps d'exécution réels changent stockage, requêtes et
  calcul Lambda. Les secondes Lambda hypothétiques servent au calcul seulement;
  elles ne viennent pas d'un benchmark.
- **AWS Config variable externe :** le code ne crée pas les évaluations. La
  règle, ses déclencheurs, son calendrier, les ressources effectivement
  enregistrées, les notifications et le bucket de livraison doivent être
  inspectés dans le compte cible.
- **Gratuit ne signifie pas universel :** seuils gratuits, crédits, ancienneté
  du compte, utilisation partagée et région peuvent modifier la facture. La
  colonne conditionnelle n'est pas un engagement d'AWS.
- **Frais exclus :** requêtes KMS des intégrations, transfert vers Slack/API,
  stockage/livraison AWS Config et CloudTrail, ressources de l'application
  cliente, opérations de contrôle sans tarif confirmé, support, TVA et taxes.
- **Pas de promesse de gain :** la quarantaine ajoute des tags de revue; elle
  ne réalise pas d'optimisation ou d'arrêt automatique des ressources.

## 9. Procédure de vérification avant publication ou devis

1. Fixer la région réellement configurée (`eu-west-1` par défaut dans le dépôt,
   `eu-west-3` pour ce modèle) et la portée exacte des ARN.
2. Relever dans AWS Config le nombre réel d'éléments enregistrés, les règles,
   les fréquences et évaluations mensuelles; séparer le coût déjà présent de
   l'incrément Red Queen.
3. Mesurer par type de ressource le nombre de créations, transitions
   `NON_COMPLIANT`, retours API, reprises, erreurs, tailles DynamoDB et durées
   Lambda. Faire un pilote dans un compte autorisé, sans extrapoler des latences
   simulées aux API réelles.
4. Obtenir l'usage de l'ensemble du compte et des quotas gratuits/credits, pas
   seulement celui de Red Queen.
5. Vérifier les lignes de facture et les classifications S3 avec Cost Explorer
   et le Cost and Usage Report. Comparer les coûts observés au modèle et
   consigner toute différence.
6. Recharger les pages officielles et le catalogue régional le jour de
   l'estimation; noter la date, les paramètres, les tarifs et les volumes
   utilisés.
7. Ne présenter que des coûts estimés avec hypothèses, et étiqueter
   « Tarif non vérifié » tout poste dont le prix ou le volume reste inconnu.

### Références officielles utilisées

- [AWS Pricing Calculator](https://calculator.aws/)
- [AWS Pricing](https://aws.amazon.com/pricing/)
- [AWS Price List et changements de prix](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/price-changes.html)
- Pages tarifaires de service et catalogues régionaux liés dans le tableau de la section 3.
