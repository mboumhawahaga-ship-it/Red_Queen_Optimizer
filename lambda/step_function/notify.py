"""
📣 NOTIFY LAMBDA — Red Queen Step Function
Envoie les alertes email (SNS) et Slack selon le track et le numéro de rappel.

Tracks / reminder_number :
  FAST  #1 → Alerte initiale NON_CRITICAL (7j SLA)
  FAST  #2 → Rappel à 12h
  SLOW  #1 → Alerte initiale CRITICAL (36h SLA)
  SLOW_ESCALATION  #2 → Escalade après 36h (7j supplémentaires)
  SLOW_FINAL_REMINDER #3 → Dernier rappel avant quarantaine

Le taskToken est stocké dans DynamoDB pour que la feedback Lambda
puisse appeler SendTaskSuccess et reprendre l'exécution.
"""

import os
import json
import time
import hmac
import hashlib
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SNS_TOPIC_ARN       = os.environ.get("SNS_TOPIC_ARN", "")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")
FEEDBACK_URL        = os.environ.get("FEEDBACK_URL", "")
FEEDBACK_SECRET     = os.environ.get("FEEDBACK_SECRET", "")
SLACK_WEBHOOK_URL   = os.environ.get("SLACK_WEBHOOK_URL", "")

sns_client = boto3.client("sns")
dynamodb   = boto3.resource("dynamodb")

# Couleurs Slack par sévérité
SLACK_COLORS = {
    "FAST":                "#f39c12",   # orange — attention
    "SLOW":                "#e74c3c",   # rouge — urgent
    "SLOW_ESCALATION":     "#8e44ad",   # violet — escalade
    "SLOW_FINAL_REMINDER": "#2c3e50",   # noir — critique final
}

TRACK_EMOJI = {
    "FAST":                "⚠️",
    "SLOW":                "🔴",
    "SLOW_ESCALATION":     "🚨",
    "SLOW_FINAL_REMINDER": "🆘",
}


def lambda_handler(event: Dict, context: Any) -> Dict:
    track           = event.get("track", "FAST")
    reminder_number = event.get("reminder_number", 1)
    sla_hours       = event.get("sla_hours", 168)
    resource_id     = event.get("resource_id", "")
    resource_type   = event.get("resource_type", "")
    missing_tags    = event.get("missing_tags", [])
    criticality     = event.get("criticality", "NON_CRITICAL")
    task_token      = event.get("task_token")

    logger.info(
        "Notify — track=%s reminder=#%d resource=%s/%s sla=%dh",
        track, reminder_number, resource_type, resource_id, sla_hours
    )

    if task_token:
        _store_task_token(resource_id, task_token, track)

    subject, email_body = _build_email(
        track, reminder_number, sla_hours,
        resource_id, resource_type, missing_tags, criticality
    )
    slack_payload = _build_slack(
        track, reminder_number, sla_hours,
        resource_id, resource_type, missing_tags, criticality
    )

    _send_email(subject, email_body)
    _send_slack(slack_payload)

    return {
        "notified":       True,
        "track":          track,
        "reminder_number": reminder_number,
        "resource_id":    resource_id,
        "channels":       _active_channels(),
    }


# ── Email (SNS) ───────────────────────────────────────────────────────────────

def _build_email(track: str, reminder_number: int, sla_hours: int,
                 resource_id: str, resource_type: str,
                 missing_tags: list, criticality: str) -> tuple:

    emoji   = TRACK_EMOJI.get(track, "⚠️")
    sla_str = f"{sla_hours}h" if sla_hours < 48 else f"{sla_hours // 24} jours"

    reminder_label = {
        1: "Alerte initiale",
        2: "Rappel",
        3: "Dernier avertissement",
    }.get(reminder_number, f"Rappel #{reminder_number}")

    subject = (
        f"[Red Queen] {emoji} {reminder_label} — "
        f"{resource_type} {resource_id} non conforme"
    )

    body = (
        f"{emoji} {reminder_label.upper()}\n"
        f"{'='*50}\n\n"
        f"Ressource    : {resource_type} '{resource_id}'\n"
        f"Criticité    : {criticality}\n"
        f"Tags manquants : {', '.join(missing_tags) if missing_tags else 'aucun détecté'}\n"
        f"SLA restant  : {sla_str}\n\n"
    )

    if track == "FAST":
        body += (
            f"Cette ressource NON_CRITICAL sera mise en quarantaine\n"
            f"(stoppée + accès bloqué) si aucune action n'est prise sous {sla_str}.\n\n"
        )
    elif track == "SLOW":
        body += (
            f"⚠️  RESSOURCE CRITIQUE — Vous avez {sla_str} pour ajouter les tags.\n"
            f"Passé ce délai, une escalade sera déclenchée.\n\n"
        )
    elif track == "SLOW_ESCALATION":
        body += (
            f"🚨 ESCALADE — La ressource CRITIQUE est toujours non conforme.\n"
            f"Vous avez {sla_str} pour agir avant mise en quarantaine.\n\n"
        )
    else:  # SLOW_FINAL_REMINDER
        body += (
            f"🆘 DERNIER AVERTISSEMENT — Quarantaine imminente sous {sla_str}.\n"
            f"La ressource sera stoppée et isolée automatiquement.\n\n"
        )

    if FEEDBACK_URL:
        body += _feedback_links_email(resource_id, resource_type)

    return subject, body


def _feedback_links_email(resource_id: str, resource_type: str) -> str:
    def _tok(action: str) -> str:
        if not FEEDBACK_SECRET:
            return "no-secret"
        return hmac.new(
            FEEDBACK_SECRET.encode(),
            f"{resource_id}:{action}".encode(),
            hashlib.sha256
        ).hexdigest()

    base = FEEDBACK_URL.rstrip("/")
    rt   = resource_type.upper()

    return (
        f"Actions disponibles :\n\n"
        f"✅ Marquer comme traitée (handled) :\n"
        f"   {base}?resource_id={resource_id}&resource_type={rt}"
        f"&action=approve&token={_tok('approve')}\n\n"
        f"🏷️  Tagger maintenant :\n"
        f"   {base}?resource_id={resource_id}&resource_type={rt}"
        f"&action=tag&token={_tok('tag')}"
        f"&owner=VOTRE_EMAIL&squad=VOTRE_SQUAD&cost_center=CC-XXX&environment=dev\n\n"
        f"🗑️  Confirmer la mise en quarantaine :\n"
        f"   {base}?resource_id={resource_id}&resource_type={rt}"
        f"&action=reject&token={_tok('reject')}\n"
    )


def _send_email(subject: str, body: str):
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN non configuré — email ignoré")
        return
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject[:100],  # SNS subject max 100 chars
            Message=body,
        )
        logger.info("Email SNS envoyé : %s", subject)
    except Exception as exc:
        logger.error("Erreur SNS : %s", exc)


# ── Slack ─────────────────────────────────────────────────────────────────────

def _build_slack(track: str, reminder_number: int, sla_hours: int,
                 resource_id: str, resource_type: str,
                 missing_tags: list, criticality: str) -> Dict:

    color   = SLACK_COLORS.get(track, "#f39c12")
    emoji   = TRACK_EMOJI.get(track, "⚠️")
    sla_str = f"{sla_hours}h" if sla_hours < 48 else f"{sla_hours // 24}j"

    fields = [
        {"title": "Ressource",      "value": f"`{resource_type}/{resource_id}`", "short": True},
        {"title": "Criticité",      "value": criticality,                        "short": True},
        {"title": "SLA restant",    "value": sla_str,                            "short": True},
        {"title": "Rappel #",       "value": str(reminder_number),               "short": True},
        {"title": "Tags manquants", "value": ", ".join(missing_tags) or "—",     "short": False},
    ]

    actions = []
    if FEEDBACK_URL:
        def _tok(action: str) -> str:
            if not FEEDBACK_SECRET:
                return "no-secret"
            return hmac.new(
                FEEDBACK_SECRET.encode(),
                f"{resource_id}:{action}".encode(),
                hashlib.sha256
            ).hexdigest()

        base = FEEDBACK_URL.rstrip("/")
        rt   = resource_type.upper()
        actions = [
            {
                "type": "button", "text": "✅ Handled",
                "url":  f"{base}?resource_id={resource_id}&resource_type={rt}"
                        f"&action=approve&token={_tok('approve')}",
                "style": "primary",
            },
            {
                "type": "button", "text": "🏷️ Tag Now",
                "url":  f"{base}?resource_id={resource_id}&resource_type={rt}"
                        f"&action=tag&token={_tok('tag')}"
                        f"&owner=VOTRE_EMAIL&squad=VOTRE_SQUAD&cost_center=CC-XXX&environment=dev",
            },
        ]

    attachment = {
        "color":       color,
        "title":       f"{emoji} Red Queen — Ressource non conforme",
        "fields":      fields,
        "footer":      "Red Queen Governance",
        "ts":          int(time.time()),
    }
    if actions:
        attachment["actions"] = actions

    return {"attachments": [attachment]}


def _send_slack(payload: Dict):
    if not SLACK_WEBHOOK_URL:
        logger.info("SLACK_WEBHOOK_URL non configuré — Slack ignoré")
        return
    try:
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            logger.info("Slack envoyé — status=%d", resp.status)
    except urllib.error.URLError as exc:
        logger.error("Erreur Slack : %s", exc)


def _active_channels() -> list:
    channels = []
    if SNS_TOPIC_ARN:
        channels.append("email")
    if SLACK_WEBHOOK_URL:
        channels.append("slack")
    return channels


# ── DynamoDB : stockage du taskToken ─────────────────────────────────────────

def _store_task_token(resource_id: str, task_token: str, track: str):
    if not DYNAMODB_TABLE_NAME:
        return
    try:
        table      = dynamodb.Table(DYNAMODB_TABLE_NAME)
        now        = datetime.now(timezone.utc).isoformat()
        ttl_expiry = int(time.time()) + (8 * 24 * 3600)

        table.put_item(Item={
            "resource_id":    resource_id,
            "scan_timestamp": now,
            "resource_type":  "TASK_TOKEN",
            "event":          "sfn_wait",
            "task_token":     task_token,
            "track":          track,
            "ttl_expiry":     ttl_expiry,
        })
        logger.info("taskToken stocké pour %s (track=%s)", resource_id, track)
    except Exception as exc:
        logger.error("Erreur stockage taskToken (%s): %s", resource_id, exc)
