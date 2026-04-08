# 📊 Dashboard Grafana - Visualisation des Coûts AWS

## 🎯 Vue d'ensemble

Le dashboard Grafana vous permet de **visualiser en temps réel** :

- 💰 Coûts AWS par équipe, projet, environnement
- 📈 Évolution des dépenses sur 30 jours
- 🏷️ Nombre de ressources par type
- ⚠️ Ressources non conformes (sans tags)
- 📉 Économies réalisées via AutoShutdown

---

## 📸 Aperçu du Dashboard

```
┌──────────────────────────────────────────────────────────────┐
│  💰 Coûts Totaux du Mois : 3,245 €        📉 Économies : 450 € │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  📊 Coûts par Équipe (Donut Chart)    🏷️ Ressources par Type │
│     Data Team:      1,250 €  (38%)        EC2:  25          │
│     Backend Team:     850 €  (26%)        RDS:  12          │
│     DevOps Team:      450 €  (14%)        S3:   48          │
│     Frontend Team:    695 €  (22%)        Lambda: 8         │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  📈 Évolution des Coûts (Line Chart - 30 jours)              │
│     [Graphique avec courbes par équipe]                     │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  💸 Top 10 Ressources les Plus Coûteuses                     │
│  ┌────────────────────────────────────────┐                 │
│  │ Instance     Owner      Cost     Squad │                 │
│  │ prod-db-01   Marie      125€     Data  │                 │
│  │ prod-api-01  Jean       98€      Backend│                │
│  └────────────────────────────────────────┘                 │
├──────────────────────────────────────────────────────────────┤
│  ⚠️ Ressources Sans Tags Obligatoires                        │
│  ┌────────────────────────────────────────┐                 │
│  │ Resource     Type    Missing Tags      │                 │
│  │ test-vm-1    EC2     Owner, Squad      │                 │
│  │ backup-s3    S3      CostCenter        │                 │
│  └────────────────────────────────────────┘                 │
└──────────────────────────────────────────────────────────────┘
```

---

## 🚀 Installation de Grafana

### Option 1 : Grafana Cloud (Recommandé - gratuit)

1. Créez un compte sur https://grafana.com/
2. Cliquez sur "Create a free account"
3. Activez votre Grafana Cloud

### Option 2 : Grafana Local (Docker)

```bash
# Lancer Grafana avec Docker
docker run -d \
  --name=grafana \
  -p 3000:3000 \
  grafana/grafana

# Accédez à http://localhost:3000
# Login par défaut : admin / admin
```

### Option 3 : Installation Windows

1. Téléchargez Grafana : https://grafana.com/grafana/download
2. Installez le fichier `.msi`
3. Démarrez Grafana : `net start grafana`
4. Accédez à http://localhost:3000

---

## 🔌 Configurer AWS CloudWatch comme source de données

### Étape 1 : Créer un utilisateur IAM pour Grafana

```bash
# Créez un utilisateur IAM avec ces permissions
aws iam create-user --user-name grafana-reader

# Attachez la politique CloudWatch ReadOnly
aws iam attach-user-policy \
  --user-name grafana-reader \
  --policy-arn arn:aws:iam::aws:policy/CloudWatchReadOnlyAccess

# Créez des Access Keys
aws iam create-access-key --user-name grafana-reader
```

**Notez** :
- `AccessKeyId`
- `SecretAccessKey`

### Étape 2 : Ajouter CloudWatch dans Grafana

1. Dans Grafana : **Configuration** ⚙️ → **Data Sources** → **Add data source**
2. Cherchez **CloudWatch**
3. Configurez :
   - **Authentication Provider** : `Access & secret key`
   - **Access Key ID** : Votre clé AWS
   - **Secret Access Key** : Votre secret AWS
   - **Default Region** : `eu-west-1` (Paris)
4. Cliquez sur **Save & Test**

✅ Vous devriez voir : "Data source is working"

---

## 📥 Importer le Dashboard

### Méthode 1 : Import du fichier JSON

1. Dans Grafana : **+** → **Import**
2. Cliquez sur **Upload JSON file**
3. Sélectionnez [grafana/dashboards/aws-tagging-costs.json](../grafana/dashboards/aws-tagging-costs.json)
4. Choisissez votre datasource CloudWatch
5. Cliquez sur **Import**

### Méthode 2 : Copier-coller le JSON

1. Ouvrez [aws-tagging-costs.json](../grafana/dashboards/aws-tagging-costs.json)
2. Copiez tout le contenu
3. Dans Grafana : **+** → **Import** → **Import via panel json**
4. Collez le JSON
5. Cliquez sur **Load**

---

## 📊 Panneaux du Dashboard

### 1. 💰 Coûts Totaux du Mois

- **Type** : Stat (chiffre unique)
- **Source** : AWS Billing EstimatedCharges
- **Affiche** : Coût total estimé du mois en cours

### 2. 📊 Coûts par Équipe (Squad)

- **Type** : Pie Chart (camembert)
- **Source** : Cost Explorer avec filtrage par tag `Squad`
- **Affiche** : Répartition des coûts par équipe

### 3. 📈 Évolution des Coûts par Équipe

- **Type** : Time Series (graphique temporel)
- **Source** : Cost Explorer historique
- **Affiche** : Courbes d'évolution sur 30 jours

### 4. 🏷️ Ressources par Type

- **Type** : Bar Gauge
- **Source** : Resource Groups Tagging API
- **Affiche** : Nombre de EC2, RDS, S3, Lambda

### 5. 💸 Top 10 Ressources Coûteuses

- **Type** : Table
- **Source** : Cost Explorer avec détail par ressource
- **Affiche** : Les 10 ressources qui coûtent le plus

### 6. ⚠️ Ressources Sans Tags

- **Type** : Table
- **Source** : Custom CloudWatch Metrics (via Lambda)
- **Affiche** : Liste des ressources non conformes

### 7. 📉 Économies AutoShutdown

- **Type** : Stat
- **Source** : Custom CloudWatch Metrics
- **Affiche** : Économies estimées grâce aux arrêts automatiques

### 8. 🔄 Conformité des Tags (%)

- **Type** : Gauge (jauge)
- **Source** : Custom CloudWatch Metrics
- **Affiche** : Pourcentage de ressources conformes

### 9. 💼 Coûts par Centre de Coûts

- **Type** : Bar Chart
- **Source** : Cost Explorer avec filtrage par tag `CostCenter`
- **Affiche** : Coûts groupés par centre de coûts

---

## 🎨 Personnalisation

### Filtres interactifs (en haut du dashboard)

Le dashboard inclut 3 filtres déroulants :

```
┌─────────────┐  ┌──────────────┐  ┌─────────────────┐
│ Équipe ▼    │  │ Environnement│  │ Centre de Coûts│
│ □ Data      │  │ □ dev        │  │ □ CC-123       │
│ □ Backend   │  │ □ staging    │  │ □ CC-456       │
│ □ DevOps    │  │ □ prod       │  │ □ CC-789       │
└─────────────┘  └──────────────┘  └─────────────────┘
```

**Utilisez ces filtres** pour :
- Voir uniquement les coûts d'une équipe
- Filtrer par environnement (dev/prod)
- Analyser un centre de coûts spécifique

### Modifier les seuils d'alerte

Dans chaque panneau, vous pouvez configurer des seuils :

**Exemple pour "Coûts Totaux"** :
```json
"thresholds": {
  "steps": [
    { "value": 0, "color": "green" },      // 0-1000€ : Vert
    { "value": 1000, "color": "yellow" },  // 1000-5000€ : Jaune
    { "value": 5000, "color": "red" }      // >5000€ : Rouge
  ]
}
```

---

## 📡 Activer AWS Cost Explorer

**Important** : AWS Cost Explorer n'est **pas activé par défaut**.

### Activation

1. AWS Console → **Cost Management** → **Cost Explorer**
2. Cliquez sur **Enable Cost Explorer**
3. Attendez 24 heures pour les premières données

### Activer les tags dans Cost Explorer

1. AWS Console → **Billing** → **Cost Allocation Tags**
2. Activez ces tags :
   - ✅ `Owner`
   - ✅ `Squad`
   - ✅ `CostCenter`
   - ✅ `Environment`
3. Cliquez sur **Activate**

**⏰ Les données apparaissent après 24-48 heures.**

---

## 🔔 Configurer les Alertes Grafana

### Exemple : Alerte si coûts > 5000€

1. Éditez le panneau "Coûts Totaux"
2. Onglet **Alert** → **Create Alert**
3. Configurez :
   ```
   WHEN max() OF query(A, 5m, now)
   IS ABOVE 5000
   FOR 10m
   ```
4. **Contact points** : Email, Slack, Teams
5. **Save**

Vous recevrez une alerte si les coûts dépassent 5000€ pendant 10 minutes.

---

## 📊 Métriques personnalisées (optionnel)

Pour les panneaux avancés (conformité, économies), créez des métriques CloudWatch :

### Script Python pour publier les métriques

```python
import boto3
cloudwatch = boto3.client('cloudwatch')

# Exemple : Publier le % de conformité
cloudwatch.put_metric_data(
    Namespace='TagCompliance',
    MetricData=[
        {
            'MetricName': 'CompliancePercentage',
            'Value': 85.5,  # 85.5% de ressources conformes
            'Unit': 'Percent'
        }
    ]
)
```

**Ajoutez ce script** dans la Lambda de cleanup pour publier automatiquement les métriques.

---

## 🎯 Cas d'usage

### 1. Surveiller le budget d'une équipe

1. Sélectionnez l'équipe dans le filtre "Squad"
2. Regardez "Coûts par Équipe"
3. Configurez une alerte si dépassement

### 2. Identifier les ressources coûteuses

1. Consultez "Top 10 Ressources les Plus Coûteuses"
2. Triez par coût décroissant
3. Contactez les propriétaires (tag `Owner`)

### 3. Vérifier la conformité

1. Regardez "Ressources Sans Tags"
2. Notez les ressources non conformes
3. La Lambda de cleanup les supprimera automatiquement

---

## 🐛 Dépannage

### Pas de données dans le dashboard

**Vérifiez** :
1. Cost Explorer est-il activé ? (24h d'attente)
2. Les tags sont-ils activés dans "Cost Allocation Tags" ?
3. La datasource CloudWatch fonctionne-t-elle ? (Test & Save)

### Erreur "Query returned no data"

**Causes possibles** :
- Aucune ressource créée
- Tags non activés dans Cost Explorer
- Permissions IAM insuffisantes

**Solution** :
```bash
# Vérifier les permissions de l'utilisateur Grafana
aws iam get-user-policy --user-name grafana-reader
```

### Les filtres ne fonctionnent pas

**Solution** : Recréez les variables de templating :
1. Dashboard Settings → **Variables**
2. Ajoutez la variable `Squad` avec query : `tag_values(Squad)`

---

## 📚 Ressources

- [Fichier JSON du dashboard](../grafana/dashboards/aws-tagging-costs.json)
- [Documentation Grafana](https://grafana.com/docs/)
- [AWS Cost Explorer](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/)

---

**Profitez de vos visualisations !** 📊✨
