# Red Queen Optimizer

[![Quality & Security Check](https://github.com/mboumhawahaga-ship-it/Red_Queen_Optimizer/actions/workflows/ci-quality.yml/badge.svg)](https://github.com/mboumhawahaga-ship-it/Red_Queen_Optimizer/actions/workflows/ci-quality.yml)

**Système de gouvernance de tagging AWS — alertes, auto-tagging et remédiation progressive.**

> "On ne supprime jamais automatiquement en production."
> Red Queen avertit, tente de corriger, escalade — et ne supprime qu'en tout dernier recours avec approbation humaine.

---

## Vue d'ensemble

```
EventBridge (cron 2h du matin)
        │
        ▼
  Lambda Cleanup          ← scanne EC2, RDS, S3, Lambda
        │
        ├── Tag CRITIQUE manquant        ├── Tag NON-CRITIQUE manquant
        │   (Owner, CostCenter, Env)     │   (Project, Team)
        │                                │
        ▼                                ▼
  FAST TRACK — 36h SLA           SLOW TRACK — 7 jours
  ┌──────────────────────┐       ┌──────────────────────┐
  │ 1. Auto-tag          │       │ 1. Email warning     │
  │ 2. Email + freeze    │       │ 2. Attente 3 jours   │
  │ 3. Attente 18h       │       │ 3. Auto-tag          │
  │ 4. Rappel manager    │       │ 4. Attente 4 jours   │
  │ 5. Approbation avant │       │ 5. Freeze + escalade │
  │    suppression       │       └──────────────────────┘
  └──────────────────────┘
        │
        ▼
  Lambda Feedback         ← bouton "j'ai géré" dans les emails
  DynamoDB                ← registre complet des événements
  Lambda Metrics          ← stats CloudWatch toutes les 6h
```

---

## Ce qui rend ce projet "real-world"

| Fonctionnalité | Description |
|----------------|-------------|
| **Tiered Alert System** | SLA 36h pour tags critiques, 7 jours pour non-critiques |
| **Auto-Tagging** | Interroge CloudTrail pour inférer Owner, Environment, CostCenter |
| **Feedback Mechanism** | Lien "j'ai géré" dans les emails — bloque la suppression |
| **Human approval** | Aucune suppression sans `waitForTaskToken` validé par un humain |
| **DRY_RUN=true** | Mode simulation activé par défaut — zéro risque au démarrage |
| **Audit trail** | Chaque action enregistrée en DynamoDB avec timestamp et auteur |

---

## Tags obligatoires

| Tag | Niveau | Description | Exemple |
|-----|--------|-------------|---------|
| `Owner` | 🔴 Critique | Email du responsable | `jean.dupont@entreprise.com` |
| `CostCenter` | 🔴 Critique | Centre de coûts | `CC-123` |
| `Environment` | 🔴 Critique | Environnement | `dev`, `staging`, `prod` |
| `Squad` | 🟡 Non-critique | Équipe responsable | `Data`, `DevOps` |
| `Project` | 🟡 Non-critique | Projet associé | `Analytics` |

---

## Stack technique

| Composant | Service AWS |
|-----------|-------------|
| Orchestration | AWS Step Functions (Standard) |
| Scan | Lambda Python 3.12 + Lambda Powertools |
| Auto-tagging | Lambda + CloudTrail Lookup |
| Feedback | Lambda URL (sans API Gateway) |
| État | DynamoDB (PAY_PER_REQUEST + TTL) |
| Notifications | SNS Email + Slack (optionnel) |
| Secrets | SSM Parameter Store (SecureString) |
| Déclencheur | EventBridge cron |
| IaC | Terraform modulaire |
| CI/CD | GitHub Actions (flake8 + terraform validate) |

---

## Structure du projet

```
Red_Queen_Optimizer/
├── lambda/
│   ├── cleanup/          # Scan des ressources non conformes
│   ├── auto_tagger/      # Inférence et application automatique des tags
│   ├── feedback/         # Endpoint "j'ai géré" (Lambda URL)
│   ├── step_function/    # Handlers Step Functions (check, notify, remediate)
│   └── metrics/          # Collecte CloudWatch toutes les 6h
├── terraform/
│   ├── modules/
│   │   ├── cleanup-lambda/
│   │   ├── auto-tagger-lambda/
│   │   ├── feedback-lambda/
│   │   ├── step-function/
│   │   ├── dynamodb/
│   │   ├── eventbridge/
│   │   ├── metrics-lambda/
│   │   └── tagged-resources/
│   └── environments/
│       └── dev/
├── docs/
│   ├── JOURNAL_DE_BORD.md   # Historique des décisions techniques
│   ├── PLAN_V2.md           # Plan de restructuration v2
│   └── SECURITY.md
└── docker-compose.yml        # Grafana local (optionnel)
```

---

## Démarrage rapide

```bash
# 1. Cloner le projet
git clone https://github.com/mboumhawahaga-ship-it/Red_Queen_Optimizer.git
cd Red_Queen_Optimizer

# 2. Configurer les credentials AWS
aws configure

# 3. Configurer les variables
cd terraform/environments/dev
cp terraform.tfvars.example terraform.tfvars
# Remplir : feedback_secret (openssl rand -hex 32)

# 4. Déployer
terraform init
terraform plan
terraform apply

# 5. Tester (mode simulation — aucune ressource supprimée)
aws lambda invoke \
  --function-name dev-tag-cleanup \
  --payload '{}' \
  output.json && cat output.json
```

---

## Comportement par type de ressource

| Ressource | Freeze J+0 | Remédiation | Suppression |
|-----------|-----------|-------------|-------------|
| EC2 | Stop instance | Restart si tags corrigés | Terminate (approbation requise) |
| RDS | Snapshot + Stop | Restart si tags corrigés | Delete avec snapshot final |
| S3 | Block public access | Retrait block si corrigé | Jamais supprimé automatiquement |
| Lambda | Concurrency = 0 | Restore si tags corrigés | Delete (approbation requise) |

---

## Sécurité

- `DRY_RUN=true` par défaut — aucune action réelle sans activation explicite
- Suppression uniquement après approbation humaine (`waitForTaskToken`)
- Credentials AWS via `aws configure` — jamais dans un fichier du projet
- Secrets dans SSM Parameter Store (SecureString)
- IAM least privilege sur toutes les Lambdas

---

## Documentation

- [Journal de bord](docs/JOURNAL_DE_BORD.md) — historique des décisions et apprentissages
- [Plan v2](docs/PLAN_V2.md) — roadmap de la restructuration
- [Sécurité](docs/SECURITY.md) — bonnes pratiques appliquées

---

## Licence

MIT License
