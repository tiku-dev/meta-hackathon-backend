"""Write audit entries for sensitive actions.

Usage::

    audit("student.matricule_issued", actor=user, entity=student,
          reason="...", summary={"matricule": "UB-2026-0001"})

Never pass passwords, tokens or payment secrets in ``summary``.
"""
from .middleware import get_request_context
from .models import AuditLog


def audit(action, *, actor=None, entity=None, entity_type="", entity_id="",
          reason="", summary=None):
    ctx = get_request_context()
    if entity is not None:
        entity_type = entity_type or entity.__class__.__name__
        entity_id = str(getattr(entity, "pk", "") or "")
    return AuditLog.objects.create(
        actor=actor if (actor and getattr(actor, "is_authenticated", False)) else None,
        actor_ip=ctx.get("ip") or None,
        request_id=ctx.get("request_id", ""),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        reason=reason or "",
        summary=summary or {},
    )
