# Journal de Bord - AWS Tagging Governance

> Carnet de laboratoire AWS - Erreurs rencontrées, solutions appliquées et lecons retenues.
> Date : 12 Fevrier 2026 | Region : eu-west-1 (Ireland) | Compte : 123456789012

---

## Table des matieres

1. [Erreur #1 - RDS : username "admin" reserve](#erreur-1---rds--username-admin-reserve)
2. [Erreur #2 - S3 : nom de bucket deja pris](#erreur-2---s3--nom-de-bucket-deja-pris)
3. [Erreur #3 - Lambda : fichier ZIP introuvable](#erreur-3---lambda--fichier-zip-introuvable)
4. [Erreur #4 - Chemin de module incorrect (path.root vs path.module)](#erreur-4---chemin-de-module-incorrect-pathroot-vs-pathmodule)
5. [Amelioration #1 - timestamp() provoque du drift](#amelioration-1---timestamp-provoque-du-drift)
6. [Amelioration #2 - Validation du Owner trop faible](#amelioration-2---validation-du-owner-trop-faible)
7. [Amelioration #3 - Suppression S3 avec versioning](#amelioration-3---suppression-s3-avec-versioning)
8. [Bug #1 - S3 : exception NoSuchTagSet non portable](#bug-1---s3--exception-nosuchtagset-non-portable)
9. [Tests unitaires - Lambda cleanup (14/02/2026)](#tests-unitaires---lambda-cleanup-14022026)
10. [Checklist de deploiement](#checklist-de-deploiement)
11. [Ressources deployees](#ressources-deployees)

---

## Erreur #1 - RDS : username "admin" reserve

| | |
|---|---|
| **Service** | Amazon RDS (PostgreSQL) |
| **Erreur** | `InvalidParameterValue: The parameter MasterUsername is not a valid identifier` |
| **Cause** | `admin` est un **mot reserve** dans PostgreSQL. AWS refuse de creer une instance RDS avec ce username. |
| **Fichier** | `terraform/environments/dev/main.tf` (ligne 62) |

### Avant (KO)
```hcl
rds_master_username = "admin"    # INTERDIT par PostgreSQL
```

### Apres (OK)
```hcl
rds_master_username = "dbadmin"  # Fonctionne
```

### Regle a retenir
> Les mots reserves PostgreSQL ne peuvent pas etre utilises comme `MasterUsername` dans RDS.
> Exemples de mots interdits : `admin`, `user`, `public`, `session`, `default`.
> Bonne pratique : utiliser un prefixe comme `db_`, `app_`, `rds_`.

---

## Erreur #2 - S3 : nom de bucket deja pris

| | |
|---|---|
| **Service** | Amazon S3 |
| **Erreur** | `BucketAlreadyExists: The requested bucket name is not available` |
| **Cause** | Les noms de buckets S3 sont **uniques mondialement** (pas juste dans ton compte). `dev-data-lake` etait deja pris par un autre compte AWS dans le monde. |
| **Fichier** | `terraform/environments/dev/main.tf` (ligne 92) |

### Avant (KO)
```hcl
resource_name = "data-lake"      # "dev-data-lake" deja pris mondialement
```

### Apres (OK)
```hcl
resource_name = "data-lake-123456789012"  # Ajout de l'Account ID = unique
```

### Regle a retenir
> Les buckets S3 ont un namespace **GLOBAL**. Deux comptes AWS ne peuvent pas avoir le meme nom de bucket.
>
> Strategies pour noms uniques :
> - Ajouter l'Account ID : `mon-bucket-123456789012`
> - Ajouter un hash aleatoire : `mon-bucket-a3f8c2`
> - Ajouter le nom de l'entreprise : `entreprise-mon-bucket-dev`
>
> En Terraform, on peut automatiser avec `data.aws_caller_identity.current.account_id`.

---

## Erreur #3 - Lambda : fichier ZIP introuvable

| | |
|---|---|
| **Service** | AWS Lambda |
| **Erreur** | `unable to load "../../modules/tagged-resources/placeholder_lambda.zip": open ...: The system cannot find the file specified` |
| **Cause** | Le module `tagged-resources` reference un `placeholder_lambda.zip` pour deployer les Lambdas, mais ce fichier n'existait pas. |
| **Fichier** | `terraform/modules/tagged-resources/main.tf` |

### Solution
Creer le fichier manquant :
```python
# placeholder_lambda.py
def handler(event, context):
    return {"statusCode": 200, "body": "Placeholder Lambda"}
```
Puis le compresser en `placeholder_lambda.zip` dans le meme dossier.

### Regle a retenir
> Quand on definit une ressource `aws_lambda_function` avec `filename`, le fichier ZIP **doit exister**
> au moment du `terraform plan`/`apply`, meme si c'est un placeholder.
>
> Bonne pratique : inclure le ZIP placeholder dans le repo Git, ou utiliser un `data "archive_file"`
> pour le generer automatiquement.

---

## Erreur #4 - Chemin de module incorrect (path.root vs path.module)

| | |
|---|---|
| **Service** | Terraform (module cleanup-lambda) |
| **Erreur** | `error reading file: The system cannot find the path specified` |
| **Cause** | Confusion entre `path.root` et `path.module` dans le module `cleanup-lambda`. |
| **Fichier** | `terraform/modules/cleanup-lambda/main.tf` (ligne 114) |

### Avant (KO)
```hcl
source_dir = "${path.root}/../../lambda/cleanup"
# path.root = terraform/environments/dev/
# Resultat : terraform/lambda/cleanup (FAUX)
```

### Apres (OK)
```hcl
source_dir = "${path.module}/../../../lambda/cleanup"
# path.module = terraform/modules/cleanup-lambda/
# Resultat : lambda/cleanup (CORRECT)
```

### Regle a retenir
> | Variable | Pointe vers | Exemple |
> |----------|-------------|---------|
> | `path.root` | Dossier ou tu lances `terraform apply` | `terraform/environments/dev/` |
> | `path.module` | Dossier du module **actuel** | `terraform/modules/cleanup-lambda/` |
> | `path.cwd` | Dossier de travail courant | Ou tu es dans le terminal |
>
> **Regle** : Dans un module reutilisable, toujours utiliser `path.module`.
> `path.root` ne fonctionne correctement que dans le module racine.

---

## Amelioration #1 - timestamp() provoque du drift

| | |
|---|---|
| **Probleme** | `timestamp()` retourne l'heure actuelle a chaque `plan`. Resultat : Terraform veut modifier le tag `CreatedAt` a chaque execution, meme sans aucun changement reel. |
| **Impact** | Faux positifs dans les plans, bruit inutile, difficulte a identifier les vrais changements. |
| **Fichier** | `terraform/modules/tagged-resources/main.tf` + `versions.tf` |

### Avant (mauvaise pratique)
```hcl
locals {
  mandatory_tags = {
    CreatedAt = timestamp()    # Change a CHAQUE plan !
  }
}
```

### Apres (bonne pratique)
```hcl
# versions.tf - Ajout du provider
terraform {
  required_providers {
    time = {
      source  = "hashicorp/time"
      version = "~> 0.11"
    }
  }
}

# main.tf
resource "time_static" "created" {}

locals {
  mandatory_tags = {
    CreatedAt = time_static.created.rfc3339  # Fixe a la creation, ne change plus
  }
}
```

### Regle a retenir
> `timestamp()` = valeur dynamique recalculee a chaque plan. A eviter dans les tags/attributs.
> `time_static` = valeur fixee une seule fois lors de la creation de la ressource. Stable et previsible.

---

## Amelioration #2 - Validation du Owner trop faible

| | |
|---|---|
| **Probleme** | La validation `length(var.owner) > 0` accepte n'importe quelle chaine : "toto", "123", "x". Impossible de garantir la tracabilite. |
| **Impact** | Des ressources avec des owners non identifiables, inutiles pour la gouvernance. |
| **Fichier** | `terraform/modules/tagged-resources/variables.tf` |

### Avant
```hcl
variable "owner" {
  validation {
    condition     = length(var.owner) > 0
    error_message = "Owner is mandatory"
  }
}
```

### Apres
```hcl
variable "owner" {
  validation {
    condition     = can(regex("^[\\w\\-\\.]+@entreprise\\.com$", var.owner))
    error_message = "L'owner doit etre une adresse email valide (@entreprise.com)"
  }
}
```

### Regle a retenir
> Terraform `validation` blocks sont le premier rempart contre les mauvaises donnees.
> Plus la validation est stricte, moins il y a de problemes en production.
> Utiliser `can(regex(...))` pour valider des formats (email, ARN, CIDR, etc.).

---

## Amelioration #3 - Suppression S3 avec versioning

| | |
|---|---|
| **Probleme** | La Lambda de cleanup essayait de supprimer les buckets S3 avec un simple `delete_objects`, mais quand le **versioning** est active, les objets ne sont pas vraiment supprimes. AWS cree des "delete markers" et les anciennes versions restent. |
| **Impact** | Impossible de supprimer le bucket (`BucketNotEmpty`), la Lambda echoue silencieusement. |
| **Fichier** | `lambda/cleanup/handler.py` |

### Avant (incomplet)
```python
def delete_all_objects_in_bucket(bucket_name):
    objects = s3_client.list_objects_v2(Bucket=bucket_name)
    # Ne gere pas les versions ni les delete markers !
```

### Apres (complet)
```python
def delete_all_objects_in_bucket(bucket_name):
    paginator = s3_client.get_paginator('list_object_versions')
    pages = paginator.paginate(Bucket=bucket_name)

    for page in pages:
        objects_to_delete = []

        # 1. Supprimer toutes les VERSIONS d'objets
        for version in page.get('Versions', []):
            objects_to_delete.append({
                'Key': version['Key'],
                'VersionId': version['VersionId']
            })

        # 2. Supprimer tous les DELETE MARKERS
        for marker in page.get('DeleteMarkers', []):
            objects_to_delete.append({
                'Key': marker['Key'],
                'VersionId': marker['VersionId']
            })

        # 3. Supprimer par lots de 1000 (limite AWS)
        if objects_to_delete:
            for i in range(0, len(objects_to_delete), 1000):
                batch = objects_to_delete[i:i + 1000]
                s3_client.delete_objects(
                    Bucket=bucket_name,
                    Delete={'Objects': batch}
                )
```

### Regle a retenir
> Quand le **versioning S3** est active :
> - `DeleteObject` ne supprime pas l'objet, il cree un **delete marker**
> - Les anciennes versions restent stockees (et facturees !)
> - Pour vraiment vider un bucket : supprimer toutes les **versions** ET tous les **delete markers**
> - `delete_objects` accepte max **1000 objets** par appel, il faut paginer
> - Alternative Terraform : utiliser `force_destroy = true` sur le bucket

---

## Bug #1 - S3 : exception NoSuchTagSet non portable

| | |
|---|---|
| **Service** | Amazon S3 (via boto3) |
| **Erreur** | `object has no attribute NoSuchTagSet. Valid exceptions are: BucketAlreadyExists, ...` |
| **Cause** | Le code utilisait `s3_client.exceptions.NoSuchTagSet` pour gerer les buckets sans tags. Cette exception **n'est pas disponible partout** : elle depend de la version de boto3 et ne fonctionne pas avec les mocks (moto). |
| **Decouvert par** | Tests unitaires avec pytest + moto |
| **Fichier** | `lambda/cleanup/handler.py` (ligne 221) |

### Avant (KO)
```python
try:
    tags_response = s3_client.get_bucket_tagging(Bucket=bucket_name)
    tags = tags_response.get('TagSet', [])
except s3_client.exceptions.NoSuchTagSet:  # N'existe pas partout !
    tags = []
```

### Apres (OK)
```python
from botocore.exceptions import ClientError

try:
    tags_response = s3_client.get_bucket_tagging(Bucket=bucket_name)
    tags = tags_response.get('TagSet', [])
except ClientError as e:
    if e.response['Error']['Code'] in ('NoSuchTagSet', 'NoSuchTagConfiguration'):
        tags = []
    else:
        raise
```

### Regle a retenir
> Les exceptions "dynamiques" de boto3 (`client.exceptions.XYZ`) ne sont pas toujours fiables.
> La methode standard et portable est d'attraper `ClientError` et verifier le code d'erreur
> dans `e.response['Error']['Code']`.
>
> Autre lecon : le dossier s'appelle `lambda/` mais `lambda` est un **mot reserve en Python**
> (comme `if`, `for`, `class`). On ne peut pas ecrire `import lambda.cleanup.handler`.
> Solution dans les tests : utiliser `sys.path` pour pointer directement vers le dossier,
> puis faire `import handler`.

---

## Tests unitaires - Lambda cleanup (14/02/2026)

| | |
|---|---|
| **Outil** | pytest 9.0.2 + moto 5.1.21 (simulateur AWS) |
| **Fichier** | `lambda/cleanup/test_handler.py` |
| **Resultat** | **12/12 tests PASSED** |

### Tests realises

| # | Test | Quoi | Resultat |
|---|------|------|----------|
| 1 | `test_check_required_tags_conforme` | Tags complets = acceptes | PASSED |
| 2 | `test_check_required_tags_incomplet` | Tags incomplets = rejetes + liste des manquants | PASSED |
| 3 | `test_check_required_tags_vide` | Liste vide = 4 tags manquants | PASSED |
| 4 | `test_check_required_tags_none` | None = pas de crash | PASSED |
| 5 | `test_ec2_sans_tags_est_supprimee` | EC2 sans tags = terminee | PASSED |
| 6 | `test_ec2_avec_tags_corrects_est_preservee` | EC2 avec tags = intacte | PASSED |
| 7 | `test_ec2_dry_run_ne_supprime_pas` | DRY_RUN = detecte mais ne supprime pas | PASSED |
| 8 | `test_s3_sans_tags_est_detecte` | S3 sans tags = non conforme | PASSED |
| 9 | `test_s3_avec_tags_est_preserve` | S3 avec tags = intacte | PASSED |
| 10 | `test_s3_avec_objets_est_vide_avant_suppression` | S3 avec objets = vide puis supprime | PASSED |
| 11 | `test_lambda_sans_tags_est_detectee` | Lambda sans tags = non conforme | PASSED |
| 12 | `test_mix_conformes_et_non_conformes` | Mix de ressources = tri correct | PASSED |

### Comment relancer les tests
```bash
# Installer les dependances (une seule fois)
pip install pytest moto boto3

# Lancer les tests
python -m pytest lambda/cleanup/test_handler.py -v
```

### Lecons apprises
> - **Toujours tester avant de push** : les tests ont revele un vrai bug S3 (exception non portable)
> - **moto** permet de simuler AWS gratuitement, sans toucher au vrai compte
> - **pytest** + **moto** = combo ideal pour tester des Lambdas Python
> - Les clients boto3 sont crees au moment de l'import du module : il faut `importlib.reload()`
>   dans les tests pour que les mocks soient actifs

---

## Checklist de deploiement

Avant chaque `terraform apply`, verifier :

- [ ] **RDS** : le `master_username` n'est pas un mot reserve (`admin`, `user`, `public`...)
- [ ] **S3** : le nom du bucket est unique mondialement (ajouter account ID ou hash)
- [ ] **Lambda** : le fichier ZIP existe et est accessible depuis le module
- [ ] **Modules** : utiliser `path.module` (pas `path.root`) pour les chemins relatifs
- [ ] **Tags** : les validations sont strictes (regex email, format cost center...)
- [ ] **Timestamps** : utiliser `time_static` au lieu de `timestamp()`
- [ ] **AWS CLI** : bien configure (`aws sts get-caller-identity` pour verifier)
- [ ] **Region** : verifier qu'on est dans la bonne region (`eu-west-1`)

---

## Ressources deployees

### Infrastructure creee le 12/02/2026

| Ressource | Type | ID | Tags Owner |
|-----------|------|----|------------|
| dev-web-server | EC2 (t3.micro) | `i-0123456789abcdef0` | jean.dupont@entreprise.com |
| dev-analytics-db | RDS PostgreSQL (db.t3.micro) | `db-XXXXXXXXXXXXXXXXXXXXXXXXXXXX` | marie.martin@entreprise.com |
| dev-data-lake-123456789012 | S3 Bucket | `dev-data-lake-123456789012` | paul.durand@entreprise.com |
| dev-data-processor | Lambda (Python 3.11) | `dev-data-processor` | sophie.leblanc@entreprise.com |
| dev-tag-cleanup | Lambda (Python 3.11) | `dev-tag-cleanup` | CloudGovernance |

### Endpoints

| Service | Endpoint |
|---------|----------|
| EC2 (IP publique) | `203.0.113.42` |
| RDS PostgreSQL | `dev-analytics-db.xxxxxxxxxxxx.eu-west-1.rds.amazonaws.com:5432` |
| Cleanup Lambda | Cron tous les jours a 2h UTC (`cron(0 2 * * ? *)`) |
| Mode cleanup | `DRY_RUN` (simulation, aucune suppression reelle) |

### Nettoyage
```bash
# Pour tout supprimer et eviter les frais :
terraform destroy -auto-approve
```

---

## Outils utilises

| Outil | Version | Utilite |
|-------|---------|---------|
| Terraform | 1.13.3 | Infrastructure as Code |
| AWS CLI | v2 | Connexion au compte AWS |
| tfsec | latest | Analyse de securite du code Terraform |
| infracost | 0.2.35 | Estimation des couts AWS |
| Provider AWS | 5.100.0 | Provider Terraform pour AWS |
| Provider time | 0.13.1 | Timestamps stables |
| Provider random | 3.8.1 | Generation de mots de passe |
| Provider archive | 2.7.1 | Creation de ZIP pour Lambda |

---

> *"En science comme en cloud, on documente ses erreurs pour ne pas les reproduire."*
