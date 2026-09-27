"""Object lookup that never trusts the identifier alone.

Obfuscating the identifier keeps the pipeline from being enumerated, but it is
not an access control. Every detail view and every action resolves its object
through `scoped`, which starts from the queryset the signed-in user is allowed
to see, so an object outside that scope is a 404 (never a 403: a 403 would
confirm that the object exists).
"""
from django.shortcuts import get_object_or_404


def visible_queryset(model, user):
    return model._default_manager.visible_to(user)


def scoped(model, user, public_id, lock=False, **extra):
    queryset = visible_queryset(model, user)
    if lock:
        queryset = queryset.select_for_update()
    return get_object_or_404(queryset, public_id=public_id, **extra)
