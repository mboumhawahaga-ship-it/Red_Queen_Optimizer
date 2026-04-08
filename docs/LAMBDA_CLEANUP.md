# 🤖 Lambda de Cleanup Automatique

## 📋 Vue d'ensemble

Cette Lambda **supprime automatiquement** les ressources AWS créées sans tags obligatoires.

### 🎯 Fonctionnalités

- ✅ Scanne toutes les ressources AWS (EC2, RDS, S3, Lambda)
- ✅ Vérifie la présence des tags obligatoires
- ✅ **Période de grâce** de 24h avant suppression
- ✅ Mode **DRY_RUN** pour simulation
- ✅ Notifications par email (SNS)
- ✅ Exécution planifiée (tous les jours à 2h)

---

## 🔒 Sécurité : Mode DRY_RUN

**⚠️ IMPORTANT** : Par défaut, la Lambda est en mode **SIMULATION**.

```hcl
dry_run = true  # ← NE SUPPRIME RIEN, juste un rapport
```

Pour activer la suppression réelle :
```hcl
dry_run = false  # ← ⚠️ SUPPRIME VRAIMENT LES RESSOURCES !
```

---

## 🚀 Déploiement

### Étape 1 : Modifier la configuration

Éditez [terraform/environments/dev/cleanup-lambda.tf](../terraform/environments/dev/cleanup-lambda.tf) :

```hcl
module "cleanup_lambda" {
  source = "../../modules/cleanup-lambda"

  environment = "dev"

  # Période de grâce : 24 heures
  grace_period_hours = 24

  # MODE SIMULATION (changez à false pour activer)
  dry_run = true

  # VOTRE EMAIL pour les notifications
  notification_email = "votre.email@entreprise.com"  # ← CHANGEZ ICI !

  # Planification : tous les jours à 2h
  schedule_expression = "cron(0 2 * * ? *)"
}
```

### Étape 2 : Déployer

```bash
cd terraform/environments/dev

terraform init
terraform plan
terraform apply
```

### Étape 3 : Confirmer l'abonnement email

Vous recevrez un email de AWS SNS :
```
Subject: AWS Notification - Subscription Confirmation

Cliquez sur "Confirm subscription"
```

---

## 📊 Comment ça fonctionne ?

### Workflow de la Lambda

```
1️⃣  Scan des ressources AWS
    ↓
2️⃣  Vérification des tags obligatoires
    ↓
3️⃣  Tags manquants ?
    ├─ NON → ✅ OK, on passe à la suivante
    └─ OUI → ⏰ Vérifier la période de grâce
              ↓
              Créée il y a < 24h ?
              ├─ OUI → ⏳ En période de grâce, on attend
              └─ NON → 🗑️ Suppression (ou simulation si DRY_RUN)
                       ↓
4️⃣  Envoi du rapport par email (SNS)
```

### Période de grâce

**Pourquoi ?** Pour éviter de supprimer une ressource en cours de création.

**Exemple** :
```
10h00 : Vous créez un serveur EC2 sans tags
10h30 : La Lambda tourne → DÉTECTE le serveur
        → Créé il y a 30 minutes < 24h
        → ⏳ EN PÉRIODE DE GRÂCE, pas de suppression

Le lendemain à 10h30 :
        → Créé il y a 24h30 > 24h
        → 🗑️ SUPPRESSION (si pas de tags)
```

---

## 📧 Format du rapport email

Vous recevrez un email comme ceci :

```
🤖 AWS Tagging Governance - Rapport de cleanup

📊 Résumé :
- Ressources scannées : 47
- Non conformes : 5
- Supprimées : 2

📝 Détails :
- EC2 : 12 scannées, 2 non conformes, 1 supprimée
- RDS : 8 scannées, 1 non conforme, 0 supprimée
- S3 : 25 scannés, 2 non conformes, 1 supprimé
- Lambda : 2 scannées, 0 non conforme, 0 supprimée

⚙️  Mode : 🔍 DRY_RUN (simulation)

⏰ Date : 2026-02-08 02:00:00
```

---

## 🧪 Tester manuellement

### Depuis la console AWS

1. Allez sur AWS Lambda
2. Cherchez `dev-tag-cleanup`
3. Cliquez sur "Test"
4. Créez un événement de test vide : `{}`
5. Cliquez sur "Invoke"

### Depuis le terminal

```bash
aws lambda invoke \
  --function-name dev-tag-cleanup \
  --payload '{}' \
  response.json

cat response.json
```

---

## 📋 Logs CloudWatch

### Voir les logs

1. AWS Console → CloudWatch → Log groups
2. Cherchez `/aws/lambda/dev-tag-cleanup`
3. Consultez les dernières exécutions

### Depuis le terminal

```bash
aws logs tail /aws/lambda/dev-tag-cleanup --follow
```

**Exemple de logs** :
```
🚀 Démarrage du cleanup - DRY_RUN=true
⏰ Période de grâce : 24 heures
🖥️  Scan des instances EC2...
❌ i-1234567890abcdef0 : Non conforme (tags manquants : ['Owner', 'Squad'])
🔍 DRY_RUN : i-1234567890abcdef0 serait supprimé
✅ Notification envoyée
```

---

## ⚙️ Configuration avancée

### Changer la planification

```hcl
# Tous les jours à 2h du matin
schedule_expression = "cron(0 2 * * ? *)"

# Tous les lundis à 8h
schedule_expression = "cron(0 8 ? * MON *)"

# Toutes les heures
schedule_expression = "rate(1 hour)"
```

### Modifier la période de grâce

```hcl
# 12 heures au lieu de 24
grace_period_hours = 12

# 48 heures (2 jours)
grace_period_hours = 48
```

### Désactiver la planification automatique

```hcl
# Exécution manuelle uniquement
enable_schedule = false
```

---

## 🛡️ Permissions IAM

La Lambda a ces permissions :

| Service | Actions | Pourquoi |
|---------|---------|----------|
| **EC2** | `DescribeInstances`, `TerminateInstances` | Lister et supprimer les instances |
| **RDS** | `DescribeDBInstances`, `DeleteDBInstance` | Lister et supprimer les BDD |
| **S3** | `ListAllMyBuckets`, `DeleteBucket` | Lister et supprimer les buckets |
| **Lambda** | `ListFunctions`, `DeleteFunction` | Lister et supprimer les fonctions |
| **SNS** | `Publish` | Envoyer les notifications |

---

## ⚠️ Checklist avant activation (DRY_RUN=false)

Avant de passer en mode production :

- [ ] Vous avez testé en mode DRY_RUN
- [ ] Vous avez lu les rapports par email
- [ ] Vous êtes sûr des ressources qui seront supprimées
- [ ] Vous avez prévenu les équipes
- [ ] Vous avez un backup/snapshot des ressources critiques
- [ ] Vous avez configuré la bonne période de grâce

**Puis** :
```hcl
dry_run = false  # ⚠️ ACTIVER LA SUPPRESSION RÉELLE
```

```bash
terraform apply
```

---

## 🐛 Dépannage

### La Lambda ne s'exécute pas

**Vérifiez** :
```bash
# Voir si la planification est active
aws events list-rules --name-prefix dev-tag-cleanup

# Voir les dernières invocations
aws lambda get-function --function-name dev-tag-cleanup
```

### Pas de notification email

**Vérifiez** :
1. Avez-vous confirmé l'abonnement SNS ?
2. Le SNS_TOPIC_ARN est-il correct ?

```bash
# Lister les abonnements SNS
aws sns list-subscriptions
```

### Erreur "Access Denied"

La Lambda n'a pas les permissions IAM nécessaires.

**Solution** :
```bash
terraform apply  # Re-déployer pour mettre à jour les permissions
```

---

## 📚 Ressources

- [Code Python de la Lambda](../lambda/cleanup/handler.py)
- [Module Terraform](../terraform/modules/cleanup-lambda/)
- [Exemple d'utilisation](../terraform/environments/dev/cleanup-lambda.tf)

---

**Besoin d'aide ?** Consultez les logs CloudWatch ou contactez l'équipe Cloud Governance.
